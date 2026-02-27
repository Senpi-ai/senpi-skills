"""
tiger_lib.py — Pure Python technical analysis library for TIGER.
No external dependencies. Uses only stdlib (math, statistics).
"""

import math
import statistics
from typing import List, Optional, Tuple, Dict


# ─── Moving Averages ────────────────────────────────────────

def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average. Returns list same length as input, None for insufficient data."""
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1:i + 1]) / period
    return result


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average."""
    result = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    # Seed with SMA
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


# ─── RSI ─────────────────────────────────────────────────────

def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """Relative Strength Index using Wilder's smoothing."""
    result = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100 - (100 / (1 + rs))

    return result


# ─── Bollinger Bands ─────────────────────────────────────────

def bollinger_bands(closes: List[float], period: int = 20, num_std: float = 2.0
                    ) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """Returns (upper, middle, lower) bands."""
    middle = sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        std = statistics.stdev(window) if len(window) > 1 else 0
        upper[i] = middle[i] + num_std * std
        lower[i] = middle[i] - num_std * std

    return upper, middle, lower


def bb_width(closes: List[float], period: int = 20, num_std: float = 2.0) -> List[Optional[float]]:
    """Bollinger Band Width = (upper - lower) / middle. Squeeze detection."""
    upper, middle, lower = bollinger_bands(closes, period, num_std)
    result = [None] * len(closes)
    for i in range(len(closes)):
        if upper[i] is not None and middle[i] and middle[i] != 0:
            result[i] = (upper[i] - lower[i]) / middle[i]
    return result


def bb_width_percentile(closes: List[float], period: int = 20, lookback: int = 100) -> Optional[float]:
    """Current BB width as percentile of recent history. Low = squeeze."""
    widths = bb_width(closes, period)
    valid = [w for w in widths[-lookback:] if w is not None]
    if len(valid) < 10:
        return None
    current = valid[-1]
    below = sum(1 for w in valid if w < current)
    return (below / len(valid)) * 100


# ─── ATR ─────────────────────────────────────────────────────

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14
        ) -> List[Optional[float]]:
    """Average True Range using Wilder's smoothing."""
    result = [None] * len(closes)
    if len(closes) < 2:
        return result

    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)

    if len(trs) < period:
        return result

    result[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        result[i] = (result[i - 1] * (period - 1) + trs[i]) / period

    return result


# ─── Volume Analysis ─────────────────────────────────────────

def volume_ratio(volumes: List[float], short_period: int = 5, long_period: int = 20) -> Optional[float]:
    """Ratio of recent avg volume to longer avg. >1 = volume increasing."""
    if len(volumes) < long_period:
        return None
    short_avg = sum(volumes[-short_period:]) / short_period
    long_avg = sum(volumes[-long_period:]) / long_period
    if long_avg == 0:
        return None
    return short_avg / long_avg


def oi_change_pct(oi_history: List[float], periods: int = 12) -> Optional[float]:
    """OI change over N periods as percentage."""
    if len(oi_history) < periods + 1:
        return None
    old = oi_history[-(periods + 1)]
    new = oi_history[-1]
    if old == 0:
        return None
    return ((new - old) / old) * 100


# ─── RSI Divergence ──────────────────────────────────────────

def detect_rsi_divergence(closes: List[float], rsi_values: List[Optional[float]],
                          lookback: int = 20) -> Optional[str]:
    """
    Detect bullish or bearish RSI divergence.
    Returns 'bullish', 'bearish', or None.
    """
    valid_rsi = [(i, r) for i, r in enumerate(rsi_values[-lookback:]) if r is not None]
    if len(valid_rsi) < 10:
        return None

    # Get last two swing lows/highs in price and RSI
    recent_closes = closes[-lookback:]

    # Simple: compare first half min/max to second half
    mid = len(recent_closes) // 2
    first_half_close = recent_closes[:mid]
    second_half_close = recent_closes[mid:]
    first_half_rsi = [r for _, r in valid_rsi[:len(valid_rsi) // 2]]
    second_half_rsi = [r for _, r in valid_rsi[len(valid_rsi) // 2:]]

    if not first_half_rsi or not second_half_rsi:
        return None

    # Bullish divergence: price makes lower low, RSI makes higher low
    if min(second_half_close) < min(first_half_close) and min(second_half_rsi) > min(first_half_rsi):
        return "bullish"

    # Bearish divergence: price makes higher high, RSI makes lower high
    if max(second_half_close) > max(first_half_close) and max(second_half_rsi) < max(first_half_rsi):
        return "bearish"

    return None


# ─── Scoring Helpers ─────────────────────────────────────────

def confluence_score(factors: Dict[str, Tuple[bool, float]]) -> float:
    """
    Calculate confluence score from named factors.
    factors = {"name": (is_true, weight)}
    Returns sum of weights where factor is true.
    """
    return sum(weight for name, (is_true, weight) in factors.items() if is_true)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float, fraction: float = 0.5) -> float:
    """
    Half-Kelly position sizing.
    Returns fraction of bankroll to risk (0 to 1).
    """
    if avg_loss == 0:
        return 0
    b = avg_win / avg_loss
    f = (win_rate * b - (1 - win_rate)) / b
    return max(0, min(f * fraction, 0.25))  # Cap at 25%


def required_daily_return(current: float, target: float, days_remaining: int) -> Optional[float]:
    """Compound daily return needed to hit target. Returns as percentage."""
    if days_remaining <= 0 or current <= 0:
        return None
    ratio = target / current
    if ratio <= 0:
        return None
    daily = math.pow(ratio, 1 / days_remaining) - 1
    return daily * 100


def aggression_mode(daily_rate_needed: float) -> str:
    """Determine aggression mode from required daily return %."""
    if daily_rate_needed is None or daily_rate_needed > 25:
        return "ABORT"
    if daily_rate_needed > 15:
        return "ELEVATED"
    if daily_rate_needed > 8:
        return "NORMAL"
    return "CONSERVATIVE"


# ─── Candle Parsing ──────────────────────────────────────────

def parse_candles(candles: List[dict]) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    """Parse Senpi candle format into (opens, highs, lows, closes, volumes)."""
    opens = [float(c["o"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    closes = [float(c["c"]) for c in candles]
    volumes = [float(c["v"]) for c in candles]
    return opens, highs, lows, closes, volumes
