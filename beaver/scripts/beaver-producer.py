#!/usr/bin/env python3
# Senpi BEAVER Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BEAVER v1.0.0 — BTC trend follower with Smart-Money direction gate.

Onboarding strategy. Single asset (BTC). Long OR short based on the
confluence of 4h trend structure + Senpi Smart-Money leaderboard
direction. Wide DSL Phase 2 ladder so winners can ride trends.

Producer ticks every 300s (5 min). Each tick:
  1. Pull BTC candles (1h + 4h) and SM market concentration.
  2. Compute 4h trend (higher-lows / lower-highs structure).
  3. Determine direction: 4h trend + SM agreement → LONG or SHORT.
  4. Score 5 components, max ~9. minScore floor 5.
  5. Push signal via senpi_runtime_helpers if score >= floor.

The runtime's LLM-gated `beaver_entry` action opens the position via
FEE_OPTIMIZED_LIMIT. DSL handles all exits (Phase 1 max_loss 20% /
retrace 8, Phase 2 Bison-pattern wide ladder, time-cuts disabled).

Producer NEVER closes positions. Producer NEVER reasons about exits.
The DSL is the only exit path.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import beaver_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
SCANNER_NAME = "beaver_signals"
SIGNAL_TYPE = "BEAVER_BTC_TREND"

# Hardcoded — Beaver is BTC-only. Forks (Heron=ETH, Hummingbird=HYPE)
# substitute the asset string and use the same scaffold.
ASSET = "BTC"

# Scoring constants — onboarding-tier simplicity. Max ~9 points.
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5

# Direction-confirmation thresholds (config-overridable)
SM_TILT_MIN_PCT_DEFAULT = 60
SM_STRONG_TILT_PCT_DEFAULT = 70

# Onboarding floor — 5 points typically requires 4h trend + 1h
# confirmation + SM aligned. Below that, no entry.
MIN_SCORE_DEFAULT = 5


def _resolve_wallet():
    env_val = (os.environ.get("BEAVER_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers
# ═══════════════════════════════════════════════════════════════

def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH.

    Returns (label, strength_pct) where strength_pct is the fraction
    of recent candles that confirmed the structure.
    """
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def volume_trend(candles, lookback=6):
    """% change in avg(recent 3) vs avg(prior 3) volume."""
    if len(candles) < lookback:
        return 0.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-lookback:]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    """Pull 1h + 4h candles for the asset."""
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h", "4h"],
        include_funding=False,
        include_order_book=False,
    )


def fetch_sm_direction(asset):
    """Return (direction, tilt_pct) for SM concentration on this asset.

    direction ∈ {"LONG", "SHORT", "NEUTRAL"}.
    tilt_pct is the percent of top-trader long/short concentration,
    e.g. 70 = "70% of top traders are positioned long here."
    """
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw or not raw.get("success", True):
        return None, 0.0
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    long_pct = 0.0
    short_pct = 0.0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset:
            continue
        found = True
        direction = str(m.get("direction", "")).upper()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if direction == "LONG":
            long_pct = pct
        elif direction == "SHORT":
            short_pct = pct

    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


# ═══════════════════════════════════════════════════════════════
# Thesis builder
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    """Score a BTC entry. Returns dict with direction + score, or None.

    Direction logic:
      1. 4h trend != NEUTRAL  (required)
      2. SM direction agrees with 4h trend  (required)
      3. SM tilt >= smTiltMinPct  (required, default 60)

    Score components (max ~9):
      4h trend aligned     +3
      1h confirms 4h       +2
      SM aligned           +2
      SM strongly tilted   +1  (when smTilt >= smStrongTiltPct)
      Volume rising        +1
    """
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    trend_4h, str_4h = trend_structure(candles_4h)
    trend_1h, str_1h = trend_structure(candles_1h)
    if trend_4h == "NEUTRAL":
        return None

    # SM gate
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_tilt_min = float(entry_cfg.get("smTiltMinPct", SM_TILT_MIN_PCT_DEFAULT))
    sm_strong_pct = float(entry_cfg.get("smStrongTiltPct", SM_STRONG_TILT_PCT_DEFAULT))
    if sm_dir not in ("LONG", "SHORT"):
        return None
    if sm_tilt < sm_tilt_min:
        return None

    # 4h trend direction and SM direction must agree
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"
    if sm_dir != direction:
        return None

    score = 0
    reasons = []

    # 4h trend aligned
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{str_4h:.0%}")

    # 1h confirms 4h
    if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{trend_1h.lower()}")

    # SM aligned
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    # SM strongly tilted
    if sm_tilt >= sm_strong_pct:
        score += 1
        reasons.append("sm_strongly_tilted")

    # Volume rising
    vol_pct = volume_trend(candles_1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": trend_4h,
        "trend_4h_strength": str_4h,
        "trend_1h": trend_1h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": vol_pct,
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False

    # On-chain held-asset dedup (defense in depth — runtime per_asset_cooldown is authoritative)
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "trend4h": thesis["trend_4h"],
        "trend4hStrength": thesis["trend_4h_strength"],
        "trend1h": thesis["trend_1h"],
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis["sm_tilt_pct"],
        "volumeTrendPct": thesis["volume_trend_pct"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / 9.0, 1.0),  # normalize 0..1 (max ~9)
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {thesis['coin']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {thesis['coin']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. Daemon owns the per-tick scanner_lock.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    asset = config.get("asset", ASSET).upper()
    entry_cfg = config

    if not STRATEGY_ADDRESS:
        cfg.output({
            "status": "error",
            "reason": "no_wallet",
            "_beaver_producer_version": VERSION,
        })
        return

    # Account state + held positions
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({
            "status": "ok",
            "note": "no account value",
            "_beaver_producer_version": VERSION,
        })
        return

    # Already holding this asset → nothing to do; DSL manages exit.
    if asset in {h.upper() for h in held_assets}:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "note": f"RIDING {asset}. DSL manages exit.",
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "_beaver_producer_version": VERSION,
        })
        return

    # Race-window dedup BEFORE building the thesis (saves MCP cost)
    if cfg.was_recently_signaled(asset):
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "note": f"DEDUP_SKIP {asset} pushed within {cfg.RECENT_SIGNAL_TTL_SEC}s",
            "held_assets": held_assets,
            "elapsed_sec": round(elapsed, 2),
            "_beaver_producer_version": VERSION,
        })
        return

    # Score the thesis
    thesis = build_thesis(asset, entry_cfg)
    if thesis is None:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "note": "WAITING — no qualifying setup (4h trend + SM gate not aligned)",
            "elapsed_sec": round(elapsed, 2),
            "_beaver_producer_version": VERSION,
        })
        return

    min_score = int(entry_cfg.get("minScore", MIN_SCORE_DEFAULT))
    if thesis["score"] < min_score:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "note": f"score_low {thesis['score']}/{min_score}",
            "direction": thesis["direction"],
            "reasons": thesis["reasons"][:5],
            "elapsed_sec": round(elapsed, 2),
            "_beaver_producer_version": VERSION,
        })
        return

    # Sizing — onboarding default: 25% of equity, 5x leverage.
    margin_pct = float(config.get("marginPct", 0.25))
    margin_usd = round(account_value * margin_pct, 2)
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)

    pushed = push_signal(thesis, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(thesis["coin"])

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": thesis["coin"],
            "direction": thesis["direction"],
            "score": thesis["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": thesis["reasons"][:5],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_beaver_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"beaver-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
