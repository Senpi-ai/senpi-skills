"""COUGAR — pure dispersion thesis math (no I/O, no MCP, no clock). Shared verbatim by
both books; direction is passed in (`leg` = "long" for the leaders book, "short" for the
laggards book). A faithful port of the v2 cougar-producer.py scoring — the gates + the
point weights are copied EXACTLY (marked `# v2-quirk` where the v2 behaviour is load-bearing
and must not be redesigned). Unit-testable on plain candle lists."""

# v2-quirk: wire-score normaliser. v2 emitted score/9.0 capped at 1.0 as the [0,1] wire
# score. The 3.0 scaffold owns the wire envelope, so we keep the raw integer score on
# data{} and only use NORM_DIV if a caller wants the v2-equivalent normalised score.
NORM_DIV = 9.0


def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4])                                  # [t,o,h,l,c,v] -> close
    if isinstance(c, dict):
        return float(c.get("close", c.get("c", 0)) or 0)
    return 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2])                                  # [t,o,h,l,c,v] -> high
    if isinstance(c, dict):
        return float(c.get("high", c.get("h", 0)) or 0)
    return 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3])                                  # [t,o,h,l,c,v] -> low
    if isinstance(c, dict):
        return float(c.get("low", c.get("l", 0)) or 0)
    return 0.0


def trend_structure(candles, lookback=6):
    """Higher-lows / lower-highs structure over the last `lookback` candles. Verbatim v2."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    """Wilder-less simple-average RSI. Verbatim v2."""
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


def score_dispersion(asset, candles_1h, candles_4h, excess, own24h, leg, inputs):
    """Cross-sectional dispersion score for one equity, given its excess return vs the
    equity-universe mean (`excess`) and its own 24h return (`own24h`). Returns a thesis
    dict or None. The point weights + skip gates are copied VERBATIM from the v2
    cougar-producer score_dispersion() — do NOT redesign.

    leg == "long":  long the relative-strength LEADERS  (positive excess + bullish trend)
    leg == "short": short the relative-strength LAGGARDS (negative excess + bearish trend)
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)
    rs_thresh = float(inputs.get("rsThresholdPct", 3.0))

    score = 0
    reasons = []

    if leg == "long":
        if excess < 0:                                      # v2-quirk: must be a relative LEADER
            return None
        if excess >= 2 * rs_thresh:
            score += 3
            reasons.append(f"leading its peers, {excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 2
            reasons.append(f"leading its peers, {excess:+.1f}%")
        else:
            score += 1
            reasons.append(f"leading its peers, {excess:+.1f}%")
        if trend4 == "BEARISH":                             # v2-quirk: never long a 4h downtrend
            return None
        if trend4 == "BULLISH":
            score += 2
            reasons.append(f"4h trend bullish at {s4:.0%} strength")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h trend bullish at {s1:.0%} strength")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h trend has turned bearish")
        if own >= 0:
            score += 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        else:
            score -= 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        rsi_ob = float(inputs.get("rsiOverbought", 80))
        if rsi > rsi_ob:                                    # v2-quirk: blow-off guard (don't chase)
            score -= 2
            reasons.append(f"RSI stretched at {rsi:.0f}, blow-off risk")
    else:  # short
        if excess > 0:                                      # v2-quirk: must be a relative LAGGARD
            return None
        if excess <= -2 * rs_thresh:
            score += 3
            reasons.append(f"lagging its peers, {excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 2
            reasons.append(f"lagging its peers, {excess:+.1f}%")
        else:
            score += 1
            reasons.append(f"lagging its peers, {excess:+.1f}%")
        if trend4 == "BULLISH":                             # v2-quirk: never short a 4h uptrend
            return None
        if trend4 == "BEARISH":
            score += 2
            reasons.append(f"4h trend bearish at {s4:.0%} strength")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h trend bearish at {s1:.0%} strength")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h trend has turned bullish")
        if own <= 0:
            score += 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        else:
            score -= 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        rsi_os = float(inputs.get("rsiOversold", 20))
        if rsi < rsi_os:                                    # v2-quirk: capitulation guard
            score -= 2
            reasons.append(f"RSI washed out at {rsi:.0f}, capitulation risk")

    return {
        "coin": asset,
        "direction": "LONG" if leg == "long" else "SHORT",
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "trend1h": trend1,
        "excess": excess,
        "own24h": own,
    }


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: equities cap LOW
    at the venue (AMD=10x, NVDA=20x, etc.) — over-leveraging a name is a venue reject, so
    this clamp is load-bearing, not cosmetic. Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
