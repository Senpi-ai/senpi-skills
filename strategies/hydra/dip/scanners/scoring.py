"""HYDRA DIP — pure thesis math (no I/O, no MCP, no clock). Ported VERBATIM from the v2
producer's `score_single()` DIP branch + technical helpers (hydra-producer.py on
origin/main, Hydra v2.1). Unit-testable on plain candle lists.

DIP = the LONG-ONLY complement. Requires a confirmed 4h uptrend (and, v2.1, a 1d that
confirms) AND a pullback (1h RSI <= dipRsiMax), then rides the resumed move. Stands down
whenever the 4h isn't bullish — so it never knife-catches against the HEDGE sleeve.
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


def clamp_lev(desired, max_lev):
    return max(1, min(int(desired), int(max_lev)))


# ── DIP scoring (v2 score_single, LEG="dip" branch — VERBATIM) ─────────────
def score_dip(c1, c4, cd, config):
    """Returns a thesis dict or None. c1/c4/cd = 1h/4h/1d candle lists. LONG-only."""
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    trendd, sd = trend_structure(cd) if len(cd) >= 6 else ("NEUTRAL", 0)
    require_daily = bool(config.get("requireDailyAlign", True))
    rsi = calc_rsi(closes1)

    max_lev = int(config.get("maxLeverage", 4))
    leverage = int(config.get("stdLeverage", 3))

    if trend4 != "BULLISH":
        return None
    # v2.1: only dip-buy inside a 1d+4h CONFIRMED uptrend (no knife-catching chop).
    if require_daily and len(cd) >= 6 and trendd != "BULLISH":
        return None
    dip_rsi = float(config.get("dipRsiMax", 42))
    pulled_back = (rsi <= dip_rsi)   # v2.1: require a REAL RSI pullback, not just a non-bullish 1h wiggle
    if not pulled_back:
        return None
    direction = "LONG"
    sc = 2 + (1 if s4 >= 0.6 else 0)
    reasons = [f"4-hour uptrend at {s4:.0%} strength"]
    if rsi <= dip_rsi:
        sc += 2
        reasons.append(f"RSI pulled back to {rsi:.0f}, a dip to buy")
    if trend1 == "BEARISH":
        sc += 1
        reasons.append("1-hour pullback within the uptrend")

    leverage = clamp_lev(leverage, max_lev)
    return {"direction": direction, "score": sc, "leverage": leverage, "reasons": reasons,
            "price": price, "rsi": rsi, "trend4h": trend4, "trend1h": trend1}
