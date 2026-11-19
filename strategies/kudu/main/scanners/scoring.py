"""KUDU — pure thesis math (no I/O, no MCP, no clock).

Hold a basket of large quality companies, timed by the Nasdaq 100's 10-day RSI (Wilder, closed daily
candles):

  * BASKET: any basket name not held is bought at the daily close unless the Nasdaq has run hot
    (RSI at or over the overheat line) — so after a trailing stop closes a name, it is bought back at
    the next close that is not overheated;
  * WASHOUT: when the Nasdaq's RSI closes under the washout line, the Nasdaq 100 itself is bought
    with more leverage, on top of the basket.

The DSL owns every exit; nothing here sells. Every input is a plain list or a number, so it
unit-tests without a mock of anything. scan.py does the reads and the state.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


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
    """Only the candles that have CLOSED, oldest first (t is the open, in epoch ms). Rows with no
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


def rsi_wilder(closes, period):
    """Wilder's RSI series aligned to `closes`; None until `period` changes have been seen.
    Seeded with the simple mean of the first `period` gains and losses, then smoothed with weight
    (period-1)/period. A flat window (no losses) reads 100."""
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


def last_rsi(daily_closed, period):
    """The RSI at the latest closed daily bar, or None with too little history."""
    closes = [_close(c) for c in daily_closed]
    if len(closes) < int(period) + 2:
        return None
    return rsi_wilder(closes, period)[-1]


def plan(nasdaq_rsi, basket, washout_asset, held, open_slots, inputs):
    """Ordered list of (asset, leg) to buy at this close. The washout leg comes first (it is the rarer
    and larger trade), then basket names in recipe order, never a held name, at most `open_slots`."""
    heat = _f(inputs.get("overheatRsi", 79), 79.0)
    wash = _f(inputs.get("washoutRsi", 30), 30.0)
    held = {bare(h) for h in held}
    out = []
    if nasdaq_rsi is None:
        return out
    if nasdaq_rsi < wash and bare(washout_asset) not in held:
        out.append((washout_asset, "washout"))
    if nasdaq_rsi < heat:
        out += [(a, "basket") for a in basket if bare(a) not in held]
    return out[: max(0, int(open_slots))]
