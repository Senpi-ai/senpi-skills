"""STAG — pure parabolic-detection math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Stag producer's pure functions
(pct_change, sma, is_above_sma, recent_high_bars_ago, volume_surge,
is_accelerating, parabolic_score) plus the all-five-gates thesis builder.
The math/indexing is reproduced VERBATIM from stag-producer.py so a fidelity
harness can diff this against the v2 producer on the same market snapshot.
Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`; fix
them only as a separate, labelled change AFTER the port is validated.

ALL FIVE gates required (caller-side, in build_thesis):
  1. Structural trend: close > 200-bar 4h SMA AND 7d high made within last 48h
  2. Strength:        7d move >= minTrendPct (25% — defines "parabolic")
  3. Volume:          recent 6 4h bars / trailing 42 bars >= 1.5x
  4. Acceleration:    4d move >= 7d move / 2 (recent half at least as fast)
  5. SM aligned:      Smart Money LONG >= smTiltMinPct (60%)

LONG only. Single-pass, unit-testable on plain candle lists."""


# ── helpers (ported from v2 _f) ──
# v2 read candle dicts via _f(c, "close", "c") — a dual-key float coercion. _fk
# reproduces that (primary key, optional alt key); _f is scalar coercion.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _fk(c, primary, alt=None, default=0.0):
    """v2 _f(c, primary, alt, default): dict field with an optional alt key.
    The list-candle branch is defensive (never fires on v2 dict candles)."""
    if isinstance(c, dict):
        val = c.get(primary)
        if val is None and alt:
            val = c.get(alt)
        return _f(val if val is not None else default, default)
    # defensive list branch [t,o,h,l,c,v] — does not change v2 (dict) behaviour
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        idx = {"close": 4, "c": 4, "volume": 5, "v": 5}.get(primary)
        if idx is not None:
            return _f(c[idx], default)
    return default


# ═══════════════════════════════════════════════════════════════
# Pure parabolic-detection logic (VERBATIM port of v2 stag-producer.py)
# ═══════════════════════════════════════════════════════════════

def pct_change(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def sma(closes, period):
    """Simple moving average over the last `period` closes. None if
    insufficient data."""
    if not closes or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def is_above_sma(closes, period):
    """True if the latest close is above the N-period SMA. False if
    SMA can't be computed."""
    s = sma(closes, period)
    if s is None or not closes:
        return False
    return closes[-1] > s


def recent_high_bars_ago(closes, lookback):
    """How many bars back the highest close in `closes[-lookback-1:]` was
    made. 0 = latest bar is the high. None if insufficient data."""
    if not closes or len(closes) <= lookback:
        return None
    window = closes[-(lookback + 1):]
    high_idx_in_window = max(range(len(window)), key=lambda i: window[i])
    # bars_ago = distance from the end of the window to the high
    return len(window) - 1 - high_idx_in_window


def volume_surge(volumes, recent_bars, baseline_bars, min_ratio):
    """True if mean(last `recent_bars`) / mean(last `baseline_bars`) >=
    min_ratio. Returns (passed, ratio) — ratio is None on insufficient data."""
    if not volumes or len(volumes) < baseline_bars:
        return False, None
    recent = volumes[-recent_bars:]
    baseline = volumes[-baseline_bars:]
    rmean = sum(recent) / len(recent) if recent else 0.0
    bmean = sum(baseline) / len(baseline) if baseline else 0.0
    if bmean <= 0:
        return False, None
    ratio = rmean / bmean
    return (ratio >= min_ratio), ratio


def is_accelerating(short_strength_pct, long_strength_pct):
    """True if the shorter window's move is at least half the longer
    window's move — i.e., the recent half is keeping pace or faster.
    Both must be positive (we're gating LONG-only on bullish acceleration)."""
    if short_strength_pct is None or long_strength_pct is None:
        return False
    if long_strength_pct <= 0:
        return False
    return short_strength_pct >= (long_strength_pct / 2.0)


def parabolic_score(trend_pct, accelerating, vol_passed, vol_ratio, sm_aligned, strong_trend_pct):
    """Composite score for a parabolic setup. Gate (caller-side) is all-5
    pass; this scores HOW strong the setup is among passing candidates."""
    score = 3   # base — all five gates passed
    reasons = [f"trend_{trend_pct:+.1f}%"]
    if trend_pct >= strong_trend_pct:
        score += 2
        reasons.append(f"strong_{trend_pct:+.1f}%")
    if accelerating:
        score += 1
        reasons.append("accelerating")
    if vol_passed and vol_ratio is not None:
        if vol_ratio >= 2.0:
            score += 1
            reasons.append(f"vol_surge_{vol_ratio:.1f}x")
        else:
            reasons.append(f"vol_{vol_ratio:.1f}x")
    if sm_aligned:
        reasons.append("sm_aligned")
    return score, reasons


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one asset, all five gates (VERBATIM port of v2 build_thesis)
# ═══════════════════════════════════════════════════════════════

def build_thesis(candles, sm, inputs):
    """All-five-gate parabolic thesis for one asset. `candles` = list of 4h
    candle dicts; `sm` = (direction, tilt_pct) tuple from the SM read (or
    (None, 0.0)). Returns the thesis dict on a full pass, else None.

    Ported VERBATIM from stag-producer.build_thesis — gate order, indexing,
    and thresholds preserved exactly so a fidelity harness diffs cleanly."""
    sma_period = int(inputs.get("smaPeriod", 200))
    trend_lb = int(inputs.get("trendLookbackBars", 42))
    accel_lb = int(inputs.get("accelLookbackBars", 24))
    fresh_high_lb = int(inputs.get("freshHighBars", 12))

    # Need enough candles for the deepest lookback (SMA, by default 200)
    if len(candles) < max(sma_period, trend_lb) + 1:
        return None

    closes = [_fk(c, "close", "c") for c in candles]
    volumes = [_fk(c, "volume", "v") for c in candles]

    # Gate 1: structural trend — above 200-SMA AND 7d high within last 48h
    above_sma = is_above_sma(closes, sma_period)
    if not above_sma:
        return None
    high_bars_ago = recent_high_bars_ago(closes, trend_lb)
    if high_bars_ago is None or high_bars_ago > fresh_high_lb:
        return None

    # Gate 2: strength — 7d move >= minTrendPct
    trend_pct = pct_change(closes, trend_lb)
    min_trend = float(inputs.get("minTrendPct", 25.0))
    if trend_pct is None or trend_pct < min_trend:
        return None

    # Gate 3: volume surge
    vol_recent = int(inputs.get("volRecentBars", 6))
    vol_base = int(inputs.get("volBaselineBars", 42))
    vol_min = float(inputs.get("volSurgeRatio", 1.5))
    vol_passed, vol_ratio = volume_surge(volumes, vol_recent, vol_base, vol_min)
    if not vol_passed:
        return None

    # Gate 4: acceleration — recent half at least as fast as full window
    short_strength = pct_change(closes, accel_lb)
    accelerating = is_accelerating(short_strength, trend_pct)
    if not accelerating:
        return None

    # Gate 5: SM aligned LONG >= threshold
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    sm_min = float(inputs.get("smTiltMinPct", 60.0))
    sm_aligned = (sm_dir == "LONG" and sm_tilt >= sm_min)
    if not sm_aligned:
        return None

    # All 5 gates passed — score the setup
    strong_trend = float(inputs.get("strongTrendPct", 40.0))
    score, reasons = parabolic_score(trend_pct, accelerating, vol_passed, vol_ratio, sm_aligned, strong_trend)

    return {
        "coin": None,   # caller (scan.py) fills the asset name; scoring is asset-agnostic
        "direction": "LONG",
        "score": score,
        "reasons": reasons,
        "trend_pct": round(trend_pct, 2),
        "short_strength_pct": round(short_strength, 2) if short_strength is not None else 0.0,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else 0.0,
        "high_bars_ago": int(high_bars_ago),
        "sm_tilt_pct": sm_tilt,
    }
