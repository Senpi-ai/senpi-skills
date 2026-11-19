"""MUSTANG — pure thesis math (no I/O, no MCP, no clock).

Ride the Nasdaq while its long-term trend is up; when it is down, buy only a washout.

  * The REGIME is the regime market's latest closed daily close against the simple average of its
    last 40 CLOSED weekly closes — the 200-trading-day average, read off weekly candles because the
    daily feed carries only ~90 days. Above it is an uptrend, at or below it a downtrend.
  * In an uptrend the trade market is bought at a daily close unless its 10-day RSI (Wilder, on
    closed daily candles) has run over the overheat line — no entry into a blow-off.
  * In a downtrend it is bought only when that RSI has closed under the washout line.
  * Nothing is ever shorted, and nothing here exits: the DSL owns every exit.

Every input is a plain candle list or a number, so it unit-tests without a mock of anything.
scan.py does the reads and the state.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

WEEK = 7 * 86400


# ── candle accessors: Hyperliquid serves o/h/l/c/v as STRINGS and t/T in epoch MILLISECONDS ──

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    return _f(c.get("c")) if isinstance(c, dict) else 0.0


def _t(c):
    return _f(c.get("t"), -1.0) if isinstance(c, dict) else -1.0


def bare(name):
    """The venue-agnostic ticker: `xyz:XYZ100` and `XYZ100` both read XYZ100."""
    return str(name or "").split(":", 1)[-1].upper()


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first (t is the open, in epoch ms). The feed's
    last row is the bar in progress; an indicator read off it moves every tick. Rows with no
    readable `t` or no positive close are dropped."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0 or _close(c) <= 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


# ── indicators ──

def rsi_wilder(closes, period):
    """Wilder's RSI series aligned to `closes`; None until `period` changes have been seen.
    Seeded with the simple mean of the first `period` gains and losses, then smoothed with
    weight (period-1)/period. A flat window (no losses) reads 100."""
    period = int(period)
    out = [None] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def weekly_average(weekly_closed, weeks):
    """Simple average of the last `weeks` closed weekly closes, or None with too little history
    (a market listed less than `weeks` weeks ago has no long-term average yet)."""
    weeks = int(weeks)
    closes = [_close(c) for c in weekly_closed]
    if weeks <= 0 or len(closes) < weeks:
        return None
    return sum(closes[-weeks:]) / weeks


# ── the thesis ──

def regime(regime_daily_closed, regime_weekly_closed, inputs):
    """{'up': bool, 'close', 'average'} or None. Uptrend = the latest closed daily close above the
    average of the last `regimeWeeks` closed weekly closes."""
    if not regime_daily_closed:
        return None
    avg = weekly_average(regime_weekly_closed, inputs.get("regimeWeeks", 40))
    if avg is None or avg <= 0:
        return None
    close = _close(regime_daily_closed[-1])
    return {"up": close > avg, "close": close, "average": avg}


def build_thesis(asset, trade_daily_closed, reg, inputs):
    """The entry for `asset` at its latest closed daily bar, or None.

    Uptrend: buy unless RSI(rsiPeriod) closed at or over `overheatRsi`.
    Downtrend: buy only when RSI closed under `dipRsi`."""
    if not reg or not trade_daily_closed:
        return None
    period = int(inputs.get("rsiPeriod", 10))
    heat = _f(inputs.get("overheatRsi", 79), 79.0)
    dip = _f(inputs.get("dipRsi", 30), 30.0)
    closes = [_close(c) for c in trade_daily_closed]
    if len(closes) < period + 2:
        return None
    rsi = rsi_wilder(closes, period)[-1]
    if rsi is None:
        return None
    weeks = int(inputs.get("regimeWeeks", 40))
    if reg["up"]:
        if rsi >= heat:
            return None
        leg = "trend"
        why = f"uptrend (regime close {reg['close']:.2f} > {weeks}-week average {reg['average']:.2f}), RSI {rsi:.1f} < {heat:g}"
    else:
        if rsi >= dip:
            return None
        leg = "washout"
        why = f"downtrend (regime close {reg['close']:.2f} <= {weeks}-week average {reg['average']:.2f}), RSI {rsi:.1f} < {dip:g}"
    lev = int(max(1, round(_f(inputs.get("leverage", 3), 3.0))))
    stop_roe = _f(inputs.get("stopRoePct", 30), 30.0)
    return {
        "coin": asset,
        "direction": "LONG",
        "leg": leg,
        "rsi": round(rsi, 2),
        "regime_up": bool(reg["up"]),
        "regime_close": reg["close"],
        "regime_average": round(reg["average"], 4),
        "signal_bar_t": int(_t(trade_daily_closed[-1])),
        "leverage": lev,
        "stop_price_pct": round(stop_roe / lev, 2),
        "reasons": [why, f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price"],
    }
