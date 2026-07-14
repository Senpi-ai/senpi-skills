"""SALMON — pure RSI mean-reversion math (no I/O, no MCP, no clock).

Dip-buyer / overbought-fader. Goes LONG when RSI(14) has dipped BELOW an oversold
line and then CROSSES BACK UP through it while rising, with the last close
confirming the turn; SHORT on the symmetric overbought case. It is deliberately
NOT a falling-knife catcher: a still-falling RSI below the line does NOT trigger —
the signal requires the cross back + a rising RSI + a price-reversal bar. It bets
price reverts to its mean, which works in chop as well as trends.

The indicator primitives (`_close`/`_high`/`_low`/`_vol`, `calc_rsi`,
`price_momentum`) are ported VERBATIM from bison/scoring.py (a validated Runtime
3.0 scorer) so the RSI math is byte-faithful. `rsi_series` reuses `calc_rsi` at
each bar to expose the trailing-window RSI history the CROSS detector needs.
`band_for`/`sizing_for` clone raven's conviction bands, clamped to salmon's tighter
caps (leverage <= 4, margin <= 20%) — reversions fail fast, so moderate size.

DSL owns every exit; this module only shapes ENTRY. Pure and unit-testable on
plain candle lists — no network, no state, no clock.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# Ported VERBATIM from bison/scoring.py.

def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


def _high(c):
    if isinstance(c, dict):
        return _f(c.get("high", c.get("h", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[2])
    return 0.0


def _low(c):
    if isinstance(c, dict):
        return _f(c.get("low", c.get("l", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[3])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported VERBATIM from bison/scoring.py) ──

def price_momentum(candles, n_bars=1):
    """% change over the last n_bars. Verbatim from bison price_momentum."""
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def calc_rsi(closes, period=14):
    """Trailing-window RSI. Verbatim from bison (uses gains[-period:])."""
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ── RSI series (reuses calc_rsi at each bar to expose the trailing-window history) ──

def rsi_series(closes, period=14):
    """RSI at each bar, computed on the trailing window ending at that bar. Reuses
    `calc_rsi` VERBATIM, so bars without enough history (< period+1 preceding
    closes) return its neutral 50. The returned list is index-aligned to `closes`
    so the caller can slice the last N bars to detect a CROSS back through a level.
    """
    return [calc_rsi(closes[: i + 1], period) for i in range(len(closes))]


# ── the mean-reversion cross detector (the centerpiece) — pure ──

def _reversion_score(depth, turn, pm):
    """Discrete conviction score (+ reason tags) for a confirmed reversion cross.
    Higher when the RSI went DEEPER past the line (`depth` = how far past the
    oversold/overbought level the extreme reached), turned back HARDER (`turn` =
    RSI reversal magnitude between the last two bars), and the price bar confirms
    more strongly (|pm| = last-bar % move). Range 1..8."""
    score, reasons = 0, []
    if depth >= 18:
        score += 4; reasons.append(f"depth_{depth:.0f}")
    elif depth >= 12:
        score += 3; reasons.append(f"depth_{depth:.0f}")
    elif depth >= 6:
        score += 2; reasons.append(f"depth_{depth:.0f}")
    else:
        score += 1; reasons.append(f"depth_{depth:.0f}")
    if turn >= 10:
        score += 2; reasons.append(f"turn_{turn:.0f}")
    elif turn >= 4:
        score += 1; reasons.append(f"turn_{turn:.0f}")
    apm = abs(pm)
    if apm >= 1.0:
        score += 2; reasons.append(f"px_{pm:+.2f}%")
    elif apm >= 0.3:
        score += 1; reasons.append(f"px_{pm:+.2f}%")
    return score, reasons


def oversold_bounce(candles, inputs):
    """Detect an RSI mean-reversion CROSS on a 1h candle list. Returns a signal dict
    {direction, score, reasons, rsi} or None.

    LONG iff — over the last `crossLookback` bars — RSI dipped BELOW `oversoldLevel`
    (default 30) AND the CURRENT RSI is back at/above it AND rising (rsi[-1] > rsi[-2])
    AND the last close > the prior close (price-reversal confirmation). SHORT is the
    symmetric case around `overboughtLevel` (default 70). None if neither.

    This is the anti-falling-knife gate: a still-FALLING RSI below the oversold line
    does NOT trigger — it needs the cross back up + the rising RSI + the up bar.
    Score scales with how deep the extreme went and the strength of the confirmation."""
    period = int(_f(inputs.get("rsiPeriod"), 14))
    oversold = _f(inputs.get("oversoldLevel"), 30)
    overbought = _f(inputs.get("overboughtLevel"), 70)
    cross_lb = int(_f(inputs.get("crossLookback"), 5))

    closes = [_close(c) for c in candles]
    if len(closes) < period + cross_lb + 1:          # need real RSI across the lookback
        return None
    rsi = rsi_series(closes, period)
    cur, prev = rsi[-1], rsi[-2]
    window = rsi[-(cross_lb + 1):-1]                  # the `cross_lb` bars BEFORE current
    if not window:
        return None
    last_close, prior_close = _close(candles[-1]), _close(candles[-2])

    # LONG — dipped below oversold, now crossed back up, RSI rising, price confirms
    min_recent = min(window)
    if min_recent < oversold and cur >= oversold and cur > prev and last_close > prior_close:
        depth, turn, pm = oversold - min_recent, cur - prev, price_momentum(candles, 1)
        score, comp = _reversion_score(depth, turn, pm)
        return {"direction": "LONG", "score": score, "rsi": round(cur, 1),
                "reasons": [f"rsi_cross_up_{prev:.0f}->{cur:.0f}", f"min_rsi_{min_recent:.0f}"] + comp}

    # SHORT — popped above overbought, now crossed back down, RSI falling, price confirms
    max_recent = max(window)
    if max_recent > overbought and cur <= overbought and cur < prev and last_close < prior_close:
        depth, turn, pm = max_recent - overbought, prev - cur, price_momentum(candles, 1)
        score, comp = _reversion_score(depth, turn, pm)
        return {"direction": "SHORT", "score": score, "rsi": round(cur, 1),
                "reasons": [f"rsi_cross_down_{prev:.0f}->{cur:.0f}", f"max_rsi_{max_recent:.0f}"] + comp}

    return None


# ── conviction band + sizing (cloned from raven; salmon's tighter caps) ──

def band_for(score, inputs):
    """Conviction band from the reversion score."""
    apex = _f(inputs.get("apexScore"), 7)
    good = _f(inputs.get("goodScore"), 5)
    if score >= apex:
        return "apex"
    if score >= good:
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100]; clamped to salmon's
    caps: leverage <= maxLeverage (default 4) and <= the venue max; margin <=
    maxMarginPct (default 20). Reversions fail fast — moderate leverage by design."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 4, "good": 3, "base": 2}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 12, "good": 9, "base": 6}
    cap = int(_f(inputs.get("maxLeverage"), 4))
    lev = int(_f(lev_tiers.get(band), 2))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(lev, cap))
    mgn = _f(mgn_tiers.get(band), 6)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 20)))
    return lev, round(mgn, 2)
