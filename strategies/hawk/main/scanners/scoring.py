"""HAWK — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Hawk producer's `detect_breakout` +
`trend_4h` + `volume_ratio` + `build_thesis` 5-component breakout scoring
(SKILL.md v1.0.0 / hawk-producer.py v1.0.1). The math/indexing is reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port is
validated.

Multi-asset, single-pass, unit-testable on plain candle lists. `sm` (smart-money
lean) is fetched by the caller and passed in, so this module stays pure.

THESIS (v2 verbatim): a breakout buyer / breakdown seller on the liquid majors.
LONG when the latest 1h close breaks ABOVE the max of the prior 7d (168h) closes
AND smart-money is net long in the same direction at >= smTiltMinPct (default 55%);
SHORT when the latest 1h close breaks BELOW the min of the prior 7d closes AND SM
is net short at >= 55%. Both the breakout and the SM-direction agreement are HARD
GATES (return None if either fails); the 4h-trend alignment and volume are score
contributors only. Max score ~9; minScore (default 5) is applied by the CALLER.
"""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts with a {primary, alt} fallback (close/c, high/h, low/l, volume/v);
# the list branch is defensive and never fires on dict candles, so it does not
# change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v if v is not None else d)
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


def _high(c):
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[3])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from v2 hawk-producer.py) ──

def detect_breakout(candles_1h, lookback_hours):
    """(direction, magnitude_pct) when the latest 1h close breaks the lookback
    high/low, else (None, 0.0). Verbatim from v2 detect_breakout.

    Needs len(candles_1h) >= lookback_hours + 1 (the latest close plus a full
    prior-closes window). The breakout level is max/min of the PRIOR closes
    (window[:-1]) — the latest bar is excluded from its own range. LONG if
    latest_close > high, SHORT if latest_close < low, else None."""
    if len(candles_1h) < lookback_hours + 1:
        return None, 0.0
    window = candles_1h[-lookback_hours:]
    latest = candles_1h[-1]
    latest_close = _close(latest)
    prior_closes = [_close(c) for c in window[:-1]]
    if not prior_closes or latest_close <= 0:
        return None, 0.0
    high = max(prior_closes)
    low = min(prior_closes)
    if latest_close > high:
        return "LONG", ((latest_close - high) / high) * 100
    if latest_close < low:
        return "SHORT", ((low - latest_close) / low) * 100
    return None, 0.0


def trend_4h(candles_4h, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH, else NEUTRAL. Verbatim from
    v2 trend_4h. v2-quirk: STRICT (>) higher-low / lower-high counting, gate at
    `>= total * 0.6` with total = lookback - 1. Reproduced exactly."""
    if len(candles_4h) < lookback:
        return "NEUTRAL"
    lows = [_low(c) for c in candles_4h[-lookback:]]
    highs = [_high(c) for c in candles_4h[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH"
    if lower_highs >= total * 0.6:
        return "BEARISH"
    return "NEUTRAL"


def volume_ratio(candles_1h):
    """Latest 1h volume / mean of the prior 9 (candles[-10:-1]). Verbatim from v2
    volume_ratio. Returns 1.0 when history is short or the prior mean is 0."""
    if len(candles_1h) < 10:
        return 1.0
    avg_prior = sum(_vol(c) for c in candles_1h[-10:-1]) / 9
    latest_vol = _vol(candles_1h[-1])
    if avg_prior <= 0:
        return 1.0
    return latest_vol / avg_prior


# ── the thesis (two hard gates + 5-component score), ported verbatim ──

def build_thesis(coin, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when ANY of the v2 short-circuits fire:
      - insufficient history (len(c1h) < lookback+1 or len(c4h) < 6), OR
      - no breakout (detect_breakout -> None), OR
      - SM gate fails: sm_dir not LONG/SHORT, OR sm_dir != breakout direction,
        OR sm_tilt < smTiltMinPct.
    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0) — the caller
    fetches it (scan._get_sm_direction)."""
    lookback = int(inputs.get("breakoutLookbackHours", 168))
    sm_min = float(inputs.get("smTiltMinPct", 55))
    sm_strong = float(inputs.get("smStrongTiltPct", 70))

    if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
        return None

    direction, magnitude_pct = detect_breakout(candles_1h, lookback)
    if direction is None:
        return None

    # ── GATE: smart-money must agree with the breakout direction at >= sm_min ──
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    # 4h trend alignment (score contributor only — NOT a gate)
    t4 = trend_4h(candles_4h)
    trend_aligned = (
        (direction == "LONG" and t4 == "BULLISH")
        or (direction == "SHORT" and t4 == "BEARISH")
    )

    vol_x = volume_ratio(candles_1h)

    score = 0
    reasons = []

    # Breakout magnitude (+3 / +2 / +1)
    if magnitude_pct >= 1.0:
        score += 3
        reasons.append(f"breakout_strong_{magnitude_pct:+.2f}%")
    elif magnitude_pct >= 0.3:
        score += 2
        reasons.append(f"breakout_{magnitude_pct:+.2f}%")
    else:
        score += 1
        reasons.append(f"breakout_weak_{magnitude_pct:+.2f}%")

    # SM aligned (gate-confirmed) (+2)
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    # SM strongly tilted (+1)
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    # 4h trend aligned (+2)
    if trend_aligned:
        score += 2
        reasons.append(f"4h_trend_aligned_{t4.lower()}")

    # Volume confirmation >= 1.5x average (+1)
    if vol_x >= 1.5:
        score += 1
        reasons.append(f"vol_{vol_x:.1f}x")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "breakout_pct": round(magnitude_pct, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": _f(sm_tilt),
        "trend_4h": t4,
        "volume_ratio": round(vol_x, 2),
    }
