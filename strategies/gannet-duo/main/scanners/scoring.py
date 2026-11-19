"""GANNET — pure thesis math (no I/O, no MCP, no clock).

A dip cascade on CLOSED daily candles. Each market's 10-day RSI (Wilder) is read off its own
daily closes; walking the markets in the order the recipe lists them, the first one whose RSI has
fallen under the oversold line is the buy. Nothing is scored into a black box: every entry can be
checked on any daily chart with an RSI(10) under it.

The order is the thesis. The most volatile market is asked first because its hard drops are the
deepest and its rebounds the largest; the broad index is the fallback when only it is washed out.

Leverage is set per market so that one ROE ladder means the same thing on each: a market that is
itself a 3x fund (xyz:SOXL) is held at 1x, an index at 3x. The DSL's distances are ROE, and in
price they are ROE / leverage, so the 3x fund at 1x and the index at 3x carry the same exits
relative to their own swings.

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


def _t(c):
    return _f(c.get("t"), -1.0) if isinstance(c, dict) else -1.0


def bare(name):
    """The venue-agnostic ticker: `xyz:SOXL` and `SOXL` both read SOXL. The clearinghouse can report
    a HIP-3 position under either spelling, and a held name that fails to match is a double open."""
    return str(name or "").split(":", 1)[-1].upper()


def closed_candles(candles, candle_seconds, now):
    """Only the candles that have CLOSED, oldest first.

    `t` is the candle OPEN in epoch milliseconds, so a candle has closed once
    t/1000 + candle_seconds <= now. The feed's last row is the day in progress: an RSI read off it
    moves every tick, so the strategy would buy a dip no daily chart ever printed. Rows with no
    readable `t` or no positive close are dropped (the runtime's numeric cast can thin a series;
    nothing here assumes fixed spacing)."""
    out = []
    for c in candles or []:
        t = _t(c)
        if t < 0 or _close(c) <= 0:
            continue
        if t / 1000.0 + candle_seconds <= now:
            out.append(c)
    out.sort(key=_t)
    return out


# ── the indicator ──

def rsi_wilder(closes, period):
    """Wilder's RSI series aligned to `closes`; None until `period` changes have been seen.

    Seeded with the simple mean of the first `period` gains and losses, then smoothed with
    weight (period-1)/period. The daily feed carries about 90 closed days, so after the seed the
    smoothing has run ~80 steps and the seed's weight is (9/10)^80, under 0.03%. A flat window
    (no losses) reads 100."""
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


# ── sizing ──

def leverage_for(asset, inputs):
    """The leverage the recipe gives this market (`leverageByAsset`, matched on the bare ticker),
    else `leverageDefault`; a whole number of at least 1, capped at `leverageMax`."""
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


# ── the thesis ──

def build_thesis(asset, daily_closed, inputs):
    """{coin, direction, rsi, rsi_prev, close, signal_bar_t, leverage, stop_price_pct, reasons}
    when the latest CLOSED daily bar's RSI is under the oversold line, else None.

    `daily_closed` is the closed daily candles, oldest first (scan.py drops the bar in progress).
    The rule is a LEVEL, not a cross: any close under the line qualifies, so a market that stays
    washed out is bought again at the next close after its previous position ended. scan.py lets
    one (market, bar) pair through once."""
    period = int(inputs.get("rsiPeriod", 10))
    line = _f(inputs.get("oversoldRsi", 30), 30.0)
    closes = [_close(c) for c in daily_closed]
    if len(closes) < period + 2:
        return None
    series = rsi_wilder(closes, period)
    rsi, rsi_prev = series[-1], series[-2]
    if rsi is None or rsi >= line:
        return None
    lev = leverage_for(asset, inputs)
    stop_roe = _f(inputs.get("stopRoePct", 21), 21.0)
    last = daily_closed[-1]
    return {
        "coin": asset,
        "direction": "LONG",
        "rsi": round(rsi, 2),
        "rsi_prev": round(rsi_prev, 2) if rsi_prev is not None else None,
        "close_px": closes[-1],
        "signal_bar_t": int(_t(last)),
        "leverage": lev,
        "stop_price_pct": round(stop_roe / lev, 2),
        "reasons": [
            f"daily RSI({period}) {rsi:.1f} < {line:g} at the close",
            f"{lev}x: the {stop_roe:g}% ROE stop is {stop_roe / lev:.1f}% of price",
        ],
    }


def cascade(candidates, open_slots):
    """The first `open_slots` qualifying markets in recipe order. `candidates` is the ordered list
    of (key, thesis) pairs that qualified this tick; the order of the recipe's `allowedAssets` is
    preserved, so with one slot the cascade buys the first market that is washed out."""
    return list(candidates)[: max(0, int(open_slots))]
