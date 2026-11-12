"""RELATIVITY XYZ — pure dispersion + regime scoring math (no I/O, no MCP, no clock).

A cross-sectional relative-strength engine that scores each candidate independently
for LONG (relative leader) and SHORT (relative laggard), with an index regime gate and
shift momentum boost. The caller (scan.py) builds the universe, fetches candles,
and determines the regime; this module does the numbers.

PORTED VERBATIM from the live fork that produced the measured run
(15-19 Oct 2026). The scoring math is the thing being promoted, so it is
copied rather than rewritten -- see tests/test_engine.py, which pins every branch.

ONE KNOWN DEAD TERM, left in place deliberately. The funding-squeeze bonus requires
|funding| > fundingSqueezeThreshold, shipped at 0.005. Measured on xyz:SP500 over 168
hourly rows (19 Oct 2026): max |funding| 0.0000523, median 0.0000063 -- the threshold is
96x the observed maximum, so the +1 never fires. It is kept because the measured run
carried it, and removing it would change scores. See runtime.yaml for the retune note.
"""

# -- Leverage tiers --
LEVERAGE_BASE = 5
LEVERAGE_MID = 7
LEVERAGE_HIGH = 10
LEVERAGE_MID_THRESHOLD = 8
LEVERAGE_HIGH_THRESHOLD = 10


def _close(c):
    if isinstance(c, dict):
        return float(c.get("close", c.get("c", 0)) or 0)
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4])
    return 0.0


def _high(c):
    if isinstance(c, dict):
        return float(c.get("high", c.get("h", 0)) or 0)
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return float(c.get("low", c.get("l", 0)) or 0)
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3])
    return 0.0


def trend_structure(candles, lookback=6):
    """Higher-lows = BULLISH, lower-highs = BEARISH."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    """Simple-average RSI(14)."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def get_leverage_for_score(score, max_leverage=LEVERAGE_HIGH):
    """Conviction-scaled leverage: 10x at score >=10, 7x at >=8, 5x base."""
    if score >= LEVERAGE_HIGH_THRESHOLD:
        return min(LEVERAGE_HIGH, max_leverage)
    if score >= LEVERAGE_MID_THRESHOLD:
        return min(LEVERAGE_MID, max_leverage)
    return LEVERAGE_BASE


def score_candidate(coin, candles_1h, candles_4h, excess, own24h, leg, inputs,
                    shift_detected=False, asset_funding=0.0):
    """Cross-sectional dispersion score for one xyz: perp.

    Returns a dict, or None when a hard gate refuses the candidate:
      LONG  needs excess >= 0 and a 4h trend that is not BEARISH
      SHORT needs excess <= 0 and a 4h trend that is not BULLISH
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)

    rs_thresh = float(inputs.get("rsThresholdPct", 2.5))
    rsi_ob = float(inputs.get("rsiOverbought", 80))
    rsi_os = float(inputs.get("rsiOversold", 20))
    funding_thresh = float(inputs.get("fundingSqueezeThreshold", 0.005))

    score = 0
    reasons = []

    if leg == "long":
        if excess < 0:
            return None
        if trend4 == "BEARISH":
            return None

        if excess >= 2 * rs_thresh:
            score += 3
            reasons.append("rs_lead_%+.1f%%" % excess)
        elif excess >= rs_thresh:
            score += 2
            reasons.append("rs_lead_%+.1f%%" % excess)
        else:
            score += 1
            reasons.append("rs_lead_%+.1f%%" % excess)

        if trend4 == "BULLISH":
            score += 2
            reasons.append("4h_bullish_%.0f%%" % (s4 * 100))

        if trend1 == "BULLISH":
            score += 1
            reasons.append("1h_bullish_%.0f%%" % (s1 * 100))
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h_bearish")

        if own >= 0:
            score += 1
            reasons.append("abs_up_%+.1f%%" % own)
        else:
            score -= 1
            reasons.append("abs_dn_%+.1f%%" % own)

        if rsi > rsi_ob:
            score -= 2
            reasons.append("rsi_blowoff_%.0f" % rsi)

        if shift_detected:
            score += 2
            reasons.append("shift_boost")

        if asset_funding < -funding_thresh:
            score += 1
            reasons.append("funding_squeeze_%+.5f" % asset_funding)

    else:  # short
        if excess > 0:
            return None
        if trend4 == "BULLISH":
            return None

        if excess <= -2 * rs_thresh:
            score += 3
            reasons.append("rs_lag_%+.1f%%" % excess)
        elif excess <= -rs_thresh:
            score += 2
            reasons.append("rs_lag_%+.1f%%" % excess)
        else:
            score += 1
            reasons.append("rs_lag_%+.1f%%" % excess)

        if trend4 == "BEARISH":
            score += 2
            reasons.append("4h_bearish_%.0f%%" % (s4 * 100))

        if trend1 == "BEARISH":
            score += 1
            reasons.append("1h_bearish_%.0f%%" % (s1 * 100))
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")

        if own <= 0:
            score += 1
            reasons.append("abs_dn_%+.1f%%" % own)
        else:
            score -= 1
            reasons.append("abs_up_%+.1f%%" % own)

        if rsi < rsi_os:
            score -= 2
            reasons.append("rsi_capitulation_%.0f" % rsi)

        if asset_funding > funding_thresh:
            score += 1
            reasons.append("funding_squeeze_%+.5f" % asset_funding)

    return {
        "coin": coin,
        "direction": "LONG" if leg == "long" else "SHORT",
        "score": score,
        "leverage": get_leverage_for_score(score, int(inputs.get("maxLeverage", 10))),
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "trend1h": trend1,
        "excess": excess,
        "own24h": own,
        "funding": asset_funding,
    }


def clamp_leverage(desired, venue_max):
    """Clamp leverage to the asset's venue max."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
