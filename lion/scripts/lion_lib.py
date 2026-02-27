#!/usr/bin/env python3
"""
lion_lib.py — Pure Python analysis library for LION.
OI metrics, cascade phase detection, book imbalance, squeeze scoring.
No external dependencies — stdlib only.
"""

import math
import time


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


# ─── OI Metrics ──────────────────────────────────────────────

def oi_change_pct(entries, periods):
    """OI change % over N periods. Returns whole number or None."""
    if len(entries) < periods + 1:
        return None
    old_oi = entries[-(periods + 1)]["oi"]
    new_oi = entries[-1]["oi"]
    if old_oi == 0:
        return None
    return round(((new_oi - old_oi) / old_oi) * 100)


def oi_velocity(entries, window=3):
    """Rate of change of OI change (second derivative). Uses last `window` 5m changes."""
    if len(entries) < window + 6:
        return None
    changes = []
    for i in range(-window, 0):
        idx = len(entries) + i
        if idx >= 5:
            old = entries[idx - 5]["oi"]
            new = entries[idx]["oi"]
            if old > 0:
                changes.append(((new - old) / old) * 100)
    if len(changes) < 2:
        return None
    # Velocity = average rate of change of the changes
    deltas = [changes[i] - changes[i - 1] for i in range(1, len(changes))]
    return round(sum(deltas) / len(deltas), 4)


def oi_acceleration(entries, window=3):
    """Rate of change of velocity (third derivative). Determines cascade phase."""
    if len(entries) < window + 12:
        return None
    velocities = []
    for offset in range(-window, 0):
        end_idx = len(entries) + offset
        if end_idx >= 10:
            sub = entries[:end_idx + 1]
            v = oi_velocity(sub, min(3, len(sub) - 6))
            if v is not None:
                velocities.append(v)
    if len(velocities) < 2:
        return None
    deltas = [velocities[i] - velocities[i - 1] for i in range(1, len(velocities))]
    return round(sum(deltas) / len(deltas), 4)


def cascade_phase(velocity_val, acceleration_val):
    """Determine cascade phase from velocity and acceleration.
    Returns: BUILDING, ACTIVE, STABILIZING, ENTRY_WINDOW, or NONE."""
    if velocity_val is None or acceleration_val is None:
        return "NONE"
    if velocity_val < -0.3 and acceleration_val < 0:
        return "ACTIVE"         # Phase 1: cascade accelerating
    if velocity_val < -0.1 and acceleration_val > 0:
        return "STABILIZING"    # Phase 2: cascade decelerating
    if abs(velocity_val) < 0.1 and acceleration_val > 0:
        return "ENTRY_WINDOW"   # Phase 3: stabilization
    if velocity_val < -0.5:
        return "BUILDING"
    return "NONE"


def price_velocity_pct(entries, periods):
    """Price change % over N periods."""
    if len(entries) < periods + 1:
        return None
    old = entries[-(periods + 1)]["price"]
    new = entries[-1]["price"]
    if old == 0:
        return None
    return round(((new - old) / old) * 100, 2)


def volume_spike_ratio(entries, recent=15, lookback=240):
    """Recent volume vs historical average. Entries must have volume15m field."""
    if len(entries) < lookback:
        return 1.0
    recent_entries = entries[-recent:]
    hist_entries = entries[-lookback:-recent]
    # If no volume data, return 1.0
    if not any("volume15m" in e for e in recent_entries):
        return 1.0
    recent_vol = sum(e.get("volume15m", 0) for e in recent_entries) / max(len(recent_entries), 1)
    hist_vol = sum(e.get("volume15m", 0) for e in hist_entries) / max(len(hist_entries), 1)
    if hist_vol == 0:
        return 1.0
    return round(recent_vol / hist_vol, 2)


# ─── Book Imbalance ─────────────────────────────────────────

def compute_book_imbalance(bids, asks, levels=5):
    """Compute bid/ask imbalance ratio from L2 order book.
    bids/asks: list of [price, size] sorted by proximity to mid.
    Returns (ratio, bid_total, ask_total, direction)."""
    bid_total = sum(float(b[1]) for b in bids[:levels]) if bids else 0
    ask_total = sum(float(a[1]) for a in asks[:levels]) if asks else 0
    if ask_total == 0:
        return 999, bid_total, ask_total, "BID_HEAVY"
    if bid_total == 0:
        return 999, bid_total, ask_total, "ASK_HEAVY"
    ratio = bid_total / ask_total
    direction = "BID_HEAVY" if ratio > 1 else "ASK_HEAVY"
    return round(ratio, 2), round(bid_total, 2), round(ask_total, 2), direction


def is_imbalance_persistent(history, min_checks=2):
    """Check if imbalance has been detected for N consecutive checks.
    history: list of {ts, ratio, direction} dicts."""
    if len(history) < min_checks:
        return False
    recent = history[-min_checks:]
    directions = [h["direction"] for h in recent]
    ratios = [h["ratio"] for h in recent]
    return (all(d == directions[0] for d in directions) and
            all(r >= 3.0 or r <= 1/3 for r in ratios))


# ─── Squeeze Detection ──────────────────────────────────────

def funding_annualized(funding_per_8h):
    """Convert per-8h funding to annualized %."""
    return abs(funding_per_8h) * 3 * 365 * 100  # 3 periods/day × 365 days


def is_squeeze_building(funding_per_8h, periods_extreme, oi_rising, price_range_24h_pct, config):
    """Check if squeeze conditions are met."""
    threshold = config.get("squeezeFundingPer8h", 8) / 10000  # 8 → 0.0008
    min_periods = config.get("squeezeMinPeriods", 2)
    compression = config.get("squeezeCompressionPct", 1)

    if abs(funding_per_8h) < threshold:
        return False
    if periods_extreme < min_periods:
        return False
    if not oi_rising:
        return False
    if price_range_24h_pct > compression:
        return False
    return True


def squeeze_direction(funding_per_8h):
    """If funding is positive, longs are crowded → squeeze direction is SHORT."""
    if funding_per_8h > 0:
        return "SHORT"
    return "LONG"


# ─── ATR ─────────────────────────────────────────────────────

def atr(highs, lows, closes, period=14):
    """Average True Range."""
    if len(highs) < period + 1:
        return None
    tr_values = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr_values.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    # Wilder smoothing
    atr_val = sum(tr_values[:period]) / period
    for i in range(period, len(tr_values)):
        atr_val = (atr_val * (period - 1) + tr_values[i]) / period
    return round(atr_val, 6)


# ─── Position Sizing ────────────────────────────────────────

def cascade_size_pct(oi_drop_pct, volume_mult, config):
    """Get position size % based on cascade strength."""
    if abs(oi_drop_pct) > 15 and volume_mult > 5:
        return config.get("sizingStrongCascade", 20)
    return config.get("sizingNormalCascade", 12)


def cascade_leverage(oi_drop_pct, config):
    """Get leverage for cascade reversal."""
    base = config.get("leverageCascade", 6)
    if abs(oi_drop_pct) > 15:
        return min(base + 1, 7)
    return base
