"""ELEPHANT TREND book — pure macro-trend thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 elephant-producer.py score_trend() — the 4h-backbone
multi-timeframe trend scoring, the point weights, and the skip gates are copied EXACTLY
(marked `# v2-quirk` where the v2 behaviour is load-bearing and must not be redesigned).
Unit-testable on plain candle lists. The runtime owns sizing/execution/DSL; this only scores.

The TREND book rides the medium-term macro trend on the cross-asset macro complex (XYZ
equity indices / metals / energy / FX + BTC). The 4h structure IS the macro backbone —
BULLISH -> LONG, BEARISH -> SHORT, NEUTRAL -> skip (no clean trend) — confirmed by 1h
structure, 24h momentum, and RSI room. BOTH directions.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score
# (NORM_DIV = 9.0, max raw ~8 for trend). The 3.0 scaffold owns the wire envelope, so we
# keep the raw integer score on data{} and only expose NORM_DIV for parity if a caller
# wants the v2-equivalent normalised score.
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


def score_trend(asset, candles_1h, candles_4h, own24h, inputs):
    """Macro multi-timeframe trend score for one asset. Returns a thesis dict or None.
    The point weights + skip gates are copied VERBATIM from the v2 elephant-producer.py
    score_trend() — do NOT redesign.

    own24h = the asset's 24h return (%) from the instrument context (ret_24h in v2).
    The 4h structure sets the direction; a NEUTRAL 4h macro = no clean trend -> None.
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:          # v2-quirk: trend warmup floors
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)
    own = own24h if own24h is not None else 0.0
    mom = float(inputs.get("momThresholdPct", 1.5))
    rsi_ob = float(inputs.get("rsiOverbought", 75))
    rsi_os = float(inputs.get("rsiOversold", 25))

    # v2-quirk: the 4h structure IS the macro trend; a NEUTRAL macro = no clean trend.
    if trend4 == "BULLISH":
        direction = "LONG"
    elif trend4 == "BEARISH":
        direction = "SHORT"
    else:
        return None

    score = 3
    reasons = [f"4h trend {trend4.lower()}, {s4:.0%} of bars confirm"]

    if (direction == "LONG" and trend1 == "BULLISH") or (direction == "SHORT" and trend1 == "BEARISH"):
        score += 2
        reasons.append(f"1h trend agrees, {s1:.0%} of bars confirm")
    elif (direction == "LONG" and trend1 == "BEARISH") or (direction == "SHORT" and trend1 == "BULLISH"):
        score -= 1
        reasons.append("1h trend runs against the move")

    if direction == "LONG":
        if own >= mom:
            score += 2
            reasons.append(f"24h momentum {own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"24h momentum {own:+.1f}%")
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"RSI {rsi:.0f} leaves room to run")
    else:
        if own <= -mom:
            score += 2
            reasons.append(f"24h momentum {own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"24h momentum {own:+.1f}%")
        if rsi > rsi_os:
            score += 1
            reasons.append(f"RSI {rsi:.0f} leaves room to run")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "own24h": own,
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
