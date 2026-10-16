"""HORNET — pure thesis math (no I/O, no MCP, no clock).

Net-new Runtime 3.0 strategy: semiconductor / AI-capex supply-chain momentum.
Thesis: AI capex bids the WHOLE semiconductor supply chain. Hornet's edge over a
per-name momentum agent (bobcat) is that it requires the SECTOR to confirm as a
complex before taking ANY name — "the chain bids together or not at all." The
cross-asset BREADTH GATE lives in scan.py (it is a property of the whole universe,
not of one candle list). This module stays pure: it holds the per-name candle
indicators (trend structure, momentum) and the per-name scorer, both unit-testable
on plain candle lists.

Structurally Hornet is a HYBRID of the two gold templates:
  - it reuses bobcat's `trend_structure` (strict higher-lows / lower-highs, the
    `>= total * 0.6` BULLISH/BEARISH gate) so sector-breadth (computed in scan.py
    by counting bullish 4h trends across the universe) and the per-name 4h-trend
    score use ONE consistent definition;
  - like bison, the per-name signals are SCORE CONTRIBUTORS, not hard gates — the
    direction is already fixed by the breadth gate (scan.py decides LONG-only or
    SHORT-only this tick), so build_thesis scores conviction FOR that direction and
    floors via the caller's minScore.

The supply-chain-gradient bonus (+1 when the EQUIPMENT sub-group is leading the
complex — early-cycle confirmation) is also a cross-group property, so scan.py
computes the boolean and passes it in; build_thesis just applies the +1.
"""


# ── candle accessors (dual-key dict {high|h}/{low|l}/{close|c}; HL xyz candles
#    use the short keys o/c/h/l/v — see market_get_asset_data response shape) ──

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _ck(c, primary, alt=None, default=0.0):
    """Read `primary`, fall back to `alt`, coerce to float. Mirrors bobcat's
    candle field accessor so the trend math is byte-for-byte consistent with the
    breadth computation in scan.py."""
    if not isinstance(c, dict):
        return default
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    return _f(val if val is not None else default, default)


def _close(c):
    return _ck(c, "close", "c")


# ── indicators ──

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH (bobcat-consistent).

    Strict (>) for higher-lows / lower-highs counting; the BULLISH/BEARISH gate is
    `>= total * 0.6` where total = lookback - 1. Strength returned is
    higher_lows/total (BULLISH) or lower_highs/total (BEARISH); NEUTRAL -> 0.0.
    This is the SAME definition scan.py uses to count sector breadth, so a name
    that contributes to bullish breadth also scores its 4h-trend component."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_ck(c, "low", "l") for c in candles[-lookback:]]
    highs = [_ck(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def price_momentum(candles, n_bars=1):
    """% change over the last n_bars closes (bison-consistent)."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


# ── the per-name thesis (direction fixed by the breadth gate; this scores
#    conviction FOR that direction) ──

def build_thesis(coin, sub_group, candles_1h, candles_4h, direction,
                 sm, equipment_leading, inputs):
    """Score one eligible name in the breadth-gate direction. Returns a thesis
    dict (with `score`) or None.

    None is returned ONLY when there is insufficient candle history
    (len(c4h) < 6 or len(c1h) < 6). Unlike bobcat there is no per-name trend GATE
    here: the breadth gate in scan.py has already decided the direction for the
    whole complex, so a name can be taken even if its own 4h trend lags slightly —
    the gradient bonus + 1h confirmation differentiate conviction. minScore is
    applied by the CALLER (scan.py).

    Args:
      coin              the xyz: ticker.
      sub_group         "equipment" | "logic" | "memory" (informational + reasons).
      direction         "LONG" or "SHORT" — fixed by the breadth gate this tick.
      sm                smart-money tuple (dir, tilt_pct) or (None, 0.0) — fetched
                        by the caller; degrades to neutral on read failure.
      equipment_leading bool — True if the EQUIPMENT sub-group breadth is leading
                        the complex (early-cycle confirmation -> +1).

    Score components (base 3 + contributors; floor minScore in scan.py):
      base                            +3
      4h trend agrees with direction  +4  (4h opposing -> -1)
      1h confirms direction           +1
      |1h momentum| tier              +2 (strong) / +1 (moderate)
      smart-money agrees              +2 (aligned) / -2 (opposing)
      supply-chain gradient           +1 (equipment sub-group leading)
    """
    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    mom_strong = float(inputs.get("momStrongPct", 1.0))
    mom_moderate = float(inputs.get("momModeratePct", 0.5))

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    mom_1h = price_momentum(candles_1h, 2)

    score = 3
    reasons = [f"{sub_group}:{coin}"]

    # 4h trend strength (the core per-name conviction; +4 agree / -1 oppose)
    if t4 != "NEUTRAL":
        if (direction == "LONG" and t4 == "BULLISH") or (direction == "SHORT" and t4 == "BEARISH"):
            score += 4
            reasons.append(f"4h_{t4.lower()}_{s4:.0%}")
        else:
            score -= 1
            reasons.append(f"4h_opposing_{t4.lower()}")

    # 1h confirmation (+1)
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 1
        reasons.append(f"1h_confirms_{t1.lower()}")

    # |1h momentum| tier (+2 strong / +1 moderate), sign must match direction
    signed = mom_1h if direction == "LONG" else -mom_1h
    if signed >= mom_strong:
        score += 2
        reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
    elif signed >= mom_moderate:
        score += 1
        reasons.append(f"1h_momentum_{mom_1h:+.2f}%")

    # smart-money agreement (+2 aligned / -2 opposing)
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{_f(sm_tilt):.0f}%")
    elif sm_dir in ("LONG", "SHORT") and sm_dir != direction:
        score -= 2
        reasons.append(f"sm_opposing_{sm_dir}")

    # supply-chain gradient bonus (+1) — equipment leading the complex is the
    # classic early-cycle confirmation: capex tools bid first, then logic/memory.
    if equipment_leading:
        score += 1
        reasons.append("equipment_leading")

    return {
        "coin": coin, "sub_group": sub_group, "direction": direction,
        "score": score, "reasons": reasons,
        "trend_4h": t4, "trend_4h_strength": round(s4, 4), "trend_1h": t1,
        "momentum_1h": round(mom_1h, 3),
        "sm_direction": sm_dir, "sm_tilt_pct": round(_f(sm_tilt), 2),
        "equipment_leading": bool(equipment_leading),
    }
