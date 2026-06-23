"""SPIDER SWING — pure scoring functions (port of spider-producer.py v5.1.1).

Ported VERBATIM from senpi-skills/spider/scripts/spider-producer.py (the
technical helpers + score_swing). No I/O, no MCP, no daemon — pure and
unit-testable. `scan.py` fetches candle/market data via ctx.senpi_mcp and
hands it to score_swing here.

Source thresholds preserved exactly:
  - candle minimums: 1h >= 8 bars, 4h >= 6 bars
  - 4h trend: BULLISH +3 / BEARISH -4
  - 1h trend: BULLISH +2 / BEARISH -1
  - 24h relative strength: >=8 +3, >=4 +2, >=1 +1, <0 -1
  - RSI: > rsiMaxLong (78) -2, < 50 +1
  - funding: < 0 +1, > 0.0002 -1
  - smart-money: ratio > 58 +2, < 42 -2
"""


# ── Technical helpers (close=c, high=h, low=l on HL candles) ──

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


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


# ── SWING scoring — Tech & AI multi-day momentum, LONG only ──

def score_swing(asset, candles_1h, candles_4h, ctx_meta, sm_ratio, config):
    """Score one asset for the SWING leg.

    Args:
      asset:        symbol (e.g. "xyz:NVDA" or "SUI")
      candles_1h:   list of 1h candle dicts (oldest -> newest)
      candles_4h:   list of 4h candle dicts
      ctx_meta:     the instrument's "ctx" dict (funding/markPx/prevDayPx)
      sm_ratio:     smart-money long-ratio pct for this asset, or None
      config:       inputs dict (reads rsiMaxLong; default 78)

    Returns a thesis dict, or None if data is insufficient. The score gate
    (>= minScore) is applied by the caller, not here.
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)

    ctx = ctx_meta or {}
    funding = float(ctx.get("funding", 0) or 0)
    markpx = float(ctx.get("markPx", price) or price)
    prevday = float(ctx.get("prevDayPx", 0) or 0)
    rs24 = ((markpx - prevday) / prevday * 100) if prevday > 0 else price_momentum(candles_1h, min(24, len(candles_1h) - 1))

    direction = "LONG"
    score = 0
    reasons = []

    # 4h trend structure: the multi-day backbone. Bearish kills it.
    if trend4 == "BULLISH":
        score += 3
        reasons.append(f"4h_bullish_{s4:.0%}")
    elif trend4 == "BEARISH":
        score -= 4
        reasons.append("4h_bearish")

    # 1h trend confirmation
    if trend1 == "BULLISH":
        score += 2
        reasons.append(f"1h_bullish_{s1:.0%}")
    elif trend1 == "BEARISH":
        score -= 1
        reasons.append("1h_bearish")

    # 24h relative-strength proxy
    if rs24 >= 8:
        score += 3
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 >= 4:
        score += 2
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 >= 1:
        score += 1
        reasons.append(f"rs_{rs24:+.1f}%")
    elif rs24 < 0:
        score -= 1
        reasons.append(f"rs_neg_{rs24:+.1f}%")

    # RSI room (overbought penalty / room bonus)
    rsi_max = config.get("rsiMaxLong", 78)
    if rsi > rsi_max:
        score -= 2
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif rsi < 50:
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # Funding: negative funding (shorts pay) favors a LONG; very crowded
    # long funding is a small penalty.
    if funding < 0:
        score += 1
        reasons.append(f"funding_neg_{funding:+.4f}")
    elif funding > 0.0002:
        score -= 1
        reasons.append("funding_crowded")

    # Smart-money consensus bonus (crypto alts only; XYZ has none)
    sm_pct = 0.0
    if sm_ratio is not None:
        sm_pct = sm_ratio
        if sm_ratio > 58:
            score += 2
            reasons.append(f"sm_long_{sm_ratio:.0f}%")
        elif sm_ratio < 42:
            score -= 2
            reasons.append(f"sm_short_{sm_ratio:.0f}%")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "trend1h": trend1, "rs": rs24,
        "smPct": sm_pct, "funding": funding,
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
