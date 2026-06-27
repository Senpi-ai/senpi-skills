"""BISON — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Bison producer's `build_thesis` +
9-component conviction scoring (SKILL.md v3.0.1). The math/indexing is reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port is
validated.

Multi-asset, single-pass, unit-testable on plain candle lists. `sm` (smart-money
lean) is fetched by the caller and passed in, so this module stays pure.

The thesis differs structurally from a gated scorer (e.g. Kodiak): in Bison ALL
signals are SCORE CONTRIBUTORS, not hard gates. The only gate is the direction
waterfall (4h trend -> SM -> 1h momentum); if no direction resolves, return None.
minScore is applied by the CALLER (scan.py), not here — `build_thesis` returns a
thesis with `score` for every asset that resolves a direction."""


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


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ── indicators (ported verbatim from v2 bison-producer.py) ──

def price_momentum(candles, n_bars=1):
    """% change over the last n_bars. Verbatim from v2 price_momentum."""
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from v2.

    v2-quirk: thresholds are STRICT (>) for higher-lows / lower-highs counting,
    and the BULLISH/BEARISH gate is `>= total * 0.6` where total = lookback - 1.
    Reproduced exactly — do NOT switch to >= counting (Kodiak's variant uses >=,
    Bison uses strict >). The strength returned is higher_lows/total (BULLISH) or
    lower_highs/total (BEARISH)."""
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


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2."""
    if len(candles) < lookback + 2:
        return 0
    vols = [_vol(c) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


def calc_rsi(closes, period=14):
    """RSI. Verbatim from v2 calc_rsi.

    v2-quirk: uses the LAST `period` gains/losses (gains[-period:]), unlike
    Kodiak's port which uses the FIRST period+1 closes. Bison's is the more
    conventional trailing-window RSI. Reproduced exactly."""
    if len(closes) < period + 1:
        return 50
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


# ── conviction-scaled margin tier (ported verbatim from v2 main()) ──

def margin_tier_pct(score, base_pct):
    """Conviction-scaled margin PERCENT. Verbatim from v2 main():
      score >= 12 -> base * 1.5   (37.5% at base 25)
      score >= 10 -> base * 1.25  (31.25% at base 25)
      else        -> base         (25%)

    NOTE: in v2 `base_pct` was a FRACTION (marginPctBase=0.25) and the result was
    multiplied by account_value to get marginUsd. In the Runtime 3.0 port the
    runtime sizes from a PERCENT in (0,100], so `base_pct` here is 25 (PERCENT)
    and this returns a PERCENT. The TIER MULTIPLIERS (1.5 / 1.25 / 1.0) and the
    score CUTOFFS (12 / 10) are preserved verbatim."""
    if score >= 12:
        return base_pct * 1.5
    elif score >= 10:
        return base_pct * 1.25
    return base_pct


# ── the thesis (direction waterfall + 9-component score), ported verbatim ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, funding, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned ONLY when:
      - insufficient candle history (len(c1h) < 8 or len(c4h) < 4), OR
      - the direction waterfall resolves no direction.
    minScore is NOT applied here — the caller gates on thesis['score'].

    `sm` is the smart-money tuple (direction, pct) or (None, 0) — the caller
    fetches it (get_sm_direction)."""
    min_vol_trend = float(inputs.get("minVolTrendPct", 10))
    rsi_max_long = float(inputs.get("rsiMaxLong", 72))
    rsi_min_short = float(inputs.get("rsiMinShort", 28))

    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None

    price = _close(candles_15m[-1]) if candles_15m else 0

    trend_4h, trend_strength = trend_structure(candles_4h)
    trend_1h, trend_1h_strength = trend_structure(candles_1h)
    sm_dir, sm_pct = sm if sm else (None, 0)
    mom_1h = price_momentum(candles_1h, 2)

    # Direction waterfall: 4H trend -> SM direction -> 1H momentum (verbatim)
    direction = None
    direction_source = None
    if trend_4h == "BULLISH":
        direction = "LONG"
        direction_source = "4h_trend"
    elif trend_4h == "BEARISH":
        direction = "SHORT"
        direction_source = "4h_trend"
    elif sm_dir and sm_dir != "NEUTRAL":
        direction = sm_dir
        direction_source = "sm_direction"
    elif mom_1h > 0.5:
        direction = "LONG"
        direction_source = "1h_momentum"
    elif mom_1h < -0.5:
        direction = "SHORT"
        direction_source = "1h_momentum"

    if direction is None:
        return None

    score = 0
    reasons = []

    # 4H trend structure (+3 / -1)
    if trend_4h != "NEUTRAL":
        if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
            score += 3
            reasons.append(f"4h_{trend_4h.lower()}_{trend_strength:.0%}")
        else:
            score -= 1
            reasons.append(f"4h_opposing_{trend_4h.lower()}")

    # 1H trend agreement (+2 / -1)
    if trend_1h != "NEUTRAL":
        if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
            score += 2
            reasons.append(f"1h_confirms_{trend_1h.lower()}")
        else:
            score -= 1
            reasons.append(f"1h_opposing_{trend_1h.lower()}")

    # 1H momentum (+2 / +1 / -1)
    if direction == "LONG":
        if mom_1h >= 1.0:
            score += 2; reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h >= 0.5:
            score += 1; reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h < -0.5:
            score -= 1; reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")
    else:
        if mom_1h <= -1.0:
            score += 2; reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h <= -0.5:
            score += 1; reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h > 0.5:
            score -= 1; reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")

    # SM alignment (+-2)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        score -= 2
        reasons.append(f"sm_opposing_{sm_dir}")

    # Funding alignment (+2 / -1)
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_aligned_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append("funding_crowded")

    # Volume trend (+1)
    vol_1h = volume_trend(candles_1h)
    if vol_1h > min_vol_trend:
        score += 1
        reasons.append(f"vol_rising_{vol_1h:+.0f}%")

    # OI proxy (+1) — recent-3 vs earlier-3 1h volume delta (verbatim)
    vol_recent = sum(_vol(c) for c in candles_1h[-3:])
    vol_earlier = sum(_vol(c) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # RSI (+1 / -1)
    closes_1h = [_close(c) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > rsi_max_long:
        score -= 1
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif direction == "SHORT" and rsi < rsi_min_short:
        score -= 1
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4H momentum (+1)
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score += 1
            reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "directionSource": direction_source,
        "price": price, "trend_4h": trend_4h, "trend_1h": trend_1h,
        "momentum_1h": round(mom_1h, 3), "momentum_4h": round(mom_4h, 3),
        "sm_direction": sm_dir, "sm_pct": _f(sm_pct), "funding": funding,
        "rsi": round(rsi, 1), "volume_trend": round(vol_1h, 2), "oi_proxy": round(oi_proxy, 2),
    }
