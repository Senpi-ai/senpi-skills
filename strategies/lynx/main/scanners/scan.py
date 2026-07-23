"""LYNX — supervised scanner (Runtime 3.0 port of the v2 Lynx adaptive self-tuner).

Multi-asset, whitelist-gated (BTC/ETH/SOL/HYPE by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - runs the SELF-TUNING AUDIT if due (every auditEverySec, default 6h): pulls its
    OWN closed-trade history via audit_query, buckets by the entry score parsed from
    each trade's ai_reasoning, and RAISES the adaptive MIN_SCORE above any bucket at
    or above the current floor that is bleeding (n >= minBucketN, avg ROE < bleed
    threshold). Ratchets UP only; caps at maxMinScore. The new floor + adjustment log
    persist in ctx.state across ticks (v2 persisted them in lynx-state.json),
  - scores every non-held, non-recently-signaled candidate via the pure
    `scoring.build_thesis` using the CURRENT adaptive floor,
  - emits the SINGLE highest-scoring candidate (v2 main() emitted only `best`),
    sized by marginPct (PERCENT) + leverage.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No
daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (lynx-producer.py v1.0.1):
  - v2 persisted the adaptive floor + audit history in lynx-state.json (current_min_
    score, last_audit_at, audit_count, adjustments). This port persists the SAME
    fields in ctx.state (carried in the per-tick record under "lynx"). Semantics are
    identical: first tick seeds current_min_score from initialMinScore; the audit
    runs only when (now - last_audit_at) >= auditEverySec; raises are logged forever.
    DEVIATION (unavoidable): ctx.state is BOUNDED (state_history_max_count); the v2
    JSON adjustments log was unbounded. With a 6h audit cadence and a 200-record
    bound, the full adjustment history is retained for well over a month of raises —
    in practice unbounded for the floor's purpose. Each tick carries the latest
    cumulative adjustments list forward so it is never lost to record eviction.
  - v2 `senpiUserId` (config) gates the audit; this port reads it from inputs
    (auditUserId). If absent, the audit is skipped (floor stays at initialMinScore)
    exactly as in v2 — momentum scoring still runs. The wallet/user id are NOT
    hardcoded (inputs / ${LYNX_WALLET} env via ctx.wallet).
  - v2 build_thesis applied MIN_SCORE internally (returned None below floor). Port
    preserves that — scoring.build_thesis takes current_min_score and gates on it.
    scan() does NOT re-gate; it just collects non-None theses.
  - v2 emitted exactly one signal (best, sorted by (score, |trend_4h|)). Preserved.
  - v2 recent-signals JSON cache (240s TTL) -> ctx.state dedup map (same TTL).
  - v2 marginPct was a FRACTION (0.20) * account_value -> marginUsd. This port emits
    `marginPct` as a PERCENT (20); the runtime sizes (marginPct/100)*withdrawable.
    The "<=1.0 means a pasted fraction, x100" guard normalizes a fraction default.
  - v2 leverage default 3, clamped to MAX_LEVERAGE 5. Preserved (clamp [1,5]).
  - v2 score normalization for the wire (score/8.0) is dropped — the contract takes
    the raw score on data{}; the runtime owns any [0,1] normalization.
"""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")



# v2 defaults (lynx-producer.py / lynx-config.json)
_WHITELIST_DEFAULT = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_RECENT_TTL = 240            # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup
_MAX_LEVERAGE = 5                    # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_LEVERAGE = 3               # v2 DEFAULT_LEVERAGE
_DEFAULT_INITIAL_MIN_SCORE = 4      # v2 DEFAULT_INITIAL_MIN_SCORE (permissive — feeds the auditor)
_DEFAULT_MAX_MIN_SCORE = 7          # v2 DEFAULT_MAX_MIN_SCORE — hard ceiling
_DEFAULT_AUDIT_EVERY_SEC = 21600    # v2 DEFAULT_AUDIT_EVERY_SEC (6h)
_DEFAULT_MIN_BUCKET_N = 8           # v2 DEFAULT_MIN_BUCKET_N
_DEFAULT_BUCKET_BLEED_PCT = -1.0    # v2 DEFAULT_BUCKET_BLEED_PCT
_DEFAULT_AUDIT_LIMIT = 200          # v2 DEFAULT_AUDIT_LIMIT


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Lynx's default whitelist is all main-DEX majors, so this returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


# ── account state (read-guarded; dual-DEX equity collapse + read-sanity guard) ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. account_value via max() across main/xyz (two views of ONE
    cross-margined wallet — summing double-counts the shared free balance -> 2x
    sizing). Ported verbatim from v2 cfg.get_positions, including the read-sanity
    guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[lynx.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning an
    # EMPTY positions list; sizing or running the held-asset dedup off that re-enters
    # held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[lynx.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0). READ-GUARDED.

    Verbatim v2 semantics: long_ratio >= 50 -> ("LONG", long_ratio), else
    ("SHORT", 100 - long_ratio); both sides 0 -> ("NEUTRAL", 50); coin absent ->
    (None, 0). The caller's sm_aligned gate requires tilt >= smTiltMinPct."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a score contributor; never crash the tick
        print(f"[lynx.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw or (isinstance(raw, dict) and raw.get("success") is False):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, coin):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct

    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def _asset_data(ctx, coin):
    """{candles{1h,4h}} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h, no funding/order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[lynx.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    return {"candles": d.get("candles", {}) or {}}


def _fetch_closed_trades(ctx, user_id, limit):
    """Pull closed-trade records via audit_query. Returns a list of
    {score, roe_pct, ts} (best-effort extraction). READ-GUARDED — a failed read
    yields [] so the audit simply finds no buckets and leaves the floor unchanged.
    Ported from v2 fetch_closed_trades."""
    if not user_id:
        return []
    try:
        raw = ctx.senpi_mcp.call_tool("audit_query", {
            "user_ids": [user_id],
            "action_type": "close",
            "limit": int(limit),
        })
    except Exception as exc:  # noqa: BLE001 — audit read must not roll back the tick
        print(f"[lynx.scan] audit_query read failed (self-tune skipped this tick): {exc!r}",
              file=sys.stderr)
        return []
    if not raw:
        return []
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    entries = d if isinstance(d, list) else (d.get("entries", d.get("results", [])) if isinstance(d, dict) else [])
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        reasoning = e.get("ai_reasoning") or e.get("reasoning") or ""
        score = scoring.parse_score_from_reasoning(reasoning)
        roe = e.get("roe_pct") or e.get("pnl_pct") or e.get("roi_pct")
        try:
            roe_v = float(roe) if roe is not None else 0.0
        except (TypeError, ValueError):
            roe_v = 0.0
        ts = e.get("ts") or e.get("timestamp") or e.get("created_at")
        out.append({"score": score, "roe_pct": roe_v, "ts": ts})
    return out


# ── self-tuning audit (the centerpiece) — operates on the persisted lynx state ──

def _run_audit_if_due(ctx, lynx_state, inputs, now):
    """If the audit interval has elapsed, pull closed trades, compute bucket stats,
    and possibly RAISE current_min_score. Returns (updated_lynx_state, audit_summary
    or None if skipped). Ported verbatim from v2 run_audit_if_due — the only change
    is the data source (ctx.senpi_mcp audit_query vs cfg.mcp_call) and the state
    carrier (ctx.state dict vs lynx-state.json)."""
    audit_every = float(inputs.get("auditEverySec", _DEFAULT_AUDIT_EVERY_SEC))
    last_audit_at = lynx_state.get("last_audit_at")
    if last_audit_at is not None:
        try:
            if (now - float(last_audit_at)) < audit_every:
                return lynx_state, None
        except (TypeError, ValueError):
            pass

    user_id = (inputs.get("auditUserId") or "").strip() if isinstance(inputs.get("auditUserId"), str) else inputs.get("auditUserId")
    if not user_id:
        return lynx_state, {"skipped": "no auditUserId in inputs — self-tuning disabled (floor stays at initialMinScore)"}

    initial_min = int(inputs.get("initialMinScore", _DEFAULT_INITIAL_MIN_SCORE))
    trades = _fetch_closed_trades(ctx, user_id, int(inputs.get("auditLimit", _DEFAULT_AUDIT_LIMIT)))
    bucket_stats = scoring.compute_bucket_stats(trades)
    current_min = int(lynx_state.get("current_min_score") or initial_min)
    min_n = int(inputs.get("minBucketN", _DEFAULT_MIN_BUCKET_N))
    bleed_pct = float(inputs.get("bucketBleedThresholdPct", _DEFAULT_BUCKET_BLEED_PCT))
    max_min = int(inputs.get("maxMinScore", _DEFAULT_MAX_MIN_SCORE))

    recommended = scoring.recommend_min_score(bucket_stats, current_min, min_n, bleed_pct, max_min)
    lynx_state = dict(lynx_state)
    lynx_state["last_audit_at"] = float(now)
    lynx_state["audit_count"] = int(lynx_state.get("audit_count", 0)) + 1

    audit_summary = {
        "trades_examined": len(trades),
        "trades_with_score": sum(1 for t in trades if t.get("score") is not None),
        "bucket_stats": {str(k): v for k, v in sorted(bucket_stats.items(), reverse=True)},
        "current_min_score": current_min,
        "recommended_min_score": recommended,
        "updated": False,
    }

    if scoring.should_update_threshold(current_min, recommended):
        adjustments = list(lynx_state.get("adjustments", []))
        adjustments.append({
            "ts": float(now),
            "prev": current_min,
            "new": recommended,
            "trades_examined": len(trades),
            "bleeding_buckets": scoring.bleeding_buckets(bucket_stats, current_min, min_n, bleed_pct),
        })
        lynx_state["adjustments"] = adjustments
        lynx_state["current_min_score"] = recommended
        audit_summary["updated"] = True
        print(f"[lynx.scan] SELF-TUNE: MIN_SCORE {current_min} -> {recommended} "
              f"(examined {len(trades)} trades)", file=sys.stderr)

    return lynx_state, audit_summary


# ── ctx.state: persisted lynx state (floor + audit log) + recent-signal dedup ──

def _load_state(ctx):
    """Return (lynx_state, signaled) from the latest ctx.state record. lynx_state
    carries the adaptive floor + audit log; signaled is the dedup map. Both default
    empty/seeded on first tick."""
    default_lynx = {
        "current_min_score": None,    # None = seed from initialMinScore
        "last_audit_at": None,
        "audit_count": 0,
        "adjustments": [],
    }
    if ctx.state is None or len(ctx.state) == 0:
        return default_lynx, {}
    last = ctx.state.last() or {}
    lynx_state = last.get("lynx")
    lynx_state = dict(lynx_state) if isinstance(lynx_state, dict) else default_lynx
    sig = last.get("signaled", {})
    signaled = dict(sig) if isinstance(sig, dict) else {}
    return lynx_state, signaled


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    whitelist = inputs.get("whitelist", _WHITELIST_DEFAULT)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    initial_min = int(inputs.get("initialMinScore", _DEFAULT_INITIAL_MIN_SCORE))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    lynx_state, signaled = _load_state(ctx)
    signaled = _prune_signaled(signaled, ttl, now)
    if lynx_state.get("current_min_score") is None:
        lynx_state["current_min_score"] = initial_min

    # ── self-tuning audit (runs only when due; raises the floor) ──
    lynx_state, audit_summary = _run_audit_if_due(ctx, lynx_state, inputs, now)
    current_min_score = int(lynx_state.get("current_min_score", initial_min))

    # ── score every eligible whitelist candidate (held + recently-signaled filtered
    #    BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for coin in whitelist:
        if not coin:
            continue
        cu = str(coin).upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        candles = md["candles"]
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(
            coin, candles.get("4h", []), candles.get("1h", []),
            sm, current_min_score, inputs,
        )
        if th:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "minScore": current_min_score, "held": held_assets,
                  "note": f"WAITING (MIN_SCORE {current_min_score})"}
        print(f"[lynx.scan] WAITING — no asset cleared MIN_SCORE={current_min_score}; "
              f"scanned={scanned} held={held_assets}"
              + (f" | self-tune {audit_summary}" if audit_summary and audit_summary.get('updated') else ""),
              file=sys.stderr)
    else:
        # v2 sorted by (score, |trend_4h_pct|) desc and emitted exactly best.
        candidates.sort(key=lambda c: (c["score"], abs(c["trend_4h_pct"])), reverse=True)
        best = candidates[0]

        # marginPct PERCENT in (0,100]. v2 marginPct was a FRACTION (0.20); a value
        # <= 1.0 is a pasted fraction -> x100 (dire/koala guard).
        margin_pct = float(inputs.get("marginPct", 20))
        if margin_pct <= 1.0:
            margin_pct *= 100.0

        leverage = min(int(inputs.get("leverage", _DEFAULT_LEVERAGE)), _MAX_LEVERAGE)
        leverage = max(leverage, 1)

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "minScore": current_min_score,
                  "leverage": leverage, "marginPct": round(margin_pct, 4),
                  "held": held_assets, "reasons": best["reasons"]}
        print(f"[lynx.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"floor={current_min_score} {leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}",
              file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "currentMinScore": current_min_score,
                "trend4hPct": best["trend_4h_pct"],
                "trend1hPct": best["trend_1h_pct"],
                "smDirection": best["sm_direction"] or "NONE",
                "smTiltPct": best["sm_tilt_pct"],
                "volRising": bool(best["vol_rising"]),
                "heldAssets": held_assets,
            },
        }]

    # ── persist lynx state (floor + audit log) + dedup map + this tick's result
    #    every tick; bounded by state_history_max_count. Each record carries the
    #    full cumulative lynx_state forward so the floor + adjustments survive
    #    record eviction. ──
    if ctx.state is not None:
        try:
            ctx.state.append({"lynx": lynx_state, "signaled": signaled,
                              "audit": audit_summary, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[lynx.scan] WARNING: state append failed; floor/dedup may reset to "
                  f"seed next tick: {exc!r}", file=sys.stderr)
    return out
