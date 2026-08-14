"""HEN — pure thesis math: pre-market-open positioning (no I/O, no MCP, no clock). Shared
engine with Rooster; Hen adds `is_trading_day` so a weekend open (which equities do not have)
is skipped. The measurements (drift / volume / range / trend / session_phase) are identical.

The edge is the CLOCK, not the chart: liquidity and directional intent build in the window
BEFORE a session opens, and the open itself is when that intent gets expressed. So the read is
taken during the pre-open window and the position is carried INTO the open.

What the scorer measures over the pre-open window (15m candles):
  - DRIFT      — signed % move across the window. This is the directional read.
  - VOLUME     — window volume vs the trailing baseline. Real pre-positioning shows up as
                 expanding volume; a drift on thin volume is noise and scores nothing.
  - RANGE POS  — where price sits inside the prior session's range. A drift that is also
                 breaking out of the prior range is a stronger setup than one mid-range.
  - 4h TREND   — context only. Aligned adds, opposed subtracts; it never sets direction.

`session_phase` is the one clock-aware function and it is PURE — it takes `minute_of_day` from
the caller (scan.py owns the clock) and returns where we are relative to the configured opens.

Candles are keyed o/h/l/c/v with STRING values — `_f`/`_close` coerce both shapes.
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


# ── the clock (pure — minute_of_day is supplied by scan.py) ───────────────────

def parse_hhmm(s):
    """'13:30' -> 810 minutes past UTC midnight. None if unparseable."""
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def is_trading_day(weekday, trading_days):
    """True if `weekday` (0=Mon .. 6=Sun, as from time.gmtime().tm_wday) is a configured
    trading day. Pure. An empty/None `trading_days` means "every day" (Rooster's behaviour) —
    Hen passes [0,1,2,3,4] so a weekend open, which does not exist for equities, is skipped.

    This does NOT know about market holidays; on a holiday the thin-volume floor is the backstop.
    """
    if not trading_days:
        return True
    try:
        allowed = {int(d) for d in trading_days}
    except (TypeError, ValueError):
        return True
    return int(weekday) in allowed


def session_phase(minute_of_day, opens_hhmm, pre_open_minutes):
    """Where the clock sits relative to the configured session opens.

    Returns (phase, open_minute, minutes_to_open):
      'pre_open' — inside [open - pre_open_minutes, open): the ONLY window that emits.
      'idle'     — anywhere else.

    Wraps midnight correctly (an 00:30 open has a pre-open window in the prior day's 23:xx).
    """
    if not opens_hhmm:
        return "idle", None, None
    span = max(1, int(pre_open_minutes))
    best = None
    for raw in opens_hhmm:
        om = parse_hhmm(raw)
        if om is None:
            continue
        delta = (om - minute_of_day) % 1440          # minutes until this open, forward-looking
        if 0 < delta <= span and (best is None or delta < best[1]):
            best = (om, delta)
    if best is None:
        return "idle", None, None
    return "pre_open", best[0], best[1]


# ── measurements ─────────────────────────────────────────────────────────────

def window_drift_pct(candles_15m, bars):
    """Signed % move across the last `bars` 15m candles. 0.0 if too short."""
    if len(candles_15m) < bars + 1 or bars < 1:
        return 0.0
    start = _close(candles_15m[-(bars + 1)])
    end = _close(candles_15m[-1])
    if start <= 0:
        return 0.0
    return (end - start) / start * 100.0


def volume_expansion(candles_15m, bars, baseline_bars):
    """Mean volume over the last `bars` vs the mean over the `baseline_bars` before them.
    Returns a ratio (1.0 = in line with baseline). 0.0 when there is not enough history."""
    need = bars + baseline_bars
    if len(candles_15m) < need or bars < 1 or baseline_bars < 1:
        return 0.0
    recent = [_vol(c) for c in candles_15m[-bars:]]
    base = [_vol(c) for c in candles_15m[-need:-bars]]
    base_mean = sum(base) / len(base)
    if base_mean <= 0:
        return 0.0
    return (sum(recent) / len(recent)) / base_mean


def prior_range_position(candles_15m, window_bars, prior_bars):
    """Where the latest close sits within the prior session's range, as a 0-1 fraction
    (0 = at/below the prior low, 1 = at/above the prior high). None if unavailable.

    The prior range EXCLUDES the pre-open window itself, so a breakout is measured against
    settled price, not against the drift we are trying to score.
    """
    need = window_bars + prior_bars
    if len(candles_15m) < need or prior_bars < 2:
        return None
    prior = candles_15m[-need:-window_bars] if window_bars > 0 else candles_15m[-prior_bars:]
    hi = max(_high(c) for c in prior)
    lo = min(_low(c) for c in prior)
    if hi <= lo:
        return None
    last = _close(candles_15m[-1])
    return max(0.0, min(1.0, (last - lo) / (hi - lo)))


def trend_structure(candles):
    """('UP'|'DOWN'|'NEUTRAL', strength 0-1) from higher-highs / lower-lows over the series.
    Context only — never sets direction."""
    if len(candles) < 6:
        return "NEUTRAL", 0.0
    highs = [_high(c) for c in candles[-6:]]
    lows = [_low(c) for c in candles[-6:]]
    up = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    down = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])
    n = len(highs) - 1
    if up >= n * 0.6:
        return "UP", up / n
    if down >= n * 0.6:
        return "DOWN", down / n
    return "NEUTRAL", 0.0


# ── the thesis ───────────────────────────────────────────────────────────────

def build_thesis(coin, candles_15m, candles_4h, minutes_to_open, inputs):
    """Score the pre-open setup. Returns a thesis dict or None (no setup).

    Scoring (max 8; `minScore` gates):
      +3  drift clears the noise floor          — the directional read
      +2  volume expanding vs baseline          — real positioning, not drift on air
      +2  breaking out of the prior range in the drift direction
      +1  4h trend agrees   (-1 if it opposes)
    """
    min_drift = float(inputs.get("minDriftPct", 0.35))
    strong_drift = float(inputs.get("strongDriftPct", 0.9))
    min_vol_ratio = float(inputs.get("minVolumeRatio", 1.15))
    window_bars = int(inputs.get("windowBars", 3))
    baseline_bars = int(inputs.get("baselineBars", 16))
    prior_bars = int(inputs.get("priorRangeBars", 24))
    breakout_hi = float(inputs.get("breakoutHighPct", 0.8))
    breakout_lo = float(inputs.get("breakoutLowPct", 0.2))

    if len(candles_15m) < window_bars + baseline_bars:
        return None

    drift = window_drift_pct(candles_15m, window_bars)
    if abs(drift) < min_drift:
        return None                                   # no directional read — the common case

    direction = "LONG" if drift > 0 else "SHORT"
    score, reasons = 3, [f"pre-open drift {drift:+.2f}% over {window_bars * 15}m"]

    vol_ratio = volume_expansion(candles_15m, window_bars, baseline_bars)
    if vol_ratio >= min_vol_ratio:
        score += 2
        reasons.append(f"volume {vol_ratio:.2f}x baseline")
    else:
        reasons.append(f"volume only {vol_ratio:.2f}x baseline (thin)")

    pos = prior_range_position(candles_15m, window_bars, prior_bars)
    if pos is not None:
        if direction == "LONG" and pos >= breakout_hi:
            score += 2
            reasons.append(f"breaking prior range high (pos {pos:.2f})")
        elif direction == "SHORT" and pos <= breakout_lo:
            score += 2
            reasons.append(f"breaking prior range low (pos {pos:.2f})")
        else:
            reasons.append(f"mid prior range (pos {pos:.2f})")

    t4, _ = trend_structure(candles_4h)
    if (t4 == "UP" and direction == "LONG") or (t4 == "DOWN" and direction == "SHORT"):
        score += 1
        reasons.append(f"4h trend {t4} agrees")
    elif t4 != "NEUTRAL":
        score -= 1
        reasons.append(f"4h trend {t4} opposes")

    if abs(drift) >= strong_drift:
        reasons.append("drift is strong")

    return {
        "coin": coin, "direction": direction, "score": max(0, score),
        "drift_pct": round(drift, 4), "vol_ratio": round(vol_ratio, 3),
        "range_pos": None if pos is None else round(pos, 3),
        "trend_4h": t4, "minutes_to_open": minutes_to_open,
        "reasons": reasons,
    }


def margin_tier_pct(score, base_pct):
    """Conviction sizing on the PERCENT scale (base_pct is a PERCENT in (0,100])."""
    if score >= 8:
        return base_pct * 1.5
    if score >= 6:
        return base_pct * 1.25
    return base_pct
