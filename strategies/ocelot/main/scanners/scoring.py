"""OCELOT — pure thesis math (no I/O, no MCP, no clock).

Tech when tech is the stronger trend, chips after a Nasdaq crash, cash otherwise. At each closed
daily bar:

  * CRASH: the Nasdaq 100's 10-day RSI (Wilder, closed daily candles) closed under the crash line ->
    buy the 3x chip fund, the deepest rebound in tech;
  * TECH LEADS: otherwise, if that RSI is under the overheat line AND above the average 10-day RSI of
    a trend basket (gold, crude oil, the euro, the dollar against the yen — the markets a
    managed-futures fund trades) -> buy the Nasdaq 100;
  * otherwise hold cash. Nothing is shorted: the short halves of the original switch lost.

Every input is a plain candle list or a number, so it unit-tests without a mock of anything.
scan.py does the reads and the state.
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


def decide(tech_rsi, basket_rsis, inputs):
    """('crash' | 'tech' | None, yardstick). The basket yardstick is the mean of its markets' RSIs and
    needs every one of them; a missing reading means no tech entry (a crash entry needs none)."""
    crash = _f(inputs.get("crashRsi", 30), 30.0)
    heat = _f(inputs.get("overheatRsi", 79), 79.0)
    if tech_rsi is None:
        return None, None
    if tech_rsi < crash:
        return "crash", None
    vals = [v for v in basket_rsis if v is not None]
    if not vals or len(vals) != len(basket_rsis):
        return None, None
    yard = sum(vals) / len(vals)
    if tech_rsi < heat and tech_rsi > yard:
        return "tech", yard
    return None, yard


def leverage_for(asset, inputs):
    """The recipe's leverage for this market (`leverageByAsset`, matched on the bare ticker), else
    `leverageDefault`; a whole number of at least 1, capped at `leverageMax`."""
    table = inputs.get("leverageByAsset") or {}
    lev = None
    if isinstance(table, dict):
        for k, v in table.items():
            if bare(k) == bare(asset):
                lev = _f(v, None)
                break
    if lev is None:
        lev = _f(inputs.get("leverageDefault", 1), 1.0)
    cap = _f(inputs.get("leverageMax", lev), lev)
    return int(max(1, min(int(round(lev)), int(cap))))
