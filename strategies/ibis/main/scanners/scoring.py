"""IBIS — pure thesis math: trend/range regime detection, then regime-specific entry logic.

The strategy is two strategies behind one gate. First classify the 4h regime, then apply the
entry rule that belongs to THAT regime — a continuation rule in a trend, a fade rule in a range.
Applying either rule in the wrong regime is how a trend-follower gets chopped up and how a
mean-reverter gets run over.

REGIME — Kaufman efficiency ratio on 4h closes:
    ER = |net move| / sum(|bar-to-bar moves|)   over the lookback
  A clean directional move travels nearly all of its path distance (ER -> 1); chop retraces
  itself and travels almost none of it (ER -> 0). Confirmed against higher-high/lower-low
  structure so a one-bar spike can't fake a trend.

TREND entry  — continuation in the structural direction, gated on OI VELOCITY: open interest
  must be RISING, i.e. new money is committing to the move rather than shorts covering into it.
  Prefers a shallow pullback over buying the extreme.

RANGE entry  — fade the band edge (long the low, short the high), and SKIPPED OUTRIGHT when
  funding is extreme: crowded funding is the tell that a range is about to break, which is
  exactly when a fade is the wrong trade.

Pure + single-pass. Candles are keyed o/h/l/c/v with STRING values — `_f`/`_close` coerce.
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


# ── funding / OI helpers ─────────────────────────────────────────────────────

def annualized_funding_pct(hourly_rate):
    """market_list_instruments `context.funding` is an HOURLY rate (string fraction,
    e.g. '0.0000125'). Annualize to a percent: rate * 24 * 365 * 100."""
    return _f(hourly_rate) * 24.0 * 365.0 * 100.0


def oi_usd(context):
    """`context.openInterest` is in BASE units (coins), not USD — multiply by mark price."""
    if not isinstance(context, dict):
        return 0.0
    return _f(context.get("openInterest")) * _f(context.get("markPx", context.get("midPx", 0)))


def oi_velocity_pct(oi_now, oi_prev):
    """% change in open interest since the previous snapshot. None when there is no baseline
    (first tick after a restart) — the caller must treat None as 'unknown', never as zero."""
    if oi_prev is None or _f(oi_prev) <= 0 or _f(oi_now) <= 0:
        return None
    return (_f(oi_now) - _f(oi_prev)) / _f(oi_prev) * 100.0


# ── regime classification ────────────────────────────────────────────────────

def efficiency_ratio(candles, lookback):
    """Kaufman efficiency ratio over the last `lookback` closes. 0.0 when too short."""
    if len(candles) < lookback + 1 or lookback < 2:
        return 0.0
    closes = [_close(c) for c in candles[-(lookback + 1):]]
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        return 0.0
    return net / path


def trend_structure(candles, bars=6):
    """('UP'|'DOWN'|'NEUTRAL', strength 0-1) from higher-highs / lower-lows."""
    if len(candles) < bars:
        return "NEUTRAL", 0.0
    highs = [_high(c) for c in candles[-bars:]]
    lows = [_low(c) for c in candles[-bars:]]
    up = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    down = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])
    n = len(highs) - 1
    if up >= n * 0.6:
        return "UP", up / n
    if down >= n * 0.6:
        return "DOWN", down / n
    return "NEUTRAL", 0.0


def classify_regime(candles_4h, inputs):
    """('TREND'|'RANGE'|'UNCLEAR', er, structure, strength).

    The deliberate gap between the two thresholds is a NO-TRADE band: a market that is neither
    cleanly trending nor cleanly ranging gets no entry rule at all, rather than the closest one.
    """
    er_lookback = int(inputs.get("erLookback", 20))
    trend_er = float(inputs.get("trendErThreshold", 0.36))
    range_er = float(inputs.get("rangeErThreshold", 0.20))

    er = efficiency_ratio(candles_4h, er_lookback)
    struct, strength = trend_structure(candles_4h)
    if er >= trend_er and struct != "NEUTRAL":
        return "TREND", er, struct, strength
    if er <= range_er:
        return "RANGE", er, struct, strength
    return "UNCLEAR", er, struct, strength


def range_bounds(candles_4h, bars):
    """(low, high, position_0_to_1) of the latest close inside the recent range. None if flat."""
    if len(candles_4h) < bars or bars < 4:
        return None
    window = candles_4h[-bars:]
    hi = max(_high(c) for c in window)
    lo = min(_low(c) for c in window)
    if hi <= lo:
        return None
    last = _close(candles_4h[-1])
    return lo, hi, max(0.0, min(1.0, (last - lo) / (hi - lo)))


def pullback_pct(candles_4h, direction, bars=6):
    """How far price has retraced from the recent extreme in the trend direction, as a percent.
    A shallow pullback is a better continuation entry than the extreme itself."""
    if len(candles_4h) < bars:
        return 0.0
    window = candles_4h[-bars:]
    last = _close(candles_4h[-1])
    if direction == "LONG":
        hi = max(_high(c) for c in window)
        return 0.0 if hi <= 0 else (hi - last) / hi * 100.0
    lo = min(_low(c) for c in window)
    return 0.0 if lo <= 0 else (last - lo) / lo * 100.0


# ── the two entry rules ──────────────────────────────────────────────────────

def trend_thesis(coin, candles_4h, er, struct, strength, oi_vel, inputs):
    """Continuation entry. HARD GATE: open interest must be rising (new money), which is the
    user's stated OI-velocity confirmation. Returns a thesis dict or None.

    Scoring (max 9): structure 3 · ER quality 2 · OI velocity 2 · pullback quality 2
    """
    min_oi_vel = float(inputs.get("minOiVelocityPct", 0.35))
    strong_oi_vel = float(inputs.get("strongOiVelocityPct", 1.5))
    max_pullback = float(inputs.get("maxPullbackPct", 4.0))
    ideal_pullback = float(inputs.get("idealPullbackPct", 1.2))

    if oi_vel is None:
        return None                      # no OI baseline yet — never guess the confirmation away
    if oi_vel < min_oi_vel:
        return None                      # flat/falling OI: short-covering, not new commitment

    direction = "LONG" if struct == "UP" else "SHORT"
    score, reasons = 3, [f"4-hour trend is {struct} (strength {strength:.2f})",
                         f"clean directional move (efficiency {er:.2f})"]
    score += 2 if er >= float(inputs.get("strongErThreshold", 0.55)) else 1

    if oi_vel >= strong_oi_vel:
        score += 2
        reasons.append(f"open interest up {oi_vel:.2f}%, strong new money")
    else:
        score += 1
        reasons.append(f"open interest rising {oi_vel:.2f}%")

    pb = pullback_pct(candles_4h, direction)
    if pb > max_pullback:
        return None                      # too deep to still be a continuation — that's a reversal
    if pb >= ideal_pullback:
        score += 2
        reasons.append(f"entering on a {pb:.2f}% pullback, not the extreme")
    else:
        reasons.append(f"shallow {pb:.2f}% pullback (near the extreme)")

    return {"coin": coin, "direction": direction, "score": score, "regime": "TREND",
            "er": round(er, 4), "structure": struct, "oi_velocity_pct": round(oi_vel, 4),
            "pullback_pct": round(pb, 4), "range_pos": None,
            "funding_apr": None, "reasons": reasons}


def range_thesis(coin, candles_4h, er, funding_apr, inputs):
    """Band-edge fade. HARD GATE: skipped when |annualized funding| is extreme — crowded funding
    is the tell that a range is about to break, and a fade into a break is the losing side.

    Scoring (max 9): at the band edge 4 · range quality 2 · funding calm 2 · deep edge 1
    """
    bars = int(inputs.get("rangeBars", 24))
    buy_below = float(inputs.get("rangeBuyBelowPct", 0.22))
    sell_above = float(inputs.get("rangeSellAbovePct", 0.78))
    extreme_apr = float(inputs.get("extremeFundingApr", 35.0))
    calm_apr = float(inputs.get("calmFundingApr", 12.0))

    if funding_apr is None:
        return None                      # the gate is mandatory — no funding read, no range trade
    if abs(funding_apr) >= extreme_apr:
        return None                      # crowded: the range is about to break, don't fade it

    rb = range_bounds(candles_4h, bars)
    if rb is None:
        return None
    lo, hi, pos = rb

    if pos <= buy_below:
        direction = "LONG"
    elif pos >= sell_above:
        direction = "SHORT"
    else:
        return None                      # mid-range: no edge, and the fee is certain

    score = 4
    reasons = [f"price at the range {'low' if direction == 'LONG' else 'high'} ({pos:.2f})",
               f"price chopping sideways (efficiency {er:.2f})"]
    width_pct = (hi - lo) / lo * 100.0 if lo > 0 else 0.0
    if width_pct >= float(inputs.get("minRangeWidthPct", 3.0)):
        score += 2
        reasons.append(f"range is {width_pct:.1f}% wide, enough to trade")
    else:
        return None                      # too tight to clear fees on the round trip

    if abs(funding_apr) <= calm_apr:
        score += 2
        reasons.append(f"funding is calm ({funding_apr:+.1f}% APR)")
    else:
        score += 1
        reasons.append(f"funding elevated but not extreme ({funding_apr:+.1f}% APR)")

    if pos <= buy_below / 2 or pos >= 1 - (1 - sell_above) / 2:
        score += 1
        reasons.append("price deep at the edge of the range")

    return {"coin": coin, "direction": direction, "score": score, "regime": "RANGE",
            "er": round(er, 4), "structure": None, "oi_velocity_pct": None,
            "pullback_pct": None, "range_pos": round(pos, 4),
            "funding_apr": round(funding_apr, 3), "reasons": reasons}


def build_thesis(coin, candles_4h, oi_vel, funding_apr, inputs):
    """Classify the regime, then apply THAT regime's entry rule. None in the no-trade band."""
    regime, er, struct, strength = classify_regime(candles_4h, inputs)
    if regime == "TREND":
        return trend_thesis(coin, candles_4h, er, struct, strength, oi_vel, inputs)
    if regime == "RANGE":
        return range_thesis(coin, candles_4h, er, funding_apr, inputs)
    return None


def margin_tier_pct(score, base_pct):
    """Conviction sizing on the PERCENT scale (base_pct is a PERCENT in (0,100])."""
    if score >= 8:
        return base_pct * 1.5
    if score >= 6:
        return base_pct * 1.25
    return base_pct
