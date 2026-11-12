"""HYDRA DIP — pure thesis math (no I/O, no MCP, no clock). Ported VERBATIM from the v2
producer's `score_single()` DIP branch + technical helpers (hydra-producer.py on
origin/main, Hydra v2.1). Unit-testable on plain candle lists.

DIP = the LONG-ONLY complement. Requires a confirmed 4h uptrend (and, v2.1, a 1d that
confirms) AND a pullback (1h RSI <= dipRsiMax), then rides the resumed move. Stands down
whenever the 4h isn't bullish — so it never knife-catches against the HEDGE sleeve. One
leverage (config.leverage) for every signal. 1.3.0: every read is on CLOSED candles — the
bar in progress never moves structure or RSI.
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


# ── closed candles only (1.3.0 — the Vulture/Moose idiom) ─────────────────
HOUR_SECONDS = 3600
FOUR_HOUR_SECONDS = 14400
DAY_SECONDS = 86400


def _t(c):
    """Candle open, epoch ms (dict `t` or list-row element 0), or -1 when unreadable."""
    if isinstance(c, dict):
        v = c.get("t")
    else:
        v = c[0] if isinstance(c, (list, tuple)) and c else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first. `t` is the candle OPEN in epoch
    milliseconds, so a candle has closed once t/1000 + candle_seconds <= now. The feed's last
    row is the bar in progress: a structure/RSI/ATR read off it changes every tick, so a trend
    could appear and vanish inside one bar. Rows with no readable `t` are dropped."""
    out = [c for c in candles or [] if _t(c) >= 0 and _t(c) / 1000.0 + candle_seconds <= now]
    out.sort(key=_t)
    return out


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


# ── DIP scoring (v2 score_single, LEG="dip"; 1.3.0: closed candles, one leverage) ──
def score_dip(c1, c4, cd, config, now):
    """Returns a thesis dict or None. c1/c4/cd = 1h/4h/1d candle lists; `now` = time.time()
    from scan.py. LONG-only. The bar in progress is dropped first: every read is on CLOSED candles."""
    c1 = closed_candles(c1, HOUR_SECONDS, now)
    c4 = closed_candles(c4, FOUR_HOUR_SECONDS, now)
    cd = closed_candles(cd, DAY_SECONDS, now)
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]                       # last CLOSED close (informational; never sized or emitted)
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    trendd, sd = trend_structure(cd) if len(cd) >= 6 else ("NEUTRAL", 0)
    require_daily = bool(config.get("requireDailyAlign", True))
    rsi = calc_rsi(closes1)

    leverage = max(1, int(config.get("leverage", 3)))

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
    reasons = [f"4h_uptrend_{s4:.0%}"]
    if rsi <= dip_rsi:
        sc += 2
        reasons.append(f"dip_rsi_{rsi:.0f}")
    if trend1 == "BEARISH":
        sc += 1
        reasons.append("1h_pullback")

    return {"direction": direction, "score": sc, "leverage": leverage, "reasons": reasons,
            "price": price, "rsi": rsi, "trend4h": trend4, "trend1h": trend1}
