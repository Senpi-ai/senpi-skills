"""CHAMELEON — supervised scanner (Runtime 3.0 port of the v2 Chameleon RV/pairs).

Relative-value / pairs (ratio mean-reversion). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - fetches 1h candles ONCE per unique asset across all configured pairs,
  - for each pair computes the latest ratio z-score, and if |z| >= zEntryMin AND
    the reversion is starting, scores the high-beta leg via pure
    `scoring.build_pair_thesis` (smart-money lean on the leg passed in),
  - emits the SINGLE most-extended candidate at/above `minScore` (v2 main()
    emitted only `best`), sized by marginPct (PERCENT) and clamped leverage.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (chameleon-producer.py v1.0.1):
  - v2 sized via marginPct * account_value -> marginUsd, with marginPct stored as
    a FRACTION (config marginPct=0.15). This port emits `marginPct` as a PERCENT
    in (0,100] (15) and the runtime sizes (marginPct/100)*withdrawable. The
    defensive "<=1.0 means a pasted v2 fraction, x100" guard is added (dire/koala
    pattern) so an operator who pastes 0.15 doesn't silently size ~100x small.
  - v2's runtime.yaml template said `margin_pct: 20`, but the PRODUCER + config.json
    are the source of truth and use marginPct 0.15 (=15%). This port uses 15% and
    FLAGS the v2 runtime-vs-producer mismatch (15% trusted, per spec source order).
  - v2 fetched candles for every unique asset across all pairs once per tick
    (numerator + denominator + leg). Preserved verbatim.
  - v2 emitted exactly one signal (the highest-scoring candidate). Preserved:
    scan() emits <= 1 signal/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup map
    (same TTL semantics; prune at 4x TTL).
  - v2 held-asset + recently-signaled filter ran on the LEG before scoring; both
    are reproduced (held checked on the leg; was_recently_signaled on the leg).
  - leverage: v2 clamped min(config.leverage, MAX_LEVERAGE=5). Preserved.
  - LLM gate in v2 was an explicit pass-through (honor the producer's signal); this
    port uses decision_mode: rule in runtime.yaml, equivalent and cheaper.
"""

import sys
import time

import scoring

# v2 defaults (chameleon-producer.py / chameleon-config.json)
_DEFAULT_PAIRS = [
    {"numerator": "ETH", "denominator": "BTC", "leg": "ETH"},
    {"numerator": "SOL", "denominator": "ETH", "leg": "SOL"},
    {"numerator": "SOL", "denominator": "BTC", "leg": "SOL"},
]
_DEFAULT_MIN_SCORE = 4               # v2 DEFAULT_MIN_SCORE / config.minScore
_DEFAULT_MARGIN_PCT = 15.0           # PERCENT (v2 config marginPct 0.15 -> 15%)
_DEFAULT_LEVERAGE = 4                # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5                    # v2 MAX_LEVERAGE (hardcoded cap)
_DEFAULT_TTL = 240                   # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Chameleon's universe is crypto majors, so this only ever returns '' here."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


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
        print(f"[chameleon.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        if isinstance(ms, dict):
            account_value = max(account_value, scoring._f(ms, "accountValue", default=0.0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos, "szi", default=0.0)
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", "")})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        if not isinstance(_ms, dict):
            continue
        _use = max(_use,
                   scoring._f(_ms, "totalMarginUsed", default=0.0),
                   abs(scoring._f(_ms, "totalNtlPos", default=0.0)))
    if _use > 1.0 and not positions:
        print("[chameleon.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_candles(ctx, asset):
    """1h candle list for `asset` or []. READ-GUARDED. Ported from v2
    fetch_candles (market_get_asset_data, 1h only, no funding/order-book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(asset),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[chameleon.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = d.get("candles", {}) or {}
    if not isinstance(candles, dict):
        return []
    return candles.get("1h", []) or []


def _fetch_sm_direction(ctx, asset):
    """Port of v2 fetch_sm_direction: net smart-money lean for `asset` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0.0).
    READ-GUARDED. Verbatim: long_ratio >= 50 -> ('LONG', long_ratio) else
    ('SHORT', 100 - long_ratio); total==0 -> ('NEUTRAL', 50.0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a +1 contributor; never crash the tick
        print(f"[chameleon.scan] leaderboard_get_markets read failed (smart-money -> none): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw:
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
        if token != asset:
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m, "pct_of_top_traders_gain", "longPct", default=0.0)
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


def scan(inputs, ctx):
    now = time.time()
    pairs = inputs.get("pairs", _DEFAULT_PAIRS)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_leverage = int(inputs.get("maxLeverage", _MAX_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1
    # (an operator who pasted the v2 FRACTION 0.15) into a PERCENT so it never
    # silently sizes ~100x small (the runtime sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[chameleon.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # leverage: clamp v2 default into <= MAX_LEVERAGE (verbatim min(leverage, MAX))
    leverage = min(lev_default, max_leverage)

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[chameleon.scan] WAITING — no account value (read degraded or zero equity)",
              file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = scoring.prune_signaled(_load_signaled(ctx), ttl, now)

    # Fetch 1h candles ONCE per unique asset across all pairs (verbatim v2 main()).
    assets = sorted({a for p in pairs for a in (p["numerator"], p["denominator"], p["leg"])})
    candles_by_asset = {a: _fetch_candles(ctx, a) for a in assets}
    closes_by_asset = {
        a: [scoring._f(c, "close", "c") for c in candles_by_asset.get(a, [])]
        for a in assets
    }

    # Score each pair whose LEG is not held / not recently signaled (v2 order:
    # held + recently-signaled filter on the leg BEFORE thesis build).
    candidates = []
    scanned = 0
    for pair in pairs:
        leg = str(pair["leg"]).upper()
        if leg in held_set or scoring.was_recently_signaled(signaled, leg, ttl, now):
            continue
        scanned += 1
        sm = _fetch_sm_direction(ctx, pair["leg"])
        th = scoring.build_pair_thesis(pair, closes_by_asset, candles_by_asset, sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets,
                  "pairs": [f"{p['numerator']}/{p['denominator']}" for p in pairs],
                  "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[chameleon.scan] WAITING — no ratio extended past z-threshold with reversion "
              f"starting (min score {min_score:.0f}); scanned={scanned} held={held_assets}",
              file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score; most-extended reversion).
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "pair": best["pair"], "zscore": best["zscore"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[chameleon.scan] EMIT {best['coin']} {best['direction']} "
              f"{best['pair']} z={best['zscore']} score={best['score']} {leverage}x "
              f"marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # <=5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "pair": best["pair"],
                "zscore": best.get("zscore") or 0.0,
                "ratio": best.get("ratio") or 0.0,
                "ratioMean": best.get("ratio_mean") or 0.0,
                "smDirection": best.get("sm_direction") or "NONE",
                "smTiltPct": best.get("sm_tilt_pct") or 0.0,
                "legVolumeTrendPct": best.get("leg_volume_trend_pct") or 0.0,
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[chameleon.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
