#!/usr/bin/env python3
"""
viper_lib.py — Pure Python TA library for VIPER.
ADX, Bollinger Bands, ATR, range scoring, boundary mapping.
No external dependencies — stdlib only.
"""

import math


def parse_candles(candles):
    """Parse candle list into OHLCV arrays."""
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for c in candles:
        if isinstance(c, dict):
            opens.append(float(c.get("o", c.get("open", 0))))
            highs.append(float(c.get("h", c.get("high", 0))))
            lows.append(float(c.get("l", c.get("low", 0))))
            closes.append(float(c.get("c", c.get("close", 0))))
            volumes.append(float(c.get("v", c.get("volume", 0))))
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            opens.append(float(c[1]))
            highs.append(float(c[2]))
            lows.append(float(c[3]))
            closes.append(float(c[4]))
            volumes.append(float(c[5]) if len(c) > 5 else 0)
    return opens, highs, lows, closes, volumes


def ema(values, period):
    """Exponential moving average."""
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    result = [None] * (period - 1)
    result.append(sum(values[:period]) / period)
    for i in range(period, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def sma(values, period):
    """Simple moving average."""
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def wilder_smooth(values, period):
    """Wilder's smoothing (used for ADX, ATR)."""
    if len(values) < period:
        return [None] * len(values)
    result = [None] * (period - 1)
    result.append(sum(values[:period]) / period)
    for i in range(period, len(values)):
        result.append((result[-1] * (period - 1) + values[i]) / period)
    return result


def true_range(highs, lows, closes):
    """True Range series."""
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    return tr


def atr(highs, lows, closes, period=14):
    """Average True Range using Wilder's smoothing."""
    tr = true_range(highs, lows, closes)
    return wilder_smooth(tr, period)


def adx(highs, lows, closes, period=14):
    """Average Directional Index (Wilder's method).
    Returns (adx_values, plus_di, minus_di)."""
    if len(highs) < period + 1:
        return [None] * len(highs), [None] * len(highs), [None] * len(highs)

    # +DM and -DM
    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0)
        minus_dm.append(down if (down > up and down > 0) else 0)

    tr = true_range(highs, lows, closes)[1:]  # Skip first (no prev close)

    # Wilder smooth all three
    smooth_tr = wilder_smooth(tr, period)
    smooth_plus = wilder_smooth(plus_dm, period)
    smooth_minus = wilder_smooth(minus_dm, period)

    # +DI and -DI
    plus_di = []
    minus_di = []
    dx_values = []
    for i in range(len(smooth_tr)):
        if smooth_tr[i] is None or smooth_tr[i] == 0:
            plus_di.append(None)
            minus_di.append(None)
            dx_values.append(None)
        else:
            pdi = (smooth_plus[i] / smooth_tr[i]) * 100 if smooth_plus[i] is not None else None
            mdi = (smooth_minus[i] / smooth_tr[i]) * 100 if smooth_minus[i] is not None else None
            plus_di.append(pdi)
            minus_di.append(mdi)
            if pdi is not None and mdi is not None and (pdi + mdi) > 0:
                dx_values.append(abs(pdi - mdi) / (pdi + mdi) * 100)
            else:
                dx_values.append(None)

    # ADX = Wilder smooth of DX
    valid_dx = [v for v in dx_values if v is not None]
    adx_raw = wilder_smooth(valid_dx, period)

    # Pad ADX to match length
    adx_result = [None] * (len(dx_values) - len(valid_dx))
    adx_result.extend(adx_raw)

    # Pad all to original length (+1 for the first candle we skipped)
    pad = len(highs) - len(adx_result)
    adx_out = [None] * pad + adx_result
    pdi_out = [None] + plus_di + [None] * max(0, len(highs) - len(plus_di) - 1)
    mdi_out = [None] + minus_di + [None] * max(0, len(highs) - len(minus_di) - 1)

    return adx_out[:len(highs)], pdi_out[:len(highs)], mdi_out[:len(highs)]


def bollinger_bands(closes, period=20, std_mult=2):
    """Bollinger Bands. Returns (upper, middle, lower, width)."""
    middle = sma(closes, period)
    upper = []
    lower = []
    width = []
    for i in range(len(closes)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
            width.append(None)
        else:
            window = closes[max(0, i - period + 1):i + 1]
            mean = middle[i]
            std = math.sqrt(sum((x - mean) ** 2 for x in window) / len(window))
            upper.append(mean + std_mult * std)
            lower.append(mean - std_mult * std)
            w = (upper[-1] - lower[-1]) / middle[i] * 100 if middle[i] > 0 else 0
            width.append(w)
    return upper, lower, middle, width


def bb_width_percentile(width_values, lookback=100):
    """Current BB width as percentile of last N values."""
    valid = [w for w in width_values if w is not None]
    if len(valid) < 10:
        return None
    recent = valid[-lookback:] if len(valid) >= lookback else valid
    current = recent[-1]
    below = sum(1 for w in recent if w < current)
    return round(below / len(recent) * 100)


def count_touches(candle_wicks, level, tolerance_pct=0.3):
    """Count how many wicks are within tolerance of a level."""
    if level == 0:
        return 0
    tol = level * tolerance_pct / 100
    return sum(1 for w in candle_wicks if abs(w - level) <= tol)


def find_support_resistance(highs, lows, closes, lookback=48):
    """Find support and resistance levels from wick clusters.
    Returns (support_level, support_zone, resistance_level, resistance_zone)."""
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]

    if not recent_highs or not recent_lows:
        return None, None, None, None

    price_range_high = max(recent_highs)
    price_range_low = min(recent_lows)
    price_range = price_range_high - price_range_low

    if price_range <= 0:
        return None, None, None, None

    # Bottom 20% = support candidates, top 20% = resistance candidates
    support_threshold = price_range_low + price_range * 0.20
    resistance_threshold = price_range_high - price_range * 0.20

    support_wicks = [l for l in recent_lows if l <= support_threshold]
    resistance_wicks = [h for h in recent_highs if h >= resistance_threshold]

    if len(support_wicks) < 2 or len(resistance_wicks) < 2:
        return None, None, None, None

    # Cluster with 0.2% tolerance
    support_clusters = _cluster(support_wicks, price_range_low * 0.002)
    resistance_clusters = _cluster(resistance_wicks, price_range_high * 0.002)

    if not support_clusters or not resistance_clusters:
        return None, None, None, None

    # Densest cluster
    sup_cluster = max(support_clusters, key=len)
    res_cluster = max(resistance_clusters, key=len)

    sup_level = sorted(sup_cluster)[len(sup_cluster) // 2]  # median
    sup_zone = (min(sup_cluster), max(sup_cluster))

    res_level = sorted(res_cluster)[len(res_cluster) // 2]
    res_zone = (min(res_cluster), max(res_cluster))

    return sup_level, sup_zone, res_level, res_zone


def _cluster(values, tolerance):
    """Simple clustering by tolerance."""
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters = [[sorted_vals[0]]]
    for v in sorted_vals[1:]:
        if v - clusters[-1][-1] <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def range_score(adx_val, bb_pctl, support_touches, resistance_touches, range_width_pct):
    """Compute range quality score (0-1)."""
    if adx_val is None or bb_pctl is None:
        return 0

    score = (
        max(0, (20 - adx_val)) / 20 * 0.25 +
        max(0, (1 - bb_pctl / 100)) * 0.20 +
        min(support_touches, 5) / 5 * 0.25 +
        min(resistance_touches, 5) / 5 * 0.25 +
        min(range_width_pct, 6) / 6 * 0.05
    )
    return round(score, 3)


def get_leverage(range_width_pct, config):
    """Get leverage based on range width."""
    lev_map = config.get("leverageByWidth", {"2-3": 7, "3-4": 6, "4-5": 5, ">5": 5})
    if range_width_pct < 3:
        return lev_map.get("2-3", 7)
    elif range_width_pct < 4:
        return lev_map.get("3-4", 6)
    elif range_width_pct < 5:
        return lev_map.get("4-5", 5)
    else:
        return lev_map.get(">5", 5)


def get_position_size_pct(range_width_pct, range_score_val, config):
    """Get position size as % of balance based on range width and score."""
    sizing = config.get("positionSizing", {})
    if range_width_pct < 3:
        bucket = sizing.get("2-3", {"low": 10, "mid": 12, "high": 15})
    elif range_width_pct < 5:
        bucket = sizing.get("3-5", {"low": 12, "mid": 15, "high": 18})
    else:
        bucket = sizing.get(">5", {"low": 15, "mid": 18, "high": 20})

    if range_score_val > 0.85:
        return bucket.get("high", 15)
    elif range_score_val > 0.70:
        return bucket.get("mid", 12)
    else:
        return bucket.get("low", 10)


def is_in_zone(price, zone):
    """Check if price is within a zone [low, high]."""
    if zone is None or len(zone) < 2:
        return False
    return zone[0] <= price <= zone[1]


def is_in_dead_zone(price, support_level, resistance_level):
    """Check if price is in the dead zone (middle 40%)."""
    range_size = resistance_level - support_level
    dead_low = support_level + range_size * 0.30
    dead_high = resistance_level - range_size * 0.30
    return dead_low <= price <= dead_high


def volume_ratio(volumes, recent_bars=4, lookback_bars=48):
    """Recent volume vs historical average."""
    if len(volumes) < lookback_bars:
        return 1.0
    recent = volumes[-recent_bars:]
    historical = volumes[-lookback_bars:-recent_bars]
    avg_hist = sum(historical) / len(historical) if historical else 1
    avg_recent = sum(recent) / len(recent) if recent else 0
    return round(avg_recent / avg_hist, 2) if avg_hist > 0 else 1.0
