"""BARNACLE — pure footprint math (no I/O, no MCP, no clock).

Autonomous "stealth accumulation / decoupler" detector. Index inclusions, M&A
targets, and catalyst run-ups all leave the SAME fingerprint in the tape WITHOUT
knowing the catalyst: a name DECOUPLING from the broad market on persistent,
rising volume with shallow (bought) dips — relentless accumulation, not a
one-bar spike. This module scores that footprint from market data alone:

  - EXCESS RETURN  — name return minus the broad-xyz benchmark return over the
                     lookback (~3-5 days of 4h bars). |excess| sets conviction;
                     its SIGN sets direction (up -> LONG accumulation, down ->
                     SHORT distribution).
  - VOLUME PERSISTENCE — 4h volume rising STEADILY across the window (recent-half
                     vs earlier-half average), not a single spike. This is the
                     "relentless" tell.
  - SHALLOW-DIP / GRIND — within the move, the largest adverse pullback is
                     SHALLOW (dips are bought). A volatile breakout has deep
                     retraces; accumulation grinds. This is the accumulation
                     signature vs a noisy spike.
  - TREND STRUCTURE (4h) — strict higher-lows (long) / lower-highs (short),
                     bobcat's trend_structure (verbatim).
  - SMART-MONEY lean — a +/- score NUDGE (fetched by the caller, passed in),
                     never a hard gate.

This is a SCORED detector, not a pure gate: the only hard requirements are
(a) sufficient candle history, (b) |excess| >= minExcessPct (the decoupling
exists), and (c) a resolvable direction. Everything else is a score contributor.
The minScore floor is applied by the CALLER (scan.py).

Multi-asset, single-pass, unit-testable on plain candle lists."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──

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
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators ───────────────────────────────────────────────────────────────

def window_return(candles, n_bars):
    """% change of close over the last n_bars (close[-1] vs close[-(n_bars+1)]).
    Used for both the name return and the benchmark return over the SAME bar
    count so the excess is apples-to-apples. Returns None on insufficient history
    or a non-positive base."""
    if len(candles) < n_bars + 1:
        return None
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old <= 0:
        return None
    return ((new - old) / old) * 100.0


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from bobcat.

    Thresholds use strict (>) for higher-lows / lower-highs counting; the
    BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1. Strength
    returned is higher_lows/total (BULLISH) or lower_highs/total (BEARISH);
    NEUTRAL returns 0.0."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def volume_persistence(candles, lookback=6):
    """Recent-half vs earlier-half average 4h volume, % change — the SAME
    construction as bison/bobcat volume_trend, used here as the "rising on
    persistent volume" tell. A steady accumulation campaign shows recent-half
    volume materially above the earlier half. Returns 0 on insufficient history."""
    if len(candles) < lookback + 2:
        return 0.0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100.0


def deepest_dip_pct(candles, direction, lookback):
    """The largest ADVERSE pullback (against `direction`) within the lookback
    window, as a positive % of the running extreme. A grind/accumulation has a
    SHALLOW deepest dip (dips bought); a volatile breakout has a deep one.

    LONG  — track the running peak close; the dip is how far the bar's LOW fell
            below the running peak. Return the max such drop.
    SHORT — mirror: track the running trough close; the dip is how far the bar's
            HIGH rose above the running trough.

    Returns a non-negative percent (0 = no adverse excursion). Insufficient
    history -> a large sentinel (999.0) so the shallow-dip bonus never fires on
    too-short data."""
    if len(candles) < lookback:
        return 999.0
    window = candles[-lookback:]
    worst = 0.0
    if direction == "LONG":
        peak = _close(window[0])
        for c in window:
            cl = _close(c)
            if cl > peak:
                peak = cl
            lo = _low(c)
            if peak > 0 and lo > 0:
                drop = (peak - lo) / peak * 100.0
                if drop > worst:
                    worst = drop
    else:  # SHORT
        trough = _close(window[0])
        for c in window:
            cl = _close(c)
            if cl > 0 and (trough <= 0 or cl < trough):
                trough = cl
            hi = _high(c)
            if trough > 0 and hi > 0:
                rise = (hi - trough) / trough * 100.0
                if rise > worst:
                    worst = rise
    return worst


# ── the footprint thesis (decoupling detector) ────────────────────────────────

def build_thesis(coin, candles_1h, candles_4h, benchmark_ret, sm, inputs):
    """Score the stealth-accumulation footprint. Returns a thesis dict (with
    `score`) or None.

    None is returned ONLY when:
      - insufficient candle history (len(c4h) < excessBars+1, or < 6, or
        len(c1h) < 6), OR
      - the decoupling is too small (|excess| < minExcessPct), OR
      - name/benchmark return cannot be computed.

    The minScore floor is applied by the CALLER (scan.py).

    `benchmark_ret` is the broad-xyz benchmark return over the SAME bar count
    (excessBars of 4h candles), fetched once by the caller. `sm` is the
    smart-money lean tuple (direction, tilt_pct) or (None, 0.0) — a NUDGE, never
    a gate."""
    excess_bars = int(inputs.get("excessBars", 24))          # ~24 * 4h = 4 days
    min_excess = float(inputs.get("minExcessPct", 3.0))
    min_vol_trend = float(inputs.get("minVolTrendPct", 10.0))
    shallow_dip_max = float(inputs.get("shallowDipMaxPct", 4.0))
    dip_lookback = int(inputs.get("dipLookbackBars", excess_bars))

    if len(candles_4h) < excess_bars + 1 or len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    name_ret = window_return(candles_4h, excess_bars)
    if name_ret is None or benchmark_ret is None:
        return None

    # ── EXCESS RETURN: the decoupling. Sign -> direction; magnitude -> conviction.
    excess = name_ret - benchmark_ret
    if abs(excess) < min_excess:
        return None
    direction = "LONG" if excess > 0 else "SHORT"

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    vol_trend = volume_persistence(candles_4h)
    dip = deepest_dip_pct(candles_4h, direction, dip_lookback)
    sm_dir, sm_pct = sm if sm else (None, 0.0)

    score = 0
    reasons = [f"excess_{excess:+.1f}%_vs_bench_{benchmark_ret:+.1f}%"]

    # ── EXCESS tier (the core signal): +2 base for clearing minExcess, +1 strong,
    #    +1 very strong. Decoupling magnitude IS the conviction.
    abs_ex = abs(excess)
    score += 2
    if abs_ex >= min_excess * 2:
        score += 1
        reasons.append(f"strong_decouple_{abs_ex:.1f}%")
    if abs_ex >= min_excess * 3:
        score += 1
        reasons.append(f"very_strong_decouple_{abs_ex:.1f}%")

    # ── VOLUME PERSISTENCE: +2 if 4h volume is rising steadily across the window
    #    (the "relentless accumulation" tell vs a flat/declining tape).
    if vol_trend >= min_vol_trend:
        score += 2
        reasons.append(f"vol_persistent_{vol_trend:+.0f}%")
    elif vol_trend <= -min_vol_trend:
        score -= 1
        reasons.append(f"vol_fading_{vol_trend:+.0f}%")

    # ── SHALLOW-DIP / GRIND: +2 if the deepest adverse pullback is shallow (dips
    #    bought = accumulation). A deep dip means a volatile breakout, not a grind.
    if dip <= shallow_dip_max:
        score += 2
        reasons.append(f"shallow_dip_{dip:.1f}%")
    elif dip >= shallow_dip_max * 2.5:
        score -= 1
        reasons.append(f"choppy_dip_{dip:.1f}%")

    # ── TREND STRUCTURE (4h) in the excess direction: +2 confirm / -1 opposing.
    if t4 != "NEUTRAL":
        if (direction == "LONG" and t4 == "BULLISH") or (direction == "SHORT" and t4 == "BEARISH"):
            score += 2
            reasons.append(f"4h_{t4.lower()}_{s4:.0%}")
        else:
            score -= 1
            reasons.append(f"4h_opposing_{t4.lower()}")

    # ── 1H trend agreement: +1 confirm.
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 1
        reasons.append(f"1h_confirms_{t1.lower()}")

    # ── SMART-MONEY nudge (+1 / -1), never a gate.
    if sm_dir in ("LONG", "SHORT"):
        if sm_dir == direction:
            score += 1
            reasons.append(f"sm_aligned_{_f(sm_pct):.0f}%")
        else:
            score -= 1
            reasons.append(f"sm_opposing_{sm_dir}")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "excess": round(excess, 3), "name_ret": round(name_ret, 3),
        "benchmark_ret": round(benchmark_ret, 3),
        "trend_4h": t4, "trend_4h_strength": round(s4, 4), "trend_1h": t1,
        "vol_trend": round(vol_trend, 2), "deepest_dip": round(dip, 3),
        "sm_direction": sm_dir, "sm_pct": round(_f(sm_pct), 2),
    }


# ── leverage clamp (venue-aware) ──────────────────────────────────────────────

def clamp_leverage(desired, venue_max, lo=1, hi=5):
    """Clamp `desired` into [lo, hi] then to the instrument's venue max (if known
    and positive). Returns an int >= lo. Mirrors the octopus/dire venue clamp."""
    lev = int(desired)
    lev = max(lo, min(lev, hi))
    if venue_max:
        try:
            vm = int(venue_max)
            if vm > 0:
                lev = min(lev, vm)
        except (TypeError, ValueError):
            pass
    return max(lo, lev)
