"""VIPER — pure market-structure math (no I/O, no MCP, no clock).

Smart-Money-Concepts / ICT + Wyckoff STRUCTURE engine, entirely unit-testable on
plain candle lists. Nothing here reaches the network; scan.py does all the MCP
orchestration and passes candle lists in.

The pipeline maps the tape the way an SMC/ICT trader reads it:

  1. swings()          — confirmed swing-high / swing-low pivots (fractal: a high
                         that exceeds `left` bars before AND `right` bars after).
  2. structure()       — Break of Structure (BOS, trend continuation) vs Change of
                         Character (CHoCH, the first close through the OPPOSING swing
                         = a reversal). Direction is set by which side breaks.
  3. liquidity_sweep() — a wick that runs stops PAST a prior swing then closes back
                         inside (above a swing high → bearish; below a swing low →
                         bullish). The classic stop-hunt / spring.
  4. fvg()             — a 3-candle Fair Value Gap (imbalance): candle[i-1].high <
                         candle[i+1].low is a bullish gap; candle[i-1].low >
                         candle[i+1].high is a bearish gap.
  5. score_structure() — direction comes from the 1h structure break; the score
                         stacks confluence (sweep aligned, FVG aligned, 4h structure
                         agreeing) and is gated to minScore by the CALLER (scan.py).

band_for / sizing_for are cloned from raven (conviction tiers, clamped to the fleet
+ venue leverage caps). VIPER never closes — the DSL owns every exit.

NOTE: the market_get_asset_data candle payload shape was NOT live-verified when this
was written (auth token was invalid). The candle accessors below are dual-shape
(dict {high|h,…} OR list [t,o,h,l,c,v]), ported verbatim from bison, so a real payload
in either shape reads correctly; scan.py extracts candles tolerantly.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None (distinguishes a real 0.0 from a missing field)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# Ported VERBATIM from bison/scoring.py — do not diverge (a fidelity harness
# diffs viper's structure reads against bison's on the same candles).

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


# ── 1. swings — confirmed fractal pivots ──────────────────────────────────────

def swings(candles, left=2, right=2):
    """Confirmed swing pivots. A pivot HIGH at i has _high(i) strictly greater than
    the `left` bars before AND the `right` bars after; a pivot LOW is the mirror on
    _low. The last `right` bars can never be pivots (unconfirmed), so every returned
    pivot is 'prior' to the forming edge. Returns a list of
    {"idx","price","kind":"high"|"low"} in index order (never raises)."""
    out = []
    n = len(candles)
    left = max(1, int(left))
    right = max(1, int(right))
    if n < left + right + 1:
        return out
    for i in range(left, n - right):
        hi = _high(candles[i])
        lo = _low(candles[i])
        is_high = (all(hi > _high(candles[j]) for j in range(i - left, i)) and
                   all(hi > _high(candles[j]) for j in range(i + 1, i + right + 1)))
        is_low = (all(lo < _low(candles[j]) for j in range(i - left, i)) and
                  all(lo < _low(candles[j]) for j in range(i + 1, i + right + 1)))
        if is_high:
            out.append({"idx": i, "price": hi, "kind": "high"})
        if is_low:
            out.append({"idx": i, "price": lo, "kind": "low"})
    return out


def _highs(pivots):
    return [p for p in pivots if p["kind"] == "high"]


def _lows(pivots):
    return [p for p in pivots if p["kind"] == "low"]


def _bias(highs, lows):
    """Structural bias from the last two swing highs + lows.
    Higher-high AND higher-low → BULLISH; lower-high AND lower-low → BEARISH;
    anything mixed / too few pivots → NEUTRAL."""
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1]["price"] > highs[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        hl = lows[-1]["price"] > lows[-2]["price"]
        ll = lows[-1]["price"] < lows[-2]["price"]
        if hh and hl:
            return "BULLISH"
        if lh and ll:
            return "BEARISH"
    return "NEUTRAL"


# ── 2. structure — BOS vs CHoCH ───────────────────────────────────────────────

def structure(candles, left=2, right=2):
    """(direction, kind). direction ∈ {"LONG","SHORT",None}; kind ∈ {"BOS","CHoCH","NONE"}.

    Reads the last close against the MOST-RECENT confirmed swing high / low:
      • close > most-recent swing high → bullish break (LONG)
      • close < most-recent swing low  → bearish break (SHORT)
    A break that CONTINUES the standing bias is a BOS; the first close through the
    OPPOSING swing (breaking up while the bias was bearish, or down while bullish)
    is a CHoCH — the reversal tell. Neutral bias + a break = BOS (a fresh breakout).
    Returns (None,"NONE") when there is no swing high/low to reference."""
    piv = swings(candles, left, right)
    highs, lows = _highs(piv), _lows(piv)
    if not candles or not highs or not lows:
        return (None, "NONE")
    sh = highs[-1]["price"]
    sl = lows[-1]["price"]
    c = _close(candles[-1])
    bias = _bias(highs, lows)
    if c > sh:
        return ("LONG", "CHoCH" if bias == "BEARISH" else "BOS")
    if c < sl:
        return ("SHORT", "CHoCH" if bias == "BULLISH" else "BOS")
    return (None, "NONE")


# ── 3. liquidity sweep — stop-hunt wick that closes back inside ────────────────

def liquidity_sweep(candles, left=2, right=2):
    """(swept, dir). The last bar runs liquidity beyond a PRIOR swing then closes
    back inside:
      • wick ABOVE the most-recent swing high but close back BELOW it → bearish (SHORT)
      • wick BELOW the most-recent swing low  but close back ABOVE it → bullish (LONG)
    Returns (False, None) when neither fires (never raises)."""
    if not candles:
        return (False, None)
    piv = swings(candles, left, right)
    highs, lows = _highs(piv), _lows(piv)
    last = candles[-1]
    h, l, c = _high(last), _low(last), _close(last)
    if highs:
        sh = highs[-1]["price"]
        if h > sh and c < sh:
            return (True, "SHORT")
    if lows:
        sl = lows[-1]["price"]
        if l < sl and c > sl:
            return (True, "LONG")
    return (False, None)


# ── 4. fair value gap — 3-candle imbalance in the last few bars ────────────────

def fvg(candles, lookback=5):
    """(present, dir). Scans the last `lookback` 3-candle windows, newest-first, for
    an imbalance around the middle bar i:
      • bullish gap: high(i-1) < low(i+1)   → LONG
      • bearish gap: low(i-1)  > high(i+1)  → SHORT
    Returns the most-recent gap found, else (False, None) (never raises)."""
    n = len(candles)
    if n < 3:
        return (False, None)
    lo_i = max(1, n - 1 - int(max(1, lookback)))
    for i in range(n - 2, lo_i - 1, -1):        # middle index, newest window first
        if i < 1 or i + 1 > n - 1:
            continue
        if _high(candles[i - 1]) < _low(candles[i + 1]):
            return (True, "LONG")
        if _low(candles[i - 1]) > _high(candles[i + 1]):
            return (True, "SHORT")
    return (False, None)


# ── 5. score — direction from 1h structure, confluence stacked on top ──────────

def score_structure(candles_1h, candles_4h, inputs):
    """Compose the structure read into {direction, score, reasons, structure} or None.

    Direction is set by the 1h structure break (None ⇒ no trade this tick, return None).
    Score stacks confluence (minScore is applied by the CALLER, scan.py):
        BOS +3 / CHoCH +2
        liquidity sweep aligned with the break  +2
        Fair Value Gap aligned with the break    +1
        4h structure agrees +2  /  opposes −2
    """
    left = int(_f((inputs or {}).get("swingLeft"), 2))
    right = int(_f((inputs or {}).get("swingRight"), 2))

    d1, kind = structure(candles_1h, left, right)
    if d1 is None:
        return None

    reasons = []
    score = 3 if kind == "BOS" else 2
    reasons.append("1h_%s_%s" % (kind.lower(), d1.lower()))

    swept, sdir = liquidity_sweep(candles_1h, left, right)
    if swept and sdir == d1:
        score += 2
        reasons.append("sweep_aligned_%s" % sdir.lower())
    elif swept and sdir is not None:
        reasons.append("sweep_opposing_%s" % sdir.lower())

    present, fdir = fvg(candles_1h)
    if present and fdir == d1:
        score += 1
        reasons.append("fvg_aligned_%s" % fdir.lower())
    elif present and fdir is not None:
        reasons.append("fvg_opposing_%s" % fdir.lower())

    d4, kind4 = structure(candles_4h, left, right)
    if d4 == d1:
        score += 2
        reasons.append("4h_agrees_%s" % kind4.lower())
    elif d4 is not None:
        score -= 2
        reasons.append("4h_opposes_%s" % d4.lower())

    return {"direction": d1, "score": score, "reasons": reasons, "structure": kind}


# ── conviction band + sizing (cloned from raven — tiers, venue-clamped) ────────

def band_for(score, inputs):
    """Conviction band from the composite score."""
    apex = _f((inputs or {}).get("apexScore"), 9)
    good = _f((inputs or {}).get("goodScore"), 7)
    if score >= apex:
        return "apex"
    if score >= good:
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct). marginPct is a PERCENT in (0,100]; leverage is clamped
    to the fleet cap (maxLeverage) AND the venue's max_leverage, floored at 1x."""
    inputs = inputs or {}
    lev_tiers = inputs.get("leverageTiers") or {"apex": 5, "good": 4, "base": 3}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 14, "good": 10, "base": 7}
    cap = int(_f(inputs.get("maxLeverage"), 5))
    lev = int(_f(lev_tiers.get(band), 3))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(lev, cap))
    mgn = _f(mgn_tiers.get(band), 7)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 25)))
    return lev, round(mgn, 2)
