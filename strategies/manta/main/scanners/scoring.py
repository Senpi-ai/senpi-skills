"""MANTA — pure thesis math: top-down multi-timeframe structure (no I/O, no MCP, no clock).

The classic top-down method, in the order a structure trader actually applies it. Each
timeframe answers exactly one question and is not allowed to answer any of the others:

  DAILY / 4h / 1h  →  WHICH WAY?   Trend bias. All three must agree; one dissenter is a
                      no-trade, because the whole premise is alignment.
  4h               →  WHERE?       The Area Of Interest: the most recent swing that price
                      broke away from, i.e. the level it is expected to retest.
  15m              →  WHEN?        Execution. Price must be INSIDE the AOI and then print a
                      break of 15m structure in the bias direction — the trigger.

Nothing here is allowed to fire on its own. A 15m break outside the AOI is noise; an AOI
touch with no trigger is a falling knife; three aligned timeframes with neither is just an
opinion. The confluence IS the strategy.

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


def _open(c):
    if isinstance(c, dict):
        return _f(c.get("open", c.get("o", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[1])
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


# ── swing pivots (shared by bias, AOI and trigger) ───────────────────────────

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
    """([(idx, price)] highs, [(idx, price)] lows) via a symmetric k-bar fractal window."""
    highs, lows = [], []
    n = len(candles)
    if n < 2 * k + 1 or k < 1:
        return highs, lows
    for i in range(k, n - k):
        h, lo = _high(candles[i]), _low(candles[i])
        w = range(i - k, i + k + 1)
        if all(h >= _high(candles[j]) for j in w) and any(h > _high(candles[j]) for j in w if j != i):
            _push(highs, i, h, k, keep_max=True)
        if all(lo <= _low(candles[j]) for j in w) and any(lo < _low(candles[j]) for j in w if j != i):
            _push(lows, i, lo, k, keep_max=False)
    return highs, lows


# ── 1. WHICH WAY — trend bias per timeframe ──────────────────────────────────

def timeframe_bias(candles, k=2, min_step_pct=0.15):
    """'UP' | 'DOWN' | 'NEUTRAL' from swing structure on one timeframe.

    `min_step_pct` is deliberately small for FX, where a 0.5% daily move is a big day —
    a crypto-scale threshold would call every FX chart neutral forever.
    """
    highs, lows = swing_points(candles, k)
    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ref = abs(highs[-2][1]) or 1.0
    h_step = abs(highs[-1][1] - highs[-2][1]) / ref * 100.0
    l_step = abs(lows[-1][1] - lows[-2][1]) / (abs(lows[-2][1]) or 1.0) * 100.0
    if max(h_step, l_step) < min_step_pct:
        return "NEUTRAL"                    # the structure moved, but not enough to mean anything
    if hh and hl:
        return "UP"
    if ll and lh:
        return "DOWN"
    return "NEUTRAL"


def aligned_bias(daily, h4, h1, inputs):
    """The shared bias across Daily/4h/1h, or None when they disagree.

    `requireAllThree` off demotes the 1h to a tiebreaker: Daily+4h must still agree, and the
    1h may be NEUTRAL but never opposed.
    """
    k = int(inputs.get("pivotWindow", 2))
    step = float(inputs.get("minStructureStepPct", 0.15))
    require_all = bool(inputs.get("requireAllThree", True))

    bd = timeframe_bias(daily, k, step)
    b4 = timeframe_bias(h4, k, step)
    b1 = timeframe_bias(h1, k, step)
    if bd == "NEUTRAL" or bd != b4:
        return None, (bd, b4, b1)
    if require_all:
        if b1 != bd:
            return None, (bd, b4, b1)
    elif b1 not in (bd, "NEUTRAL"):
        return None, (bd, b4, b1)
    return bd, (bd, b4, b1)


# ── 2. WHERE — the 4h Area Of Interest ───────────────────────────────────────

def find_aoi(candles_4h, bias, inputs):
    """(lo, hi) of the 4h Area Of Interest, or None.

    In an uptrend the AOI is the most recent swing LOW that price rallied away from — the
    level it is expected to retest and hold. In a downtrend, the most recent swing HIGH.
    The zone is that pivot widened by `aoiPaddingPct`, because price respects areas, not lines.
    """
    k = int(inputs.get("pivotWindow", 2))
    pad = float(inputs.get("aoiPaddingPct", 0.12)) / 100.0
    max_age = int(inputs.get("maxAoiAgeBars", 30))

    highs, lows = swing_points(candles_4h, k)
    n = len(candles_4h)
    if bias == "UP":
        if not lows:
            return None
        idx, level = lows[-1]
    else:
        if not highs:
            return None
        idx, level = highs[-1]
    if n - 1 - idx > max_age or level <= 0:
        return None                          # the level is stale; price has moved on from it
    return level * (1 - pad), level * (1 + pad)


def in_aoi(price, aoi):
    return aoi is not None and aoi[0] <= price <= aoi[1]


def touched_aoi(candles_15m, aoi, bars):
    """Did price TRADE INTO the zone within the last `bars` candles? (touched, bars_ago)

    Deliberately not `in_aoi(latest_close)`. The entry trigger is a break of structure, and a
    real break carries price OUT of the zone on the very bar that triggers it — so testing the
    current close rejects precisely the setups the method is built to take. What matters is
    that price came back and TAGGED the level (wick counts, hence high/low rather than close)
    and then broke. Measured on synthetic data: the close-based test rejected a textbook
    entry whose break closed 0.04% above the zone.
    """
    if aoi is None or not candles_15m:
        return False, None
    lo, hi = aoi
    window = candles_15m[-int(max(1, bars)):]
    for offset, cd in enumerate(reversed(window)):
        if _low(cd) <= hi and _high(cd) >= lo:      # the bar's range overlaps the zone
            return True, offset
    return False, None


def distance_to_aoi_pct(price, aoi):
    """How far price sits outside the zone, as a percent (0.0 when inside)."""
    if aoi is None or price <= 0:
        return None
    lo, hi = aoi
    if price < lo:
        return (lo - price) / price * 100.0
    if price > hi:
        return (price - hi) / price * 100.0
    return 0.0


# ── 3. WHEN — the 15m structure trigger ──────────────────────────────────────

def structure_break(candles_15m, bias, inputs):
    """Did 15m structure just break in the bias direction? (broke, level, kind).

    'continuation' — price took out the most recent 15m swing high (up) / low (down).
    A break AGAINST the bias is never a trigger; it is the reason to stand aside.
    """
    k = int(inputs.get("pivotWindow15m", 1))
    lookback = int(inputs.get("triggerLookbackBars", 12))
    if len(candles_15m) < lookback + 2 * k + 1:
        return False, None, None
    recent = candles_15m[-(lookback + 2 * k + 1):]
    highs, lows = swing_points(recent, k)
    last = _close(recent[-1])
    if bias == "UP":
        if not highs:
            return False, None, None
        level = highs[-1][1]
        return (last > level), level, "continuation"
    if not lows:
        return False, None, None
    level = lows[-1][1]
    return (last < level), level, "continuation"


def displacement_pct(candles_15m, bars=3):
    """Size of the most recent 15m push, as a percent — the 'is this a real break' check."""
    if len(candles_15m) < bars + 1:
        return 0.0
    start = _close(candles_15m[-(bars + 1)])
    end = _close(candles_15m[-1])
    return 0.0 if start <= 0 else abs(end - start) / start * 100.0


# ── the thesis ───────────────────────────────────────────────────────────────

def build_thesis(coin, daily, h4, h1, m15, inputs):
    """The full top-down cascade. Returns a thesis dict or None at the first failed step.

    Scoring (max 9):
      +4  Daily/4h/1h bias aligned
      +3  price inside the 4h AOI
      +2  15m structure break in the bias direction
      +1  displacement confirms the break is real   (never awarded without the break)
    """
    min_disp = float(inputs.get("minDisplacementPct", 0.08))

    if min(len(daily), len(h4), len(h1), len(m15)) < int(inputs.get("minCandles", 20)):
        return None

    bias, seen = aligned_bias(daily, h4, h1, inputs)
    if bias is None:
        return None                          # step 1 failed: no agreed direction
    direction = "LONG" if bias == "UP" else "SHORT"
    reasons = [f"D/4h/1h all {bias} ({'/'.join(seen)})"]

    aoi = find_aoi(h4, bias, inputs)
    if aoi is None:
        return None                          # step 2 failed: no valid area of interest
    price = _close(m15[-1])
    touched, bars_ago = touched_aoi(m15, aoi, int(inputs.get("aoiTouchWindowBars", 8)))
    if not touched:
        return None                          # never came back to the level — the common stand-aside
    reasons.append(f"price tagged the 4h AOI [{aoi[0]:.6g}, {aoi[1]:.6g}] "
                   f"{'this bar' if bars_ago == 0 else f'{bars_ago} bars ago'}")

    broke, level, kind = structure_break(m15, bias, inputs)
    if not broke:
        return None                          # step 3 failed: at the level but no trigger yet
    reasons.append(f"15m {kind} break of {level:.6g}")

    score = 4 + 3 + 2
    disp = displacement_pct(m15)
    if disp >= min_disp:
        score += 1
        reasons.append(f"displacement {disp:.3f}% confirms the break")
    else:
        reasons.append(f"weak displacement {disp:.3f}%")

    return {"coin": coin, "direction": direction, "score": score,
            "bias": bias, "biases": list(seen), "aoi_low": aoi[0], "aoi_high": aoi[1],
            "trigger_level": level, "displacement_pct": round(disp, 4),
            "reasons": reasons}


def margin_tier_pct(score, base_pct):
    """Conviction sizing on the PERCENT scale (base_pct is a PERCENT in (0,100])."""
    if score >= 9:
        return base_pct * 1.5
    if score >= 8:
        return base_pct * 1.25
    return base_pct
