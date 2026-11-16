"""DIP LADDER — pure thesis math (no I/O, no MCP, no clock). The engine behind squirrel and chipmunk.

One rung of a dip ladder, judged on a coin's CLOSED daily candles:

  * the DIP: the latest daily close is at least `depthPct` percent under the highest close of the
    `highLookbackDays` completed days before it (the latest close itself is not in that window). The
    window is read by TIME — the bars that opened in the `highLookbackDays` days before the latest one —
    never by counting rows back: the runtime's numeric cast can drop a row, and a window counted by
    index would then reach a day further back than the rule says, where an older, higher close could
    turn a non-dip into a buy;
  * the TREND GATE, only when `regimeWeeks` > 0: the latest daily close is also above the simple
    average of the last `regimeWeeks` CLOSED weekly closes. It gates the BUY and nothing else.

A coin that qualifies is bought at `leverage` (1x in every shipped rung). Nothing here exits: the DSL's
stop and profit lock close every position. A rung that is flat in a coin buys it again at any later
close where the rule holds — after a stop-out in a deep fall that is a re-buy lower, by design.

Every input is a plain candle list or a number, so it unit-tests without a mock of anything.
scan.py does the reads and the state.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

DAY = 86400
WEEK = 7 * DAY


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
    """The venue-agnostic ticker: `xyz:GOLD` and `GOLD` both read GOLD, `btc` reads BTC."""
    return str(name or "").split(":", 1)[-1].upper()


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first (t is the open, in epoch ms). The feed's last
    row is the bar in progress; a level read off it moves every tick. Rows with no readable `t` or no
    positive close are dropped (the runtime's numeric cast can thin a series; nothing here assumes
    fixed spacing)."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0 or _close(c) <= 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


# ── the inputs ──

def leverage(inputs):
    """The recipe's one leverage, a whole number of at least 1."""
    return int(max(1, round(_f(inputs.get("leverage", 1), 1.0))))


def regime_weeks(inputs):
    """How many closed weekly closes the trend gate averages; 0 (or missing) turns the gate off."""
    return max(0, int(_f(inputs.get("regimeWeeks", 0), 0.0)))


# ── the levels ──

def prior_high(daily_closed, days):
    """The highest close of the `days` completed days before the latest closed bar, or None when the
    series cannot cover them: fewer than `days` + 1 closed candles, or none that opened as far back
    as the window starts (a coin listed too recently to have that history).

    The window is the bars that opened in [latest open - `days` days, latest open), so a missing row
    shortens it by a day instead of stretching it a day further back."""
    days = int(days)
    if days <= 0 or len(daily_closed or []) < days + 1:
        return None
    last_t = _t(daily_closed[-1])
    start = last_t - days * DAY * 1000.0
    if _t(daily_closed[0]) > start:
        return None
    window = [_close(c) for c in daily_closed[:-1] if start <= _t(c) < last_t]
    return max(window) if window else None


def weekly_average(weekly_closed, weeks):
    """Simple average of the last `weeks` closed weekly closes, or None with fewer than that many (a
    coin listed less than `weeks` weeks ago has no long-term average yet)."""
    weeks = int(weeks)
    closes = [_close(c) for c in weekly_closed or []]
    if weeks <= 0 or len(closes) < weeks:
        return None
    return sum(closes[-weeks:]) / weeks


def weekly_current(weekly_closed, decision_close_s):
    """True when the newest closed weekly candle closed less than a week before the decision close, so
    an average read off the weekly series is this week's and not last week's."""
    if not weekly_closed:
        return False
    return decision_close_s - (_t(weekly_closed[-1]) / 1000.0 + WEEK) < WEEK


# ── the rule ──

def measure(asset, daily_closed, weekly_closed, inputs):
    """{coin, close_px, high_px, trigger_px, below_high_pct, depth_pct, regime_average, dip, qualifies,
    signal_bar_t} for the latest closed daily bar, or None when there is too little history to judge
    it: the daily series does not cover `highLookbackDays`, or, with the trend gate on, there are fewer
    than `regimeWeeks` closed weekly candles. `regime_average` is None when the gate is off.

    The rule: close <= (1 - depthPct/100) x the prior high; with the gate on, also close > the average."""
    if not daily_closed:
        return None
    depth = _f(inputs.get("depthPct", 10), 10.0)
    if not 0 < depth < 100:
        return None
    high = prior_high(daily_closed, int(_f(inputs.get("highLookbackDays", 60), 60.0)))
    close = _close(daily_closed[-1])
    if high is None or high <= 0 or close <= 0:
        return None
    weeks = regime_weeks(inputs)
    average = None
    if weeks:
        average = weekly_average(weekly_closed, weeks)
        if average is None or average <= 0:
            return None
    trigger = (1.0 - depth / 100.0) * high
    dip = close <= trigger
    return {
        "coin": asset,
        "close_px": close,
        "high_px": high,
        "trigger_px": trigger,
        "below_high_pct": round((1.0 - close / high) * 100.0, 2),
        "depth_pct": depth,
        "regime_average": average,
        "dip": dip,
        "qualifies": dip and (average is None or close > average),
        "signal_bar_t": int(_t(daily_closed[-1])),
    }


def thesis(m, inputs):
    """The emitted thesis for a qualifying measure: direction, leverage, the stop in price, reasons."""
    lev = leverage(inputs)
    stop_roe = _f(inputs.get("stopRoePct", 35), 35.0)
    days = int(_f(inputs.get("highLookbackDays", 60), 60.0))
    reasons = [f"close {m['close_px']:.6g} is {m['below_high_pct']:.1f}% under its {days}-day high "
               f"{m['high_px']:.6g}; this rung buys from {m['depth_pct']:g}% under"]
    if m.get("regime_average") is not None:
        reasons.append(f"above its {regime_weeks(inputs)}-week average {m['regime_average']:.6g}")
    reasons.append(f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price")
    return dict(m, direction="LONG", leverage=lev, stop_price_pct=round(stop_roe / lev, 2), reasons=reasons)
