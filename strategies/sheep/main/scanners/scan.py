"""SHEEP — supervised scanner (Runtime 3.0 port of the v2 Sheep producer).

Long-only triple-EMA-stacked trend follower. Multi-asset, whitelist-gated
(BTC/ETH/SOL/HYPE by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for each non-held, non-recently-signaled whitelist asset, fetches 15m/1h/4h
    candles and scores via the pure `scoring.build_thesis` (HARD GATE: fast EMA >
    slow EMA on ALL `minStackedFrames` timeframes),
  - emits the SINGLE highest-scoring candidate at/above `minScore`, sorted by
    (score, 4h spread) — exactly as v2 main() emitted only `best`.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position. Sheep never shorts.

FIDELITY NOTES vs sheep-producer.py v1.0.1:
  - v2 DEFAULT_MARGIN_PCT / config marginPct was 0.20 (a FRACTION) * account_value
    -> marginUsd. This port carries marginPct=20 (a PERCENT) in runtime.yaml and
    emits a top-level `marginPct`; the runtime sizes (marginPct/100)*withdrawable.
    A defensive guard converts a value <= 1.0 (an operator who pasted the v2
    fraction) to a PERCENT (*100) and logs it. FLAGGED.
  - v2 emitted exactly one signal (best, sorted by score then 4h spread). Preserved:
    scan() emits <= 1 signal/tick.
  - v2 recent-signals.json cache (RECENT_SIGNAL_TTL_SEC=240, 4x-TTL prune) -> ctx.state
    dedup map with identical TTL semantics.
  - v2 `push_signal` skipped if the coin was already held; here held assets are
    filtered BEFORE the per-asset MCP fetch (as in v2 main()), and heldAssets is
    also carried in data{} for the rule action's belt-and-braces.
  - v2 fetch_sm_direction SM logic (>=50 long-ratio split, NEUTRAL on zero total)
    is ported verbatim in _get_sm_direction — note this differs from bison's
    58/42 thresholds; Sheep's is the v2 Sheep mapping. SM is a BONUS, never a gate.
  - v2 wrapped the producer in a daemon (interval 300s, tick_timeout 180); that
    loop is OWNED by the runtime here. The producer's order-lifecycle code was
    nil (Sheep never closes — DSL owns exits), so nothing was dropped on that axis.
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



# v2 defaults (sheep-producer.py / sheep-config.json)
_DEFAULT_WHITELIST = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 4            # v2 DEFAULT_MIN_SCORE / config minScore
_DEFAULT_MARGIN_PCT = 20.0       # PERCENT (v2 fraction 0.20 -> 20)
_DEFAULT_LEVERAGE = 3            # v2 DEFAULT_LEVERAGE / config leverage
_MAX_LEVERAGE = 5               # v2 MAX_LEVERAGE (hardcoded cap)
_DEFAULT_TTL = 240              # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Sheep's default whitelist is crypto majors (all main-DEX), but honour an
    operator-supplied xyz: prefix defensively."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[sheep.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[sheep.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _asset_data(ctx, coin):
    """{candles{}} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data: market_get_asset_data over 15m/1h/4h,
    no funding, no order book."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["15m", "1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[sheep.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    return {"candles": candles}


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0).
    READ-GUARDED. SM is a BONUS, never a gate.

    Verbatim v2 mapping: per-coin long_pct/short_pct from pct_of_top_traders_gain;
    if total <= 0 -> ("NEUTRAL", 50.0); else long_ratio = long_pct/total*100, and
    ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio).
    Note this 50-split differs from bison's 58/42 — Sheep uses the v2 Sheep map."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a score contributor; never crash the tick
        print(f"[sheep.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}",
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
    whitelist = inputs.get("whitelist", _DEFAULT_WHITELIST)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1.0
    # (an operator who pasted the v2 FRACTION 0.20) into a PERCENT so it never
    # silently sizes ~100x small (the runtime sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[sheep.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible whitelist candidate; held + recently-signaled are
    #    filtered BEFORE the per-asset MCP fetch, exactly as v2 main() did ──
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
            coin,
            candles.get("15m", []), candles.get("1h", []), candles.get("4h", []),
            sm, inputs,
        )
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[sheep.scan] WAITING — no whitelisted asset has a full 15m+1h+4h "
              f"EMA-stacked-bullish setup (min score {min_score:.0f}); "
              f"scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 sorted by (score, spread_4h_pct) desc and emitted exactly the best.
        candidates.sort(key=lambda c: (c["score"], c["spread_4h_pct"]), reverse=True)
        best = candidates[0]

        # leverage: clamp v2 default into [1, MAX_LEVERAGE] (verbatim min(lev, MAX))
        leverage = min(lev_default, _MAX_LEVERAGE)

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": "LONG",
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[sheep.scan] EMIT {best['coin']} LONG score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% stack={best['stack_score']} "
              f"spread4h={best['spread_4h_pct']:+.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": "LONG",                 # Sheep never shorts
            "marginPct": margin_pct,             # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,                # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": "LONG",
                "reasons": best["reasons"],
                "spread4hPct": best["spread_4h_pct"],
                "spread1hPct": best["spread_1h_pct"],
                "stackScore": best["stack_score"],
                "smDirection": best["sm_direction"] or "NONE",
                "smTiltPct": best["sm_tilt_pct"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[sheep.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
