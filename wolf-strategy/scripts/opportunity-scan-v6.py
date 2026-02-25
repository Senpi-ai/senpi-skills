#!/usr/bin/env python3
"""
Opportunity Scanner v6 — Fixed, multi-strategy aware, all v5 features.

Fixes from v5:
  - Stage 0: BTC macro context (prevents alt longs during BTC crash)
  - Parallel candle fetches via ThreadPoolExecutor (~20s vs ~60s)
  - classify_hourly_trend() — the #1 missing filter (the "$346 lesson")
  - Hard disqualifiers that skip assets entirely (not just penalize)
  - Cross-scan momentum (scoreDelta, scanStreak) via scan-history.json
  - Configurable thresholds via scanner-config.json
  - Per-TF error recovery (one failed fetch doesn't kill the asset)
  - Structured error output (every code path outputs valid JSON)
  - Multi-strategy position awareness (checks ALL strategies' DSL states)
  - First scan produces baseline results immediately (no cold start)

3-stage funnel, 4-pillar scoring. Scans all Hyperliquid perps.
"""

import json, sys, subprocess, time, os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add scripts dir to path for wolf_config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import get_all_active_positions, WORKSPACE, atomic_write

# --- Config ---
HISTORY_DIR = os.path.join(WORKSPACE, "history")
SCAN_HISTORY_FILE = os.path.join(HISTORY_DIR, "scan-history.json")
SCANNER_CONFIG_FILE = os.path.join(HISTORY_DIR, "scanner-config.json")

DEFAULT_CONFIG = {
    "topNDeep": 15,
    "minVolume24h": 500_000,
    "maxWorkers": 8,
    "pillarWeights": {
        "smartMoney": 0.25,
        "marketStructure": 0.25,
        "technicals": 0.25,
        "funding": 0.25
    },
    "macroModifiers": {
        "strong_downLong": -30,
        "strong_downShort": 15,
        "downLong": -15,
        "downShort": 8,
        "neutralLong": 0,
        "neutralShort": 0,
        "upLong": 8,
        "upShort": -15,
        "strong_upLong": 15,
        "strong_upShort": -30
    },
    "disqualifyThresholds": {
        "counterTrendStrength": 50,
        "extremeRsiLow": 20,
        "extremeRsiHigh": 80,
        "volumeDeadThreshold": 0.5,
        "heavyFundingAnnualized": 50,
        "btcHeadwindPoints": 30
    }
}


def deep_merge(base, override):
    """Recursively merge override into base. Override values win.
    For nested dicts, merge recursively instead of replacing."""
    result = dict(base)
    for key, value in override.items():
        if (key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    """Load scanner config with deep merge of user overrides."""
    try:
        with open(SCANNER_CONFIG_FILE) as f:
            user = json.load(f)
        return deep_merge(DEFAULT_CONFIG, user)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def log(level, msg):
    print(f"[{level.upper()}] {msg}", file=sys.stderr)


# --- Helpers ---

def fetch_json(payload):
    """Fetch from Hyperliquid info API."""
    try:
        r = subprocess.run(
            ["curl", "-s", "https://api.hyperliquid.xyz/info",
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(r.stdout)
    except Exception as e:
        log("warn", f"fetch_json failed: {e}")
        return None


def fetch_candles(coin, interval, hours):
    """Fetch candle data for a coin at given interval and lookback hours."""
    now_ms = int(time.time() * 1000)
    start = now_ms - (hours * 3600 * 1000)
    return fetch_json({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": now_ms}
    })


def fetch_mcporter(tool, args=""):
    """Call mcporter tool, return parsed JSON."""
    import tempfile
    tmp = tempfile.mktemp(suffix=".json")
    cmd = f"mcporter call senpi.{tool} --output json {args} > {tmp} 2>/dev/null"
    try:
        subprocess.run(cmd, shell=True, timeout=60)
        with open(tmp) as f:
            data = json.load(f)
        return data
    except Exception as e:
        log("warn", f"mcporter call failed: {e}")
        return {}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_ema(values, period):
    if not values:
        return []
    ema = [values[0]]
    k = 2 / (period + 1)
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def price_change(candles, hours_back):
    """Calculate price change over the last N hours from candle data."""
    if not candles or len(candles) < 2:
        return 0
    current = float(candles[-1]["c"])
    idx = max(0, len(candles) - hours_back)
    ref = float(candles[idx]["o"])
    return round((current - ref) / ref * 100, 2) if ref else 0


def volume_ratio(candles, recent_n=4):
    if len(candles) < 6:
        return 1.0
    recent = candles[-recent_n:]
    prior = candles[:-recent_n]
    if not prior:
        return 1.0
    recent_avg = sum(float(c["v"]) for c in recent) / len(recent)
    prior_avg = sum(float(c["v"]) for c in prior) / len(prior)
    if prior_avg == 0:
        return 1.0
    return round(recent_avg / prior_avg, 2)


def find_swing_levels(candles, lookback=5):
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(candles) - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            swing_lows.append(lows[i])
    return swing_highs[-3:] if swing_highs else [], swing_lows[-3:] if swing_lows else []


def detect_patterns(candles):
    if len(candles) < 3:
        return []
    patterns = []
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]

    def body(c): return abs(float(c["c"]) - float(c["o"]))
    def full_range(c): return float(c["h"]) - float(c["l"])
    def is_bullish(c): return float(c["c"]) > float(c["o"])
    def upper_wick(c): return float(c["h"]) - max(float(c["c"]), float(c["o"]))
    def lower_wick(c): return min(float(c["c"]), float(c["o"])) - float(c["l"])

    fr3, b3 = full_range(c3), body(c3)
    if fr3 > 0:
        if lower_wick(c3) > b3 * 2 and upper_wick(c3) < b3 * 0.5:
            patterns.append("hammer" if is_bullish(c3) else "inverted_hammer")
        if upper_wick(c3) > b3 * 2 and lower_wick(c3) < b3 * 0.5:
            patterns.append("shooting_star")
        if b3 < fr3 * 0.1:
            patterns.append("doji")

    if not is_bullish(c2) and is_bullish(c3):
        if float(c3["c"]) > float(c2["o"]) and float(c3["o"]) < float(c2["c"]):
            patterns.append("bullish_engulfing")
    if is_bullish(c2) and not is_bullish(c3):
        if float(c3["c"]) < float(c2["o"]) and float(c3["o"]) > float(c2["c"]):
            patterns.append("bearish_engulfing")
    if is_bullish(c1) and is_bullish(c2) and is_bullish(c3):
        if float(c3["c"]) > float(c2["c"]) > float(c1["c"]):
            patterns.append("three_soldiers")
    if not is_bullish(c1) and not is_bullish(c2) and not is_bullish(c3):
        if float(c3["c"]) < float(c2["c"]) < float(c1["c"]):
            patterns.append("three_crows")

    return patterns


# --- Stage 0: BTC Macro Context (NEW) ---

def fetch_btc_macro(config):
    """Analyze BTC 4h+1h trend for macro context."""
    try:
        candles_4h = fetch_candles("BTC", "4h", hours=168)
        candles_1h = fetch_candles("BTC", "1h", hours=24)
        if not candles_4h or not candles_1h:
            return {"trend": "neutral", "strength": 50, "chg1h": 0,
                    "modifier": {"LONG": 0, "SHORT": 0}, "error": "candle_fetch_failed"}

        closes_4h = [float(c["c"]) for c in candles_4h]
        ema5 = calc_ema(closes_4h, 5)
        ema13 = calc_ema(closes_4h, 13)
        if not ema5 or not ema13:
            return {"trend": "neutral", "strength": 50, "chg1h": 0,
                    "modifier": {"LONG": 0, "SHORT": 0}}

        diff = (ema5[-1] - ema13[-1]) / ema13[-1] * 100
        chg_1h = price_change(candles_1h, 1)

        if diff < -1.5 and chg_1h < -1:
            trend = "strong_down"
        elif diff < -0.5:
            trend = "down"
        elif diff > 1.5 and chg_1h > 1:
            trend = "strong_up"
        elif diff > 0.5:
            trend = "up"
        else:
            trend = "neutral"

        mods = config.get("macroModifiers", DEFAULT_CONFIG["macroModifiers"])
        return {
            "trend": trend,
            "strength": round(abs(diff) * 10),
            "chg1h": round(chg_1h, 2),
            "modifier": {
                "LONG": mods.get(f"{trend}Long", 0),
                "SHORT": mods.get(f"{trend}Short", 0)
            }
        }
    except Exception as e:
        log("warn", f"BTC macro failed: {e}")
        return {"trend": "neutral", "strength": 50, "chg1h": 0,
                "modifier": {"LONG": 0, "SHORT": 0}, "error": str(e)}


# --- classify_hourly_trend() — THE #1 MISSING FILTER ---

def classify_hourly_trend(candles_1h):
    """Analyze swing highs/lows in 1h data to determine trend.

    Returns "UP", "DOWN", or "NEUTRAL".
    Counter-trend on hourly = hard skip. This was the "$346 lesson."
    """
    if not candles_1h or len(candles_1h) < 6:
        return "NEUTRAL"

    highs = [float(c["h"]) for c in candles_1h]
    lows = [float(c["l"]) for c in candles_1h]
    closes = [float(c["c"]) for c in candles_1h]

    # Find swing points (lookback=2 for 1h timeframe)
    swing_highs = []
    swing_lows = []
    lb = 2
    for i in range(lb, len(candles_1h) - lb):
        if highs[i] == max(highs[i-lb:i+lb+1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-lb:i+lb+1]):
            swing_lows.append((i, lows[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Not enough structure, use EMA
        ema8 = calc_ema(closes, 8)
        ema21 = calc_ema(closes, 21)
        if ema8 and ema21:
            if ema8[-1] > ema21[-1] and closes[-1] > ema8[-1]:
                return "UP"
            elif ema8[-1] < ema21[-1] and closes[-1] < ema8[-1]:
                return "DOWN"
        return "NEUTRAL"

    # Check last 2 swing highs and lows
    last_highs = [h[1] for h in swing_highs[-2:]]
    last_lows = [l[1] for l in swing_lows[-2:]]

    higher_highs = last_highs[-1] > last_highs[-2]
    higher_lows = last_lows[-1] > last_lows[-2]
    lower_highs = last_highs[-1] < last_highs[-2]
    lower_lows = last_lows[-1] < last_lows[-2]

    if higher_highs and higher_lows:
        return "UP"
    elif lower_highs and lower_lows:
        return "DOWN"
    else:
        return "NEUTRAL"


# --- 4h Trend Analysis ---

def analyze_trend(candles_4h):
    if len(candles_4h) < 5:
        return "neutral", 0
    closes = [float(c["c"]) for c in candles_4h]
    ema_fast = calc_ema(closes, 5)
    ema_slow = calc_ema(closes, 13)
    if not ema_fast or not ema_slow:
        return "neutral", 0
    fast_now = ema_fast[-1]
    slow_now = ema_slow[-1]
    price_now = closes[-1]

    if fast_now > slow_now and price_now > fast_now:
        trend, strength = "strong_up", min(100, int((fast_now - slow_now) / slow_now * 1000))
    elif fast_now > slow_now:
        trend, strength = "up", min(70, int((fast_now - slow_now) / slow_now * 500))
    elif fast_now < slow_now and price_now < fast_now:
        trend, strength = "strong_down", min(100, int((slow_now - fast_now) / slow_now * 1000))
    elif fast_now < slow_now:
        trend, strength = "down", min(70, int((slow_now - fast_now) / slow_now * 500))
    else:
        trend, strength = "neutral", 0

    if len(ema_fast) >= 3 and len(ema_slow) >= 3:
        gap_now = abs(ema_fast[-1] - ema_slow[-1])
        gap_prev = abs(ema_fast[-3] - ema_slow[-3])
        if gap_now > gap_prev:
            strength = min(100, strength + 15)

    return trend, strength


# --- Hard Disqualifiers (NEW) ---

def check_disqualifiers(direction, hourly_trend, rsi1h, trend_4h, trend_strength,
                         vol_ratio_1h, vol_ratio_15m, funding_rate, btc_macro, config):
    """Six conditions that SKIP an asset entirely (not just penalize).
    Returns (disqualified: bool, reason: str or None).
    """
    thresholds = config.get("disqualifyThresholds", DEFAULT_CONFIG["disqualifyThresholds"])

    # 1. Counter-trend on hourly
    if direction == "LONG" and hourly_trend == "DOWN":
        return True, "Counter-trend: hourly structure is DOWN for LONG"
    if direction == "SHORT" and hourly_trend == "UP":
        return True, "Counter-trend: hourly structure is UP for SHORT"

    # 2. Extreme RSI
    if direction == "SHORT" and rsi1h < thresholds["extremeRsiLow"]:
        return True, f"Extreme RSI {rsi1h} < {thresholds['extremeRsiLow']} for SHORT (oversold bounce risk)"
    if direction == "LONG" and rsi1h > thresholds["extremeRsiHigh"]:
        return True, f"Extreme RSI {rsi1h} > {thresholds['extremeRsiHigh']} for LONG (overbought reversal risk)"

    # 3. Counter-trend on 4h with strength > threshold
    if direction == "LONG" and trend_4h in ("strong_down", "down") and trend_strength > thresholds["counterTrendStrength"]:
        return True, f"Counter-trend: 4h {trend_4h} with strength {trend_strength} > {thresholds['counterTrendStrength']}"
    if direction == "SHORT" and trend_4h in ("strong_up", "up") and trend_strength > thresholds["counterTrendStrength"]:
        return True, f"Counter-trend: 4h {trend_4h} with strength {trend_strength} > {thresholds['counterTrendStrength']}"

    # 4. Volume dying on both timeframes
    if vol_ratio_1h < thresholds["volumeDeadThreshold"] and vol_ratio_15m < thresholds["volumeDeadThreshold"]:
        return True, f"Volume dying: 1h={vol_ratio_1h}x, 15m={vol_ratio_15m}x (both < {thresholds['volumeDeadThreshold']})"

    # 5. Heavy unfavorable funding
    ann_rate = abs(funding_rate * 24 * 365 * 100)
    favorable = (direction == "LONG" and funding_rate <= 0) or (direction == "SHORT" and funding_rate >= 0)
    if not favorable and ann_rate > thresholds["heavyFundingAnnualized"]:
        return True, f"Heavy unfavorable funding: {ann_rate:.0f}% annualized against {direction}"

    # 6. BTC macro headwind
    btc_mod = btc_macro.get("modifier", {}).get(direction, 0)
    if btc_mod < -thresholds["btcHeadwindPoints"]:
        return True, f"BTC macro headwind: {btc_mod} points for {direction} (BTC trend: {btc_macro.get('trend')})"

    return False, None


# --- Scoring functions (each returns 0-100) ---

def score_smart_money(asset_data):
    if not asset_data:
        return 0, "LONG", {}
    pnl_pct = abs(asset_data.get("pnlContributionPct", 0))
    traders = asset_data.get("traderCount", 0)
    accel = asset_data.get("contributionChange4h", 0)
    direction = asset_data.get("dominantDirection", "LONG")

    score = 0
    if pnl_pct > 15: score += 50
    elif pnl_pct > 5: score += 35
    elif pnl_pct > 1: score += 20
    elif pnl_pct > 0.3: score += 10

    if traders > 300: score += 25
    elif traders > 100: score += 18
    elif traders > 30: score += 10
    elif traders > 10: score += 5

    if abs(accel) > 10: score += 20
    elif abs(accel) > 3: score += 12
    elif abs(accel) > 1: score += 6

    avg_at_peak = asset_data.get("avgAtPeak", 50)
    near_peak_pct = asset_data.get("nearPeakPct", 0)
    if avg_at_peak > 85: score += 15
    elif avg_at_peak > 70: score += 8
    elif avg_at_peak < 50: score -= 10
    if near_peak_pct > 50: score += 10

    details = {
        "pnlPct": round(pnl_pct, 1), "traders": traders,
        "accel": round(accel, 1), "direction": direction,
        "avgAtPeak": avg_at_peak, "nearPeakPct": near_peak_pct
    }
    return min(100, round(score)), direction, details


def compute_volume_trend(candles_1h):
    """Compare recent vs prior volume using 1h candles we already fetch."""
    if len(candles_1h) < 8:
        return 1.0  # Not enough data

    mid = len(candles_1h) // 2
    prior = candles_1h[:mid]
    recent = candles_1h[mid:]

    prior_avg = sum(float(c["v"]) for c in prior) / len(prior)
    recent_avg = sum(float(c["v"]) for c in recent) / len(recent)

    if prior_avg == 0:
        return 1.0
    return round(recent_avg / prior_avg, 2)


def score_market_structure(meta, technicals=None):
    """Pillar 2: Volume, OI, market dynamics."""
    vol24h = meta.get("volume24h", 0)
    oi = meta.get("openInterest", 0)
    score = 0

    # Volume magnitude
    if vol24h > 50_000_000: score += 30
    elif vol24h > 10_000_000: score += 20
    elif vol24h > 1_000_000: score += 10

    # Volume trend — use candle-derived ratio instead of broken prevDayVolume
    vol_trend = 1.0
    if technicals and "volumeTrend" in technicals:
        vol_trend = technicals["volumeTrend"]

    if vol_trend > 2.0: score += 30  # volume surge
    elif vol_trend > 1.3: score += 20
    elif vol_trend > 1.0: score += 10

    # OI significance
    if oi > 10_000_000: score += 20
    elif oi > 1_000_000: score += 10

    # OI to volume ratio
    if vol24h > 0:
        oi_vol = oi / vol24h
        if 0.3 < oi_vol < 3.0: score += 20

    details = {
        "vol24h": round(vol24h), "oi": round(oi),
        "volTrend": vol_trend
    }
    return min(100, round(score)), details


def score_technicals(tf_data, direction):
    score = 0
    rsi1h = tf_data.get("rsi1h", 50)
    rsi15m = tf_data.get("rsi15m", 50)
    vol1h = tf_data.get("volRatio1h", 1.0)
    vol15m = tf_data.get("volRatio15m", 1.0)
    trend = tf_data.get("trend4h", "neutral")
    patterns15m = tf_data.get("patterns15m", [])
    patterns1h = tf_data.get("patterns1h", [])
    momentum15m = tf_data.get("momentum15m", 0)
    divergence = tf_data.get("divergence")
    chg4h = tf_data.get("chg4h", 0)

    # 4H Trend alignment (0-20 pts)
    if direction == "LONG":
        if trend in ("strong_up", "up"): score += 20
        elif trend == "neutral": score += 5
        elif trend in ("strong_down", "down"): score -= 5
    else:
        if trend in ("strong_down", "down"): score += 20
        elif trend == "neutral": score += 5
        elif trend in ("strong_up", "up"): score -= 5

    # 1H RSI (0-20 pts)
    if direction == "LONG":
        if rsi1h < 30: score += 20
        elif rsi1h < 40: score += 15
        elif rsi1h < 55: score += 8
        elif rsi1h > 70: score -= 10
    else:
        if rsi1h > 70: score += 20
        elif rsi1h > 60: score += 15
        elif rsi1h > 45: score += 8
        elif rsi1h < 30: score -= 10

    # 15M RSI convergence (0-10 pts)
    if direction == "LONG":
        if rsi15m < 35 and rsi1h < 45: score += 10
        elif rsi15m < 40: score += 5
    else:
        if rsi15m > 65 and rsi1h > 55: score += 10
        elif rsi15m > 60: score += 5

    # Volume confirmation (0-15 pts)
    best_vol = max(vol1h, vol15m)
    if best_vol > 2.0: score += 15
    elif best_vol > 1.5: score += 10
    elif best_vol > 1.2: score += 5
    elif best_vol < 0.5: score -= 5

    # 15M patterns (0-15 pts)
    bullish_patterns = {"hammer", "bullish_engulfing", "three_soldiers", "doji"}
    bearish_patterns = {"shooting_star", "bearish_engulfing", "three_crows", "doji"}
    relevant = bullish_patterns if direction == "LONG" else bearish_patterns
    found = set(patterns15m) & relevant
    if found: score += min(15, len(found) * 8)
    found_1h = set(patterns1h) & relevant
    if found_1h: score += min(5, len(found_1h) * 3)

    # Momentum alignment (0-10 pts)
    if direction == "LONG" and momentum15m > 0.1: score += 10
    elif direction == "LONG" and momentum15m < -0.3: score += 5
    elif direction == "SHORT" and momentum15m < -0.1: score += 10
    elif direction == "SHORT" and momentum15m > 0.3: score += 5

    # 4h momentum (0-10 pts)
    if direction == "LONG" and chg4h > 1: score += 10
    elif direction == "LONG" and chg4h < -2: score += 7
    elif direction == "SHORT" and chg4h < -1: score += 10
    elif direction == "SHORT" and chg4h > 2: score += 7

    # Volume-price divergence (0-10 pts)
    if divergence == "bullish" and direction == "LONG": score += 10
    elif divergence == "bearish" and direction == "SHORT": score += 10
    elif divergence == "bullish" and direction == "SHORT": score -= 5
    elif divergence == "bearish" and direction == "LONG": score -= 5

    details = {
        "rsi1h": rsi1h, "rsi15m": rsi15m,
        "volRatio1h": vol1h, "volRatio15m": vol15m,
        "trend4h": trend, "trendStrength": tf_data.get("trendStrength", 0),
        "hourlyTrend": tf_data.get("hourlyTrend", "NEUTRAL"),
        "patterns15m": patterns15m, "patterns1h": patterns1h,
        "momentum15m": momentum15m, "divergence": divergence,
        "chg1h": tf_data.get("chg1h", 0), "chg4h": chg4h,
        "chg24h": tf_data.get("chg24h", 0),
        "support": tf_data.get("support"), "resistance": tf_data.get("resistance"),
    }
    return max(0, min(100, round(score))), details


def score_funding(funding_rate, direction):
    score = 0
    ann_rate = funding_rate * 24 * 365 * 100
    favorable = (direction == "LONG" and funding_rate <= 0) or \
                (direction == "SHORT" and funding_rate >= 0)

    if abs(ann_rate) < 5: score += 40
    elif abs(ann_rate) < 15: score += 25 if favorable else 15

    if favorable:
        if abs(ann_rate) > 50: score += 35
        elif abs(ann_rate) > 15: score += 25
        elif abs(ann_rate) > 5: score += 15
    else:
        if abs(ann_rate) > 50: score -= 20
        elif abs(ann_rate) > 15: score -= 10

    details = {
        "rate": round(funding_rate * 100, 4),
        "annualized": round(ann_rate, 1),
        "favorable": favorable
    }
    return max(0, min(100, round(score))), details


# --- Deep dive with parallel fetches ---

def deep_dive_asset(name, direction, meta, btc_macro, config):
    """Full multi-TF analysis for a single asset. Runs in thread pool."""
    result = {"asset": name, "direction": direction, "error": None}
    try:
        # Fetch 3 timeframes (per-TF error recovery)
        candles_4h = fetch_candles(name, "4h", hours=168) or []
        candles_1h = fetch_candles(name, "1h", hours=24) or []
        candles_15m = fetch_candles(name, "15m", hours=6) or []

        if not candles_1h:
            result["error"] = "no_1h_candles"
            return result

        closes_1h = [float(c["c"]) for c in candles_1h]
        closes_15m = [float(c["c"]) for c in candles_15m] if candles_15m else []

        # Hourly trend classification (THE #1 filter)
        hourly_trend = classify_hourly_trend(candles_1h)

        # 4h trend
        trend_4h, trend_strength = analyze_trend(candles_4h) if candles_4h else ("neutral", 0)

        # RSI
        rsi1h = calc_rsi(closes_1h)
        rsi15m = calc_rsi(closes_15m) if closes_15m else 50

        # Volume
        vol1h = volume_ratio(candles_1h, 4)
        vol15m = volume_ratio(candles_15m, 4) if candles_15m else 1.0

        # Swing levels
        swing_highs, swing_lows = find_swing_levels(candles_1h, 3)

        # Price changes
        chg1h = price_change(candles_1h, 1)
        chg4h = price_change(candles_1h, 4) if len(candles_1h) >= 4 else price_change(candles_1h, len(candles_1h))
        chg24h = price_change(candles_1h, len(candles_1h))

        # Patterns
        patterns15m = detect_patterns(candles_15m) if candles_15m and len(candles_15m) >= 3 else []
        patterns1h = detect_patterns(candles_1h)

        # 15m momentum
        momentum15m = 0
        if candles_15m and len(candles_15m) >= 4:
            recent_close = float(candles_15m[-1]["c"])
            hour_ago_open = float(candles_15m[-4]["o"])
            if hour_ago_open > 0:
                momentum15m = round((recent_close - hour_ago_open) / hour_ago_open * 100, 3)

        # Divergence
        divergence = None
        if candles_15m and len(candles_15m) >= 8:
            recent_vol = sum(float(c["v"]) for c in candles_15m[-4:])
            prior_vol = sum(float(c["v"]) for c in candles_15m[-8:-4])
            recent_chg = float(candles_15m[-1]["c"]) - float(candles_15m[-4]["c"])
            if prior_vol > 0:
                vol_surge = recent_vol / prior_vol
                if vol_surge > 1.5 and recent_chg < 0: divergence = "bullish"
                elif vol_surge > 1.5 and recent_chg > 0: divergence = "bearish"

        # Volume trend from 1h candles (fixes bug where prevDayVolume == vol24h)
        volume_trend = compute_volume_trend(candles_1h)

        result["technicals"] = {
            "hourlyTrend": hourly_trend,
            "trend4h": trend_4h, "trendStrength": trend_strength,
            "rsi1h": rsi1h, "rsi15m": rsi15m,
            "volRatio1h": vol1h, "volRatio15m": vol15m,
            "volumeTrend": volume_trend,
            "resistance": round(max(swing_highs), 4) if swing_highs else None,
            "support": round(min(swing_lows), 4) if swing_lows else None,
            "chg1h": chg1h, "chg4h": chg4h, "chg24h": chg24h,
            "patterns15m": patterns15m, "patterns1h": patterns1h,
            "momentum15m": momentum15m, "divergence": divergence,
        }
        result["candleCounts"] = {
            "4h": len(candles_4h), "1h": len(candles_1h), "15m": len(candles_15m)
        }
    except Exception as e:
        result["error"] = str(e)

    return result


# --- Cross-scan momentum ---

def load_scan_history():
    try:
        with open(SCAN_HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"scans": []}


def save_scan_history(history):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    atomic_write(SCAN_HISTORY_FILE, history)


def compute_momentum(asset, score, scan_history):
    """Compute scoreDelta and scanStreak from history."""
    scans = scan_history.get("scans", [])
    if not scans:
        return {"scoreDelta": 0, "scanStreak": 1, "isBaseline": True}

    last_scan = scans[-1].get("results", {})
    prev_score = last_scan.get(asset, {}).get("finalScore", 0)
    score_delta = score - prev_score if prev_score else 0

    # Count consecutive appearances
    streak = 0
    for scan in reversed(scans):
        if asset in scan.get("results", {}):
            streak += 1
        else:
            break

    return {"scoreDelta": score_delta, "scanStreak": streak + 1, "isBaseline": False}


# ===============================================
# MAIN
# ===============================================

def main():
    try:
        config = load_config()
        TOP_N_DEEP = config["topNDeep"]
        MIN_VOLUME_24H = config["minVolume24h"]
        W = config["pillarWeights"]

        scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Stage 0: BTC Macro Context ---
        log("info", "Stage 0: Fetching BTC macro context...")
        btc_macro = fetch_btc_macro(config)
        log("info", f"Stage 0: BTC trend={btc_macro['trend']}, strength={btc_macro['strength']}, mods={btc_macro['modifier']}")

        # --- Stage 1: Bulk screen ---
        log("info", "Stage 1: Fetching market structure for all assets...")
        meta_raw = fetch_json({"type": "metaAndAssetCtxs"})
        if not meta_raw or len(meta_raw) < 2:
            print(json.dumps({"success": False, "error": "Failed to fetch metaAndAssetCtxs", "stage": "stage1"}))
            sys.exit(1)

        meta_info = meta_raw[0]["universe"]
        meta_ctx = meta_raw[1]

        assets = {}
        for i, (info, ctx) in enumerate(zip(meta_info, meta_ctx)):
            name = info["name"]
            try:
                funding = float(ctx.get("funding", 0))
                vol24h = float(ctx.get("dayNtlVlm", 0))
                oi = float(ctx.get("openInterest", 0))
                mark = float(ctx.get("markPx", 0))
            except (ValueError, TypeError):
                continue
            if vol24h < MIN_VOLUME_24H:
                continue
            assets[name] = {
                "funding": funding, "volume24h": vol24h,
                "openInterest": oi, "markPrice": mark,
            }

        log("info", f"Stage 1: {len(assets)} assets pass volume filter (of {len(meta_info)} total)")

        # --- Stage 2: Smart money overlay ---
        log("info", "Stage 2: Fetching smart money data...")
        sm_by_asset = {}
        try:
            momentum_raw = fetch_mcporter("leaderboard_get_markets")
            markets_list = momentum_raw.get("data", {}).get("markets", {}).get("markets", [])
            for item in markets_list:
                name = item.get("token", "")
                if name not in assets:
                    continue
                pnl = float(item.get("pct_of_top_traders_gain", 0)) * 100
                accel = item.get("contribution_pct_change_4h")
                entry = {
                    "pnlContributionPct": pnl,
                    "traderCount": int(item.get("trader_count", 0)),
                    "contributionChange4h": float(accel) if accel is not None else 0.0,
                    "dominantDirection": item.get("direction", "long").upper()
                }
                if name not in sm_by_asset or pnl > sm_by_asset[name]["pnlContributionPct"]:
                    sm_by_asset[name] = entry

            # Freshness data
            try:
                top_traders_raw = fetch_mcporter("leaderboard_get_top", "-p limit=100")
                top_traders = top_traders_raw.get("data", {}).get("leaderboard", {}).get("data", [])
                from collections import defaultdict
                market_peaks = defaultdict(list)
                for t in top_traders:
                    upnl = t.get("unrealized_pnl", 0)
                    ath = t.get("ath_delta", 0)
                    ratio = upnl / ath if ath > 0 else 0
                    for m in t.get("top_markets", []):
                        market_peaks[m].append(ratio)
                for name in sm_by_asset:
                    if name in market_peaks:
                        ratios = market_peaks[name]
                        sm_by_asset[name]["avgAtPeak"] = round(sum(ratios) / len(ratios) * 100, 1)
                        sm_by_asset[name]["nearPeakPct"] = round(sum(1 for r in ratios if r > 0.85) / len(ratios) * 100, 1)
            except Exception as e:
                log("warn", f"Stage 2b: Peak data failed ({e})")
        except Exception as e:
            log("warn", f"Stage 2: SM fetch failed ({e})")

        log("info", f"Stage 2: Smart money data for {len(sm_by_asset)} assets")

        # Quick score all assets and pick top N
        quick_scores = []
        for name, meta in assets.items():
            sm = sm_by_asset.get(name, {})
            sm_score, direction, _ = score_smart_money(sm)
            if not sm:
                direction = "LONG" if meta["funding"] < 0 else "SHORT"
            ms_score, _ = score_market_structure(meta)
            fund_score, _ = score_funding(meta["funding"], direction)
            quick = (sm_score * W["smartMoney"] + ms_score * W["marketStructure"] + fund_score * W["funding"]) / (1 - W["technicals"])
            quick_scores.append((name, quick, direction))

        sm_top = sorted(sm_by_asset.items(), key=lambda x: x[1]["pnlContributionPct"], reverse=True)
        forced_assets = set(name for name, _ in sm_top[:8] if name in assets)

        quick_scores.sort(key=lambda x: x[1], reverse=True)
        top_names = set()
        top_assets = []
        for item in quick_scores:
            if len(top_assets) >= TOP_N_DEEP and item[0] not in forced_assets:
                continue
            if item[0] not in top_names:
                top_assets.append(item)
                top_names.add(item[0])
            if len(top_names) >= TOP_N_DEEP + len(forced_assets):
                break

        log("info", f"Stage 2: Top {len(top_assets)} for deep analysis")

        # --- Multi-strategy position awareness ---
        active_positions = get_all_active_positions()

        # --- Stage 3: Deep dive (PARALLEL) ---
        log("info", "Stage 3: Parallel candle fetches for top assets...")
        deep_results = {}
        max_workers = config.get("maxWorkers", 8)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(deep_dive_asset, n, d, assets[n], btc_macro, config): (n, d)
                for n, _, d in top_assets
            }
            for future in as_completed(futures):
                name, direction = futures[future]
                try:
                    result = future.result(timeout=30)
                    if result and not result.get("error"):
                        deep_results[result["asset"]] = result
                    elif result:
                        log("warn", f"  {name}: {result.get('error')}")
                except Exception as e:
                    log("warn", f"  {name}: deep dive exception: {e}")

        log("info", f"Stage 3: {len(deep_results)} assets deep-dived successfully")

        # --- Full scoring + disqualification ---
        results = []
        disqualified = []
        scan_history = load_scan_history()

        for name, quick, direction in top_assets:
            if name not in deep_results:
                continue

            dd = deep_results[name]
            tf_data = dd.get("technicals", {})

            # Check hard disqualifiers
            dq, dq_reason = check_disqualifiers(
                direction=direction,
                hourly_trend=tf_data.get("hourlyTrend", "NEUTRAL"),
                rsi1h=tf_data.get("rsi1h", 50),
                trend_4h=tf_data.get("trend4h", "neutral"),
                trend_strength=tf_data.get("trendStrength", 0),
                vol_ratio_1h=tf_data.get("volRatio1h", 1.0),
                vol_ratio_15m=tf_data.get("volRatio15m", 1.0),
                funding_rate=assets[name]["funding"],
                btc_macro=btc_macro,
                config=config
            )

            # Full 4-pillar scoring (compute even if disqualified for transparency)
            sm = sm_by_asset.get(name, {})
            sm_score, _, sm_details = score_smart_money(sm)
            ms_score, ms_details = score_market_structure(assets[name], technicals=tf_data)
            tech_score, tech_details = score_technicals(tf_data, direction)
            fund_score, fund_details = score_funding(assets[name]["funding"], direction)

            final = round(
                sm_score * W["smartMoney"] * 4 +
                ms_score * W["marketStructure"] * 4 +
                tech_score * W["technicals"] * 4 +
                fund_score * W["funding"] * 4
            )

            # Apply BTC macro modifier
            macro_mod = btc_macro.get("modifier", {}).get(direction, 0)
            final_with_macro = final + macro_mod

            # Cross-scan momentum
            momentum = compute_momentum(name, final_with_macro, scan_history)

            # Position conflict check
            conflict = name in active_positions
            existing_positions = active_positions.get(name, [])

            # Risk flags
            risks = []
            rsi1h = tech_details.get("rsi1h", 50)
            if direction == "LONG" and rsi1h > 65: risks.append("overbought RSI")
            elif direction == "SHORT" and rsi1h < 35: risks.append("oversold RSI")
            best_vol = max(tech_details.get("volRatio1h", 1), tech_details.get("volRatio15m", 1))
            if best_vol < 0.5: risks.append("volume dying")
            elif best_vol < 0.7: risks.append("volume declining")
            if not fund_details["favorable"]:
                risks.append(f"funding against you ({fund_details['annualized']:+.1f}% ann)")
            if abs(tech_details.get("chg24h", 0)) > 5:
                risks.append("extended move, may revert")
            trend = tech_details.get("trend4h", "neutral")
            if direction == "LONG" and trend in ("strong_down", "down"):
                risks.append("counter-trend (4h downtrend)")
            elif direction == "SHORT" and trend in ("strong_up", "up"):
                risks.append("counter-trend (4h uptrend)")

            entry = {
                "asset": name,
                "direction": direction,
                "finalScore": final_with_macro,
                "rawScore": final,
                "macroModifier": macro_mod,
                "pillarScores": {
                    "smartMoney": sm_score, "marketStructure": ms_score,
                    "technicals": tech_score, "funding": fund_score
                },
                "smartMoney": sm_details,
                "marketStructure": ms_details,
                "technicals": tech_details,
                "funding": fund_details,
                "markPrice": assets[name]["markPrice"],
                "risks": risks,
                "conflict": conflict,
                "existingPositions": existing_positions if conflict else None,
                "momentum": momentum,
            }

            if dq:
                entry["disqualified"] = True
                entry["disqualifyReason"] = dq_reason
                entry["wouldHaveScored"] = final_with_macro
                disqualified.append(entry)
            else:
                entry["disqualified"] = False
                results.append(entry)

            log("info", f"  {name}: score={final_with_macro} (raw={final}, macro={macro_mod:+d})"
                        f"{' [DQ: ' + dq_reason + ']' if dq else ''}"
                        f"{' [CONFLICT]' if conflict else ''}")

        # Sort by final score
        results.sort(key=lambda x: x["finalScore"], reverse=True)

        # Save scan history for cross-scan momentum
        new_scan_entry = {
            "time": scan_time,
            "results": {r["asset"]: {"finalScore": r["finalScore"], "direction": r["direction"]}
                        for r in results}
        }
        scan_history["scans"].append(new_scan_entry)
        if len(scan_history["scans"]) > 20:
            scan_history["scans"] = scan_history["scans"][-20:]
        save_scan_history(scan_history)

        # --- Output ---
        output = {
            "success": True,
            "scanTime": scan_time,
            "btcMacro": btc_macro,
            "assetsScanned": len(meta_info),
            "passedStage1": len(assets),
            "passedStage2": len(top_assets),
            "deepDived": len(deep_results),
            "qualified": len(results),
            "disqualifiedCount": len(disqualified),
            "pillarWeights": W,
            "opportunities": results[:15],
            "disqualified": disqualified[:5],
            "activePositions": {k: v for k, v in active_positions.items()},
            "scanHistory": {
                "totalScans": len(scan_history["scans"]),
                "isFirstScan": len(scan_history["scans"]) <= 1,
            }
        }

        print(json.dumps(output, indent=2))
        log("info", f"Done. {len(results)} qualified, {len(disqualified)} disqualified.")

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e), "stage": "unknown"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
