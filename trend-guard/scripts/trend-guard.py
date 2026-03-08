#!/usr/bin/env python3
"""
Trend Guard — Hourly trend classifier for Hyperliquid assets.

Standalone usage:
    TREND_ASSET=HYPE python3 trend-guard.py
    TREND_ASSET=HYPE TREND_DIRECTION=LONG python3 trend-guard.py

Batch mode (hourly cron — classifies all SM top-50 assets, writes trend-cache.json):
    TREND_BATCH=1 python3 trend-guard.py

Importable usage:
    from trend_guard import classify_trend
    result = classify_trend("HYPE", direction="LONG")
"""

import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
TREND_CACHE_FILE = os.environ.get("TREND_CACHE", os.path.join(WORKSPACE, "trend-cache.json"))


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def atomic_write(path, data):
    """Write JSON atomically — crash-safe."""
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# MCP helper (single-script skill — no separate _config.py needed)
# ---------------------------------------------------------------------------

def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter. Returns the `data` portion of the response."""
    args = []
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, (list, dict, bool)):
            args.append(f"{k}={json.dumps(v)}")
        else:
            args.append(f"{k}={v}")

    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd_str = " ".join(
        [shlex.quote(mcporter_bin), "call", shlex.quote(f"senpi.{tool}")]
        + [shlex.quote(a) for a in args]
    )
    last_error = None

    for attempt in range(retries):
        fd, tmp = None, None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            subprocess.run(
                f"{cmd_str} > {shlex.quote(tmp)} 2>/dev/null",
                shell=True, timeout=timeout,
            )
            with open(tmp) as f:
                d = json.load(f)
            if d.get("success"):
                return d.get("data", {})
            last_error = d.get("error", d)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
            last_error = str(e)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        if attempt < retries - 1:
            time.sleep(3)

    raise RuntimeError(f"mcporter {tool} failed after {retries} attempts: {last_error}")


def mcporter_call_safe(tool, retries=3, timeout=30, **kwargs):
    """Like mcporter_call but returns None instead of raising on failure."""
    try:
        return mcporter_call(tool, retries=retries, timeout=timeout, **kwargs)
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# EMA computation
# ---------------------------------------------------------------------------

def compute_ema(values, period):
    """Compute EMA over a list of values. Returns list of the same length."""
    if not values or period <= 0:
        return []
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


# ---------------------------------------------------------------------------
# Swing high / low detection
# ---------------------------------------------------------------------------

def find_swing_highs(values, lookback=3):
    """Return list of (index, value) for local maxima using ±lookback window."""
    swings = []
    for i in range(lookback, len(values) - lookback):
        window = values[i - lookback: i + lookback + 1]
        if values[i] == max(window):
            swings.append((i, values[i]))
    return swings


def find_swing_lows(values, lookback=3):
    """Return list of (index, value) for local minima using ±lookback window."""
    swings = []
    for i in range(lookback, len(values) - lookback):
        window = values[i - lookback: i + lookback + 1]
        if values[i] == min(window):
            swings.append((i, values[i]))
    return swings


# ---------------------------------------------------------------------------
# Trend classification
# ---------------------------------------------------------------------------

def _classify_from_candles(candles):
    """
    Internal: classify trend from a list of candle dicts.
    Each candle must have keys: 'c' (close), 'h' (high), 'l' (low), 'v' (volume).
    Returns (trend, strength, ema5_last, ema13_last).
    """
    if len(candles) < 8:
        return "NEUTRAL", 0, None, None

    closes = [float(c["c"]) for c in candles]
    highs  = [float(c["h"]) for c in candles]
    lows   = [float(c["l"]) for c in candles]
    vols   = [float(c.get("v", 0)) for c in candles]

    # EMA-5 and EMA-13
    ema5_series  = compute_ema(closes, 5)
    ema13_series = compute_ema(closes, 13)
    ema5_last  = ema5_series[-1]
    ema13_last = ema13_series[-1]

    # EMA separation as % of price
    mid_price = (ema5_last + ema13_last) / 2.0
    ema_sep_pct = abs(ema5_last - ema13_last) / mid_price * 100 if mid_price else 0

    ema_bullish = ema5_last > ema13_last
    ema_bearish = ema5_last < ema13_last
    ema_flat    = ema_sep_pct < 0.1

    # Swing structure
    swing_highs = find_swing_highs(highs, lookback=3)
    swing_lows  = find_swing_lows(lows,  lookback=3)

    higher_lows  = False
    lower_highs  = False

    if len(swing_lows) >= 2:
        recent_lows = [v for _, v in swing_lows[-3:]]
        higher_lows = all(recent_lows[i] > recent_lows[i - 1] for i in range(1, len(recent_lows)))

    if len(swing_highs) >= 2:
        recent_highs = [v for _, v in swing_highs[-3:]]
        lower_highs  = all(recent_highs[i] < recent_highs[i - 1] for i in range(1, len(recent_highs)))

    # Primary classification
    if ema_flat:
        trend = "NEUTRAL"
    elif higher_lows and ema_bullish:
        trend = "UP"
    elif lower_highs and ema_bearish:
        trend = "DOWN"
    elif ema_bullish and not lower_highs:
        trend = "UP"
    elif ema_bearish and not higher_lows:
        trend = "DOWN"
    else:
        trend = "NEUTRAL"

    # -----------------------------------------------------------------------
    # Strength score (0–100)
    # -----------------------------------------------------------------------

    # 1. EMA separation component (0–40 pts)
    # Cap at 2% separation = full 40 pts
    ema_pts = min(ema_sep_pct / 2.0, 1.0) * 40

    # 2. Swing structure consistency (0–40 pts)
    swing_pts = 0.0
    if trend == "UP" and higher_lows:
        swing_pts += 20
        if len(swing_highs) >= 2:
            recent_highs = [v for _, v in swing_highs[-3:]]
            higher_highs = all(recent_highs[i] > recent_highs[i - 1] for i in range(1, len(recent_highs)))
            if higher_highs:
                swing_pts += 20
    elif trend == "DOWN" and lower_highs:
        swing_pts += 20
        if len(swing_lows) >= 2:
            recent_lows = [v for _, v in swing_lows[-3:]]
            lower_lows = all(recent_lows[i] < recent_lows[i - 1] for i in range(1, len(recent_lows)))
            if lower_lows:
                swing_pts += 20

    # 3. Volume confirmation (0–20 pts)
    # Compare average volume on up-candles vs down-candles
    vol_pts = 0.0
    if trend in ("UP", "DOWN") and len(candles) >= 4:
        up_vols   = [vols[i] for i in range(len(candles)) if float(candles[i]["c"]) >= float(candles[i]["o"])]
        down_vols = [vols[i] for i in range(len(candles)) if float(candles[i]["c"]) <  float(candles[i]["o"])]
        avg_up   = sum(up_vols)   / len(up_vols)   if up_vols   else 0
        avg_down = sum(down_vols) / len(down_vols) if down_vols else 0
        total_avg = (avg_up + avg_down) / 2 if (avg_up + avg_down) > 0 else 1
        if trend == "UP" and avg_up > avg_down:
            vol_pts = min(avg_up / total_avg - 1, 1.0) * 20
        elif trend == "DOWN" and avg_down > avg_up:
            vol_pts = min(avg_down / total_avg - 1, 1.0) * 20

    strength = int(round(ema_pts + swing_pts + vol_pts))
    strength = max(0, min(100, strength))

    return trend, strength, ema5_last, ema13_last


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_trend(asset, direction=None):
    """
    Classify the hourly trend for a Hyperliquid asset.

    Args:
        asset:     Asset symbol, e.g. "HYPE", "BTC"
        direction: Optional — "LONG" or "SHORT". When provided, adds `aligned` field.

    Returns:
        dict with keys:
            asset, trend, strength, ema5, ema13, candles_used, timestamp
            + aligned, alignedDirection  (only when direction is provided)
            + error  (only on failure — trend will be "NEUTRAL")
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    base_result = {
        "asset": asset,
        "trend": "NEUTRAL",
        "strength": 0,
        "ema5": None,
        "ema13": None,
        "candles_used": 0,
        "timestamp": ts,
    }
    if direction:
        base_result["alignedDirection"] = direction.upper()
        base_result["aligned"] = True  # default safe

    data = mcporter_call_safe(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h"],
        include_order_book=False,
        include_funding=False,
        timeout=30,
    )

    if not data:
        base_result["error"] = "MCP call failed or returned empty response"
        return base_result

    # Extract 1h candles — market_get_asset_data returns candles under "candles"
    candles_map = data.get("candles", {})
    candles_1h  = candles_map.get("1h", [])

    # Fallback: some versions nest differently
    if not candles_1h:
        candles_1h = data.get("candles_1h", [])

    if len(candles_1h) < 8:
        base_result["error"] = f"Insufficient candle data: {len(candles_1h)} candles (need >= 8)"
        base_result["candles_used"] = len(candles_1h)
        return base_result

    # Use up to last 24 candles
    candles_used = candles_1h[-24:]

    trend, strength, ema5, ema13 = _classify_from_candles(candles_used)

    result = {
        "asset": asset,
        "trend": trend,
        "strength": strength,
        "ema5": round(ema5, 6) if ema5 is not None else None,
        "ema13": round(ema13, 6) if ema13 is not None else None,
        "candles_used": len(candles_used),
        "timestamp": ts,
    }

    if direction:
        d = direction.upper()
        result["alignedDirection"] = d
        if trend == "NEUTRAL":
            result["aligned"] = True  # don't block neutral
        elif d == "LONG":
            result["aligned"] = (trend == "UP")
        elif d == "SHORT":
            result["aligned"] = (trend == "DOWN")
        else:
            result["aligned"] = True  # unknown direction — don't block

    return result


# ---------------------------------------------------------------------------
# Batch mode — classifies all SM top-50 assets, writes trend-cache.json
# ---------------------------------------------------------------------------

def run_batch_mode():
    """
    Fetch SM leaderboard top 50, classify hourly trend for each asset,
    write results to trend-cache.json. Runs as an hourly cron at :02.
    Output: summary JSON with counts and cache path.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch leaderboard to get the asset list
    data = mcporter_call_safe("leaderboard_get_markets", limit=100)
    if not data:
        print(json.dumps({"status": "error", "error": "leaderboard fetch failed", "timestamp": ts}))
        sys.exit(1)

    raw_markets = data.get("markets", {}).get("markets", [])
    if not raw_markets:
        print(json.dumps({"status": "error", "error": "empty leaderboard response", "timestamp": ts}))
        sys.exit(1)

    # Deduplicate — same token can appear as both LONG and SHORT; classify once per token
    seen = set()
    assets = []
    for m in raw_markets[:50]:
        token = m.get("token", "")
        dex   = m.get("dex", "")
        key   = f"xyz:{token}" if dex == "xyz" else token
        if key not in seen and token:
            seen.add(key)
            assets.append(key)

    # Classify each asset
    cache = {}
    ok_count = 0
    err_count = 0
    for asset in assets:
        result = classify_trend(asset)
        entry = {
            "trend":       result["trend"],
            "strength":    result["strength"],
            "ema5":        result["ema5"],
            "ema13":       result["ema13"],
            "computedAt":  result["timestamp"],
        }
        if "error" in result:
            entry["error"] = result["error"]
            err_count += 1
        else:
            ok_count += 1
        cache[asset] = entry

    atomic_write(TREND_CACHE_FILE, cache)

    print(json.dumps({
        "status": "ok",
        "assetsClassified": ok_count,
        "assetsErrored": err_count,
        "cacheFile": TREND_CACHE_FILE,
        "timestamp": ts,
    }))


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    # Batch mode: classify all SM top-50 assets and write cache
    if os.environ.get("TREND_BATCH", "").strip() == "1":
        run_batch_mode()
        return

    # Single-asset mode
    asset     = os.environ.get("TREND_ASSET", "").strip()
    direction = os.environ.get("TREND_DIRECTION", "").strip() or None

    if not asset:
        print(json.dumps({
            "error": "TREND_ASSET or TREND_BATCH=1 environment variable is required",
            "usage": "TREND_ASSET=HYPE python3 trend-guard.py  OR  TREND_BATCH=1 python3 trend-guard.py",
        }))
        sys.exit(1)

    result = classify_trend(asset, direction=direction)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
