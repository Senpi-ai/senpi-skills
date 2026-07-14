"""ARMADILLO — pure thesis math (no I/O, no MCP, no clock).

Capital-Preservation / Low-Vol scorer. The multi-factor MOMENTUM ENGINE (indicator
math + direction-waterfall + weighted score) is ported VERBATIM from bison/scoring.py
(a validated Runtime 3.0 scorer) so a fidelity harness can diff armadillo's thesis
against bison's on the same candles; behaviour-preserving quirks carry bison's
`# v2-quirk` flags. Armadillo adds NO new indicator — its conservatism lives entirely
in the CONFIG (a HIGH minScore floor applied by the caller, and LOW leverage/margin
tiers via band_for + sizing_for below). The DSL owns every exit.

Multi-asset, single-pass, unit-testable on plain candle lists. `sm` (smart-money
lean) is passed in as (None, 0) by armadillo's caller — armadillo trades only the
tape, so the SM contributor never fires — keeping this module pure.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# Ported verbatim from bison/scoring.py. v2 read dicts only; the list branch is
# defensive and never fires on dict candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    """Float or None (distinguishes a real 0.0 from a missing field). Used by the
    scanner's tolerant funding extraction; not part of the verbatim bison port."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


# ── indicators (ported verbatim from bison/scoring.py) ──

def price_momentum(candles, n_bars=1):
    """% change over the last n_bars. Verbatim from bison price_momentum."""
    if len(candles) < n_bars + 1:
        return 0
    old = _close(candles[-(n_bars + 1)])
    new = _close(candles[-1])
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH. Verbatim from bison.

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
    """Recent-half vs earlier-half average volume, % change. Verbatim from bison."""
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
    """RSI. Verbatim from bison calc_rsi.

    v2-quirk: uses the LAST `period` gains/losses (gains[-period:]) — the more
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


# ── the thesis (direction waterfall + 9-component score), ported verbatim from bison ──

def build_thesis(coin, candles_15m, candles_1h, candles_4h, funding, sm, inputs):
    """Port of bison build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned ONLY when:
      - insufficient candle history (len(c1h) < 8 or len(c4h) < 4), OR
      - the direction waterfall resolves no direction.
    minScore is NOT applied here — the caller (scan.py) gates on thesis['score']
    using armadillo's HIGH floor. `sm` is (direction, pct) or (None, 0); armadillo
    always passes (None, 0), so the SM contributor is inert."""
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

    # SM alignment (+-2) — inert for armadillo (sm always (None, 0))
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


# ── conviction band + LOW-cap sizing (armadillo's conservatism, raven-shaped) ──
# NOT ported from bison's margin_tier_pct — armadillo replaces conviction-scaled
# margin with a hard LOW ceiling. The whole point is few, small, low-leverage bets.

def band_for(score, inputs):
    """Conviction band from the score, relative to the HIGH capital-preservation
    floor. apex/good default well above a typical thesis so only the strongest
    setups earn even the (still tiny) apex sizing."""
    apex = _f(inputs.get("apexScore"), 13)
    good = _f(inputs.get("goodScore"), 12)
    if score >= apex:
        return "apex"
    if score >= good:
        return "good"
    return "base"


def sizing_for(band, inputs, venue_max=None):
    """(leverage, marginPct) for a conviction band. marginPct is a PERCENT in
    (0,100]. Capital-preservation LOW caps: leverage clamped to maxLeverage (2)
    AND the venue max; marginPct clamped to maxMarginPct (10). Even an apex score
    can never exceed 2x / 10% — the ceiling is structural, not adaptive."""
    lev_tiers = inputs.get("leverageTiers") or {"apex": 2, "good": 2, "base": 1}
    mgn_tiers = inputs.get("marginPctTiers") or {"apex": 8, "good": 6, "base": 4}
    cap = int(_f(inputs.get("maxLeverage"), 2))
    lev = int(_f(lev_tiers.get(band), 1))
    if venue_max:
        cap = min(cap, int(_f(venue_max, cap)))
    lev = max(1, min(lev, cap))
    mgn = _f(mgn_tiers.get(band), 4)
    mgn = max(1.0, min(mgn, _f(inputs.get("maxMarginPct"), 10)))
    return lev, round(mgn, 2)
