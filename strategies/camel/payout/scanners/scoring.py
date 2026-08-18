"""CAMEL — pure funding-carry thesis math (no I/O, no MCP, no clock). Shared VERBATIM by
both books (this file is byte-identical in harvest/ and payout/); the direction is passed
in via `leg` ("harvest" = SHORT the most-positive-funding names that collect funding;
"payout" = LONG the most-negative-funding names that get paid to hold). A faithful port of
the v2 camel-producer.py scoring — the funding tiers, the 4h-trend gates, the RSI
confirmation, and the 24h roll-over/bounce points are copied EXACTLY (marked `# v2-quirk`
where the v2 behaviour is load-bearing and must not be redesigned). Unit-testable on plain
candle lists.

The edge is CARRY: funding magnitude drives the base score (1/2/3 tiers), with 4h trend +
RSI + 24h direction as EXHAUSTION confirmation and a hard disqualify on a fresh trend
against the carry (squeeze risk on the harvest short / knife-catch on the payout long).
"""

# Funding is HOURLY decimal in the instrument context; annualized = x 8760 (v2 HOURS_PER_YEAR).
HOURS_PER_YEAR = 8760.0

# v2-quirk: wire-score normaliser. v2 emitted min(score/8.0, 1.0) as the [0,1] wire score.
# The 3.0 scaffold owns the wire envelope, so we keep the raw integer score on data{} and
# only expose NORM_DIV for a caller that wants the v2-equivalent normalised score.
NORM_DIV = 8.0


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
    """Higher-lows = BULLISH, lower-highs = BEARISH over the last `lookback`. Verbatim v2."""
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


def score_carry(asset, candles_1h, candles_4h, fund, own24h, leg, inputs):
    """Score one carry candidate. `fund` is the asset's HOURLY funding (decimal); `own24h`
    is its 24h % return (from the instrument context). Returns a thesis dict or None
    (None = disqualified). The point weights + skip gates are copied VERBATIM from the v2
    camel-producer score_carry() — do NOT redesign.

    leg == "harvest": SHORT the most-POSITIVE funding (short collects); needs fund >= floor;
                      never short a fresh 4h uptrend (squeeze risk dwarfs the carry).
    leg == "payout":  LONG the most-NEGATIVE funding (long gets paid); needs fund <= -floor;
                      never long a fresh 4h downtrend (don't catch a knife).
    """
    if len(candles_1h) < 8 or len(candles_4h) < 6:          # v2-quirk: needs >=8 1h, >=6 4h
        return None
    closes1 = [_close(c) for c in candles_1h]
    price = closes1[-1]
    own = own24h if own24h is not None else 0.0

    floor = float(inputs.get("fundingFloorHourly", 0.00003))
    t2 = float(inputs.get("fundingTier2Hourly", 0.00006))
    t3 = float(inputs.get("fundingTier3Hourly", 0.0001))
    ann = fund * HOURS_PER_YEAR * 100.0                     # annualized %

    trend4, s4 = trend_structure(candles_4h)
    rsi = calc_rsi(closes1)

    score = 0
    reasons = []

    if leg == "harvest":
        # v2-quirk: need meaningful POSITIVE funding to short-collect.
        if fund < floor:
            return None
        if fund >= t3:
            score += 3
        elif fund >= t2:
            score += 2
        else:
            score += 1
        reasons.append(f"funding {ann:+.0f}%/yr pays shorts")
        # v2-quirk: never short a fresh uptrend — squeeze risk dwarfs the carry.
        if trend4 == "BULLISH":
            return None
        if trend4 == "BEARISH":
            score += 2
            reasons.append(f"4h trend bearish at {s4:.0%} strength")
        else:
            score += 1
            reasons.append("4h trend flat")
        rsi_ob = float(inputs.get("rsiOverbought", 70))
        if rsi >= rsi_ob:
            score += 1
            reasons.append(f"RSI overbought at {rsi:.0f}")
        if own <= 0:
            score += 1
            reasons.append(f"price rolling over, {own:+.1f}% in 24h")
        elif own >= 5:
            score -= 1
            reasons.append(f"still rallying, {own:+.1f}% in 24h")
    else:  # payout
        # v2-quirk: need meaningful NEGATIVE funding to long-collect.
        if fund > -floor:
            return None
        if fund <= -t3:
            score += 3
        elif fund <= -t2:
            score += 2
        else:
            score += 1
        reasons.append(f"funding {ann:+.0f}%/yr pays longs")
        # v2-quirk: never long a fresh downtrend — don't catch a knife.
        if trend4 == "BEARISH":
            return None
        if trend4 == "BULLISH":
            score += 2
            reasons.append(f"4h trend bullish at {s4:.0%} strength")
        else:
            score += 1
            reasons.append("4h trend flat")
        rsi_os = float(inputs.get("rsiOversold", 30))
        if rsi <= rsi_os:
            score += 1
            reasons.append(f"RSI oversold at {rsi:.0f}")
        if own >= 0:
            score += 1
            reasons.append(f"price bouncing, {own:+.1f}% in 24h")
        elif own <= -5:
            score -= 1
            reasons.append(f"still falling, {own:+.1f}% in 24h")

    return {
        "coin": asset,
        "direction": "SHORT" if leg == "harvest" else "LONG",
        "score": score,
        "reasons": reasons,
        "price": price,
        "rsi": rsi,
        "trend4h": trend4,
        "fundingAnnPct": ann,
        "own24h": own,
    }


def clamp_leverage(desired, venue_max):
    """Clamp the desired leverage to the asset's HL venue max. v2-quirk: per-name venue cap
    is load-bearing (over-leveraging a thin name is a venue reject), not cosmetic. Verbatim
    v2 clamp_leverage()."""
    try:
        venue = int(venue_max)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))
