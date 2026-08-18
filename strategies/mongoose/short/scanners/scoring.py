"""MONGOOSE — pure thematic thesis math (no I/O, no MCP, no clock). Shared verbatim by both
books; direction is passed in (`leg` = "long" for the on-chain rails book, "short" for the
legacy hedge book). A faithful port of the v2 mongoose-producer.py scoring — the gates + the
point weights + the per-name conviction sizing weights are copied EXACTLY (marked `# v2-quirk`
where the v2 behaviour is load-bearing and must not be redesigned). Unit-testable on plain
candle lists.

THE THESIS (v2 score_thematic): unlike a pure cross-sectional book (Cougar), Mongoose's
universe is THEMATIC — every name is already a thesis pick (a "have" or a "have-not"). So the
HARD GATE is ABSOLUTE trend (long a have only while it is actually trending up; short a
have-not only while it is actually rolling over). Cross-sectional excess (vs the leg-universe
mean) is a SCORE MODIFIER / TIEBREAKER, not a disqualifier — it tilts size and ranking toward
the strongest leaders / weakest laggards without benching a genuinely-trending winner.
"""

# v2-quirk: wire-score normaliser. v2 emitted min(score/9.0, 1.0) as the [0,1] wire score.
# The 3.0 scaffold owns the wire envelope, so we keep the raw integer score on data{} and only
# expose NORM_DIV if a caller wants the v2-equivalent normalised score.
NORM_DIV = 9.0

# v2 per-group sizing-weight fallbacks (mongoose_config defaults). The runtime config's
# sizingWeights override these; "_default" is the per-leg fallback. Kept here as the in-scoring
# defaults so the pure math has no I/O dependency.
_HAVES_WEIGHTS = {
    "HYPE": 1.3,    # the on-chain venue + token — the core of the thesis
    "CRCL": 1.2,    # stablecoin leader — the clearest on-chain-finance winner
    "COIN": 1.0,
    "HOOD": 1.0,
    "MSTR": 0.7,    # levered BTC proxy — double-count risk, sized down
    "PURRDAT": 0.6,  # small / levered HYPE proxy
    "_default": 1.0,
}
_HAVE_NOTS_WEIGHTS = {"SP500": 1.2, "BX": 0.8, "_default": 1.0}


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


def _bare(asset):
    """Bare ticker for sizing-weight lookups: 'xyz:CRCL' -> 'CRCL'. Verbatim v2 _bare()."""
    a = str(asset or "")
    return (a.split(":", 1)[1] if ":" in a else a).upper()


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
    """Thematic absolute-trend score for one curated name, given its excess return vs the
    leg-universe mean (`excess`) and its own 24h return (`own24h`). Returns a thesis dict or
    None. The point weights + skip gates are copied VERBATIM from the v2 mongoose-producer
    score_thematic() — do NOT redesign.

    leg == "long":  long an on-chain rail while it is trending up (HARD GATE: never long a
                    confirmed 4h downtrend); excess is a bonus tiebreaker, never disqualifies.
    leg == "short": short a legacy incumbent while it is rolling over (HARD GATE: never short
                    a confirmed 4h uptrend); capitulation guard so it never shorts an exhausted
                    bottom.
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
            reasons.append(f"4h making higher lows {s4:.0%} of the time")
        else:
            score += 1
            reasons.append("4h trend flat")
        if trend1 == "BULLISH":
            score += 1
            reasons.append(f"1h making higher lows {s1:.0%} of the time")
        elif trend1 == "BEARISH":
            score -= 1
            reasons.append("1h making lower highs")
        # absolute momentum
        if own >= 0:
            score += 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        else:
            score -= 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        # relative strength = TIEBREAKER (bonus only; never disqualifies a have)
        if excess >= 2 * rs_thresh:
            score += 2
            reasons.append(f"beating the group average by {excess:+.1f}%")
        elif excess >= rs_thresh:
            score += 1
            reasons.append(f"beating the group average by {excess:+.1f}%")
        elif excess < -rs_thresh:
            reasons.append(f"trailing the group average by {excess:+.1f}%")  # noted, not penalized
        rsi_ob = float(inputs.get("rsiOverbought", 84))
        if rsi > rsi_ob:                                    # v2-quirk: blow-off guard (don't chase)
            score -= 2
            reasons.append(f"RSI stretched at {rsi:.0f}")
    else:  # short
        # ── HARD GATE: never short a confirmed uptrend ──
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 3
            reasons.append(f"4h making lower highs {s4:.0%} of the time")
        else:
            score += 1
            reasons.append("4h trend flat")
        if trend1 == "BEARISH":
            score += 1
            reasons.append(f"1h making lower highs {s1:.0%} of the time")
        elif trend1 == "BULLISH":
            score -= 1
            reasons.append("1h making higher lows")
        if own <= 0:
            score += 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        else:
            score -= 1
            reasons.append(f"price {own:+.1f}% over the last 24h")
        if excess <= -2 * rs_thresh:
            score += 2
            reasons.append(f"trailing the group average by {excess:+.1f}%")
        elif excess <= -rs_thresh:
            score += 1
            reasons.append(f"trailing the group average by {excess:+.1f}%")
        elif excess > rs_thresh:
            reasons.append(f"beating the group average by {excess:+.1f}%")
        rsi_os = float(inputs.get("rsiOversold", 18))
        if rsi < rsi_os:                                    # v2-quirk: capitulation guard
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


def sizing_weight(asset, leg, inputs):
    """Per-group conviction multiplier (HYPE 1.3x large, PURRDAT 0.6x small; SP500 1.2x core).
    Keyed by BARE ticker; falls back to '_default'. Clamped to [0.1, 3.0]. Verbatim v2
    sizing_weight(). The runtime config's sizingWeights override the per-leg defaults."""
    default = _HAVES_WEIGHTS if leg == "long" else _HAVE_NOTS_WEIGHTS
    weights = inputs.get("sizingWeights") or default
    if not isinstance(weights, dict):
        weights = default
    try:
        w = float(weights.get(_bare(asset), weights.get("_default", 1.0)))
    except (TypeError, ValueError):
        w = 1.0
    return max(0.1, min(3.0, w))


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: each name caps at its
    venue max (over-leveraging is a venue reject), so this clamp is load-bearing, not cosmetic.
    Verbatim v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
