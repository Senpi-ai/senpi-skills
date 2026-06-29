"""EEL — pure thematic-trend thesis math (no I/O, no MCP, no clock). Shared verbatim by
both books; direction is passed in (`leg` = "long" for the AI-power complex, "short" for
crude oil). A faithful port of the v2 eel-producer.py score_thematic() — the gates + the
point weights + the per-group sizing-weight resolution are copied EXACTLY (marked
`# v2-quirk` where the v2 behaviour is load-bearing and must not be redesigned).

UNLIKE a pure cross-sectional book (cougar): Eel's universe is THEMATIC — every name is
already a thesis pick (a "have" / "have-not"). So the HARD GATE is ABSOLUTE trend (long a
have only while it is actually trending up; short a have-not only while it is rolling over),
and cross-sectional excess vs the leg-universe mean is a SCORE MODIFIER / tiebreaker that
tilts size + ranking but NEVER benches a genuinely-trending name. Unit-testable on plain
candle lists."""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score.
# The 3.0 scaffold owns the wire envelope, so we keep the raw integer score on data{} and
# only expose NORM_DIV for callers wanting the v2-equivalent normalised score.
NORM_DIV = 9.0


def _close(c):
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return float(c[4])                                  # [t,o,h,l,c,v] -> close
    if isinstance(c, dict):
        return float(c.get("close", c.get("c", 0)) or 0)
    return 0.0


def _high(c):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return float(c[2])                                  # [t,o,h,l,c,v] -> high
    if isinstance(c, dict):
        return float(c.get("high", c.get("h", 0)) or 0)
    return 0.0


def _low(c):
    if isinstance(c, (list, tuple)) and len(c) >= 4:
        return float(c[3])                                  # [t,o,h,l,c,v] -> low
    if isinstance(c, dict):
        return float(c.get("low", c.get("l", 0)) or 0)
    return 0.0


def trend_structure(candles, lookback=6):
    """Higher-lows / lower-highs structure over the last `lookback` candles. Verbatim v2."""
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    """Wilder-less simple-average RSI. Verbatim v2."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def score_thematic(asset, candles_1h, candles_4h, excess, own24h, leg, inputs):
    """Thematic-trend score for one name, given its excess return vs the leg-universe mean
    (`excess`) and its own 24h return (`own24h`). Returns a thesis dict or None. The point
    weights + skip gates are copied VERBATIM from the v2 eel-producer score_thematic() — do
    NOT redesign.

    leg == "long":  long an AI-power "have"  — gate: never long a confirmed 4h downtrend
    leg == "short": short a crude "have-not" — gate: never short a confirmed 4h uptrend,
                    capitulation guard (never short an exhausted bottom).
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    trend4, s4 = trend_structure(candles_4h)
    trend1, s1 = trend_structure(candles_1h)
    rsi = calc_rsi(closes1)
    rs_thresh = float(inputs.get("rsThresholdPct", 3.0))

    score = 0
    reasons = []

    if leg == "long":
        # ── HARD GATE: never long a confirmed downtrend ──
        if trend4 == "BEARISH":                             # v2-quirk
            return None
        if trend4 == "BULLISH":
            score += 3
            reasons.append(f"4h_bullish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h_bullish_{s1:.0%}")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h_bearish")
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"abs_up_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)  # v2-quirk
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"rs_lead_{excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"rs_lag_{excess:+.1f}%")        # noted, NOT penalized
        rsi_ob = float(inputs.get("rsiOverbought", 82))
        if rsi > rsi_ob:                                     # v2-quirk: blow-off guard
            score -= 2
            reasons.append(f"rsi_blowoff_{rsi:.0f}")
    else:  # short
        # ── HARD GATE: never short a confirmed uptrend ──
        if trend4 == "BULLISH":                             # v2-quirk
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h_bearish_{s4:.0%}")
        else:
            score += 1
            reasons.append("4h_neutral")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h_bearish_{s1:.0%}")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h_bullish")
        if own <= 0:
            score += 1
            reasons.append(f"abs_dn_{own:+.1f}%")
        else:
            score -= 1
            reasons.append(f"abs_up_{own:+.1f}%")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"rs_lag_{excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"rs_lead_{excess:+.1f}%")        # noted, NOT penalized
        rsi_os = float(inputs.get("rsiOversold", 18))
        if rsi < rsi_os:                                     # v2-quirk: capitulation guard
            score -= 2
            reasons.append(f"rsi_capitulation_{rsi:.0f}")

    return {
        "coin": asset,
        "direction": "LONG" if leg == "long" else "SHORT",
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "trend1h": trend1,
        "excess": excess,
        "own24h": own,
    }


def _bare(asset):
    """Bare ticker for sizing-weight lookups: 'xyz:URNM' -> 'URNM'. Verbatim v2 _bare()."""
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


def sizing_weight(asset, sizing_weights):
    """Per-group conviction multiplier (URNM 1.2x, COPPER 1.1x, BE 0.6x, …). Keyed by bare
    ticker; falls back to '_default'. Clamped to [0.1, 3.0]. Verbatim v2 sizing_weight()."""
    if not isinstance(sizing_weights, dict):
        sizing_weights = {"_default": 1.0}
    try:
        w = float(sizing_weights.get(_bare(asset), sizing_weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: XYZ commodities cap
    at the venue (COPPER 20x, URNM/NATGAS/BE/USAR 10x, BRENTOIL/CL 20x) — over-leveraging a
    name is a venue reject, so this clamp is load-bearing. Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
