"""ELEPHANT FADE book — pure macro mean-reversion thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 elephant-producer.py score_fade() — the RSI-extreme +
stretch-from-MA over-extension scoring, the 4h regime knife guard, the point weights, and
the skip gates are copied EXACTLY (marked `# v2-quirk` where the v2 behaviour is load-bearing
and must not be redesigned). Unit-testable on plain candle lists. The runtime owns
sizing/execution/DSL; this only scores.

The FADE book fades short-TF over-extensions on the cross-asset macro complex (XYZ equity
indices / metals / energy / FX + BTC) back toward the mean. It picks the MORE-extreme side
(oversold -> LONG, overbought -> SHORT) from 1h RSI extreme + stretch from the 20-bar 1h MA,
with a 4h regime filter (knife guard) so it never fades a strong macro trend. BOTH directions.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score
# (NORM_DIV = 9.0, max raw ~7 for fade). The 3.0 scaffold owns the wire envelope, so we keep
# the raw integer score on data{} and only expose NORM_DIV for parity.
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
    """Higher-lows / lower-highs structure over the last `lookback` candles. Verbatim v2.
    Used by the fade book ONLY as the 4h regime knife guard (never fade a strong trend)."""
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


def simple_ma(closes, period):
    """Simple moving average over the last `period` closes (or all, if fewer). Verbatim v2."""
    if not closes:
        return 0
    window = closes[-period:] if len(closes) >= period else closes
    return sum(window) / len(window)


def score_fade(asset, candles_1h, candles_4h, inputs):
    """Macro mean-reversion (fade) score for one asset. Returns a thesis dict or None.
    The point weights, the more-extreme-side selection, and the 4h knife guard are copied
    VERBATIM from the v2 elephant-producer.py score_fade() — do NOT redesign.

    Picks the more-extreme side: oversold -> LONG, overbought -> SHORT, from 1h RSI extreme
    + stretch from the 20-bar 1h MA. 4h regime: +1 fading WITH the higher-TF bias, -2 against
    a strong macro trend (knife guard).
    """
    if len(candles_1h) < 22 or len(candles_4h) < 6:         # v2-quirk: 20-bar MA + 4h regime warmup
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    ma = simple_ma(closes1, 20)
    stretch = ((price - ma) / ma * 100) if ma > 0 else 0
    rsi = calc_rsi(closes1)
    trend4, _ = trend_structure(candles_4h)
    rsi_os = float(inputs.get("rsiOversold", 30))
    rsi_ob = float(inputs.get("rsiOverbought", 70))
    st = float(inputs.get("stretchThresholdPct", 1.0))

    # v2-quirk: pick the more-extreme side; magnitudes blend normalised RSI excess + stretch.
    oversold_mag = max(rsi_os - rsi, 0) / max(rsi_os, 1) + max(-stretch, 0) / st
    overbought_mag = max(rsi - rsi_ob, 0) / max(100 - rsi_ob, 1) + max(stretch, 0) / st
    if oversold_mag <= 0 and overbought_mag <= 0:
        return None
    direction = "LONG" if oversold_mag >= overbought_mag else "SHORT"

    score = 0
    reasons = []
    if direction == "LONG":
        if rsi <= 20:
            score += 3
            reasons.append(f"RSI {rsi:.0f} deeply oversold")
        elif rsi <= 25:
            score += 2
            reasons.append(f"RSI {rsi:.0f} oversold")
        elif rsi <= rsi_os:
            score += 1
            reasons.append(f"RSI {rsi:.0f} oversold")
        if -stretch >= 2 * st:
            score += 2
            reasons.append(f"price {stretch:+.2f}% from its 1h average")
        elif -stretch >= st:
            score += 1
            reasons.append(f"price {stretch:+.2f}% from its 1h average")
        # v2-quirk: regime knife guard — never fade a strong macro downtrend (knife guard).
        if trend4 == "BULLISH":
            score += 1
            reasons.append("dip within a 4h uptrend")
        elif trend4 == "BEARISH":
            score -= 2
            reasons.append("fighting a 4h downtrend")
    else:
        if rsi >= 80:
            score += 3
            reasons.append(f"RSI {rsi:.0f} deeply overbought")
        elif rsi >= 75:
            score += 2
            reasons.append(f"RSI {rsi:.0f} overbought")
        elif rsi >= rsi_ob:
            score += 1
            reasons.append(f"RSI {rsi:.0f} overbought")
        if stretch >= 2 * st:
            score += 2
            reasons.append(f"price {stretch:+.2f}% from its 1h average")
        elif stretch >= st:
            score += 1
            reasons.append(f"price {stretch:+.2f}% from its 1h average")
        if trend4 == "BEARISH":
            score += 1
            reasons.append("bounce within a 4h downtrend")
        elif trend4 == "BULLISH":
            score -= 2
            reasons.append("fighting a 4h uptrend")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "stretchPct": stretch,
    }


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: indices / metals /
    FX cap LOW at the venue, so over-leveraging a name is a venue reject — this clamp is
    load-bearing, not cosmetic. Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
