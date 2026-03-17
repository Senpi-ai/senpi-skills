#!/usr/bin/env python3
# Senpi BARRACUDA Scanner v1.0.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BARRACUDA v1.0 — Funding Decay Collector.

Finds assets where extreme funding persists for 6+ hours, confirmed by SM
alignment and trend structure. Enters to COLLECT funding while riding the trend.

The insight: Croc's pure funding arb lost -42.7% because it entered funding
trades without checking if SM or trend agreed. When funding flipped, Croc was
counter-trend and lost on both price AND funding. Barracuda requires:
  1. Extreme funding (annualized 30%+)
  2. Funding persistent for 6+ hours (not a spike)
  3. SM aligned in our direction (from Hyperfeed)
  4. 4H trend confirms direction
  5. RSI not extreme (don't buy overbought)
  6. OI stable (the imbalance is real, not evaporating)

The edge is double: price appreciation from the confirmed trend + funding
income from trapped traders on the other side.

Runs every 15 minutes.
"""

import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barracuda_config as cfg
from barracuda_lib import parse_candles, rsi, sma, volume_ratio

# ─── Hardcoded Constants ─────────────────────────────────────

MAX_LEVERAGE = 10
MIN_LEVERAGE = 5
MAX_POSITIONS = 3
MIN_FUNDING_ANN_PCT = 30
MIN_FUNDING_PERSISTENCE_HOURS = 6

BARRACUDA_DSL_TIERS = [
    {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

BARRACUDA_STAGNATION_TP = {"enabled": True, "roeMin": 8, "hwStaleMin": 120,
                            "_note": "Patient — funding positions need time to accumulate yield"}

# ─── Funding History Tracking ─────────────────────────────────

FUNDING_HISTORY_FILE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / \
    "skills" / "barracuda-strategy" / "state" / "funding-history.json"


def load_funding_history():
    try:
        if FUNDING_HISTORY_FILE.exists():
            with open(FUNDING_HISTORY_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def save_funding_history(history):
    FUNDING_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfg.atomic_write(str(FUNDING_HISTORY_FILE), history)


def update_funding_history(instruments):
    """Snapshot current funding rates and track persistence."""
    history = load_funding_history()
    now = time.time()

    for inst in instruments:
        name = inst.get("name", "")
        if not name or inst.get("is_delisted"):
            continue
        ctx = inst.get("context", {})
        funding = float(ctx.get("funding", 0))
        funding_ann = abs(funding) * 3 * 365 * 100

        if name not in history:
            history[name] = {"snapshots": [], "currentDirection": None, "streakStarted": None}

        entry = history[name]

        # Track direction persistence
        current_dir = "SHORT" if funding > 0 else "LONG" if funding < 0 else None

        if current_dir and funding_ann >= MIN_FUNDING_ANN_PCT:
            if entry.get("currentDirection") == current_dir:
                # Same direction — streak continues
                pass
            else:
                # Direction changed or new — reset streak
                entry["currentDirection"] = current_dir
                entry["streakStarted"] = now
        else:
            # Funding not extreme — reset
            entry["currentDirection"] = None
            entry["streakStarted"] = None

        # Keep last 48 snapshots (12 hours at 15 min intervals)
        entry["snapshots"].append({"ts": now, "funding": funding, "ann": funding_ann})
        entry["snapshots"] = entry["snapshots"][-48:]

    save_funding_history(history)
    return history


def get_funding_persistence_hours(asset, history):
    """How many hours has this asset had extreme funding in the same direction?"""
    entry = history.get(asset, {})
    started = entry.get("streakStarted")
    if not started or not entry.get("currentDirection"):
        return 0
    return (time.time() - started) / 3600


# ─── SM Data ─────────────────────────────────────────────────

def get_sm_data():
    """Fetch SM market data once, return as dict keyed by token."""
    data = cfg.mcporter_call("leaderboard_get_markets", limit=50)
    if not data or not data.get("success"):
        return {}

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    sm = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        direction = m.get("direction", "").upper()
        pct = float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        if token not in sm or pct > sm[token].get("pct", 0):
            sm[token] = {"direction": direction, "pct": pct, "traders": traders}
    return sm


# ─── Asset Analysis ──────────────────────────────────────────

def analyze_funding_opportunity(asset, inst_ctx, funding_history, sm_data):
    """Analyze a single asset for funding collection opportunity."""
    funding = float(inst_ctx.get("funding", 0))
    funding_ann = abs(funding) * 3 * 365 * 100

    if funding_ann < MIN_FUNDING_ANN_PCT:
        return None

    # Direction: collect funding by going opposite to the crowd
    direction = "SHORT" if funding > 0 else "LONG"

    # Gate 1: Funding must be persistent (6+ hours)
    persistence_hours = get_funding_persistence_hours(asset, funding_history)
    if persistence_hours < MIN_FUNDING_PERSISTENCE_HOURS:
        return None

    # Gate 2: SM must be aligned (from Hyperfeed)
    sm_info = sm_data.get(asset, {})
    sm_aligned = sm_info.get("direction", "") == direction
    if not sm_aligned:
        return None  # Hard block — don't collect funding against SM

    # Gate 3: Trend must confirm — fetch candle data
    candle_data = cfg.mcporter_call("market_get_asset_data", asset=asset,
                                     candle_intervals=["1h", "4h"],
                                     include_funding=False, include_order_book=False)
    if not candle_data or not candle_data.get("success"):
        return None

    data = candle_data.get("data", candle_data)
    candles_4h = data.get("candles", {}).get("4h", [])
    candles_1h = data.get("candles", {}).get("1h", [])

    if len(candles_4h) < 25 or len(candles_1h) < 12:
        return None

    _, _, _, c4, _ = parse_candles(candles_4h)

    # 4H SMA trend
    sma20 = sma(c4, 20)
    if sma20[-1] and sma20[-5]:
        sma_trend = "UP" if sma20[-1] > sma20[-5] else "DOWN"
    else:
        sma_trend = None

    trend_aligned = (direction == "LONG" and sma_trend == "UP") or \
                    (direction == "SHORT" and sma_trend == "DOWN")

    if not trend_aligned:
        return None  # Hard block — don't collect funding counter-trend

    # Gate 4: RSI not extreme
    rsi_values = rsi(c4, 14)
    current_rsi = rsi_values[-1]
    if current_rsi:
        if direction == "LONG" and current_rsi > 72:
            return None
        if direction == "SHORT" and current_rsi < 28:
            return None

    # Gate 5: OI stable (the funding imbalance is real)
    oi = float(data.get("asset_context", {}).get("openInterest", inst_ctx.get("openInterest", 0)))

    # Calculate yield
    max_lev = inst_ctx.get("max_leverage", 10)
    leverage = min(max_lev, MAX_LEVERAGE)
    if leverage < MIN_LEVERAGE:
        return None

    daily_yield_pct = abs(funding) * 3 * leverage * 100
    weekly_yield_pct = daily_yield_pct * 7

    # Score
    score = 0
    reasons = []

    score += 3
    reasons.append(f"extreme_funding_{funding_ann:.0f}%_ann")

    score += 2
    reasons.append(f"persistent_{persistence_hours:.1f}h")

    score += 2
    reasons.append(f"sm_aligned_{sm_info.get('pct', 0):.0f}%_{sm_info.get('traders', 0)}traders")

    score += 1
    reasons.append(f"trend_confirmed_{sma_trend}")

    if daily_yield_pct > 5:
        score += 1
        reasons.append(f"high_yield_{daily_yield_pct:.1f}%/day")

    if sm_info.get("pct", 0) > 0.3:
        score += 1
        reasons.append("sm_strongly_tilted")

    return {
        "asset": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "fundingRate8h": round(funding * 100, 4),
        "fundingAnnPct": round(funding_ann, 1),
        "persistenceHours": round(persistence_hours, 1),
        "dailyYieldPct": round(daily_yield_pct, 2),
        "weeklyYieldPct": round(weekly_yield_pct, 1),
        "leverage": leverage,
        "smAligned": True,
        "trendAligned": True,
        "rsi": round(current_rsi, 1) if current_rsi else None,
    }


# ─── DSL State Builder ───────────────────────────────────────

def build_dsl_state_template(asset, direction, score):
    return {
        "active": True,
        "asset": asset,
        "direction": direction,
        "score": score,
        "phase": 1,
        "highWaterPrice": None,
        "highWaterRoe": None,
        "currentTierIndex": -1,
        "consecutiveBreaches": 0,
        "lockMode": "pct_of_high_water",
        "phase2TriggerRoe": 5,
        "phase1": {
            "enabled": True,
            "retraceThreshold": 0.03,
            "consecutiveBreachesRequired": 3,
            "phase1MaxMinutes": 0,
            "weakPeakCutMinutes": 0,
            "deadWeightCutMin": 30,
            "absoluteFloorRoe": -20,
        },
        "phase2": {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": BARRACUDA_DSL_TIERS,
        "stagnationTp": BARRACUDA_STAGNATION_TP,
        "execution": {"phase1SlOrderType": "MARKET", "phase2SlOrderType": "MARKET", "breachCloseOrderType": "MARKET"},
        "_barracuda_version": "1.0.1",
        "_note": "Funding position — wider stops, patient stagnation TP. Yield accumulates over time.",
    }


# ─── Main ─────────────────────────────────────────────────────

def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # Check trade counter / gate
    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"gate={tc['gate']}"})
        return

    # Check daily entry limit
    max_entries = config.get("risk", {}).get("maxEntriesPerDay", 3)
    if tc.get("entries", 0) >= max_entries:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        return

    # Check position count
    account_value, positions = cfg.get_positions(wallet)
    if len(positions) >= MAX_POSITIONS:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"max positions ({len(positions)}/{MAX_POSITIONS})"})
        return

    active_coins = {p["coin"] for p in positions}

    # Fetch all instruments
    instruments = cfg.get_all_instruments()
    if not instruments:
        cfg.output({"status": "error", "error": "failed to fetch instruments"})
        return

    # Update funding history
    funding_history = update_funding_history(instruments)

    # Fetch SM data once
    sm_data = get_sm_data()

    # Find candidates with extreme persistent funding
    signals = []
    scanned = 0

    for inst in instruments:
        name = inst.get("name", "")
        if not name or inst.get("is_delisted"):
            continue
        if name.lower().startswith("xyz:"):
            continue
        # Skip assets we already hold
        if name in active_coins:
            continue

        ctx = inst.get("context", {})
        funding = float(ctx.get("funding", 0))
        funding_ann = abs(funding) * 3 * 365 * 100

        if funding_ann < MIN_FUNDING_ANN_PCT:
            continue

        scanned += 1
        result = analyze_funding_opportunity(name, ctx, funding_history, sm_data)
        if result and result["score"] >= config.get("entry", {}).get("minScore", 8):
            signals.append(result)

    signals.sort(key=lambda s: s["score"], reverse=True)

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"scanned {scanned} extreme funding, none qualified"})
        return

    best = signals[0]
    margin = round(account_value * 0.25, 2)

    cfg.output({
        "status": "ok",
        "signal": best,
        "entry": {
            "coin": best["asset"],
            "direction": best["direction"],
            "leverage": best["leverage"],
            "margin": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
        },
        "dslState": build_dsl_state_template(best["asset"], best["direction"], best["score"]),
        "constraints": {
            "minLeverage": MIN_LEVERAGE,
            "maxLeverage": MAX_LEVERAGE,
            "maxPositions": MAX_POSITIONS,
            "stagnationTp": BARRACUDA_STAGNATION_TP,
            "_dslNote": "Use dslState as the DSL state file. Do NOT merge with dsl-profile.json.",
        },
        "allSignals": signals[:3],
        "scanned": scanned,
    })


if __name__ == "__main__":
    run()
