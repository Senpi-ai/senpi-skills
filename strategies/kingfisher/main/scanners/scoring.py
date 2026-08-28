"""KINGFISHER — pure thesis math: RSI + MACD classic-indicator crossover (no I/O, no MCP, no clock).

Two textbook indicators, combined the way retail traders actually use them:
  - MACD (EMA fast/slow, signal EMA, histogram) is the DIRECTIONAL trigger — a fresh signal-line
    crossover (the histogram flips sign) is the entry; an already-crossed, still-expanding histogram
    is a weaker continuation.
  - RSI(14) CONFIRMS: a long wants RSI with room above 50 (momentum, not yet overbought); a short
    wants RSI below 50 (not yet oversold). RSI at the opposite extreme vetoes the crossover.
  - 4h trend structure is a context bonus/penalty.

Pure + single-pass + unit-testable on plain candle lists. Candles are keyed o/h/l/c/v with STRING
values — `_close`/`_f` handle both the short key and the string coercion.
"""


def _f(v, d=0.0):
    try:
        return float(v)
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


# ── indicators ──

def _ema(vals, period):
    """EMA series seeded with the SMA of the first `period` values (k = 2/(period+1)).
    Length = len(vals) - period + 1; empty if too short."""
    if len(vals) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(vals[:period]) / period]
    for v in vals[period:]:
        ema.append(v * k + ema[-1] * (1.0 - k))
    return ema


def calc_macd(closes, fast=12, slow=26, signal=9):
    """Classic MACD. Returns (macd_line, signal_line, histogram) as EQUAL-LENGTH tail series (aligned
    to where the signal EMA exists), or (None, None, None) if history is too short.
    macd = EMA(fast) - EMA(slow); signal = EMA(macd, signal); histogram = macd - signal."""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast, ema_slow = _ema(closes, fast), _ema(closes, slow)
    n = len(ema_slow)                       # ema_slow is the shorter series (slow > fast)
    fast_tail = ema_fast[-n:]
    macd_line = [fast_tail[i] - ema_slow[i] for i in range(n)]
    sig = _ema(macd_line, signal)
    if not sig:
        return None, None, None
    m = len(sig)
    macd_tail = macd_line[-m:]
    hist = [macd_tail[i] - sig[i] for i in range(m)]
    return macd_tail, sig, hist


def calc_rsi(closes, period=14):
    """Trailing-window RSI (last `period` gains/losses). 50 when history is too short."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def trend_structure(candles, lookback=6):
    """Higher-lows = BULLISH / lower-highs = BEARISH — the 4h context bonus. (BULLISH|BEARISH|NEUTRAL, strength)."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if total and higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if total and lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


# ── conviction-scaled margin PERCENT ──

def margin_tier_pct(score, base_pct):
    """marginPct (PERCENT in (0,100]) scaled by conviction: score>=8 -> base*1.5, >=6 -> base*1.25,
    else base. The runtime sizes (marginPct/100) x withdrawable."""
    if score >= 8:
        return base_pct * 1.5
    if score >= 6:
        return base_pct * 1.25
    return base_pct


# ── the thesis: MACD crossover trigger + RSI confirmation + 4h trend context ──

def build_thesis(coin, candles_1h, candles_4h, inputs):
    """Returns a thesis dict (with `score`) or None. None when history is insufficient or no MACD
    direction resolves. `minScore` is applied by the CALLER (scan.py)."""
    fast = int(inputs.get("macdFast", 12))
    slow = int(inputs.get("macdSlow", 26))
    sig_p = int(inputs.get("macdSignal", 9))
    rsi_p = int(inputs.get("rsiPeriod", 14))
    rsi_ob = float(inputs.get("rsiOverbought", 70))
    rsi_os = float(inputs.get("rsiOversold", 30))
    hist_min_pct = float(inputs.get("histStrengthPct", 0.03))

    closes = [_close(c) for c in candles_1h]
    if len(closes) < slow + sig_p + 2:
        return None

    macd_line, sig_line, hist = calc_macd(closes, fast, slow, sig_p)
    if hist is None or len(hist) < 2:
        return None
    rsi = calc_rsi(closes, rsi_p)
    hist_now, hist_prev, macd_now = hist[-1], hist[-2], macd_line[-1]

    # MACD state = which side of its signal line the MACD line sits on (the sign of the histogram).
    # Being on-side is a valid signal; a FRESH crossover this bar (the histogram just flipped sign) is
    # the strongest trigger and scores higher. Exactly flat -> no signal.
    if hist_now > 0:
        direction, fresh = "LONG", hist_prev <= 0
    elif hist_now < 0:
        direction, fresh = "SHORT", hist_prev >= 0
    else:
        return None
    core = 4 if fresh else 2
    source = f"macd_{'bull' if direction == 'LONG' else 'bear'}_{'cross' if fresh else 'state'}"

    score, reasons = core, [
        f"MACD {'crossed' if fresh else 'holding'} {'bullish' if direction == 'LONG' else 'bearish'}"
    ]

    # MACD zero-line context (+1 when the histogram side agrees with the MACD line's sign)
    if (direction == "LONG" and macd_now > 0) or (direction == "SHORT" and macd_now < 0):
        score += 1
        reasons.append("MACD " + ("above" if macd_now > 0 else "below") + " the zero line")

    # RSI as MOMENTUM confirmation (not a mean-reversion fade): a long wants RSI above 50, a short
    # below 50 — that CONFIRMS the MACD direction. Only a TRUE extreme (>= rsi_overbought /
    # <= rsi_oversold) is a mild caution, because a strong MACD trend legitimately runs RSI hot.
    if direction == "LONG":
        if rsi >= rsi_ob:
            score -= 1; reasons.append(f"RSI overbought at {rsi:.0f}")
        elif rsi >= 50:
            score += 2; reasons.append(f"RSI confirms at {rsi:.0f}")
        else:
            reasons.append(f"RSI weak at {rsi:.0f}")
    else:
        if rsi <= rsi_os:
            score -= 1; reasons.append(f"RSI oversold at {rsi:.0f}")
        elif rsi <= 50:
            score += 2; reasons.append(f"RSI confirms at {rsi:.0f}")
        else:
            reasons.append(f"RSI weak at {rsi:.0f}")

    # histogram strength, normalised by price (+1)
    price = closes[-1]
    hist_pct = (hist_now / price * 100.0) if price else 0.0
    if abs(hist_pct) >= hist_min_pct:
        score += 1
        reasons.append(f"MACD histogram {hist_pct:+.3f}% of price")

    # 4h trend structure context (+2 aligned / -1 opposing)
    trend_4h, ts = trend_structure(candles_4h)
    if trend_4h != "NEUTRAL":
        if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
            score += 2; reasons.append(f"4h trend {trend_4h.lower()} ({ts:.0%} of bars)")
        else:
            score -= 1; reasons.append(f"4h trend {trend_4h.lower()} opposes this trade")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "directionSource": source, "rsi": round(rsi, 1),
        "macd": round(macd_now, 6), "signal": round(sig_line[-1], 6), "hist": round(hist_now, 6),
        "trend_4h": trend_4h, "price": price,
    }
