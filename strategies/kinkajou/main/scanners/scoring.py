"""KINKAJOU — pure thesis math (no I/O, no MCP, no clock).

Three conditions, read off CLOSED candles at each daily close:

  * WASHOUT: the Nasdaq 100's 10-day RSI (Wilder) closed under the washout line -> buy the Nasdaq 100
    with leverage, for the rebound;
  * HEALTHY: the Nasdaq 100 is above the average of its last 40 weekly closes and its RSI is under the
    overheat line -> buy the strongest of the single big-tech names: the one with the largest 13-week
    rise among those above their own 40-week average;
  * otherwise (the Nasdaq is overheated, or under its long-term trend without a washout) -> cash.

Nothing is shorted. Every input is a plain candle list or a number, so it unit-tests without a mock
of anything. scan.py does the reads and the state.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

WEEK = 7 * 86400
DAY = 86400


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
    """The venue-agnostic ticker: `xyz:GOLD` and `GOLD` both read GOLD."""
    return str(name or "").split(":", 1)[-1].upper()


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first (t is the open, in epoch ms). The feed's last
    row is the bar in progress. Rows with no readable `t` or no positive close are dropped."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0 or _close(c) <= 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


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


def measure(asset, daily_closed, weekly_closed, inputs):
    """{coin, close_px, average, momentum, trend_up, qualifies, signal_bar_t} or None when the market
    cannot be measured (no closed daily bar, or fewer than `trendWeeks` / `momentumWeeks` weekly
    closes)."""
    weeks = int(inputs.get("trendWeeks", 40))
    mweeks = int(inputs.get("momentumWeeks", 13))
    if not daily_closed or weeks <= 0 or mweeks <= 0:
        return None
    wc = [_close(c) for c in weekly_closed]
    if len(wc) < max(weeks, mweeks):
        return None
    avg = sum(wc[-weeks:]) / weeks
    ref = close_weeks_back(weekly_closed, _t(daily_closed[-1]) / 1000.0 + DAY, mweeks)
    close = _close(daily_closed[-1])
    if ref is None or avg <= 0 or ref <= 0 or close <= 0:
        return None
    mom = close / ref - 1.0
    up = close > avg
    return {"coin": asset, "close_px": close, "average": avg, "momentum": mom, "trend_up": up,
            "qualifies": up and mom > 0, "signal_bar_t": int(_t(daily_closed[-1]))}


def close_weeks_back(weekly_closed, decision_close_s, weeks):
    """The close of the weekly candle that closed nearest to `weeks` weeks before the decision close
    (within half a week), or None. Counting closed weekly rows back from the newest is off by one: on
    the day a week closes, the newest row IS that close, so `[-13]` reaches back only 12 weeks."""
    target = decision_close_s - int(weeks) * WEEK
    best = None
    for c in weekly_closed or []:
        closed_at = _t(c) / 1000.0 + WEEK
        gap = abs(closed_at - target)
        if gap <= WEEK / 2 and (best is None or gap < best[0]):
            best = (gap, _close(c))
    return best[1] if best else None


def weekly_current(weekly_closed, decision_close_s):
    """True when the newest closed weekly candle closed within one week of the decision close, so a
    40-week average read off it is this week's and not last week's."""
    if not weekly_closed:
        return False
    return decision_close_s - (_t(weekly_closed[-1]) / 1000.0 + WEEK) < WEEK


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


def nasdaq_state(daily_closed, weekly_closed, inputs):
    """'washout' | 'healthy' | 'risk' | None (unmeasurable), with the RSI and the 40-week average."""
    rsi = last_rsi(daily_closed, int(inputs.get("rsiPeriod", 10)))
    weeks = int(inputs.get("trendWeeks", 40))
    wc = [_close(c) for c in weekly_closed]
    if rsi is None or not daily_closed:
        return None, rsi, None
    if rsi < _f(inputs.get("washoutRsi", 30), 30.0):
        return "washout", rsi, None
    if len(wc) < weeks:
        return None, rsi, None
    avg = sum(wc[-weeks:]) / weeks
    if _close(daily_closed[-1]) > avg and rsi < _f(inputs.get("overheatRsi", 79), 79.0):
        return "healthy", rsi, avg
    return "risk", rsi, avg


def strongest(measures):
    """The qualifying single name with the largest momentum, or None."""
    q = [m for m in measures if m and m["qualifies"]]
    return max(q, key=lambda m: m["momentum"]) if q else None
