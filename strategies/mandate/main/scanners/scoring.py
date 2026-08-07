"""MANDATE — pure capital-preservation portfolio math (no I/O, no MCP, no clock).

A diversified, LONG-ONLY, NO-LEVERAGE basket across curated classes (crypto blue chips,
equity indices + blue-chip names, metals, energy). The mandate: preserve capital first,
enter only high-quality multi-timeframe trends, and only when the expected move clears the
round-trip cost (fee-aware) — 'no trade is better than a poor trade'. Hard per-position
caps, no leverage. scan.py owns the reads/state; this module is the numbers."""


def _f(x, *keys, default=0.0):
    if keys:
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _close(c):
    return _f(c, "close", "c")


def _high(c):
    return _f(c, "high", "h")


def _low(c):
    return _f(c, "low", "l")


def trend_structure(candles, lookback=6):
    """Higher-lows => BULLISH, lower-highs => BEARISH, 60% confirm threshold (strength =
    fraction of confirming bars). Same structure read Ox uses."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if total <= 0:
        return "NEUTRAL", 0.0
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def atr_pct(candles, n=14):
    """Average true range over n bars as a PERCENT of the last close — the expected-move
    proxy the fee hurdle is measured against."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = _high(candles[i]), _low(candles[i]), _close(candles[i - 1])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    win = trs[-n:] if len(trs) >= n else trs
    atr = sum(win) / len(win) if win else 0.0
    last = _close(candles[-1])
    return (atr / last * 100.0) if last > 0 else 0.0


def quality(c4, c1d, inputs):
    """LONG-only quality gate: 4h structure BULLISH with strength above the floor, and the
    daily NOT breaking down (BULLISH or NEUTRAL) — multi-timeframe evidence. Returns
    (ok, score, strength, reasons). 'No trade is better than a poor trade.'"""
    t4, s4 = trend_structure(c4, int(inputs.get("trendLookback", 6)))
    t1d, s1d = trend_structure(c1d, int(inputs.get("dailyLookback", 6)))
    min_strength = float(inputs.get("minTrendStrength", 0.6))
    ok = (t4 == "BULLISH" and s4 >= min_strength and t1d in ("BULLISH", "NEUTRAL"))
    score = 5 + (1 if t4 == "BULLISH" else 0) + (1 if t1d == "BULLISH" else 0) + (1 if s4 >= 0.8 else 0)
    return ok, score, s4, [f"4h {t4} ({s4:.0%})", f"1d {t1d} ({s1d:.0%})"]


def passes_fee_hurdle(edge_pct, inputs):
    """The mandate's cost-awareness: the expected move (4h ATR%) must clear the round-trip
    cost (taker fees + slippage) times a buffer, so we never churn a tiny edge into fees."""
    rt_cost = float(inputs.get("roundTripCostPct", 0.2))    # ~2x taker (~5bps) + slippage
    buffer = float(inputs.get("costBufferMult", 6.0))       # require the move to be >= 6x the cost
    return edge_pct >= rt_cost * buffer


def alloc_pct(score, inputs):
    """Position size as PERCENT of equity, conviction-scaled within [minAlloc, maxAlloc] and
    HARD-CAPPED at the mandate ceiling (default 10%). No leverage — this is notional."""
    lo = float(inputs.get("minAllocationPct", 3.0))
    base = float(inputs.get("baseAllocationPct", 5.0))
    cap = float(inputs.get("maxAllocationPct", 10.0))
    good = float(inputs.get("goodScore", 7.0))
    scaled = base + (cap - base) * max(0.0, min(1.0, (score - good) / 2.0))
    return round(min(max(scaled, lo), cap), 4)


def class_of(bare, classes):
    """Map a bare-uppercase coin to its declared asset class (for diversification caps)."""
    for cls, coins in (classes or {}).items():
        for c in coins:
            if str(c).split(":", 1)[-1].upper() == bare:
                return cls
    return "?"
