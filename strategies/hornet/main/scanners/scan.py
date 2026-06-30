"""HORNET — supervised scanner (net-new Runtime 3.0 strategy).

Semiconductor / AI-capex supply-chain momentum on Hyperliquid XYZ (HIP-3 DEX).
A 16-name semis basket grouped into three supply-chain sub-groups:
  equipment  — xyz:ASML, xyz:AMAT, xyz:MRVL
  logic      — xyz:NVDA, xyz:AMD, xyz:AVGO, xyz:TSM, xyz:QCOM, xyz:ARM, xyz:INTC, xyz:SMH
  memory     — xyz:MU, xyz:SNDK, xyz:SMSN, xyz:SKHX, xyz:WDC

THE EDGE — the chain bids together or not at all. Per tick Hornet:
  1. reads account state + held positions (dual-DEX equity via max(), never sum(),
     with the fleet-standard read-sanity guard),
  2. reads each universe name's 4h+1h candles (READ-GUARDED — a name that fails to
     read is simply skipped, both from breadth and from scoring),
  3. computes SECTOR BREADTH = fraction of the universe whose 4h trend is bullish
     (strict higher-lows, bobcat/scoring.trend_structure),
  4. applies the BREADTH GATE (the core edge):
        breadth >= longBreadthPct  (default 0.55) -> only LONGs eligible
        breadth <= shortBreadthPct (default 0.35) -> only SHORTs eligible
        otherwise (chop)                          -> emit NOTHING (WAITING),
  5. scores each eligible name in the breadth direction (base 3 + 4h-trend +
     1h-confirm + |momentum| tier + smart-money agreement + supply-chain-gradient
     bonus), floors via minScore,
  6. emits the TOP 1-2 by score, with held-asset + recent-signal dedup via ctx.state
     (240s TTL).

Read-only + single-pass — emits `marginPct` (PERCENT) + `leverage` intents; the
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No
daemon, no push_signal, no create_position.

XYZ notes (xyz-equity DEX handling, matching bobcat):
  - every asset is xyz:NAME and EVERY market read passes dex="xyz" (the prefix is
    mandatory for the HIP-3 DEX),
  - 23/5 trading; the 48h hard_timeout (runtime.yaml) caps holds across the weekend
    pricing gap. No scan-level market-hours gate.
"""

import sys
import time

import scoring


# ── universe + sub-group tags (validated live vs HL xyz meta 2026-06-30) ──
# Sub-group tags drive the supply-chain-gradient bonus. Config-overridable via
# inputs.universe (a flat list of xyz: tickers); inputs.subGroups (optional) can
# override the name->group map. Anything not in the map defaults to "logic".
_DEFAULT_UNIVERSE = [
    # equipment
    "xyz:ASML", "xyz:AMAT", "xyz:MRVL",
    # logic
    "xyz:NVDA", "xyz:AMD", "xyz:AVGO", "xyz:TSM", "xyz:QCOM", "xyz:ARM", "xyz:INTC", "xyz:SMH",
    # memory
    "xyz:MU", "xyz:SNDK", "xyz:SMSN", "xyz:SKHX", "xyz:WDC",
]
_DEFAULT_SUB_GROUPS = {
    "xyz:ASML": "equipment", "xyz:AMAT": "equipment", "xyz:MRVL": "equipment",
    "xyz:NVDA": "logic", "xyz:AMD": "logic", "xyz:AVGO": "logic", "xyz:TSM": "logic",
    "xyz:QCOM": "logic", "xyz:ARM": "logic", "xyz:INTC": "logic", "xyz:SMH": "logic",
    "xyz:MU": "memory", "xyz:SNDK": "memory", "xyz:SMSN": "memory",
    "xyz:SKHX": "memory", "xyz:WDC": "memory",
}

_DEFAULT_MIN_SCORE = 5
_DEFAULT_LONG_BREADTH = 0.55       # breadth >= this -> LONG-only eligible
_DEFAULT_SHORT_BREADTH = 0.35      # breadth <= this -> SHORT-only eligible
_DEFAULT_MARGIN_PCT = 18           # flat marginPct base (PERCENT)
_DEFAULT_LEVERAGE = 4
_MIN_LEVERAGE = 1
_MAX_LEVERAGE = 5
_DEFAULT_MAX_EMIT = 2              # emit the top 1-2 by score
_DEFAULT_RECENT_TTL = 240         # race-window dedup (seconds)


def _dex_for(asset, inputs):
    """XYZ (HIP-3) names must pass dex="xyz"; this whole universe is xyz."""
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). assetPositions are per-sub-DEX so they are
    enumerated across both sections. Includes the fleet-standard read-sanity guard
    (margin in use + empty positions -> skip tick) to avoid re-entering held names."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[hornet.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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

    # read-sanity guard (funding/$0 glitch family): a corrupt clearinghouse read
    # can report margin/notional IN USE while returning an EMPTY positions list;
    # sizing or running held-asset dedup off that re-enters held names (pyramiding)
    # and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[hornet.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_sm_direction(ctx, coin):
    """Net smart-money lean for `coin` from leaderboard_get_markets. Returns
    (direction, tilt_pct) or (None, 0.0). READ-GUARDED -> degrades to neutral
    on failure (smart-money is a score CONTRIBUTOR here, never a hard gate).

    Thresholds (bobcat-consistent): long_ratio >= 50 -> (LONG, long_ratio); else
    -> (SHORT, 100 - long_ratio); total <= 0 -> (NEUTRAL, 50.0); not-found ->
    (None, 0.0). Token match is case-insensitive on the full xyz: name."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — never crash the tick; degrade to neutral
        print(f"[hornet.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}",
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
    cu = coin.upper()
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != cu:
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
    """{candles_1h, candles_4h} for `coin` or None. READ-GUARDED — a name that
    fails to read is dropped from BOTH breadth and scoring (so a flaky read can
    only soften the gate, never fabricate a signal)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["1h", "4h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": dex,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[hornet.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
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


# ── ctx.state: recent-signal dedup ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL."""
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
    sub_groups = inputs.get("subGroups", _DEFAULT_SUB_GROUPS)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    long_breadth = float(inputs.get("longBreadthPct", _DEFAULT_LONG_BREADTH))
    short_breadth = float(inputs.get("shortBreadthPct", _DEFAULT_SHORT_BREADTH))
    lev_cfg = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_emit = int(inputs.get("maxEmit", _DEFAULT_MAX_EMIT))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct: PERCENT in (0,100]. Defensive fraction guard (bobcat/dire/koala
    # pattern): a value <= 1.0 is a pasted FRACTION (e.g. 0.18) -> *100 -> 18.
    margin_pct = float(inputs.get("marginPctBase", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        margin_pct = margin_pct * 100

    # leverage: clamp to [1,5].
    leverage = min(lev_cfg, _MAX_LEVERAGE)
    leverage = max(leverage, _MIN_LEVERAGE)

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── read every name's candles ONCE (READ-GUARDED). Breadth and scoring both
    #    consume this single map; a name that fails to read is absent from both. ──
    trends = {}     # coin -> (trend_4h, market_dict)
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        scanned += 1
        md = _asset_data(ctx, coin, _dex_for(coin, inputs))
        if not md:
            continue
        t4, _ = scoring.trend_structure(md["candles_4h"])
        trends[coin] = (t4, md)

    if not trends:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": "WAITING (no readable names)"}
        print(f"[hornet.scan] WAITING — no readable universe names; scanned={scanned}",
              file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    n_read = len(trends)

    # ── SECTOR BREADTH = fraction of READABLE names whose 4h trend is bullish ──
    n_bull = sum(1 for (t4, _) in trends.values() if t4 == "BULLISH")
    n_bear = sum(1 for (t4, _) in trends.values() if t4 == "BEARISH")
    breadth = n_bull / n_read if n_read else 0.0

    # ── BREADTH GATE (the core edge): the chain bids together or not at all ──
    if breadth >= long_breadth:
        gate_dir = "LONG"
    elif breadth <= short_breadth:
        gate_dir = "SHORT"
    else:
        gate_dir = None  # chop — emit nothing this tick

    # ── supply-chain gradient: is the EQUIPMENT sub-group leading the complex? ──
    # "leading" = the equipment sub-group's directional breadth (in the gate
    # direction) is STRICTLY GREATER than the whole-universe directional breadth —
    # i.e. capex tools are bidding/rolling ahead of logic+memory. Early-cycle
    # confirmation -> +1 per name (applied in scoring.build_thesis).
    equipment_leading = False
    if gate_dir is not None:
        eq_total, eq_match = 0, 0
        for coin, (t4, _) in trends.items():
            if sub_groups.get(coin) != "equipment":
                continue
            eq_total += 1
            if (gate_dir == "LONG" and t4 == "BULLISH") or (gate_dir == "SHORT" and t4 == "BEARISH"):
                eq_match += 1
        if eq_total > 0:
            eq_breadth = eq_match / eq_total
            whole_dir_breadth = (n_bull / n_read) if gate_dir == "LONG" else (n_bear / n_read)
            equipment_leading = eq_breadth > whole_dir_breadth

    out = []
    if gate_dir is None:
        result = {"ts": now, "scanned": scanned, "readable": n_read,
                  "breadth": round(breadth, 3), "emitted": False, "held": held_assets,
                  "note": f"WAITING (breadth {breadth:.2f} in chop band "
                          f"[{short_breadth:.2f},{long_breadth:.2f}])"}
        print(f"[hornet.scan] WAITING — sector breadth {breadth:.2f} in chop band "
              f"[{short_breadth:.2f},{long_breadth:.2f}]; bull={n_bull} bear={n_bear} "
              f"read={n_read} held={held_assets}", file=sys.stderr)
        _persist(ctx, signaled, result)
        return out

    # ── score each eligible name in the gate direction (held + recently-signaled
    #    filtered out; smart-money fetched per candidate, read-guarded) ──
    candidates = []
    for coin, (t4, md) in trends.items():
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(
            coin, sub_groups.get(coin, "logic"),
            md["candles_1h"], md["candles_4h"], gate_dir,
            sm, equipment_leading, inputs,
        )
        if th and th["score"] >= min_score:
            candidates.append(th)

    if not candidates:
        result = {"ts": now, "scanned": scanned, "readable": n_read,
                  "breadth": round(breadth, 3), "gateDir": gate_dir,
                  "emitted": False, "held": held_assets,
                  "note": f"WAITING (gate {gate_dir} open, no name >= min score {min_score:.0f})"}
        print(f"[hornet.scan] WAITING — breadth gate {gate_dir} OPEN (breadth {breadth:.2f}) "
              f"but no name cleared min score {min_score:.0f}; read={n_read} "
              f"equipment_leading={equipment_leading} held={held_assets}", file=sys.stderr)
        _persist(ctx, signaled, result)
        return out

    # ── emit the top 1-2 by score ──
    candidates.sort(key=lambda x: x["score"], reverse=True)
    chosen = candidates[:max(1, max_emit)]

    for best in chosen:
        signaled[best["coin"].upper()] = now
        out.append({
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes (marginPct/100)*withdrawable
            "leverage": leverage,             # flat, clamped [1,5]; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "subGroup": best["sub_group"],
                "reasons": best["reasons"],
                "sectorBreadth": round(breadth, 4),
                "gateDir": gate_dir,
                "equipmentLeading": best["equipment_leading"],
                "trend4h": best["trend_4h"],
                "trend4hStrength": best["trend_4h_strength"],
                "trend1h": best["trend_1h"],
                "momentum1hPct": best["momentum_1h"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smTiltPct": best["sm_tilt_pct"],
                "heldAssets": held_assets,
            },
        })

    emitted = [(c["coin"], c["direction"], c["score"]) for c in chosen]
    result = {"ts": now, "scanned": scanned, "readable": n_read,
              "breadth": round(breadth, 3), "gateDir": gate_dir,
              "equipmentLeading": equipment_leading, "emitted": True,
              "emit": emitted, "held": held_assets}
    print(f"[hornet.scan] EMIT {gate_dir} x{len(chosen)} (breadth {breadth:.2f}, "
          f"equipment_leading={equipment_leading}) {emitted} marginPct={margin_pct:.2f}% "
          f"{leverage}x held={held_assets}", file=sys.stderr)
    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    """Persist dedup map + this tick's result EVERY tick; bounded by
    state_history_max_count. Read back via ctx.state.recent(n)."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[hornet.scan] WARNING: state append failed; next tick may re-emit "
              f"a suppressed signal: {exc!r}", file=sys.stderr)
