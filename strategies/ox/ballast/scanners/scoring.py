"""OX — pure risk-parity / all-weather math (no I/O, no MCP, no clock).

Shared VERBATIM by both instances (core + ballast); the book is parametrized via `inputs`
(the sleeve basket, budget, scaler). Ported faithfully from the v2 Ox producer
(ox-producer.py) — the inverse-volatility weighting that IS the product is reproduced
exactly. v2 lines reproduced verbatim are marked `# v2-quirk`. Unit-testable on plain
candle lists.

The distinctive mechanic: each sleeve's margin is proportional to 1/realized_vol,
normalized across the WHOLE basket — w_i = (1/vol_i) / sum_j(1/vol_j) — so a low-vol sleeve
(gold, indices) carries MORE notional than a high-vol one (a crypto alt) and no single asset
class dominates portfolio risk. True risk parity.
"""


# ── candle accessors — dict-form OHLC (verified live: market_get_asset_data returns
#    candles like {"o","c","h","l","v"} with STRING values). Ported verbatim from v2. ──

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def trend_structure(candles, lookback=6):
    # v2-quirk: verbatim from ox-producer.py — higher-lows => BULLISH, lower-highs => BEARISH,
    # 60% threshold; (strength = fraction of confirming bars).
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def realized_vol(closes, n):
    """Per-bar realized volatility = stdev of pct returns over the last n bars.
    Relative magnitude is what matters for inverse-vol weighting."""
    # v2-quirk: verbatim from ox-producer.py realized_vol().
    window = closes[-(n + 1):] if len(closes) >= n + 1 else closes
    rets = [(window[i] / window[i - 1] - 1.0)
            for i in range(1, len(window)) if window[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def inverse_vol_weights(vols):
    """Risk-parity weights: w_i = (1/vol_i) / sum_j(1/vol_j). All-equal fallback if every
    vol is zero/degenerate. `vols` is {asset: realized_vol}."""
    # v2-quirk: verbatim from ox-producer.py inverse_vol_weights() — THE product mechanic.
    inv = {a: (1.0 / v) for a, v in vols.items() if v and v > 0}
    tot = sum(inv.values())
    if tot <= 0:
        n = len(vols)
        return {a: (1.0 / n) for a in vols} if n else {}
    return {a: inv[a] / tot for a in inv}


def clamp_leverage(desired, venue_max):
    """Clamp desired leverage to the asset's Hyperliquid venue max. Ported from v2
    clamp_leverage() (here venue_max is passed in directly, not via meta dict)."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def score_sleeve(trend4, strength4, min_score):
    """v2-quirk: verbatim score rule from ox-producer.py PASS 2 —
        score = 6 + (1 if 4h trend is BULLISH else 0)
    plus the producer floor check. Returns (score, ok) where ok=False means the sleeve
    failed the producer minScore floor."""
    score = 6 + (1 if trend4 == "BULLISH" else 0)
    return score, (score >= min_score)
