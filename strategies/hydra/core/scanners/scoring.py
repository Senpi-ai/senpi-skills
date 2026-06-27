"""HYDRA CORE — pure thesis math (no I/O, no MCP, no clock). Ported VERBATIM from the
v2 producer's `score_single()` CORE branch + technical helpers (hydra-producer.py on
origin/main, Hydra v2.1). Unit-testable on plain candle lists.

CORE = the directional thesis spine. The only sleeve that goes both ways: LONG the 4h
uptrend / SHORT a confirmed 4h downtrend, sit out a NEUTRAL tape. v2.1 chop filter: the
1d must confirm the 4h (in a range the 1d is NEUTRAL -> stand down). Conviction-tiered
leverage: stdLeverage standard, maxLeverage at apexScore.
"""


# ── candle accessors (dict OR [t,o,h,l,c,v] list rows) ─────────────────────
def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4] or 0)
    return float(c.get("close", c.get("c", 0)) or 0) if isinstance(c, dict) else 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2] or 0)
    return float(c.get("high", c.get("h", 0)) or 0) if isinstance(c, dict) else 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3] or 0)
    return float(c.get("low", c.get("l", 0)) or 0) if isinstance(c, dict) else 0.0


def trend_structure(candles, lookback=6):
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


def _funding(ctx_block):
    try:
        return float(ctx_block.get("funding", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def clamp_lev(desired, max_lev):
    return max(1, min(int(desired), int(max_lev)))


# ── CORE scoring (v2 score_single, LEG="core" branch — VERBATIM) ───────────
def score_core(c1, c4, cd, ctx_block, config):
    """Returns a thesis dict or None. c1/c4/cd = 1h/4h/1d candle lists."""
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    trendd, sd = trend_structure(cd) if len(cd) >= 6 else ("NEUTRAL", 0)
    require_daily = bool(config.get("requireDailyAlign", True))
    rsi = calc_rsi(closes1)
    fund = _funding(ctx_block)

    sc, reasons = 0, []
    max_lev = int(config.get("maxLeverage", 5))
    std_lev = int(config.get("stdLeverage", 3))

    if trend4 == "NEUTRAL":
        return None
    direction = "LONG" if trend4 == "BULLISH" else "SHORT"
    # v2.1 CHOP FILTER: the 1d must confirm the 4h direction. In a range the 1d is
    # NEUTRAL -> no entry, so the core stops buying the top of a chop. (Fails open only
    # if 1d data is unavailable.)
    if require_daily and len(cd) >= 6 and trendd != ("BULLISH" if direction == "LONG" else "BEARISH"):
        return None
    sc += 3
    reasons.append(f"4h_{trend4.lower()}_{s4:.0%}_1d_{trendd.lower()}")
    if (direction == "LONG" and trend1 == "BULLISH") or (direction == "SHORT" and trend1 == "BEARISH"):
        sc += 2
        reasons.append(f"1h_confirms_{trend1.lower()}")
    elif (direction == "LONG" and trend1 == "BEARISH") or (direction == "SHORT" and trend1 == "BULLISH"):
        sc -= 1
        reasons.append("1h_against")
    ob = float(config.get("rsiOverbought", 80))
    os_ = float(config.get("rsiOversold", 20))
    if direction == "LONG" and rsi > ob:
        sc -= 2
        reasons.append(f"rsi_blowoff_{rsi:.0f}")
    if direction == "SHORT" and rsi < os_:
        sc -= 2
        reasons.append(f"rsi_capitulation_{rsi:.0f}")
    if direction == "LONG" and fund < 0:
        sc += 1
        reasons.append("funding_pays_long")
    if direction == "SHORT" and fund > 0:
        sc += 1
        reasons.append("funding_pays_short")
    leverage = max_lev if sc >= int(config.get("apexScore", 7)) else std_lev

    leverage = clamp_lev(leverage, max_lev)
    return {"direction": direction, "score": sc, "leverage": leverage, "reasons": reasons,
            "price": price, "rsi": rsi, "trend4h": trend4, "trend1h": trend1}
