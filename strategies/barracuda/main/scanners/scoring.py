"""BARRACUDA — pure scoring engine (no I/O, no MCP, no clock, no state).

A dynamic dual-direction breakout/pump screener. Every candidate is scored,
independently for LONG (breakout) and SHORT (breakdown), on:

  * multi-timeframe candle structure (1h + 4h, 4h trend weighted 60%)
  * price momentum (4h primary, 1h confirmation, pullback-tolerant)
  * momentum acceleration (last candle body vs the prior three)
  * RSI(14) on 1h (gated in scan.py per asset class)
  * smart-money positioning (how many top traders hold the name)
  * funding squeeze (annualized funding; aligned crowd = squeeze fuel)
  * open-interest velocity (rising OI confirms, falling OI = exhaustion)
  * 24h notional volume + a micro-cap size bonus (small caps pump harder)
  * a 15m fast-track for a real volume-spike breakout in progress

All of the thesis math lives here so it unit-tests without a network.
`scan.py` does the MCP reads, the gates, and the emit; it calls into these
pure functions and `combine_score()` for the final per-direction total.

Candle dicts are Hyperliquid `o/h/l/c/v` (values may be strings) — every
numeric field is read through `_f()` (float of a number is a no-op, so it is
correct on every runtime version and every data path).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0

# ─── Momentum / breakout thresholds ───────────────────────────────────
MIN_PRICE_CHANGE_4H = 0.5     # % — 4h must be moving to score a momentum signal
MIN_PRICE_CHANGE_15M = 0.3    # % — minimum 15m move to consider the fast-track path
NEAR_EXTREME_PCT = 4.0        # distance from 20-candle high/low to count as "near extreme"

# ─── Fast-track (a breakout caught early, before it shows on the 1h) ──
FAST_TRACK_MOVE_PCT = 0.5     # 15m move required for the fast-track
FAST_TRACK_VOL_RATIO = 3.0    # volume spike (x average) required for the fast-track
FAST_TRACK_WINDOW = 2         # candles back for the 15m change (~30 min)
MAX_15M_MOVE_PCT = 8.0        # skip coins already pumped/dumped >8% in 15m (overextension)

# ─── Score weights ────────────────────────────────────────────────────
SMART_MONEY_WEIGHT = 1.0      # weight of the smart-money sub-score in the total
OI_WEIGHT = 2.0               # weight of the OI-confirmation sub-score in the total
SIZE_WEIGHT = 1.5             # weight of the size bonus (small caps pump harder)
CONFLICT_PENALTY = 3.0        # subtracted when a coin scores high in BOTH directions (choppy)

# ─── RSI ──────────────────────────────────────────────────────────────
RSI_PERIOD = 14               # RSI lookback on 1h candles

# ─── Size bonus (market size proxied by 24h notional volume, $M) ──────
SIZE_MICRO_MAX_VOL = 15.0     # < $15M/day = micro cap (highest pump potential)
SIZE_SMALL_MAX_VOL = 50.0     # < $50M/day = small cap
SIZE_LARGE_MIN_VOL = 200.0    # > $200M/day = large cap (penalty — rarely pumps)
SIZE_MICRO_BONUS = 4.0
SIZE_SMALL_BONUS = 2.0
SIZE_LARGE_PENALTY = 2.0

# ─── Funding squeeze ──────────────────────────────────────────────────
# LONG collects funding  -> funding negative -> shorts crowded -> bullish for LONG.
# SHORT collects funding  -> funding positive -> longs  crowded -> bullish for SHORT.

# ─── Conviction tiers (from the combined total score) ─────────────────
CONVICTION_HIGH = 26.0
CONVICTION_MEDIUM = 20.0


def _f(v, d=0.0):
    """Defensive numeric read: no-op on numbers, casts strings, `d` on None/garbage.
    Gate on presence first — a fallback 0.0 reads as a real price."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(coin) -> bool:
    """True if this is an XYZ DEX asset (equity / metal / index / pre-IPO)."""
    return str(coin).startswith("xyz:")


def pct_change(prev, last) -> float:
    """Percent change from `prev` to `last`; 0.0 when `prev` is non-positive."""
    prev = _f(prev)
    last = _f(last)
    return ((last - prev) / prev) * 100.0 if prev > 0 else 0.0


def compute_rsi(candles, period: int = RSI_PERIOD):
    """RSI from candle closes. Returns 0-100, or None if not enough data."""
    if not candles or len(candles) < period + 1:
        return None
    closes = [_f(c["c"]) for c in candles[-(period + 1):]]
    gains = 0.0
    losses = 0.0
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def score_candle(candles, price):
    """Breakout momentum from OHLCV candles. Returns bullish (LONG) and
    bearish (SHORT) sub-scores plus the structure fields scan.py reports."""
    if not candles or len(candles) < 10:
        return None

    closes = [_f(c["c"]) for c in candles]
    current = closes[-1]
    if current <= 0:
        return None

    sma_20 = sum(closes[-20:]) / min(20, len(closes))
    sma_50 = sum(closes[-50:]) / min(50, len(closes))

    recent_range = max(closes[-5:]) - min(closes[-5:])
    vol_pct = (recent_range / current) * 100

    volumes = [_f(c["v"]) for c in candles]
    avg_vol = sum(volumes[-20:]) / min(20, len(volumes))
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    # ── BULLISH (for LONG) ──
    above_sma_20 = current > sma_20
    above_sma_50 = current > sma_50 if len(closes) >= 50 else True
    recent_high = max(closes[-20:])
    breakout_dist = (recent_high - current) / current * 100 if recent_high > 0 else 100
    near_high = breakout_dist < NEAR_EXTREME_PCT

    score_long = 0.0
    if above_sma_20:
        score_long += 2.0
    if above_sma_50:
        score_long += 1.0
    if near_high:
        score_long += 3.0
    if vol_ratio > 1.5:
        score_long += 2.0
    elif vol_ratio > 1.0:
        score_long += 1.0
    else:
        score_long -= 1.0
    if 1.0 < vol_pct < 10.0:
        score_long += 1.0

    # ── BEARISH (for SHORT) ──
    below_sma_20 = current < sma_20
    below_sma_50 = current < sma_50 if len(closes) >= 50 else True
    recent_low = min(closes[-20:])
    breakdown_dist = (current - recent_low) / current * 100 if recent_low > 0 else 100
    near_low = breakdown_dist < NEAR_EXTREME_PCT

    score_short = 0.0
    if below_sma_20:
        score_short += 2.0
    if below_sma_50:
        score_short += 1.0
    if near_low:
        score_short += 3.0
    if vol_ratio > 1.5:
        score_short += 2.0
    elif vol_ratio > 1.0:
        score_short += 1.0
    else:
        score_short -= 1.0
    if 1.0 < vol_pct < 10.0:
        score_short += 1.0

    return {
        "long_score": round(max(0.0, score_long), 1),
        "short_score": round(max(0.0, score_short), 1),
        "near_high_pct": round(breakout_dist, 2),
        "near_low_pct": round(breakdown_dist, 2),
        "vol_ratio": round(vol_ratio, 2),
        "above_sma_20": above_sma_20,
        "below_sma_20": below_sma_20,
    }


def score_candle_multi(candles_1h, candles_4h, price):
    """Multi-timeframe candle score — 4h sets the trend bias (60%), 1h the
    near-term setup (40%)."""
    s_1h = score_candle(candles_1h, price)
    s_4h = score_candle(candles_4h, price)
    if not s_1h and not s_4h:
        return None
    if not s_1h:
        return s_4h
    if not s_4h:
        return s_1h

    blend = {}
    for key in ("long_score", "short_score"):
        blend[key] = round(s_4h[key] * 0.6 + s_1h[key] * 0.4, 1)
    for key in ("near_high_pct", "near_low_pct", "vol_ratio"):
        blend[key] = min(s_1h[key], s_4h[key])  # tighter value
    blend["above_sma_20"] = s_1h["above_sma_20"] or s_4h["above_sma_20"]
    blend["below_sma_20"] = s_1h["below_sma_20"] or s_4h["below_sma_20"]
    return blend


def score_price_change(ch_1h: float, ch_4h: float):
    """Price-momentum score. 4h direction is primary; 1h is a softer
    confirmation and a strong 1h reversal vetoes the trade. Allows a small 1h
    pullback inside a strong 4h trend (consolidation before continuation)."""
    if abs(ch_4h) < MIN_PRICE_CHANGE_4H:
        return None

    result = {"long_score": 0.0, "short_score": 0.0}

    if ch_4h > 0:
        # 4h uptrend — long bias
        result["long_score"] = min(5.0, ch_4h)
        if ch_1h > 0:
            result["long_score"] += min(3.0, ch_1h)
        elif ch_1h > -1.0:
            result["long_score"] += 0.5   # small pullback = consolidation, still valid
        if ch_1h < -1.0:
            result["long_score"] = 0.0    # 1h drop > 1% = bearish turn, do not long
    elif ch_4h < 0:
        # 4h downtrend — short bias
        result["short_score"] = min(5.0, abs(ch_4h))
        if ch_1h < 0:
            result["short_score"] += min(3.0, abs(ch_1h))
        elif ch_1h < -0.5:
            result["short_score"] += 1.0  # still dropping = momentum confirmed
        if ch_1h > 1.0:
            result["short_score"] = 0.0   # 1h bounce > 1% = rebound underway, do not short
    else:
        return None

    if result["long_score"] < 0.5 and result["short_score"] < 0.5:
        return None

    result["ch_1h"] = ch_1h
    result["ch_4h"] = ch_4h
    return result


def score_funding(funding_info, direction: str):
    """Funding-squeeze score for one direction. `funding_direction` = which side
    COLLECTS funding; when it aligns with the trade direction the squeeze fuels it."""
    if not funding_info:
        return None

    annualized = abs(_f(funding_info.get("funding_annualized_pct", 0)))

    score = 0.0
    if annualized > 10:
        score += 5.0
    elif annualized > 5:
        score += 4.0
    elif annualized > 2:
        score += 3.0
    elif annualized > 1:
        score += 2.0
    else:
        score += 1.0

    fund_dir = funding_info.get("funding_direction", "")
    if (direction == "LONG" and fund_dir == "LONG") or \
       (direction == "SHORT" and fund_dir == "SHORT"):
        score += 2.0  # aligned squeeze is a stronger signal

    return {"score": score, "annualized_pct": annualized}


def score_momentum_acceleration(candles_1h) -> float:
    """Last candle's body vs the average of the previous three. Positive =
    momentum building, negative = fading. Returns a bonus/penalty in [-2, +2]."""
    if not candles_1h or len(candles_1h) < 5:
        return 0.0
    recent = candles_1h[-1]
    prev_3 = candles_1h[-4:-1]
    recent_body = abs(_f(recent["c"]) - _f(recent["o"]))
    avg_prev_body = sum(abs(_f(c["c"]) - _f(c["o"])) for c in prev_3) / 3.0
    if avg_prev_body <= 0:
        return 0.0
    ratio = recent_body / avg_prev_body
    if ratio > 1.5:
        return 2.0    # accelerating
    if ratio > 1.0:
        return 1.0    # building
    if ratio > 0.5:
        return -0.5   # fading
    return -2.0       # stalling


def score_oi_velocity(oi_data) -> float:
    """OI confirmation. Rising OI = move backed by new positions; falling OI =
    exhaustion (positions closing). Returns a value clamped to [-3, +3]."""
    if not oi_data:
        return 0.0
    trend = oi_data.get("oi_trend", "FLAT")
    accel = oi_data.get("oi_acceleration", "STABLE")
    change_1h = abs(_f(oi_data.get("oi_change_pct_1h", 0)))

    score = 0.0
    if trend == "BUILDING":
        score += 2.0
    elif trend == "DECLINING":
        score -= 2.0

    if accel == "INCREASING":
        score += 1.0
    elif accel == "DECREASING":
        score -= 1.0

    if change_1h > 2.0 and trend == "BUILDING":
        score += 0.5

    return max(-3.0, min(3.0, score))


def score_size(volume_24h_m: float) -> float:
    """Size bonus/penalty from 24h notional volume ($M). Small caps pump harder
    (bonus); large caps rarely pump (penalty). Returns a value in [-2, +4]."""
    if volume_24h_m < SIZE_MICRO_MAX_VOL:
        return SIZE_MICRO_BONUS
    if volume_24h_m < SIZE_SMALL_MAX_VOL:
        return SIZE_SMALL_BONUS
    if volume_24h_m > SIZE_LARGE_MIN_VOL:
        return -SIZE_LARGE_PENALTY
    return 0.0


def market_size_label(volume_24h_m: float) -> str:
    """Human label for the size bucket used in the size bonus."""
    if volume_24h_m < SIZE_MICRO_MAX_VOL:
        return "micro"
    if volume_24h_m < SIZE_SMALL_MAX_VOL:
        return "small"
    if volume_24h_m > SIZE_LARGE_MIN_VOL:
        return "large"
    return "mid"


def detect_consolidation(candles_1h, lookback: int = 6) -> bool:
    """True if the coin was in a tight (<2% of price) range over the last
    `lookback` hours — a squeeze ripe for a breakout. This is the filter that
    keeps the fast-track from buying the TOP of a move: we take breakouts FROM
    consolidation, not continuation after the move already ran."""
    if not candles_1h or len(candles_1h) < lookback + 1:
        return False
    closes = [_f(c["c"]) for c in candles_1h[-(lookback + 1):]]
    current = closes[-1]
    if current <= 0:
        return False
    range_closes = closes[:-1]
    price_range = max(range_closes) - min(range_closes)
    range_pct = (price_range / current) * 100
    return range_pct < 2.0


def score_fast_momentum(candles_15m):
    """15m momentum for the fast-track — catches a pump/dump in its early stage.
    Uses a short (~30 min) window and flags `is_fast_track` when a strong move
    coincides with a real volume spike. Returns long/short sub-scores + ch_15m."""
    if not candles_15m or len(candles_15m) < 4:
        return None

    closes = [_f(c["c"]) for c in candles_15m]
    current = closes[-1]
    if current <= 0:
        return None

    if len(closes) < FAST_TRACK_WINDOW + 1:
        return None
    ref = closes[-(FAST_TRACK_WINDOW + 1)]
    ch_15m = ((current - ref) / ref) * 100 if ref > 0 else 0.0

    if abs(ch_15m) < MIN_PRICE_CHANGE_15M:
        return None
    if abs(ch_15m) > MAX_15M_MOVE_PCT:   # overextension guard — don't chase
        return None

    volumes = [_f(c["v"]) for c in candles_15m]
    avg_vol = sum(volumes[-8:]) / min(8, len(volumes))
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    result = {"long_score": 0.0, "short_score": 0.0, "ch_15m": round(ch_15m, 2)}
    is_fast_track = abs(ch_15m) > FAST_TRACK_MOVE_PCT and vol_ratio > FAST_TRACK_VOL_RATIO

    if ch_15m > 0:
        result["long_score"] = min(4.0, ch_15m * 1.5)
        if vol_ratio > 2.0:
            result["long_score"] += 2.0
        elif vol_ratio > 1.5:
            result["long_score"] += 1.0
    elif ch_15m < 0:
        result["short_score"] = min(4.0, abs(ch_15m) * 1.5)
        if vol_ratio > 2.0:
            result["short_score"] += 2.0
        elif vol_ratio > 1.5:
            result["short_score"] += 1.0

    result["is_fast_track"] = is_fast_track
    result["vol_ratio_15m"] = round(vol_ratio, 2)
    return result


def combine_score(cs, ps, fs, smart_money_score, accel_adj, oi_adj,
                  fast_adj, size_score, regime_bonus, conflict_penalty=0.0):
    """The crux: fold the per-direction sub-scores into one total, applying the
    fixed weights (smart-money, OI, size). All inputs are already direction-
    adjusted by scan.py. Pure arithmetic so the weighting is unit-testable."""
    return (cs + ps + fs
            + smart_money_score * SMART_MONEY_WEIGHT
            + accel_adj
            + oi_adj * OI_WEIGHT
            + fast_adj
            + size_score * SIZE_WEIGHT
            + regime_bonus
            - conflict_penalty)


def conviction_tier(total: float) -> str:
    """Conviction label from the combined total score."""
    if total >= CONVICTION_HIGH:
        return "high"
    if total >= CONVICTION_MEDIUM:
        return "medium"
    return "low"
