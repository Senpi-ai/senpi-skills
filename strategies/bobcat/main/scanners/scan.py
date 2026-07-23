"""BOBCAT — supervised scanner (Runtime 3.0 port of the v2 Bobcat big-tech follower).

Multi-asset, whitelist-gated big-tech equity perps on Hyperliquid XYZ (HIP-3 DEX):
NVDA/TSLA/AAPL/META/MSFT/GOOGL/AMZN/AMD/MU/INTC/TSM/ORCL. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - iterates the whitelist (held + recently-signaled filtered BEFORE the per-asset
    MCP fetch, as in v2 main()),
  - scores each candidate via the pure `scoring.build_thesis` (4h-trend gate +
    Smart-Money-direction gate + tilt floor),
  - emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main() emitted
    only `best`), sized at a flat marginPct / leverage.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No
daemon, no push_signal, no create_position.

XYZ notes (preserved from v2 — do NOT redesign):
  - every asset is xyz:NAME, dex = "xyz" — the prefix is mandatory for the HIP-3 DEX.
  - 23/5 trading; the 48h hard_timeout (in runtime.yaml) caps holds across the
    weekend pricing gap. There is NO scan-level market-hours gate (none in v2).

FIDELITY NOTES vs the v2 producer (bobcat-producer.py v1.0.1):
  - v2's get_positions / build_thesis / fetch_sm_direction / scoring are ported
    VERBATIM (math + thresholds + gate order). The SM-direction thresholds are
    Bobcat's own (long_ratio >= 50 -> LONG, else SHORT; NEUTRAL only when total<=0),
    NOT Bison's 58/42 band — preserved exactly.
  - v2 stored margin as a FRACTION (config marginPct 0.20) and computed
    marginUsd = account_value * 0.20. In Runtime 3.0 the runtime sizes from a
    PERCENT in (0,100], so this port emits `marginPct` (20). A defensive guard
    converts any pasted FRACTION (<=1.0) to a PERCENT (*100), matching dire/koala.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics, TTL 240s).
  - v2 leverage = min(config.leverage, MAX_LEVERAGE 5); preserved (clamped to 5).
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



# v2 defaults (bobcat-producer.py / bobcat-config.json)
_DEFAULT_UNIVERSE = [
    "xyz:NVDA", "xyz:TSLA", "xyz:AAPL", "xyz:META", "xyz:MSFT",
    "xyz:GOOGL", "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC",
    "xyz:TSM", "xyz:ORCL",
]
_DEFAULT_MIN_SCORE = 5            # v2 DEFAULT_MIN_SCORE
_DEFAULT_SM_TILT_MIN = 55         # v2 DEFAULT_SM_TILT_MIN
_DEFAULT_SM_STRONG = 70          # v2 DEFAULT_SM_STRONG
_DEFAULT_MARGIN_PCT = 20          # v2 config 0.20 FRACTION -> 20 PERCENT
_DEFAULT_LEVERAGE = 5             # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5                # v2 MAX_LEVERAGE (hardcoded clamp)
_DEFAULT_RECENT_TTL = 240        # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[bobcat.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[bobcat.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0.0).
    READ-GUARDED.

    v2-quirk: token match is case-insensitive uppercase. Verbatim thresholds:
    long_ratio >= 50 -> (LONG, long_ratio); else -> (SHORT, 100 - long_ratio).
    total<=0 -> (NEUTRAL, 50.0). Not-found -> (None, 0.0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — SM is a hard gate; a read error degrades to (None,0) -> gate blocks
        print(f"[bobcat.scan] leaderboard_get_markets read failed (smart-money -> none): {exc!r}",
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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def _asset_data(ctx, coin, dex):
    """{candles_1h, candles_4h} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h candles, no funding/order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[bobcat.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    return {"candles_1h": candles.get("1h", []) or [], "candles_4h": candles.get("4h", []) or []}


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


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
    universe = inputs.get("universe", _DEFAULT_UNIVERSE)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    lev_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct: PERCENT in (0,100]. Defensive fraction guard (dire/koala pattern):
    # a value <= 1.0 is a pasted v2 FRACTION (e.g. 0.20) -> *100 -> 20.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible whitelist candidate (held + recently signaled
    #    filtered BEFORE the per-asset MCP fetch, exactly as v2 main()) ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin, _dex_for(coin, inputs))
        if not md:
            continue
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(coin, md["candles_1h"], md["candles_4h"], sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[bobcat.scan] WAITING — no big-tech setup with 4h trend + SM agreement "
              f"(min score {min_score:.0f}); scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        # leverage: clamp v2 config default to MAX_LEVERAGE (verbatim min(cfg, 5)).
        leverage = min(lev_cfg, _MAX_LEVERAGE)

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[bobcat.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes (marginPct/100)*withdrawable
            "leverage": leverage,             # flat 5x; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "trend4h": best["trend_4h"],
                "trend4hStrength": best["trend_4h_strength"],
                "trend1h": best["trend_1h"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smTiltPct": best["sm_tilt_pct"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result EVERY tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[bobcat.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
