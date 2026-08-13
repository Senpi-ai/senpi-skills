"""PIRANHA — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Piranha producer's microstructure helpers
+ forced-flow thesis builder (SKILL.md v1.0.0, producer v1.0.1). The math/indexing
is reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same market snapshot. Behaviour-preserving quirks from v2 are kept.

Thesis: ride FORCED FLOW. When open interest is unwinding fast (positions being
force-closed / liquidated) AND price is moving violently in one direction, that's
forced flow — liquidations begetting liquidations.
  - OI dropping + price spiking UP   = shorts squeezed   -> ride LONG
  - OI dropping + price dropping HARD = longs liquidated  -> ride SHORT

GATES (all required, applied inside build_thesis -> None if any fails):
  1. OI velocity known (None -> cache warming -> skip)
  2. OI unwinding >= oiDropMinPct over 1h (oi_pct <= -oiDropMinPct)
  3. |1h price move| >= priceMoveMinPct
  4. 5m candle still moving the SAME way as the 1h move (flow ongoing)

SCORE components (max ~9):
  +2 OI unwind (gate-confirmed)
  +1 OI unwind strong (<= -oiDropStrongPct)
  +2 violent 1h move (gate-confirmed)
  +1 5m acceleration (|move_5m| >= priceMoveMinPct/2)
  +1 book thin on the side price is running into
  +1 volume spike (>= volSpikePct)
  +1 smart-money aligned with the flow (>= 55%)

minScore is applied by the CALLER (scan.py), not here.

OI velocity self-compute (oi_velocity_1h with a prev-OI value) and the recent-OI
cache live in scan.py / ctx.state — this module receives the resolved prev-OI and
stays pure (no file I/O).
"""


def _f(c, primary="close", alt=None, default=0.0):
    """Tolerant float getter. Mirrors v2 `_f(c, primary, alt, default)`.

    When `c` is a dict, reads c[primary] (falling back to c[alt] when primary is
    None). When `c` is a bare value, coerces it directly. Verbatim semantics from
    the v2 producer's `_f`."""
    if isinstance(c, dict):
        val = c.get(primary)
        if val is None and alt:
            val = c.get(alt)
    else:
        val = c
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Microstructure helpers (ported verbatim from v2 piranha-producer.py)
# ═══════════════════════════════════════════════════════════════

def price_move_pct(candles, n_bars):
    """Signed % move over the last n_bars candles. Verbatim from v2."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f(candles[-(n_bars + 1)], "close", "c")
    new = _f(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def oi_velocity_1h(asset_data, prev_oi):
    """(oi_change_pct_1h, source). Prefers the oi_velocity object; falls back to a
    self-computed delta from the persisted last-OI value (prev_oi) supplied by the
    caller. None if neither is available. Verbatim from v2 except the prev-OI is
    passed in (caller owns the cache) instead of read from a file here.

    Returns (None, "unavailable") when OI velocity cannot be resolved.

    Live `market_get_asset_data` returns oi_velocity with FLAT keys
    (`oi_change_pct_1h`, `oi_change_pct_5m`, …). The original port only read a
    NESTED `oi_change_pct: {"1h": …}` shape, so the primary source never
    resolved and every tick fell through to the self-computed path. Both shapes
    are accepted now; the flat one is what production actually sends."""
    data = asset_data.get("data", {}) if isinstance(asset_data, dict) else {}
    ctx = data.get("asset_context", {}) or {}
    cur_oi = _f(ctx, "openInterest")
    oiv = data.get("oi_velocity")
    if isinstance(oiv, dict):
        ch = oiv.get("oi_change_pct")
        if isinstance(ch, dict) and ch.get("1h") is not None:
            try:
                return float(ch["1h"]), "oi_velocity"
            except (TypeError, ValueError):
                pass
        flat = oiv.get("oi_change_pct_1h")
        if flat is not None:
            try:
                return float(flat), "oi_velocity"
            except (TypeError, ValueError):
                pass
    if cur_oi > 0 and prev_oi and float(prev_oi) > 0:
        return ((cur_oi - float(prev_oi)) / float(prev_oi)) * 100, "computed"
    return None, "unavailable"


def current_oi(asset_data):
    """The current openInterest for this asset, or 0.0. Used by the caller to
    refresh the OI-state cache every tick (warms the self-compute fallback)."""
    data = asset_data.get("data", {}) if isinstance(asset_data, dict) else {}
    ctx = data.get("asset_context", {}) or {}
    return _f(ctx, "openInterest")


def book_thin_side(asset_data):
    """(bid_depth, ask_depth) summed over the visible L2 book levels. A thin ask
    side means little resistance above (favors an up-move); thin bid side means
    little support below (favors a down-move). Verbatim from v2."""
    data = asset_data.get("data", {}) if isinstance(asset_data, dict) else {}
    ob = data.get("order_book", {}) or {}
    levels = ob.get("levels") or []
    if len(levels) < 2:
        return 0.0, 0.0
    bids, asks = levels[0], levels[1]
    bid_depth = sum(_f(l, "sz") for l in bids if isinstance(l, dict))
    ask_depth = sum(_f(l, "sz") for l in asks if isinstance(l, dict))
    return bid_depth, ask_depth


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2.

    v2-quirk: requires len(candles) >= lookback (NOT lookback+2 like bison's
    volume_trend) and slices the full window (vols = candles[-lookback:]).
    Reproduced exactly."""
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Thesis builder — ride the forced flow (ported verbatim from v2)
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, asset_data, prev_oi, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when any GATE fails:
      - insufficient candle history (len(c1h) < 3 or len(c5m) < 4),
      - OI velocity unknown (cache warming),
      - OI not unwinding >= oiDropMinPct,
      - |1h move| < priceMoveMinPct,
      - 5m no longer moving the 1h direction (flow reversed).
    minScore is NOT applied here — the caller gates on thesis['score'].

    `asset_data` is the raw market_get_asset_data document (or its inner shape);
    `prev_oi` is the last-seen openInterest for this asset (or None) supplied by
    the caller's OI cache; `sm` is the smart-money tuple (direction, tilt) or
    (None, 0.0). All fetching/caching is done by scan.py so this stays pure."""
    oi_drop_min = float(inputs.get("oiDropMinPct", 3.0))
    oi_strong = float(inputs.get("oiDropStrongPct", 6.0))
    move_min = float(inputs.get("priceMoveMinPct", 2.0))
    vol_spike = float(inputs.get("volSpikePct", 50.0))

    data = asset_data.get("data", {}) if isinstance(asset_data, dict) else {}
    candles = data.get("candles", {}) or {}
    candles_1h = candles.get("1h", []) or []
    candles_5m = candles.get("5m", []) or []
    if len(candles_1h) < 3 or len(candles_5m) < 4:
        return None  # insufficient history (caller still refreshes the OI cache)

    # GATE 1 — OI unwinding fast (positions force-closing)
    oi_pct, oi_src = oi_velocity_1h(asset_data, prev_oi)
    if oi_pct is None:
        return None  # OI unknown (cache warming) — can't confirm forced flow
    if oi_pct > -oi_drop_min:
        return None  # OI not falling fast enough — no liquidation/unwind signature

    # GATE 2 — violent price move (the flow direction)
    move_1h = price_move_pct(candles_1h, 1)
    if abs(move_1h) < move_min:
        return None
    direction = "LONG" if move_1h > 0 else "SHORT"   # ride the forced flow

    # GATE 3 — 5m must still be moving the same way (flow ongoing, not reversed)
    move_5m = price_move_pct(candles_5m, 1)
    if (direction == "LONG" and move_5m <= 0) or (direction == "SHORT" and move_5m >= 0):
        return None

    bid_depth, ask_depth = book_thin_side(asset_data)
    vol_pct = volume_trend(candles_5m)

    score = 0
    reasons = []

    # OI unwind magnitude (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"oi_unwind_{oi_pct:+.1f}%_{oi_src}")
    if oi_pct <= -oi_strong:
        score += 1
        reasons.append("oi_unwind_strong")

    # Violent move (gate-confirmed) + acceleration
    score += 2
    reasons.append(f"move_1h_{move_1h:+.2f}%")
    if abs(move_5m) >= move_min / 2:
        score += 1
        reasons.append(f"accel_5m_{move_5m:+.2f}%")

    # Thin book on the side price is running into = little resistance left
    thin_into = (direction == "LONG" and ask_depth > 0 and bid_depth > ask_depth * 1.3) or \
                (direction == "SHORT" and bid_depth > 0 and ask_depth > bid_depth * 1.3)
    if thin_into:
        score += 1
        reasons.append("book_thin_into_move")

    # Volume spike confirms forced activity
    if vol_pct >= vol_spike:
        score += 1
        reasons.append(f"vol_spike_{vol_pct:+.0f}%")

    # SM aligned with the flow (optional confirm)
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir == direction and sm_tilt >= 55:
        score += 1
        reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "oi_change_pct": round(oi_pct, 3),
        "oi_source": oi_src,
        "move_1h_pct": round(move_1h, 3),
        "move_5m_pct": round(move_5m, 3),
        "bid_depth": round(bid_depth, 2),
        "ask_depth": round(ask_depth, 2),
        "volume_trend_pct": round(vol_pct, 2),
        "sm_direction": sm_dir if sm_dir else "NONE",
    }
