"""HARE — pure crypto-majors session-scalp math (no I/O, no MCP; the clock is supplied).

BTC/ETH only. Sub-hourly momentum bursts during the Asia / London / US high-volume windows.
The fee guard is structural (runtime.yaml: maker-only entries + a hard frequency cap); this
module refuses to score a burst that is not backed by real volume, so a scalp only fires when
the move is large enough to clear costs. Candles keyed o/h/l/c/v (string-safe)."""


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


# ── the clock (pure — minute_of_day is supplied by scan.py) ──

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


def in_session(minute_of_day, windows):
    """Inside any configured [start, end) UTC high-volume window? Returns (bool, name).
    Handles a window that wraps midnight (start > end)."""
    for w in windows or []:
        s, e = parse_hhmm(w.get("start")), parse_hhmm(w.get("end"))
        if s is None or e is None:
            continue
        inside = (s <= minute_of_day < e) if s < e else (minute_of_day >= s or minute_of_day < e)
        if inside:
            return True, str(w.get("name", "session"))
    return False, None


# ── measurements ──

def window_drift_pct(candles, bars):
    """Signed % move across the last `bars` candles. 0.0 if too short."""
    if len(candles) < bars + 1 or bars < 1:
        return 0.0
    start, end = _close(candles[-(bars + 1)]), _close(candles[-1])
    return (end - start) / start * 100.0 if start > 0 else 0.0


def volume_expansion(candles, bars, baseline_bars):
    """Mean volume over the last `bars` vs the mean over the `baseline_bars` before them
    (1.0 = in line). 0.0 when there is not enough history."""
    need = bars + baseline_bars
    if len(candles) < need or bars < 1 or baseline_bars < 1:
        return 0.0
    recent = [_vol(c) for c in candles[-bars:]]
    base = [_vol(c) for c in candles[-need:-bars]]
    base_mean = sum(base) / len(base)
    return (sum(recent) / len(recent)) / base_mean if base_mean > 0 else 0.0


def trend_structure(candles):
    """('UP'|'DOWN'|'NEUTRAL', strength) from higher-highs / lower-lows over the series.
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


# ── the scalp thesis ──

def scalp_thesis(coin, c15, c1h, session, inputs):
    """A sub-hourly momentum burst that clears the fee bar. REQUIRES real volume expansion
    (a burst on thin volume is fee-bait -> rejected). Returns a thesis dict or None.

      base 4  drift over the burst window AND volume expanding (both required)
      +1      drift is strong
      +1/-1   1h trend agrees / opposes
    """
    burst_bars = int(inputs.get("burstBars", 2))            # 2 x 15m = 30m
    base_bars = int(inputs.get("baselineBars", 12))
    min_burst = float(inputs.get("minBurstPct", 0.4))
    strong_burst = float(inputs.get("strongBurstPct", 0.9))
    min_vol = float(inputs.get("minVolumeRatio", 1.3))
    if len(c15) < burst_bars + base_bars:
        return None
    drift = window_drift_pct(c15, burst_bars)
    if abs(drift) < min_burst:
        return None
    vr = volume_expansion(c15, burst_bars, base_bars)
    if vr < min_vol:                                         # fee-bait guard — no volume, no scalp
        return None
    direction = "LONG" if drift > 0 else "SHORT"
    score, reasons = 4, [f"price moved {drift:+.2f}% in {burst_bars * 15}m ({session} session)",
                         f"volume running {vr:.2f}x its recent average"]
    if abs(drift) >= strong_burst:
        score += 1
        reasons.append("the move is unusually strong")
    t1, _ = trend_structure(c1h)
    if (t1 == "UP" and direction == "LONG") or (t1 == "DOWN" and direction == "SHORT"):
        score += 1
        reasons.append(f"hourly trend is {t1}, same direction")
    elif t1 != "NEUTRAL":
        score -= 1
        reasons.append(f"hourly trend is {t1}, against this move")
    return {"coin": coin, "direction": direction, "score": max(0, score), "drift": round(drift, 4),
            "vol_ratio": round(vr, 3), "trend_1h": t1, "session": session, "reasons": reasons}


def sizing(score, inputs):
    """Conviction-tiered (marginPct PERCENT of withdrawable, leverage). Returns (marginPct, leverage)."""
    base_m = float(inputs.get("marginPct", 12))
    cap = float(inputs.get("maxMarginPct", 22))
    std_l = int(inputs.get("stdLeverage", 6))
    max_l = int(inputs.get("maxLeverage", 10))
    if score >= 6:
        return round(min(base_m * 1.4, cap), 4), max_l
    if score >= 5:
        return round(min(base_m * 1.2, cap), 4), min(std_l + 2, max_l)
    return round(base_m, 4), std_l
