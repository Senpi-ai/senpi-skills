"""CARACAL (BREAKOUT book) — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 caracal-producer.py volatility
compression->expansion scorer (SKILL.md "Caracal v1.0"). The technical
helpers (true range / ATR / trend structure) and the `score_vol_breakout`
scoring table — range breakout + compression precondition + expansion surge
+ higher-TF agreement — are reproduced VERBATIM so a fidelity harness can
diff this against the v2 producer on the same candle snapshot.

This is the CRYPTO book (wantXyz=false): the universe is the liquid main-DEX
crypto cross-section. The CATALYST book runs the IDENTICAL engine on the XYZ
DEX; the two scoring modules differ only by docstring/persona — the math is
shared by construction (verbatim).

Single-pass, unit-testable on plain candle lists. The caller (scan.py) owns
the clock and the MCP reads."""

# Score normalization divisor for the 0..1 ingest-ranking score (v2 NORM_DIV).
# Retained for parity with the v2 wire score; the Runtime 3.0 wire emits the
# raw integer score on data{} and the runtime ranks — kept here so a harness
# can reproduce the v2 normalized value if needed.
NORM_DIV = 8.0

# ── v2 defaults (caracal-producer.py _DEFAULTS["breakout"] / config) ──
MIN_SCORE = 5
MARGIN_PCT = 18          # PERCENT in (0,100] — v2 stored 0.18 (FRACTION); ×100 here
MAX_LEVERAGE = 5
MAX_SLOTS = 3
VOL_FLOOR_PCT_OF_MEDIAN = 0.2
UNIVERSE_MAX_NAMES = 20
BREAKOUT_BARS = 20       # prior-range lookback for the break
RECENT_BARS = 10         # ATR window — "recent" vol
BASE_BARS = 30           # ATR window — baseline vol
SQUEEZE_TIGHT = 0.70     # recent/baseline ATR <= this = tight coil
SQUEEZE_LOOSE = 0.90     # ... <= this = mild coil
SURGE_STRONG = 2.0       # breakout-bar TR / baseline ATR
SURGE_MOD = 1.3


# ── numeric coercion ──

def _f(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# Technical helpers — ported VERBATIM from caracal-producer.py
# ═══════════════════════════════════════════════════════════════

def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])      # [t, o, h, l, c, v] ohlcv array shape
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


def trend_structure(candles, lookback=6):
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


def true_range(c, prev_close):
    h, l = _high(c), _low(c)
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr(candles, period):
    """Average true range over the last `period` bars."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], _close(candles[i - 1])))
    w = trs[-period:] if len(trs) >= period else trs
    return sum(w) / len(w) if w else 0.0


# ═══════════════════════════════════════════════════════════════
# Volatility-breakout scoring — compression precedes expansion
# (ported VERBATIM from caracal-producer.py score_vol_breakout)
# ═══════════════════════════════════════════════════════════════

def score_vol_breakout(asset, c1, c4, config=None):
    """Detect a range breakout from a low-volatility coil and ride the break
    direction. `c1` = 1h candles, `c4` = 4h candles. Returns None if no
    breakout / not enough data. The caller fetches the candles; this stays
    pure so a harness can replay v2 snapshots."""
    config = config or {}
    look = int(config.get("breakoutBars", BREAKOUT_BARS))
    recent_bars = int(config.get("recentBars", RECENT_BARS))
    base_bars = int(config.get("baseBars", BASE_BARS))
    sq_tight = float(config.get("squeezeTight", SQUEEZE_TIGHT))
    sq_loose = float(config.get("squeezeLoose", SQUEEZE_LOOSE))
    surge_strong = float(config.get("surgeStrong", SURGE_STRONG))
    surge_mod = float(config.get("surgeMod", SURGE_MOD))

    if not c1 or not c4:
        return None
    need = max(look, base_bars) + 2
    if len(c1) < need or len(c4) < 6:
        return None
    highs = [_high(c) for c in c1]
    lows = [_low(c) for c in c1]
    closes = [_close(c) for c in c1]
    price = closes[-1]

    # Range breakout vs the prior `look` bars (excluding the current bar).
    prior_high = max(highs[-(look + 1):-1])
    prior_low = min(lows[-(look + 1):-1])
    broke_up = price > prior_high
    broke_dn = price < prior_low
    if not (broke_up or broke_dn):
        return None
    direction = "LONG" if broke_up else "SHORT"

    a_recent = atr(c1[-(recent_bars + 1):], recent_bars)
    a_base = atr(c1[-(base_bars + 1):], base_bars)
    squeeze = (a_recent / a_base) if a_base > 0 else 1.0
    last_tr = true_range(c1[-1], closes[-2])
    surge = (last_tr / a_base) if a_base > 0 else 0.0
    trend4, _ = trend_structure(c4)

    score = 0
    reasons = [f"price broke {'above' if broke_up else 'below'} its recent range"]
    score += 3  # the breakout trigger

    # Compression precondition — the edge. A coil scores; no coil is penalized.
    if squeeze <= sq_tight:
        score += 2
        reasons.append(f"volatility squeezed to {squeeze:.2f}x normal")
    elif squeeze <= sq_loose:
        score += 1
        reasons.append(f"volatility squeezed to {squeeze:.2f}x normal")
    else:
        score -= 1
        reasons.append(f"no squeeze, volatility {squeeze:.2f}x normal")

    # Expansion surge — the breakout bar should be an outsized move.
    if surge >= surge_strong:
        score += 2
        reasons.append(f"breakout bar {surge:.1f}x the usual range")
    elif surge >= surge_mod:
        score += 1
        reasons.append(f"breakout bar {surge:.1f}x the usual range")

    # Higher-TF agreement (a break with the 4h structure follows through more).
    if (broke_up and trend4 == "BULLISH") or (broke_dn and trend4 == "BEARISH"):
        score += 1
        reasons.append(f"4h trend {trend4.lower()} backs the break")
    elif (broke_up and trend4 == "BEARISH") or (broke_dn and trend4 == "BULLISH"):
        score -= 1
        reasons.append("4h trend runs against the break")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price,
        "squeeze": squeeze, "surge": surge, "trend4h": trend4,
    }


def clamp_leverage(desired, venue_max):
    """Clamp desired leverage to [1, venue_max]. Ported verbatim from v2
    clamp_leverage. A missing/invalid venue cap falls back to `desired`."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = int(desired)
    if venue <= 0:
        venue = int(desired)
    return max(1, min(int(desired), venue))
