"""SHEEP — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Sheep producer's EMA-stack logic +
spread/SM scoring (sheep-producer.py v1.0.1, SKILL.md v1.0.0). The math is
reproduced VERBATIM so a fidelity harness can diff this module against the v2
producer on the same candle snapshot. Behaviour-preserving quirks from v2 are
kept and flagged `# v2-quirk`.

Long-only, multi-asset (whitelist), single-pass, unit-testable on plain candle
lists. `sm` (smart-money lean) is fetched by the caller and passed in, so this
module stays pure.

The thesis is a HARD GATE on the EMA stack: fire LONG only when fast EMA >
slow EMA on ALL `minStackedFrames` of {15m, 1h, 4h} (default 3 = all three).
Below the gate -> None. Above it, score from spread magnitude + SM bonus.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    """Dual-shape close accessor (dict {close|c} OR list [t,o,h,l,c,v]).
    v2 read dicts only via _f(c, "close", "c"); the list branch is defensive
    and never fires on dict candles, so it does not change v2 behaviour."""
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


def closes(candles):
    return [_close(c) for c in candles]


# ── EMA + stack logic (ported VERBATIM from v2 sheep-producer.py) ──

def ema(cs, period):
    """Exponential moving average. Returns the final EMA value, or None for
    insufficient data. Verbatim from v2 ema(): SMA seed over the first
    `period` closes, then the standard k=2/(period+1) recurrence."""
    if not cs or len(cs) < period:
        return None
    k = 2.0 / (period + 1.0)
    seed = sum(cs[:period]) / period
    ema_val = seed
    for c in cs[period:]:
        ema_val = (c - ema_val) * k + ema_val
    return ema_val


def is_stacked_bullish(cs, fast_period, slow_period):
    """True if fast EMA > slow EMA on this single timeframe. Verbatim from v2."""
    f = ema(cs, fast_period)
    s = ema(cs, slow_period)
    if f is None or s is None:
        return False
    return f > s


def stack_score(stacks_per_timeframe):
    """How many of the per-timeframe stack booleans are True. Verbatim from v2."""
    return sum(1 for s in stacks_per_timeframe if s)


def fast_slow_spread(cs, fast_period, slow_period):
    """% spread of fast EMA above slow EMA on this timeframe. Negative when
    fast is below slow. None for insufficient data. Verbatim from v2."""
    f = ema(cs, fast_period)
    s = ema(cs, slow_period)
    if f is None or s is None or s <= 0:
        return None
    return ((f - s) / s) * 100.0


# ── the thesis (gate on full stack, then spread + SM scoring) ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a LONG thesis dict (with `score`) or
    None. Sheep is long-only.

    None is returned ONLY when the EMA stack count < minStackedFrames (the
    only hard gate). minScore is applied by the CALLER (scan.py), not here.

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0) — the
    caller fetches it (leaderboard_get_markets); SM is a BONUS, never a gate.
    """
    fast = int(inputs.get("emaFast", 9))
    slow = int(inputs.get("emaSlow", 21))
    min_stacked = int(inputs.get("minStackedFrames", 3))
    sm_min = _f(inputs.get("smTiltMinPct", 55))
    sm_strong = _f(inputs.get("smStrongTiltPct", 70))

    cs_15m = closes(candles_15m)
    cs_1h = closes(candles_1h)
    cs_4h = closes(candles_4h)

    stacks = [
        is_stacked_bullish(cs_15m, fast, slow),
        is_stacked_bullish(cs_1h, fast, slow),
        is_stacked_bullish(cs_4h, fast, slow),
    ]
    score_stack = stack_score(stacks)
    if score_stack < min_stacked:
        return None

    spread_4h = fast_slow_spread(cs_4h, fast, slow) or 0.0
    spread_1h = fast_slow_spread(cs_1h, fast, slow) or 0.0

    sm_dir, sm_tilt = sm if sm else (None, 0.0)

    # Score: base 3 for the full triple-stack, then spread + SM bonuses (verbatim)
    s = 3
    reasons = [f"{coin} trending up on 15m, 1h and 4h", f"4h trend strength {spread_4h:+.2f}%"]
    if spread_4h >= 1.0:
        s += 1
        reasons.append("strong trend on the 4h chart")
    if spread_1h >= 0.5:
        s += 1
        reasons.append("strong trend on the 1h chart")
    if sm_dir == "LONG" and sm_tilt >= sm_min:
        s += 1
        reasons.append(f"smart money {sm_tilt:.0f}% long")
        if sm_tilt >= sm_strong:
            s += 1
            reasons.append("smart money heavily long")

    return {
        "coin": coin,
        "direction": "LONG",
        "score": s,
        "reasons": reasons,
        "spread_4h_pct": round(spread_4h, 2),
        "spread_1h_pct": round(spread_1h, 2),
        "stack_score": score_stack,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": _f(sm_tilt),
    }
