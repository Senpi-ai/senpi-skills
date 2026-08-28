"""LION — pure two-speed (K-shaped) thematic thesis math (no I/O, no MCP, no clock).

Shared VERBATIM by both books; direction is passed in via `leg` ("long" for the
"haves" book, "short" for the "have-nots" book). A faithful port of the v2
lion-producer.py scoring — the ABSOLUTE-trend hard gate, the relative-strength
TIEBREAKER (bonus only, never a disqualifier), the point weights, the per-group
conviction sizing weights, and the venue-leverage clamp are copied EXACTLY
(marked `# v2-quirk` where the v2 behaviour is load-bearing and must not be
redesigned). Unit-testable on plain candle lists.

KEY DIFFERENCE vs cougar (do NOT confuse them): Lion's universe is THEMATIC — every
name is already a thesis pick (a "have" or a "have-not"). So the HARD GATE is
ABSOLUTE 4h trend (never long a confirmed downtrend / never short a confirmed
uptrend), and cross-sectional excess is a SCORE MODIFIER (tilts size + ranking)
that NEVER benches a genuinely-trending winner. Cougar, by contrast, DISQUALIFIES
the wrong-sign excess. The point weights also differ (Lion 4h_bullish=+3 with a
+1 neutral fallback; Cougar 4h_bullish=+2 with no neutral fallback). Ported per
the v2 lion-producer score_thematic() exactly.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire
# score. The Runtime 3.0 scaffold owns the wire envelope, so we keep the raw integer
# score on data{} and only expose NORM_DIV if a caller wants the v2-equivalent value.
NORM_DIV = 9.0

# Per-group sizing weights (conviction, NOT dollars) — verbatim v2 defaults. The
# producer (scan.py) overrides via inputs.sizingWeights; "_default" is the fallback.
HAVES_WEIGHTS = {
    "HYPE": 1.5,    # highest-conviction crypto winner
    "SOL": 0.6,     # modest crypto growth
    "SPCX": 0.6,    # frontier (xAI) but pre-IPO / 5x-capped / volatile -> smaller
    "QNT": 0.5,     # quantum, most speculative -> smallest
    "CBRS": 0.7,    # smaller-cap AI-chip
    "NBIS": 0.7,    # smaller-cap AI-cloud
    "_default": 1.0,  # core AI complex (megacap chips + hyperscalers) at full slot
}
HAVE_NOTS_WEIGHTS = {"SP500": 1.2, "_default": 0.7}


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


def bare(asset):
    """Bare ticker for sizing-weight + dedup lookups: 'xyz:NVDA' -> 'NVDA'.
    Verbatim v2 _bare()."""
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


def trend_structure(candles, lookback=6):
    """Higher-lows / lower-highs structure over the last `lookback` candles. Verbatim v2.

    v2-quirk: the BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1,
    counting STRICT (>) higher-lows / lower-highs. Strength = higher_lows/total
    (BULLISH) or lower_highs/total (BEARISH). Reproduced exactly."""
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
    """Trailing-window simple-average RSI. Verbatim v2 calc_rsi (gains[-period:])."""
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


def score_thematic(asset, candles_1h, candles_4h, own24h, excess, leg, inputs):
    """Two-speed thematic score for one name. ABSOLUTE trend is the GATE; relative
    strength (`excess` = own 24h return minus the leg-universe mean) is a TIEBREAKER.
    Returns a thesis dict or None. The point weights + gates are copied VERBATIM from
    the v2 lion-producer score_thematic() — do NOT redesign.

    leg == "long":  long a "have" only while its 4h trend is not BEARISH (hard gate);
                    relative strength is a bonus that never disqualifies a trending have.
    leg == "short": short a "have-not" only while its 4h trend is not BULLISH (hard gate);
                    relative weakness is a bonus.
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
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 3
            reasons.append(f"4h trend bullish ({s4:.0%} of bars)")
        else:
            score += 1
            reasons.append("4h trend neutral")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h trend bullish ({s1:.0%} of bars)")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h trend bearish")
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"up {own:+.1f}% in 24h")
        else:
            score -= 1
            reasons.append(f"down {own:+.1f}% in 24h")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"leading its peers by {excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"leading its peers by {excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"lagging its peers by {excess:+.1f}%")        # noted, not penalized
        rsi_ob = float(inputs.get("rsiOverbought", 82))
        if rsi > rsi_ob:
            score -= 2
            reasons.append(f"RSI overheated at {rsi:.0f}")
    else:  # short
        # ── HARD GATE: never short a confirmed uptrend ──
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h trend bearish ({s4:.0%} of bars)")
        else:
            score += 1
            reasons.append("4h trend neutral")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h trend bearish ({s1:.0%} of bars)")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h trend bullish")
        if own <= 0:
            score += 1
            reasons.append(f"down {own:+.1f}% in 24h")
        else:
            score -= 1
            reasons.append(f"up {own:+.1f}% in 24h")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"lagging its peers by {excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"lagging its peers by {excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"leading its peers by {excess:+.1f}%")        # noted, not penalized
        rsi_os = float(inputs.get("rsiOversold", 18))
        if rsi < rsi_os:
            score -= 2
            reasons.append(f"RSI washed out at {rsi:.0f}")

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


def sizing_weight(asset, weights):
    """Per-group conviction multiplier (HYPE large, SOL modest, SP500 core).
    Keyed by bare ticker; falls back to '_default'. Clamped to [0.1, 3.0]. Verbatim
    v2 sizing_weight()."""
    if not isinstance(weights, dict):
        weights = HAVES_WEIGHTS
    try:
        w = float(weights.get(bare(asset), weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


def clamp_leverage(desired, venue_max):
    """Clamp desired leverage to the asset's HL venue max. v2-quirk: cross-asset names
    cap LOW at the venue (SPCX 5x, equities vary) — over-leveraging is a venue reject,
    so this clamp is load-bearing, not cosmetic. Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
