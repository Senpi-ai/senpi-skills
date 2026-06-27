"""KESTREL — supervised scanner (Runtime 3.0 port of the v2 KESTREL XYZ macro
breakout rider).

Multi-asset, non-crypto XYZ universe (12 names on the Hyperliquid HIP-3 DEX):
commodities (CL, BRENTOIL), precious metals (GOLD), indices (SP500, XYZ100), and
high-volume equities (AAPL, NVDA, GOOGL, TSLA, AMZN, META, MSFT). Each tick:

  1. Read the strategy clearinghouse state (held-asset dedup + position-count
     guard — the runtime's slots also backstop this).
  2. Build the XYZ-dex-filtered smart-money map once (leaderboard_get_markets).
  3. Loop the universe; for each name a READ-GUARDED market_get_asset_data fetch
     (1h/4h candles + funding + order book on dex="xyz"), scored by the pure
     `scoring.score_breakout` (v2 verbatim). Held names are skipped; a per-asset
     signal-dedup map (ctx.state) mirrors the v2 180m re-emit cooldown.
  4. Emit the SINGLE strongest candidate at score >= minScore (v2 emits one
     signal/tick; slots: 2 lets the runtime own the 2-position macro book).

Read-only + single-pass. Emits per-signal `leverage` (score-tiered 3x/5x) and
`marginPct` (fixed 30 PERCENT of withdrawable); the runtime sizes the dollars,
owns the cooldowns / daily caps / drawdown halt, and trails the DSL exit. No
daemon, no push_signal.

XYZ / 24-7 notes (preserved from v2, do NOT redesign):
  - dex = "xyz" — HIP-3 DEX, the "xyz:" prefix is mandatory on every read.
  - macroAsset = "" — no BTC-correlation factor (these are not crypto perps).
  - XYZ trades 24/7 incl weekends — NO market-hours / session / weekday gate.
  - account value summed correctly across the main + xyz sub-DEX views (one
    wallet, two views per HIP-3 — count equity ONCE, never sum/double-count).
"""

import sys
import time

import scoring

# v2 producer constants (preferred over config.json per the port directive)
_DEFAULT_UNIVERSE = [
    "xyz:CL", "xyz:BRENTOIL", "xyz:GOLD", "xyz:SP500", "xyz:XYZ100",
    "xyz:AAPL", "xyz:NVDA", "xyz:GOOGL", "xyz:TSLA", "xyz:AMZN",
    "xyz:META", "xyz:MSFT",
]
_DEFAULT_MIN_SCORE = 5                 # v2 MIN_SCORE_DEFAULT
_DEFAULT_MARGIN_PCT = 30               # v2 MARGIN_PCT 0.30 -> PERCENT of withdrawable
_DEFAULT_MAX_POSITIONS = 2             # v2 MAX_POSITIONS (macro book)
_DEFAULT_TTL = 10800                   # 180m — mirror the v2 ASSET_COOLDOWN_MINUTES (anti re-fire)
_DEFAULT_LEVERAGE_TIERS = [[9, 5], [5, 3]]   # v2 LEVERAGE_TIERS (score >= 9 -> 5x; >= 5 -> 3x)
_DEFAULT_LEVERAGE = 3                  # v2 DEFAULT_LEVERAGE


def _strip_xyz(token):
    return str(token).replace("xyz:", "").replace("XYZ:", "").upper()


def _norm_tiers(raw):
    """Accept either v2 dict tiers [{min_score,leverage}] or the runtime.yaml
    list-of-pairs shape [[9,5],[5,3]]. Return the dict shape scoring expects."""
    out = []
    for t in raw or []:
        if isinstance(t, dict):
            out.append({"min_score": int(t.get("min_score", t.get("minScore", 0))),
                        "leverage": int(t.get("leverage", _DEFAULT_LEVERAGE))})
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            out.append({"min_score": int(t[0]), "leverage": int(t[1])})
    return out or [{"min_score": 9, "leverage": 5}, {"min_score": 5, "leverage": 3}]


def _account_and_held(ctx, wallet):
    """READ-GUARD: clearinghouse read must never roll back the whole tick.
    Returns (account_value, pos_count, held_tokens:set). Account value counted
    ONCE across the main + xyz sub-DEX views (one wallet, two views per HIP-3 —
    max, not sum; summing double-counts the shared free balance -> 2x sizing).
    Held tokens are stripped of the 'xyz:' prefix to match bare universe tokens.
    Degrades to (0.0, 0, set()) on any error so the runtime's slot gate backstops."""
    if not wallet:
        return 0.0, 0, set()
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    except Exception as exc:  # noqa: BLE001
        print(f"[kestrel.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, 0, set()
    if not ch:
        return 0.0, 0, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    if not isinstance(data, dict):
        return 0.0, 0, set()
    account_value = 0.0
    pos_count = 0
    held = set()
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring.safe_float(pos.get("szi", 0)) != 0:
                pos_count += 1
                token = _strip_xyz(pos.get("coin", ""))
                if token:
                    held.add(token)
    return account_value, pos_count, held


def _fetch_sm_xyz_map(ctx):
    """READ-GUARD: build a {token -> SM market record} map, XYZ dex only.
    VERBATIM port of v2 `fetch_sm_xyz_map`. Degrades to {} on any error."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {"limit": 100})
    except Exception as exc:  # noqa: BLE001
        print(f"[kestrel.scan] leaderboard_get_markets read failed (degrade): {exc!r}", file=sys.stderr)
        return {}
    sm_map = {}
    if not raw:
        return sm_map
    sm_markets = raw.get("data", raw)
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", sm_markets)
    if isinstance(sm_markets, dict):
        sm_markets = sm_markets.get("markets", [])
    if isinstance(sm_markets, list):
        for m in sm_markets:
            if isinstance(m, dict):
                token = str(m.get("token", "")).upper()
                dex = str(m.get("dex", "")).lower()
                if dex == "xyz" and token:
                    sm_map[token] = m
    return sm_map


def _fetch_asset_snapshot(ctx, asset, dex):
    """READ-GUARD per-asset: one bad/illiquid XYZ name must not roll back the
    whole universe tick. Returns the inner data dict or None (skip this name)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h", "4h"],
            "include_funding": True,
            "include_order_book": True,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[kestrel.scan] market_get_asset_data({asset}) read failed, skipping: {exc!r}",
              file=sys.stderr)
        return None
    if not md:
        return None
    return md.get("data", md) if isinstance(md, dict) else None


def scan(inputs, ctx):
    universe = inputs.get("universe", _DEFAULT_UNIVERSE)
    dex = inputs.get("dex", "xyz")
    _macro_asset = inputs.get("macroAsset", "")    # "" disables the BTC factor — these are not crypto
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT of withdrawable (0,100]
    max_positions = int(inputs.get("maxPositions", _DEFAULT_MAX_POSITIONS))
    tiers = _norm_tiers(inputs.get("leverageTiers", _DEFAULT_LEVERAGE_TIERS))
    default_leverage = int(inputs.get("defaultLeverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    # ── account + held assets (READ-GUARDED) ──
    account_value, pos_count, held = _account_and_held(ctx, ctx.wallet)

    # ── max-positions guard (Kestrel runs a 2-position macro book; slots also backstops) ──
    if pos_count >= max_positions:
        print(f"[kestrel.scan] perched ({pos_count} positions): {sorted(held)}", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"ts": now, "emitted": False, "gate": "perched",
                                  "pos_count": pos_count, "held": sorted(held)})
            except Exception as exc:  # noqa: BLE001
                print(f"[kestrel.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []

    # ── per-asset signal-dedup map (mirrors the v2 180m re-emit cooldown) ──
    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    # ── SM data (XYZ-filtered), one read per tick ──
    sm_map = _fetch_sm_xyz_map(ctx)

    # ── score every name in the universe ──
    candidates = []
    all_scored = []
    skipped_held = 0
    for asset in sorted(universe):
        token = _strip_xyz(asset)

        # held dedup
        if token in held:
            skipped_held += 1
            continue

        # signal-dedup (anti re-fire)
        last = recent.get(token)
        if last is not None and (now - last) < ttl:
            continue

        snap = _fetch_asset_snapshot(ctx, asset, dex)
        if not snap:
            continue

        candles = snap.get("candles", {}) or {}
        candles_1h = candles.get("1h", []) or []
        asset_context = snap.get("asset_context", snap.get("assetContext", {})) or {}
        order_book = snap.get("order_book", snap.get("orderBook", {})) or {}
        sm_record = sm_map.get(token)

        scored = scoring.score_breakout(token, candles_1h, asset_context, order_book,
                                        sm_record, inputs)
        if scored is None:
            continue

        all_scored.append({"token": scored["token"], "direction": scored["direction"],
                           "score": scored["score"], "pct_1h": round(scored["pct_1h"], 2)})

        if scored["score"] >= min_score:
            candidates.append(scored)

    out = []
    result = None
    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        result = {"ts": now, "emitted": False, "gate": "no_candidates",
                  "min_score": min_score, "scored": len(all_scored), "top3": top3,
                  "skipped_held": skipped_held, "held": sorted(held)}
        print(f"[kestrel.scan] 0 candidates at score >= {min_score:.0f} "
              f"({len(all_scored)} scored) | top3={top3}", file=sys.stderr)
    else:
        # ── pick the single strongest candidate (v2 emits one signal/tick) ──
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        token = best["token"]
        score = best["score"]
        direction = best["direction"]
        leverage = scoring.get_leverage_for_score(score, tiers, default_leverage)

        if leverage <= 0 or margin_pct <= 0:
            result = {"ts": now, "emitted": False, "gate": "sizing_unresolved",
                      "token": token, "score": score, "direction": direction,
                      "leverage": leverage, "marginPct": margin_pct}
            print(f"[kestrel.scan] {token} HOLD: sizing unresolved lev={leverage} "
                  f"marginPct={margin_pct}", file=sys.stderr)
        else:
            recent[token] = now
            result = {"ts": now, "emitted": True, "gate": "pass", "token": token,
                      "score": score, "direction": direction, "leverage": leverage,
                      "marginPct": margin_pct, "reasons": best["reasons"]}
            print(f"[kestrel.scan] xyz:{token} EMIT: score={score} {direction} "
                  f"{leverage}x marginPct={margin_pct} | {best['reasons']}", file=sys.stderr)
            out = [{
                "asset": f"xyz:{token}",
                "direction": direction,
                "marginPct": margin_pct,      # PERCENT of withdrawable — runtime sizes (marginPct/100)*withdrawable
                "leverage": leverage,         # score-tiered (3x/5x); runtime applies it
                "data": {
                    "score": float(score),
                    "leverage": float(leverage),
                    "marginPct": float(margin_pct),
                    "direction": direction,
                    "rawAsset": token,
                    "pct1h": float(round(best["pct_1h"], 4)),
                    "pct4h": float(round(best["pct_4h"], 4)),
                    "smPct": float(best["sm_pct"]),
                    "smTraders": int(best["sm_traders"]),
                    "smDir": best["sm_dir"],
                    "fundingRate": float(best["funding"]),
                    "spreadPct": float(best["spread_pct"]),
                    "reasons": best["reasons"],
                    "heldAssets": sorted(held),
                },
            }]

    # ── persist dedup map + this tick's result every tick; self-trims at
    #    state_history_max_count. Read history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[kestrel.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return out
