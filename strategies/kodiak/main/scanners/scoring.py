"""KODIAK — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Kodiak producer's `build_sol_thesis` +
multi-factor scoring (SKILL.md v7.0.0). The math/indexing is reproduced VERBATIM
so a fidelity harness can diff this against the v2 producer on the same market
snapshot. Behaviour-preserving quirks from v2 are kept and flagged `# v2-quirk`;
fix them only as a separate, labelled change AFTER the port is validated.

Single-asset, single-pass, unit-testable on plain candle lists. `tod_modifier`
takes the hour as an argument (the caller owns the clock) so this stays pure."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

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


# ── indicators (ported verbatim from v2) ──

def mom(candles, n_bars=1):
    """% change over the last n_bars (price_momentum)."""
    if len(candles) < n_bars + 1:
        return 0.0
    cur = _close(candles[-1])
    prev = _close(candles[-1 - n_bars])
    if prev <= 0:
        return 0.0
    return (cur - prev) / prev * 100


def trend_structure(candles, lookback=6):
    """(label, strength): fraction of higher-lows (BULLISH) / lower-highs (BEARISH)
    over the last `lookback` bars. Entry requires strength >= 0.75."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    recent = candles[-lookback:]
    highs = [_high(c) for c in recent]
    lows = [_low(c) for c in recent]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] >= lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] <= highs[i - 1])
    bullish = higher_lows / (lookback - 1)
    bearish = lower_highs / (lookback - 1)
    if bullish >= 0.6:
        return "BULLISH", bullish
    if bearish >= 0.6:
        return "BEARISH", bearish
    return "NEUTRAL", max(bullish, bearish)


def rsi(closes, period=14):
    # v2-quirk: uses the FIRST period+1 closes (closes[0..period]), not the most
    # recent. Reproduced verbatim for fidelity — do not "fix" inside the port.
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def tod_modifier(hour):
    """UTC hour -> (score_delta, reason|None). Caller passes the hour (keeps this pure)."""
    if 4 <= hour < 14:
        return 1, "active trading hours"
    if hour >= 18 or hour < 2:
        return -2, "choppy off-hours window"
    return 0, None


def get_leverage(score, tiers):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score."""
    for t in tiers:
        if score >= t[0]:
            return int(t[1])
    return int(tiers[-1][1]) if tiers else 5


# ── the thesis (gates + multi-factor score), ported verbatim from build_sol_thesis ──

def build_thesis(c5, c15, c1h, c4h, funding, oi, btc_mom_1h, sm, hour, inputs):
    """Returns a thesis dict (with `score`) or None if any gate blocks. `sm` is the
    smart-money dict {direction, pct, traders, cc_15m} or None (the caller fetches it)."""
    min_ts_4h = float(inputs.get("minTrendStrength4h", 0.75))
    min_mom_15m = float(inputs.get("minMom15mPct", 0.1))
    rsi_long_max = float(inputs.get("rsiMaxLong", 72))
    rsi_short_min = float(inputs.get("rsiMinShort", 28))

    if len(c5) < 12 or len(c15) < 8 or len(c1h) < 8 or len(c4h) < 6:
        return None

    # ── 4h structure ──
    trend_4h, ts_4h = trend_structure(c4h)
    if trend_4h == "NEUTRAL" or ts_4h < min_ts_4h:
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── 1h confirmation ──
    trend_1h, _ = trend_structure(c1h)
    if trend_1h != trend_4h:
        return None

    # ── 15m momentum + base-tech floor ──
    mom_5m = mom(c5, 1)
    mom_15m = mom(c15, 1)
    mom_1h = mom(c1h, 2)
    mom_4h = mom(c4h, 1)
    if direction == "LONG" and mom_15m < min_mom_15m:
        return None
    if direction == "SHORT" and mom_15m > -min_mom_15m:
        return None
    strong_15m = abs(mom_15m) > min_mom_15m * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return None

    # ── RSI gate ──
    closes_1h = [_close(c) for c in c1h]
    r = rsi(closes_1h)
    if direction == "LONG" and r > rsi_long_max:
        return None
    if direction == "SHORT" and r < rsi_short_min:
        return None

    # ── ALL GATES PASSED — SCORE ──
    score = 0
    reasons = []
    score += 3
    reasons.append(f"4h trend {trend_4h.lower()} ({ts_4h:.0%} of bars)")
    score += 2
    reasons.append(f"1h move {mom_1h:+.2f}% confirms the trend")
    if strong_15m:
        score += 1
        reasons.append(f"strong 15m move {mom_15m:+.2f}%")
    if aligned_5m and strong_15m:
        score += 1
        reasons.append("all 4 timeframes aligned")

    # smart-money concentration
    sm_aligned = False
    sm_pct = sm_traders = sm_cc15m = 0
    if sm and sm.get("direction") == direction:
        sm_aligned = True
        sm_pct = _f(sm.get("pct", 0))
        sm_traders = int(sm.get("traders", 0))
        sm_cc15m = _f(sm.get("cc_15m", 0))
        if sm_pct >= 70 and sm_traders >= 50:
            score += 3
            reasons.append(f"smart money dominant: {sm_pct:.1f}% ({sm_traders} traders)")
        elif sm_pct >= 60 and sm_traders >= 30:
            score += 2
            reasons.append(f"smart money strong: {sm_pct:.1f}% ({sm_traders} traders)")
        else:
            score += 1
            reasons.append(f"smart money aligned: {sm_pct:.1f}% ({sm_traders} traders)")
        if sm_cc15m > 0.5:
            score += 2
            reasons.append(f"smart-money share surging +{sm_cc15m:.2f} in 15m")
        elif sm_cc15m > 0.2:
            score += 1
            reasons.append(f"smart-money share up +{sm_cc15m:.2f} in 15m")
        if sm_traders >= 80:
            score += 1
            reasons.append(f"deep bench: {sm_traders} smart-money traders")

    # funding
    if direction == "LONG" and funding < -0.0001:
        score += 2
        reasons.append(f"funding pays longs at {funding:+.4f}")
    elif direction == "SHORT" and funding > 0.0001:
        score += 2
        reasons.append(f"funding pays shorts at {funding:+.4f}")
    elif (direction == "LONG" and funding > 0.0005) or (direction == "SHORT" and funding < -0.0005):
        score -= 1
        reasons.append(f"funding shows a crowded side at {funding:+.4f}")

    # BTC correlation
    if (direction == "LONG" and btc_mom_1h > 0.3) or (direction == "SHORT" and btc_mom_1h < -0.3):
        score += 1
        reasons.append(f"BTC confirms with {btc_mom_1h:+.2f}% in 1h")

    # RSI room
    if (direction == "LONG" and r < 55) or (direction == "SHORT" and r > 45):
        score += 1
        reasons.append(f"RSI has room to run at {r:.0f}")

    # 4h momentum bonus / exhaustion penalty
    if (direction == "LONG" and mom_4h > 1.0) or (direction == "SHORT" and mom_4h < -1.0):
        score += 1
        reasons.append(f"4h momentum {mom_4h:+.1f}%")
    if direction == "LONG" and mom_4h > 5.0:
        score -= 2
        reasons.append(f"move may be exhausted at {mom_4h:+.1f}% in 4h")
    elif direction == "SHORT" and mom_4h < -5.0:
        score -= 2
        reasons.append(f"move may be exhausted at {mom_4h:+.1f}% in 4h")
    elif direction == "LONG" and mom_4h > 3.0:
        score -= 1
        reasons.append(f"move tiring at {mom_4h:+.1f}% in 4h")
    elif direction == "SHORT" and mom_4h < -3.0:
        score -= 1
        reasons.append(f"move tiring at {mom_4h:+.1f}% in 4h")

    # time-of-day
    tod_mod, tod_reason = tod_modifier(hour)
    score += tod_mod
    if tod_reason:
        reasons.append(tod_reason)

    return {
        "direction": direction,
        "score": round(score, 2),
        "trend_4h": trend_4h,
        "trend_strength_4h": round(ts_4h, 3),
        "trend_1h": trend_1h,
        "mom_15m": round(mom_15m, 3),
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "funding": funding,
        "oi": oi,
        "rsi": round(r, 1),
        "btc_mom_1h": round(btc_mom_1h, 3),
        "sm_aligned": sm_aligned,
        "sm_pct": round(_f(sm_pct), 2),
        "sm_traders": int(sm_traders),
        "sm_cc15m": round(_f(sm_cc15m), 3),
        "reasons": reasons,
    }
