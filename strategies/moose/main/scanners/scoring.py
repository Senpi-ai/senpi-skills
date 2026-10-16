"""MOOSE — pure thesis math (no I/O, no MCP, no clock).

The oldest trend-following rule, kept textbook. On CLOSED 4h candles a fast EMA crossing a slow
EMA (default 20/50) is the entry trigger — long on the cross up, short on the cross down — or, as
the alternate detector, a Donchian-channel breakout (default 20 bars). Either is taken only in the
direction of the daily trend: the signal close against a simple moving average of closed daily
candles (default 50 days). Nothing here is scored into a black box; every entry can be checked
on any chart.

Volatility sets the leverage, not the stop. The DSL's hard stop is a fixed percent of margin
(ROE), and its distance in PRICE is that percent divided by leverage. So `leverage_for` picks the
largest whole leverage in [leverageMin, leverageMax] at which the stop sits at least
`atrStopMultiple` ATRs (ATR(14) on the same 4h candles) from entry — calmer coin, more leverage;
wilder coin, less — and the same ROE ladder re-expresses itself in each coin's own volatility.

Everything takes plain candle lists and plain numbers, so it unit-tests without a mock of
anything. scan.py does the reads and the state.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


# ── candle accessors: Hyperliquid serves o/h/l/c/v as STRINGS and t/T in epoch MILLISECONDS ──

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    return _f(c.get("c")) if isinstance(c, dict) else 0.0


def _high(c):
    return _f(c.get("h")) if isinstance(c, dict) else 0.0


def _low(c):
    return _f(c.get("l")) if isinstance(c, dict) else 0.0


def _t(c):
    return _f(c.get("t"), -1.0) if isinstance(c, dict) else -1.0


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first.

    `t` is the candle OPEN in epoch milliseconds, so a candle has closed once
    t/1000 + candle_seconds <= now. The feed's last row is the bar in progress: an EMA or a channel
    read off it changes every tick, so a cross could appear and vanish inside one bar and the
    strategy would trade a signal no chart ever printed. Rows with no readable `t` are dropped
    (the runtime's numeric cast can thin a series; nothing here assumes fixed spacing)."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


# ── indicators ──

def ema(values, period):
    """EMA series seeded with the SMA of the first `period` values (k = 2/(period+1)).
    Aligned to values[period-1:]; empty if the series is shorter than the period."""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def ema_cross(closes, fast, slow, max_bars_ago):
    """The most recent fast/slow EMA cross among the last `max_bars_ago + 1` closed bars.

    A cross at bar i means the sign of (fast − slow) at i is non-zero and differs from the sign at
    i−1 (a bar exactly on the line counts as neither side, so leaving it is a cross). Walking
    back from the latest bar, the first flip found is the most recent one, so a cross that has
    since reversed can never be returned. Returns
    {direction, bars_ago, ema_fast, ema_slow, spread_pct} or None (no cross in the window, an
    exact tie, or too little history: the slow EMA needs `slow` bars to seed and one more to
    move)."""
    fast, slow = int(fast), int(slow)
    if fast <= 0 or slow <= fast or len(closes) < slow + 2:
        return None
    ef, es = ema(closes, fast), ema(closes, slow)
    n = len(es)
    ef = ef[-n:]
    diff = [a - b for a, b in zip(ef, es)]
    last = len(diff) - 1
    for back in range(0, int(max_bars_ago) + 1):
        i = last - back
        if i < 1:
            break
        side = _sign(diff[i])
        if side == 0:
            continue
        if _sign(diff[i - 1]) != side:
            price = closes[-1]
            return {
                "direction": "LONG" if side > 0 else "SHORT",
                "bars_ago": back,
                "ema_fast": ef[last],
                "ema_slow": es[last],
                "spread_pct": (diff[last] / price * 100.0) if price > 0 else 0.0,
            }
    return None


def donchian_breakout(candles, lookback, max_bars_ago):
    """A fresh Donchian-channel breakout: the latest closed bar closed beyond the highest high
    (LONG) or lowest low (SHORT) of the `lookback` bars before it, and the run of such bars ending
    now started within the last `max_bars_ago + 1` bars — so a trend that has been making new
    highs for days is not chased. Returns {direction, bars_ago, channel_high, channel_low,
    breakout_pct} or None."""
    lookback = int(lookback)
    highs = [_high(c) for c in candles]
    lows = [_low(c) for c in candles]
    closes = [_close(c) for c in candles]
    n = len(closes)
    if lookback <= 0 or n < lookback + int(max_bars_ago) + 2:
        return None

    def state(i):
        hh, ll = max(highs[i - lookback:i]), min(lows[i - lookback:i])
        if closes[i] > hh:
            return "LONG", hh, ll
        if closes[i] < ll:
            return "SHORT", hh, ll
        return None, hh, ll

    last = n - 1
    side, hh, ll = state(last)
    if side is None:
        return None
    run = 1
    while last - run >= lookback and state(last - run)[0] == side:
        run += 1
    bars_ago = run - 1
    if bars_ago > int(max_bars_ago):
        return None
    price = closes[last]
    beyond = (price - hh) if side == "LONG" else (ll - price)
    return {
        "direction": side,
        "bars_ago": bars_ago,
        "channel_high": hh,
        "channel_low": ll,
        "breakout_pct": (beyond / price * 100.0) if price > 0 else 0.0,
    }


def atr_pct(candles, period):
    """Wilder's ATR over `period` bars, as a PERCENT of the latest close. 0.0 when the series is
    too short or the close is not positive."""
    period = int(period)
    highs = [_high(c) for c in candles]
    lows = [_low(c) for c in candles]
    closes = [_close(c) for c in candles]
    n = len(closes)
    if period <= 0 or n < period + 1 or closes[-1] <= 0:
        return 0.0
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, n)]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr / closes[-1] * 100.0


def daily_regime(daily_closes, period, price):
    """(regime, sma): BULL when `price` is above the simple moving average of the last `period`
    closed daily candles, BEAR below it, FLAT on it; (None, 0.0) with too little history."""
    period = int(period)
    if period <= 0 or len(daily_closes) < period or price <= 0:
        return None, 0.0
    sma = sum(daily_closes[-period:]) / period
    if price > sma:
        return "BULL", sma
    if price < sma:
        return "BEAR", sma
    return "FLAT", sma


# ── volatility-sized leverage ──

def leverage_for(atr_pct_value, stop_roe_pct, atr_multiple, lev_min, lev_max):
    """The largest WHOLE leverage in [lev_min, lev_max] at which the DSL's fixed ROE stop sits at
    least `atr_multiple` ATRs from entry in PRICE.

    The stop's price distance is stop_roe_pct / leverage, so the condition is
    leverage <= stop_roe_pct / (atr_multiple x atr_pct). Clamped both ways: a calm coin never
    exceeds lev_max, and a wild one never drops below lev_min — there the stop is closer than the
    multiple asks, which `stop_price_pct` makes visible on the signal. An unreadable ATR (0) gets
    the floor, the most conservative answer."""
    lo = max(1, int(lev_min))
    hi = max(lo, int(lev_max))
    if atr_pct_value <= 0 or atr_multiple <= 0 or stop_roe_pct <= 0:
        return lo
    return max(lo, min(hi, int(stop_roe_pct / (atr_multiple * atr_pct_value))))


# ── the thesis ──

def build_thesis(coin, candles_4h, candles_1d, inputs):
    """Returns a thesis dict or None. `candles_4h` / `candles_1d` are CLOSED candles, oldest first
    (scan.py applies `closed_candles`). None when history is short, no trigger fired inside the
    freshness window, the daily trend disagrees, or the ATR is unreadable. `minScore` is applied
    by the CALLER (scan.py); the score is a count of confirmations, not a black box: 2 for the
    trigger with the daily trend behind it, +1 entered on the trigger bar itself, +1 when the
    cross (or breakout) is wide enough not to be a hairline."""
    detector = str(inputs.get("entryDetector", "ema_cross")).strip().lower()
    fast = int(inputs.get("emaFast", 20))
    slow = int(inputs.get("emaSlow", 50))
    lookback = int(inputs.get("donchianLookback", 20))
    max_bars = int(inputs.get("maxBarsSinceSignal", 2))
    sma_period = int(inputs.get("regimeSmaPeriod", 50))
    atr_period = int(inputs.get("atrPeriod", 14))
    atr_multiple = float(inputs.get("atrStopMultiple", 2.0))
    stop_roe = float(inputs.get("stopRoePct", 10.0))
    lev_min = int(inputs.get("leverageMin", 2))
    lev_max = int(inputs.get("leverageMax", 5))
    min_spread = float(inputs.get("minSpreadPct", 0.10))

    closes = [_close(c) for c in candles_4h]
    need = max(slow, lookback) + max_bars + 2
    if len(closes) < need or closes[-1] <= 0:
        return None
    price = closes[-1]

    if detector == "donchian":
        sig = donchian_breakout(candles_4h, lookback, max_bars)
        strength = sig["breakout_pct"] if sig else 0.0
    else:
        detector = "ema_cross"
        sig = ema_cross(closes, fast, slow, max_bars)
        strength = abs(sig["spread_pct"]) if sig else 0.0
    if not sig:
        return None

    regime, sma = daily_regime([_close(c) for c in candles_1d], sma_period, price)
    if regime is None:
        return None
    direction = sig["direction"]
    if (direction == "LONG" and regime != "BULL") or (direction == "SHORT" and regime != "BEAR"):
        return None

    atr = atr_pct(candles_4h, atr_period)
    if atr <= 0:
        return None
    leverage = leverage_for(atr, stop_roe, atr_multiple, lev_min, lev_max)
    stop_price_pct = stop_roe / leverage

    score, reasons = 2, [f"{detector}_{direction.lower()}", f"daily_{regime.lower()}_sma{sma_period}"]
    if sig["bars_ago"] == 0:
        score += 1
        reasons.append("on_the_signal_bar")
    else:
        reasons.append(f"{sig['bars_ago']}_bars_after_signal")
    if strength >= min_spread:
        score += 1
        reasons.append(f"spread_{strength:.2f}pct")
    reasons.append(f"atr_{atr:.2f}pct_{leverage}x_stop_{stop_price_pct:.2f}pct")

    th = {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "detector": detector,
        "signal_bar_t": _t(candles_4h[-1 - sig["bars_ago"]]),
        "bars_since_signal": sig["bars_ago"],
        "daily_sma": round(sma, 6), "daily_regime": regime,
        "atr_pct": round(atr, 4), "leverage": leverage,
        "stop_price_pct": round(stop_price_pct, 4), "price": price,
    }
    if detector == "donchian":
        th.update({"channel_high": sig["channel_high"], "channel_low": sig["channel_low"],
                   "breakout_pct": round(sig["breakout_pct"], 4)})
    else:
        th.update({"ema_fast": round(sig["ema_fast"], 6), "ema_slow": round(sig["ema_slow"], 6),
                   "ema_spread_pct": round(sig["spread_pct"], 4)})
    return th
