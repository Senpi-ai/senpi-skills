"""SALAMANDER — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Salamander producer's `trend_4h` +
`detect_pullback` + `build_thesis` 5-component pullback scoring (SKILL.md v1.0.0 /
salamander-producer.py v1.0.1). The math/indexing is reproduced VERBATIM so a
fidelity harness can diff this against the v2 producer on the same market
snapshot. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`;
fix them only as a separate, labelled change AFTER the port is validated.

Multi-asset, single-pass, unit-testable on plain candle lists. `sm` (smart-money
lean) is fetched by the caller and passed in, so this module stays pure.

THESIS (v2 verbatim): buy the dip in an uptrend / short the rally in a downtrend.
LONG when the 4h trend is BULLISH AND the latest 1h close has pulled back 3-7%
from the recent (prior 24h) high AND smart-money is net LONG at >= smTiltMinPct
(default 55%); SHORT when the 4h trend is BEARISH AND the latest 1h close has
rallied 3-7% from the recent (prior 24h) low AND SM is net SHORT at >= 55%. The
4h-trend-non-neutral gate, the pullback band, and the SM-direction agreement are
ALL HARD GATES (return None if any fails). The midpoint (4-6%) and strong-SM
(>=70%) bonuses are score contributors only. Max score ~9; minScore (default 5)
is applied by the CALLER.
"""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts with a {primary, alt} fallback (close/c, high/h, low/l, volume/v)
# via its own `_f(c, primary, alt)`; here that fallback lives in the accessors. The
# list branch is defensive and never fires on dict candles, so it does not change
# v2 behaviour.

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


# ── indicators (ported verbatim from v2 salamander-producer.py) ──

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


def detect_pullback(candles_1h, lookback_hours, min_pct, max_pct, trend):
    """(direction, pullback_pct) when a counter-move within the trend lands in the
    [min_pct, max_pct] band, else (None, 0.0). Verbatim from v2 detect_pullback.

    For a BULLISH trend: how far the latest 1h close is BELOW the recent (prior
    24h) high. If 3-7%, it's a LONG dip.
    For a BEARISH trend: how far the latest 1h close is ABOVE the recent (prior
    24h) low. If 3-7%, it's a SHORT rally.

    v2-quirk: the recent high/low is taken over window[:-1] (the prior closes,
    excluding the latest bar). Needs len(candles_1h) >= lookback_hours + 1;
    NEUTRAL trend -> (None, 0.0)."""
    if len(candles_1h) < lookback_hours + 1 or trend == "NEUTRAL":
        return None, 0.0
    window = candles_1h[-lookback_hours:]
    closes = [_close(c) for c in window]
    if not closes or closes[-1] <= 0:
        return None, 0.0
    if trend == "BULLISH":
        recent_high = max(closes[:-1])
        if recent_high <= 0:
            return None, 0.0
        pullback_pct = ((recent_high - closes[-1]) / recent_high) * 100
        if min_pct <= pullback_pct <= max_pct:
            return "LONG", pullback_pct
    else:  # BEARISH
        recent_low = min(closes[:-1])
        if recent_low <= 0:
            return None, 0.0
        rally_pct = ((closes[-1] - recent_low) / recent_low) * 100
        if min_pct <= rally_pct <= max_pct:
            return "SHORT", rally_pct
    return None, 0.0


# ── the thesis (three hard gates + 5-component score), ported verbatim ──

def build_thesis(coin, candles_1h, candles_4h, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when ANY of the v2 short-circuits fire:
      - insufficient history (len(c1h) < lookback+1 or len(c4h) < 6), OR
      - 4h trend NEUTRAL, OR
      - no pullback in band (detect_pullback -> None), OR
      - SM gate fails: sm_dir not LONG/SHORT, OR sm_dir != pullback direction,
        OR sm_tilt < smTiltMinPct.
    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, tilt_pct) or (None, 0) — the caller
    fetches it (scan._get_sm_direction)."""
    lookback = int(inputs.get("pullbackLookbackHours", 24))
    min_pct = float(inputs.get("pullbackMinPct", 3.0))
    max_pct = float(inputs.get("pullbackMaxPct", 7.0))
    sm_min = float(inputs.get("smTiltMinPct", 55))
    sm_strong = float(inputs.get("smStrongTiltPct", 70))

    if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
        return None

    # ── GATE: 4h trend must be non-neutral (the foundation) ──
    t4 = trend_4h(candles_4h)
    if t4 == "NEUTRAL":
        return None

    # ── GATE: a 1h pullback/rally in the 3-7% band, in the trend direction ──
    direction, pullback_pct = detect_pullback(candles_1h, lookback, min_pct, max_pct, t4)
    if direction is None:
        return None

    # ── GATE: smart-money must agree with the trend/pullback direction at >= sm_min ──
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    score = 0
    reasons = []

    # 4h trend aligned (gate-confirmed, this is the foundation) (+3)
    score += 3
    reasons.append(f"4h trend is {t4.lower()}")

    # Pullback in the 3-7% sweet spot (gate-confirmed) (+2)
    score += 2
    reasons.append(f"price pulled back {pullback_pct:+.2f}% against the trend")

    # Midpoint bonus (4-6% ideal — too shallow = noise, too deep = trend break) (+1)
    if 4.0 <= pullback_pct <= 6.0:
        score += 1
        reasons.append("pullback in the ideal 4-6% zone")

    # SM aligned (gate-confirmed) (+2)
    score += 2
    reasons.append(f"smart money {sm_tilt:.0f}% on the same side")

    # SM strongly tilted (>= 70%) (+1)
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("smart money strongly one-sided")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": t4,
        "pullback_pct": round(pullback_pct, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": _f(sm_tilt),
    }
