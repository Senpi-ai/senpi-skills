"""SPIDER SCALP — pure scoring functions (port of spider-producer.py v5.1.1).

Ported VERBATIM from senpi-skills/spider/scripts/spider-producer.py (the
technical helpers + score_scalp). No I/O, no MCP, no daemon — pure and
unit-testable. `scan.py` fetches candle/market data via ctx.senpi_mcp and
hands it to score_scalp here.

Source thresholds preserved exactly:
  - candle minimums: 15m >= 20 bars, 1h >= 6 bars
  - MA: simple 20-bar on 15m closes; stretch = (price - ma)/ma * 100
  - side select: fade the larger of oversold_mag / overbought_mag
  - LONG: rsi<=20 +3 / <=25 +2 / <=rsiOversold +1; -stretch>=2*thr +2 / >=thr +1;
          1h BULLISH +1 / BEARISH -2; funding<0 +1
  - SHORT: rsi>=80 +3 / >=75 +2 / >=rsiOverbought +1; stretch>=2*thr +2 / >=thr +1;
          1h BEARISH +1 / BULLISH -2; funding>0 +1
"""


# ── Technical helpers (close=c, high=h, low=l on HL candles) ──

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH."""
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
    if not closes:
        return 0
    window = closes[-period:] if len(closes) >= period else closes
    return sum(window) / len(window)


# ── SCALP scoring — Macro & majors fast mean-reversion, BOTH directions ──

def score_scalp(asset, candles_15m, candles_1h, ctx_meta, config):
    """Score one asset for the SCALP leg.

    Args:
      asset:        symbol (e.g. "BTC" or "xyz:BRENTOIL")
      candles_15m:  list of 15m candle dicts (oldest -> newest)
      candles_1h:   list of 1h candle dicts
      ctx_meta:     the instrument's "ctx" dict (funding)
      config:       inputs dict (reads rsiOversold/rsiOverbought/stretchThresholdPct)

    Returns a thesis dict, or None if data is insufficient OR neither side is
    stretched. The score gate (>= minScore) is applied by the caller.
    """
    if len(candles_15m) < 20 or len(candles_1h) < 6:
        return None
    closes15 = [_close(c) for c in candles_15m]
    price = closes15[-1]

    ma = simple_ma(closes15, 20)
    stretch = ((price - ma) / ma * 100) if ma > 0 else 0
    rsi = calc_rsi(closes15)
    trend1, _ = trend_structure(candles_1h)

    ctx = ctx_meta or {}
    funding = float(ctx.get("funding", 0) or 0)

    rsi_os = config.get("rsiOversold", 30)
    rsi_ob = config.get("rsiOverbought", 70)
    stretch_thresh = config.get("stretchThresholdPct", 0.8)

    # Which side is more extreme? Fade it.
    oversold_mag = max(rsi_os - rsi, 0) / max(rsi_os, 1) + max(-stretch, 0) / stretch_thresh
    overbought_mag = max(rsi - rsi_ob, 0) / max(100 - rsi_ob, 1) + max(stretch, 0) / stretch_thresh
    if oversold_mag <= 0 and overbought_mag <= 0:
        return None
    direction = "LONG" if oversold_mag >= overbought_mag else "SHORT"

    score = 0
    reasons = []

    if direction == "LONG":
        if rsi <= 20:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_oversold")
        elif rsi <= 25:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_oversold")
        elif rsi <= rsi_os:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_oversold")
        if -stretch >= 2 * stretch_thresh:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif -stretch >= stretch_thresh:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        if trend1 == "BULLISH":
            score += 1
            reasons.append("1h_uptrend_dip")
        elif trend1 == "BEARISH":
            score -= 2
            reasons.append("1h_downtrend_knife")
        if funding < 0:
            score += 1
            reasons.append(f"funding_neg_{funding:+.4f}")
    else:  # SHORT
        if rsi >= 80:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_overbought")
        elif rsi >= 75:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_overbought")
        elif rsi >= rsi_ob:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_overbought")
        if stretch >= 2 * stretch_thresh:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif stretch >= stretch_thresh:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        if trend1 == "BEARISH":
            score += 1
            reasons.append("1h_downtrend_rip")
        elif trend1 == "BULLISH":
            score -= 2
            reasons.append("1h_uptrend_knife")
        if funding > 0:
            score += 1
            reasons.append(f"funding_pos_{funding:+.4f}")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend1h": trend1, "stretchPct": stretch, "funding": funding,
    }


def clamp_leverage(desired, venue_max):
    """Clamp desired leverage to the asset's Hyperliquid venue max."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
