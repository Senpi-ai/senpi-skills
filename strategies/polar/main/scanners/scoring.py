"""POLAR — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Polar producer's `build_eth_thesis` +
multi-factor scoring (SKILL.md v5.0.0, thesis v4.2.0). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same market snapshot. Behaviour-preserving quirks from v2 are kept and
flagged `# v2-quirk`; fix them only as a separate, labelled change AFTER the
port is validated.

POLAR IS SM-LED (unlike kodiak, which is structure-led): the smart-money lean
picks the side FIRST and is a HARD gate (pct/traders/cc_15m floors); 4h/1h
structure must then AGREE with that side. The whole pre-score gate cascade and
the scoring weights differ from kodiak — do NOT reuse kodiak's scoring.py here.

Single-asset, single-pass, unit-testable on plain candle lists. The clock-owned
quiet-hours decision lives in scan.py; this module stays pure."""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read dicts only; the list branch is defensive and never fires on dict candles,
# so it does not change v2 behaviour.

def _f(v, d=0.0):
    if v is None:
        return d
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


# ── indicators (ported verbatim from v2 polar-producer.py) ──

def mom(candles, n_bars=1):
    """% change over the last n_bars (price_momentum)."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """(label, strength): fraction of strictly-higher-lows (BULLISH) /
    strictly-lower-highs (BEARISH) over the last `lookback` bars.

    v2-quirk: polar uses STRICT > / < (lows[i] > lows[i-1]), whereas kodiak
    used >= / <=. Reproduced verbatim — strictness changes which bars count.
    v2-quirk: the NEUTRAL branch returns strength 0 (not max(bullish,bearish))."""
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


def rsi(closes, period=14):
    """Wilder-less RSI over the LAST `period` deltas.

    v2-quirk: polar builds gains/losses over ALL deltas then slices the last
    `period` (g, l = gains[-period:], losses[-period:]) — i.e. the MOST RECENT
    window. This differs from kodiak's rsi (which used the FIRST period+1
    closes). Reproduced verbatim from polar's calc_rsi — do not "fix"."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def get_leverage(score, tiers):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score.

    v2 LEVERAGE_TIERS: [[17,10],[15,7],[14,5]], DEFAULT_LEVERAGE 5. Note the v2
    fall-through default (5) when score < 14 — but the runtime never reaches here
    below minScore (12), and below 14 the fall-through still yields 5x."""
    for t in tiers:
        if score >= t[0]:
            return int(t[1])
    return 5  # v2 DEFAULT_LEVERAGE


def leverage_label(score, tiers):
    """Tier label for telemetry, mirroring v2 get_leverage_tier labels."""
    labels = {10: "apex", 7: "conviction", 5: "standard"}
    lev = get_leverage(score, tiers)
    # below the lowest tier min_score, v2 returns ("default")
    if tiers and score < tiers[-1][0]:
        return "default"
    return labels.get(lev, "default")


# ── the thesis (gates + multi-factor score), ported verbatim from build_eth_thesis ──

def build_thesis(c5, c15, c1h, c4h, funding, oi_change_1h,
                 btc_mom_15m, btc_mom_1h, sm, inputs):
    """Returns a thesis dict (with `score`) or None if any gate blocks.

    `sm` is the smart-money dict {direction, pct, traders, cc_15m, cc_1h, cc_4h}
    or None (the caller fetches it). POLAR IS SM-LED: SM gates come FIRST and the
    SM direction is the candidate side; 4h/1h structure must then agree.

    `oi_change_1h` and the BTC momenta are pre-extracted by the caller (scan.py)
    so this stays pure."""
    min_ts_4h = _f(inputs.get("minTrendStrength4h", 0.75))
    min_mom_15m = _f(inputs.get("minMom15mPct", 0.1))
    rsi_long_max = _f(inputs.get("rsiMaxLong", 74))
    rsi_short_min = _f(inputs.get("rsiMinShort", 26))
    min_sm_conc = _f(inputs.get("minSmConcentration", 5.0))
    min_sm_traders = _f(inputs.get("minSmTraderCount", 30))
    min_sm_accel = _f(inputs.get("minSmAccelPct", 0.3))

    # ── HARD SM GATES (v2: SM picks the side FIRST) ──
    if not sm:
        return None  # no_sm_data
    if sm.get("direction") not in ("LONG", "SHORT"):
        return None  # sm_neutral
    if _f(sm.get("pct", 0)) < min_sm_conc:
        return None  # sm_weak
    if int(sm.get("traders", 0)) < min_sm_traders:
        return None  # sm_shallow
    if _f(sm.get("cc_15m", 0)) < min_sm_accel:
        return None  # sm_stale

    direction = sm["direction"]

    if len(c5) < 12 or len(c15) < 8 or len(c1h) < 8 or len(c4h) < 6:
        return None  # insufficient_candles

    # ── 4h structure (must agree with SM side) ──
    trend_4h, ts_4h = trend_structure(c4h)
    if trend_4h == "NEUTRAL":
        return None  # 4h_NEUTRAL
    if ts_4h < min_ts_4h:
        return None  # 4h_weak
    structural_dir = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if structural_dir != direction:
        return None  # direction_conflict

    # ── 1h confirmation ──
    trend_1h, _ = trend_structure(c1h)
    if trend_1h != trend_4h:
        return None  # 1h_vs_4h mismatch

    # ── 15m momentum + base-tech floor ──
    mom_5m = mom(c5, 1)
    mom_15m = mom(c15, 1)
    mom_1h = mom(c1h, 2)
    mom_4h = mom(c4h, 1)
    if direction == "LONG" and mom_15m < min_mom_15m:
        return None
    if direction == "SHORT" and mom_15m > -min_mom_15m:
        return None

    # ── RSI gate (v2 polar band 26-74) ──
    closes_1h = [_close(c) for c in c1h]
    r = rsi(closes_1h)
    if direction == "LONG" and r > rsi_long_max:
        return None
    if direction == "SHORT" and r < rsi_short_min:
        return None

    # ── base-tech floor (v2-quirk: aligned_5m alone clears, no strong_15m required) ──
    strong_15m = abs(mom_15m) > min_mom_15m * 2
    aligned_5m = (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0)
    if not (strong_15m or aligned_5m):
        return None  # base_tech_weak

    # ── ALL GATES PASSED — SCORE (v3.0.6 weights, ported verbatim) ──
    score = 0
    reasons = []

    score += 3
    reasons.append(f"4h trend {trend_4h.lower()}, {ts_4h:.0%} strength")
    score += 2
    reasons.append(f"1h trend confirms, {mom_1h:+.2f}%")
    if strong_15m:
        score += 1
        reasons.append(f"strong 15m move, {mom_15m:+.2f}%")
    # v2-quirk: polar scores 4TF_aligned on aligned_5m ALONE (kodiak required
    # aligned_5m AND strong_15m). Reproduced verbatim.
    if aligned_5m:
        score += 1
        reasons.append("all four timeframes aligned")

    # smart-money concentration (v2 polar pct thresholds: 15/10/5)
    sm_pct = _f(sm.get("pct", 0))
    sm_traders = int(sm.get("traders", 0))
    sm_cc15m = _f(sm.get("cc_15m", 0))
    sm_cc1h = _f(sm.get("cc_1h", 0))
    sm_cc4h = _f(sm.get("cc_4h", 0))
    if sm_pct >= 15:
        score += 3
        reasons.append(f"smart money dominant, {sm_pct:.1f}% ({sm_traders} traders)")
    elif sm_pct >= 10:
        score += 2
        reasons.append(f"smart money strong, {sm_pct:.1f}% ({sm_traders} traders)")
    elif sm_pct >= 5:
        score += 1
        reasons.append(f"smart money aligned, {sm_pct:.1f}% ({sm_traders} traders)")

    # SM 15m velocity (v2 polar thresholds: 2.0 / 0.5)
    if sm_cc15m > 2.0:
        score += 2
        reasons.append(f"smart money piling in, +{sm_cc15m:.2f} in 15m")
    elif sm_cc15m > 0.5:
        score += 1
        reasons.append(f"smart money adding, +{sm_cc15m:.2f} in 15m")

    # SM accelerating (15m velocity exceeds 1h velocity)
    if sm_cc15m > 0 and sm_cc1h > 0 and sm_cc15m > sm_cc1h:
        score += 1
        reasons.append(f"smart money accelerating: 15m {sm_cc15m:.2f} vs 1h {sm_cc1h:.2f}")

    # SM depth
    if sm_traders >= 100:
        score += 1
        reasons.append(f"deep smart-money crowd, {sm_traders} traders")

    # funding (v2-quirk: polar uses sign-only thresholds funding<0 / >0, and a
    # crowded penalty at +/-0.005 — NOT kodiak's -0.0001 / 0.0005). Verbatim.
    if direction == "LONG" and funding < 0:
        score += 2
        reasons.append(f"funding pays longs ({funding:+.4f})")
    elif direction == "SHORT" and funding > 0:
        score += 2
        reasons.append(f"funding pays shorts ({funding:+.4f})")
    elif (direction == "LONG" and funding > 0.005) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append(f"funding crowded ({funding:+.4f})")

    # OI velocity (polar-specific factor; kodiak has none). oi_change_1h is the
    # oi_velocity.oi_change_pct_1h field extracted by the caller, or None.
    oi_change = None
    if oi_change_1h is not None:
        oi_change = _f(oi_change_1h)
        if oi_change > 5:
            score += 2
            reasons.append(f"open interest surging, {oi_change:+.1f}% in 1h")
        elif oi_change > 2:
            score += 1
            reasons.append(f"open interest rising, {oi_change:+.1f}% in 1h")
        elif oi_change < -3:
            score -= 1
            reasons.append(f"open interest draining, {oi_change:+.1f}% in 1h")

    # BTC correlation (v2-quirk: polar requires BOTH 15m AND 1h to agree;
    # kodiak only checked 1h > 0.3). Verbatim.
    if btc_mom_15m is not None and btc_mom_1h is not None:
        btc_agrees = (direction == "LONG" and btc_mom_15m > 0 and btc_mom_1h > 0) or \
                     (direction == "SHORT" and btc_mom_15m < 0 and btc_mom_1h < 0)
        if btc_agrees:
            score += 1
            reasons.append(f"BTC moving the same way, {btc_mom_1h:+.2f}% in 1h")

    # RSI room
    if (direction == "LONG" and r < 55) or (direction == "SHORT" and r > 45):
        score += 1
        reasons.append(f"RSI at {r:.0f}, room to run")

    # 4h momentum bonus (v2-quirk: polar uses abs(mom_4h) > 1.0 — direction-
    # agnostic bonus; kodiak required directional agreement). Verbatim.
    if abs(mom_4h) > 1.0:
        score += 1
        reasons.append(f"4h momentum {mom_4h:+.1f}%")

    # move-exhaustion / tiring penalty (v2 polar thresholds 4.0 / 2.5,
    # direction-gated; NOT kodiak's 5.0 / 3.0). Verbatim.
    if abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"move may be exhausted, {mom_4h:+.1f}% in 4h")
    elif abs(mom_4h) >= 2.5:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"move tiring, {mom_4h:+.1f}% in 4h")

    # v2-quirk: polar has NO time-of-day modifier (kodiak does). Quiet-hours is a
    # scan-level emission gate (FP-001), handled in scan.py, not a score delta.

    return {
        "direction": direction,
        "score": round(score, 2),
        "trend_4h": trend_4h,
        "trend_strength_4h": round(ts_4h, 3),
        "trend_1h": trend_1h,
        "mom_5m": round(mom_5m, 3),
        "mom_15m": round(mom_15m, 3),
        "mom_1h": round(mom_1h, 3),
        "mom_4h": round(mom_4h, 3),
        "funding": funding,
        "oi_change_1h": oi_change,
        "rsi": round(r, 1),
        "btc_mom_15m": None if btc_mom_15m is None else round(_f(btc_mom_15m), 3),
        "btc_mom_1h": None if btc_mom_1h is None else round(_f(btc_mom_1h), 3),
        "sm_pct": round(sm_pct, 2),
        "sm_traders": int(sm_traders),
        "sm_cc15m": round(sm_cc15m, 3),
        "sm_cc1h": round(sm_cc1h, 3),
        "sm_cc4h": round(sm_cc4h, 3),
        "reasons": reasons,
    }
