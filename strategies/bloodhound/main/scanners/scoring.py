"""BLOODHOUND — pure thesis math: chart-pattern recognition (no I/O, no MCP, no clock).

Finds the patterns retail traders actually draw, on whatever the market is currently showing:

  DOUBLE BOTTOM (W)  two swing lows at a similar level with a peak between them. LONG once
                     price closes back above that peak (the neckline). The second low holding
                     where the first one did is the evidence that sellers are done.
  DOUBLE TOP (M)     the mirror. SHORT on a close below the intervening trough.
  HIGHER HIGH / LOW  an intact uptrend structure — each swing high above the last AND each
                     swing low above the last. LONG continuation.
  LOWER LOW / HIGH   the mirror. SHORT continuation.

Everything is built on SWING PIVOTS found with a symmetric fractal window: a bar is a swing
high if its high is the maximum of the `k` bars either side of it. A pivot therefore needs `k`
bars of hindsight to exist, which is exactly why patterns are only ever confirmed late — the
alternative is inventing pivots out of noise.

Pure + single-pass + unit-testable on plain candle lists. Candles are keyed o/h/l/c/v with
STRING values — `_f`/`_close` coerce both shapes.
"""


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


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
        return _f(c.get("volume", c.get("v", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── swing pivots ─────────────────────────────────────────────────────────────

def _push(pivots, idx, price, k, keep_max):
    """Append a pivot, COLLAPSING a plateau into one point.

    A flat top (two or more bars at the same extreme) satisfies the `>=` fractal test on EVERY
    bar of the run, so a naive append records the same pivot twice. The last two entries are
    then duplicates of each other, every higher-high / double-bottom comparison sees a 0% step,
    and the strategy silently never trades. Collapse anything within `k` bars of the previous
    pivot into the more extreme of the two.
    """
    if pivots and idx - pivots[-1][0] <= k:
        if (keep_max and price >= pivots[-1][1]) or (not keep_max and price <= pivots[-1][1]):
            pivots[-1] = (idx, price)
        return
    pivots.append((idx, price))


def swing_points(candles, k=2):
    """([(idx, price)] highs, [(idx, price)] lows) using a symmetric k-bar fractal window.

    A pivot is only recognised once `k` bars have printed after it — patterns are confirmed
    late by construction, which is the honest behaviour. Bars within `k` of either end can
    never qualify.
    """
    highs, lows = [], []
    n = len(candles)
    if n < 2 * k + 1 or k < 1:
        return highs, lows
    for i in range(k, n - k):
        h = _high(candles[i])
        lo = _low(candles[i])
        window = range(i - k, i + k + 1)
        if all(h >= _high(candles[j]) for j in window) and \
                any(h > _high(candles[j]) for j in window if j != i):
            _push(highs, i, h, k, keep_max=True)
        if all(lo <= _low(candles[j]) for j in window) and \
                any(lo < _low(candles[j]) for j in window if j != i):
            _push(lows, i, lo, k, keep_max=False)
    return highs, lows


def _pct_apart(a, b):
    """|a-b| as a percent of the mean. Used to decide whether two pivots are 'the same level'."""
    m = (abs(a) + abs(b)) / 2.0
    return 0.0 if m <= 0 else abs(a - b) / m * 100.0


# ── the patterns ─────────────────────────────────────────────────────────────

def double_bottom(candles, highs, lows, inputs):
    """W. Returns dict or None. `confirmed` = price has closed back above the neckline."""
    tol = float(inputs.get("levelTolerancePct", 1.8))
    min_sep = int(inputs.get("minPivotSeparation", 4))
    max_age = int(inputs.get("maxPatternAgeBars", 14))

    if len(lows) < 2 or not highs:
        return None
    (i1, l1), (i2, l2) = lows[-2], lows[-1]
    if i2 - i1 < min_sep:
        return None                                   # too close together to be two distinct tests
    if _pct_apart(l1, l2) > tol:
        return None                                   # not the same level
    necks = [(i, h) for i, h in highs if i1 < i < i2]
    if not necks:
        return None                                   # no peak between the two lows -> not a W
    ni, neck = max(necks, key=lambda t: t[1])
    if len(candles) - 1 - i2 > max_age:
        return None                                   # stale — the setup has moved on
    last = _close(candles[-1])
    depth_pct = (neck - min(l1, l2)) / neck * 100.0 if neck > 0 else 0.0
    if depth_pct < float(inputs.get("minPatternDepthPct", 2.0)):
        return None                                   # too shallow to be a pattern rather than noise
    return {"pattern": "double_bottom", "direction": "LONG", "neckline": neck,
            "confirmed": last > neck, "depth_pct": depth_pct,
            "levels": [l1, l2], "neck_idx": ni, "second_idx": i2}


def double_top(candles, highs, lows, inputs):
    """M — the mirror of double_bottom."""
    tol = float(inputs.get("levelTolerancePct", 1.8))
    min_sep = int(inputs.get("minPivotSeparation", 4))
    max_age = int(inputs.get("maxPatternAgeBars", 14))

    if len(highs) < 2 or not lows:
        return None
    (i1, h1), (i2, h2) = highs[-2], highs[-1]
    if i2 - i1 < min_sep:
        return None
    if _pct_apart(h1, h2) > tol:
        return None
    troughs = [(i, l) for i, l in lows if i1 < i < i2]
    if not troughs:
        return None
    ni, neck = min(troughs, key=lambda t: t[1])
    if len(candles) - 1 - i2 > max_age:
        return None
    last = _close(candles[-1])
    depth_pct = (max(h1, h2) - neck) / neck * 100.0 if neck > 0 else 0.0
    if depth_pct < float(inputs.get("minPatternDepthPct", 2.0)):
        return None
    return {"pattern": "double_top", "direction": "SHORT", "neckline": neck,
            "confirmed": last < neck, "depth_pct": depth_pct,
            "levels": [h1, h2], "neck_idx": ni, "second_idx": i2}


def structure_trend(highs, lows, min_move_pct=1.5):
    """Higher-high/higher-low or lower-low/lower-high structure. Returns dict or None.

    Two requirements, both of which exist to keep noise out:
      1. BOTH series must agree. Rising highs on falling lows is a broadening pattern, not an
         uptrend, and treating it as one is how a scanner buys into a widening mess.
      2. The step must CLEAR `min_move_pct`. On random data consecutive pivots are descending
         about half the time by chance, so without a magnitude floor this fires on anything —
         measured on synthetic noise, which produced a clean `lower_low` before this gate.
    """
    if len(highs) < 2 or len(lows) < 2:
        return None
    h_step = _pct_apart(highs[-1][1], highs[-2][1])
    l_step = _pct_apart(lows[-1][1], lows[-2][1])
    if h_step < min_move_pct or l_step < min_move_pct:
        return None
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    depth = min(h_step, l_step)
    if hh and hl:
        return {"pattern": "higher_high", "direction": "LONG", "confirmed": True,
                "neckline": highs[-2][1], "depth_pct": depth, "levels": [],
                "second_idx": highs[-1][0]}
    if ll and lh:
        return {"pattern": "lower_low", "direction": "SHORT", "confirmed": True,
                "neckline": lows[-2][1], "depth_pct": depth, "levels": [],
                "second_idx": lows[-1][0]}
    return None


def volume_confirms(candles, pattern_idx, bars=3, baseline=12):
    """Did volume expand on the bars that formed/broke the pattern? Ratio vs baseline (0.0 = unknown)."""
    if pattern_idx is None or len(candles) < baseline + bars:
        return 0.0
    recent = [_vol(c) for c in candles[-bars:]]
    base = [_vol(c) for c in candles[-(baseline + bars):-bars]]
    bm = sum(base) / len(base) if base else 0.0
    if bm <= 0:
        return 0.0
    return (sum(recent) / len(recent)) / bm


def trend_structure_4h(candles, bars=6):
    """('UP'|'DOWN'|'NEUTRAL') on the higher timeframe — context only."""
    if len(candles) < bars:
        return "NEUTRAL"
    highs = [_high(c) for c in candles[-bars:]]
    lows = [_low(c) for c in candles[-bars:]]
    up = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    down = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])
    n = len(highs) - 1
    if up >= n * 0.6:
        return "UP"
    if down >= n * 0.6:
        return "DOWN"
    return "NEUTRAL"


# ── the thesis ───────────────────────────────────────────────────────────────

def build_thesis(coin, candles, candles_4h, inputs):
    """Best pattern on `candles`, scored. Returns a thesis dict or None.

    Scoring (max 9):
      +5  confirmed reversal pattern (double bottom/top, neckline broken)
      +3  unconfirmed reversal still forming, or a continuation structure
      +2  volume expansion on the pattern
      +2  pattern depth is meaningful
      +1  higher-timeframe trend agrees   (-1 if it opposes)
    """
    k = int(inputs.get("pivotWindow", 2))
    min_vol_ratio = float(inputs.get("minVolumeRatio", 1.15))
    require_confirmed = bool(inputs.get("requireConfirmed", True))
    good_depth = float(inputs.get("goodDepthPct", 4.0))

    if len(candles) < int(inputs.get("minCandles", 40)):
        return None
    highs, lows = swing_points(candles, k)

    found = [p for p in (double_bottom(candles, highs, lows, inputs),
                         double_top(candles, highs, lows, inputs)) if p]
    if not found:
        st = structure_trend(highs, lows, float(inputs.get("minStructureMovePct", 1.5)))
        if st:
            found = [st]
    if not found:
        return None

    # A confirmed reversal outranks a continuation structure.
    found.sort(key=lambda p: (p["confirmed"], p["depth_pct"]), reverse=True)
    pat = found[0]
    if require_confirmed and not pat["confirmed"]:
        return None

    is_reversal = pat["pattern"] in ("double_bottom", "double_top")
    score = 5 if (is_reversal and pat["confirmed"]) else 3
    reasons = [f"{pat['pattern'].replace('_', ' ')} "
               f"{'confirmed (neckline broken)' if pat['confirmed'] else 'forming'}"]

    vr = volume_confirms(candles, pat.get("second_idx"))
    if vr >= min_vol_ratio:
        score += 2
        reasons.append(f"volume {vr:.2f}x baseline on the pattern")
    else:
        reasons.append(f"volume only {vr:.2f}x baseline")

    if pat["depth_pct"] >= good_depth:
        score += 2
        reasons.append(f"pattern depth {pat['depth_pct']:.1f}%")
    elif is_reversal:
        reasons.append(f"shallow pattern ({pat['depth_pct']:.1f}%)")

    t4 = trend_structure_4h(candles_4h)
    if (t4 == "UP" and pat["direction"] == "LONG") or (t4 == "DOWN" and pat["direction"] == "SHORT"):
        score += 1
        reasons.append(f"4h trend {t4.lower()} agrees")
    elif t4 != "NEUTRAL":
        score -= 1
        reasons.append(f"4h trend {t4.lower()} opposes")

    return {"coin": coin, "direction": pat["direction"], "score": max(0, score),
            "pattern": pat["pattern"], "confirmed": pat["confirmed"],
            "depth_pct": round(pat["depth_pct"], 3), "neckline": round(pat["neckline"], 8),
            "vol_ratio": round(vr, 3), "trend_4h": t4, "reasons": reasons}


def margin_tier_pct(score, base_pct):
    """Conviction sizing on the PERCENT scale (base_pct is a PERCENT in (0,100])."""
    if score >= 8:
        return base_pct * 1.5
    if score >= 6:
        return base_pct * 1.25
    return base_pct
