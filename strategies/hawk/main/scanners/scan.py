"""HAWK — supervised scanner (Runtime 3.0 port of the v2 Hawk breakout producer).

Multi-asset, basket-gated (BTC/ETH/SOL by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - iterates the basket universe (XYZ banned; held + recently-signaled filtered
    BEFORE the per-asset MCP fetch, as in v2 main()),
  - for each candidate fetches 1h+4h candles + the smart-money lean and scores a
    breakout thesis via the pure `scoring.build_thesis` (breakout + SM agreement
    are HARD GATES; 4h-trend + volume are score contributors),
  - emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main()
    emitted only `best`), sized by a flat margin PERCENT + clamped leverage.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus `leverage`;
the runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.
No daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (hawk-producer.py v1.0.1 / SKILL.md v1.0.0):
  - v2 sized margin as marginPct=0.20 (a FRACTION) * account_value -> marginUsd.
    This port emits a `marginPct` PERCENT (default 20) and the runtime sizes
    (marginPct/100)*withdrawable. The defensive "<=1.0 means a pasted fraction,
    x100" guard converts a config that still carries 0.20 -> 20. Flat sizing (no
    conviction tiers) is preserved verbatim.
  - v2 leverage = min(config.leverage(5), MAX_LEVERAGE(5)). Preserved: clamp the
    input leverage to MAX_LEVERAGE (5).
  - v2 emitted exactly one signal (best, highest score). Preserved: scan() emits
    <= 1 signal/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240) -> ctx.state dedup
    map (same TTL + 4x-TTL prune semantics).
  - v2 smart-money lean (fetch_sm_direction) thresholds reproduced VERBATIM in
    _get_sm_direction (>=50 long_ratio -> LONG else SHORT; token compared UPPER).
    Note this differs from bison's 58/42 band — Hawk's own thresholds are kept.
  - v2 build_thesis short-circuited on data.get("success") False; the read-guard
    here treats success:false as "skip asset" (None), same effect.
  - v2's hawk_config.py docstring and runtime.yaml description called Hawk a
    "single-asset BTC" strategy; the producer + config/hawk-config.json + SKILL.md
    all define a 3-asset BTC/ETH/SOL breakout basket. The producer/config/SKILL
    are source-of-truth — this port is a 3-asset basket. (Mismatch FLAGGED.)
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



# v1.0.1 defaults (hawk-producer.py / hawk-config.json)
_UNIVERSE_DEFAULT = ["BTC", "ETH", "SOL"]
_DEFAULT_MIN_SCORE = 5            # v1 DEFAULT_MIN_SCORE / config.minScore
_DEFAULT_MARGIN_PCT = 20         # PERCENT; v2 config.marginPct=0.20 fraction -> 20%
_DEFAULT_LEVERAGE = 5            # v1 DEFAULT_LEVERAGE / config.leverage
_MAX_LEVERAGE = 5               # v1 MAX_LEVERAGE (hardcoded, not configurable)
_DEFAULT_RECENT_TTL = 240        # v1 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''.
    Hawk's basket is liquid crypto majors, so this only ever returns '' in
    practice (XYZ is also banned at scan level below)."""
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
        print(f"[hawk.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[hawk.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Net smart-money lean for `coin` from leaderboard_get_markets. Returns
    (direction, tilt_pct) or (None, 0.0). READ-GUARDED.

    Ported VERBATIM from v2 fetch_sm_direction (NOT bison's 58/42 band):
      - token compared UPPER-cased against coin (assumed upper),
      - long_ratio = long_pct/(long+short)*100,
      - long_ratio >= 50 -> ("LONG", long_ratio), else ("SHORT", 100 - long_ratio),
      - total == 0 -> ("NEUTRAL", 50.0); coin not found -> (None, 0.0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is the entry GATE; a read failure must skip,
        print(f"[hawk.scan] leaderboard_get_markets read failed (smart-money -> none): {exc!r}",
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

    long_pct = 0.0
    short_pct = 0.0
    found = False
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


def _asset_data(ctx, coin):
    """{candles_1h, candles_4h} for `coin` or None. READ-GUARDED.
    Ported from v2 fetch_market_data (1h/4h, no funding, no order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[hawk.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = d.get("candles", {}) or {}
    if not isinstance(candles, dict):
        return None
    return {"candles_1h": candles.get("1h", []) or [],
            "candles_4h": candles.get("4h", []) or []}


# ── ctx.state: recent-signal dedup (port of v1 recent-signals.json) ──

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
    universe = [str(a).upper() for a in inputs.get("universe", _UNIVERSE_DEFAULT)]
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    base_margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT in (0,100]
    # defensive: a config that still stores margin as a FRACTION (e.g. 0.20) -> x100
    if base_margin_pct <= 1.0:
        base_margin_pct *= 100
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible basket candidate (XYZ banned; held + recently
    #    signaled filtered BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin or coin.lower().startswith("xyz:"):   # XYZ banned (liquid-crypto basket)
            continue
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        sm = _get_sm_direction(ctx, cu)
        th = scoring.build_thesis(coin, md["candles_1h"], md["candles_4h"], sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets,
                  "note": f"WAITING — no breakout with SM agreement (min score {min_score:.0f})"}
        print(f"[hawk.scan] WAITING — no breakout with SM agreement (min score {min_score:.0f}); "
              f"scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        # flat margin PERCENT (v2 had no conviction tiers); leverage clamped to MAX.
        margin_pct = base_margin_pct
        leverage = min(lev_default, _MAX_LEVERAGE)

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[hawk.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:6]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # clamped to MAX_LEVERAGE (5); runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "breakoutPct": best["breakout_pct"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smTiltPct": best["sm_tilt_pct"],
                "trend4h": best["trend_4h"],
                "volumeRatio": best["volume_ratio"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[hawk.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
