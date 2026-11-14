"""YAK — pure thesis math (no I/O, no MCP, no clock).

All-weather with trend gates: every sleeve that is trending up is bought.

For each sleeve's market:
  * TREND: its latest closed daily close is above the average of its last 40 CLOSED weekly closes
    (the 200-trading-day average, read off weekly candles because the daily feed has ~90 days);
  * MOMENTUM: that close against the weekly close nearest to 13 weeks before it (about three
    months), above zero.
A market qualifies on both; every qualifier is bought (one slot per sleeve), strongest momentum
first. A market without 40 closed weekly candles cannot be measured yet and does not qualify.

Leverage is set per market so one ROE ladder means about the same in each market's own volatility.
Every input is a plain candle list or a number, so it unit-tests without a mock of anything.
scan.py does the reads and the state.
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


def rank(measures, open_slots):
    """The qualifying markets, strongest momentum first, at most `open_slots` of them."""
    q = [m for m in measures if m and m["qualifies"]]
    q.sort(key=lambda m: m["momentum"], reverse=True)
    return q[: max(0, int(open_slots))]


def thesis(m, inputs):
    """The emitted thesis for a ranked measure."""
    lev = leverage_for(m["coin"], inputs)
    stop_roe = _f(inputs.get("stopRoePct", 30), 30.0)
    weeks = int(inputs.get("trendWeeks", 40))
    mweeks = int(inputs.get("momentumWeeks", 13))
    return dict(m, direction="LONG", leverage=lev, stop_price_pct=round(stop_roe / lev, 2), reasons=[
        f"close {m['close_px']:.4g} above its {weeks}-week average {m['average']:.4g}",
        f"{mweeks}-week momentum {m['momentum'] * 100:+.1f}% — the strongest that qualifies",
        f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price",
    ])
