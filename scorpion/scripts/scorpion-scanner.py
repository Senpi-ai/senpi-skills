#!/usr/bin/env python3
# Senpi SCORPION Scanner v2.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SCORPION v2.1 — Altcoin swarm hunter (Python, 10 gates, mcporter)."""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorpion_config as cfg

MAX_ENTRIES_PER_DAY = 3
COOLDOWN_MINUTES = 90
PER_ASSET_COOLDOWN_MINUTES = 120
MIN_SCORE = 5
MARGIN_AMOUNT = 350
MAX_POSITIONS = 1
LEVERAGE = 5
SAME_DIR_COOLDOWN_MINUTES = 60

MAJOR_ASSETS = {"BTC", "ETH", "SOL", "HYPE"}
MIN_SWARM_SM_PCT = 2.0
MIN_SWARM_COUNT = 5
XYZ_BANNED_TRADING = True

LEVERAGE_OVERRIDES = {
    "AVAX": 10, "DOGE": 10, "LINK": 10, "XRP": 10,
    "ADA": 10, "NEAR": 10, "DOT": 10, "UNI": 10,
    "AAVE": 10, "kPEPE": 10, "FARTCOIN": 10, "LTC": 10,
}


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders. Ignores DSL stop-losses."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list):
        for o in orders:
            if not o.get("reduceOnly", False):
                return True
    return False


def sync_last_win_from_chain(wallet, tc):
    data = cfg.mcporter_call(
        "discovery_get_trader_history",
        trader_address=wallet,
        limit=20,
        latest=True,
    )
    if not data:
        return tc
    closed = data.get("closed_positions", data.get("closedPositions", []))
    if not isinstance(closed, list):
        return tc
    for t in closed:
        try:
            pnl = float(t.get("realizedPnl", t.get("closedPnl", t.get("pnl", 0))))
        except (TypeError, ValueError):
            continue
        if pnl <= 0:
            continue
        szi = float(t.get("closedSz", t.get("sz", 0)))
        dir_guess = "LONG" if szi > 0 else "SHORT"
        ts = t.get("closedTime", t.get("time", t.get("timestamp", 0)))
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = 0
        elif not isinstance(ts, (int, float)):
            ts = 0
        if ts and ts > tc.get("last_win_ts", 0):
            tc["last_win_direction"] = dir_guess
            tc["last_win_ts"] = ts
            cfg.save_trade_counter(tc)
        break
    return tc


def fetch_markets():
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return None
    data = raw.get("data", raw)
    if isinstance(data, dict):
        markets = data.get("markets", [])
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
    elif isinstance(data, list):
        markets = data
    else:
        markets = []
    return markets


def detect_swarm(markets):
    short_swarm, long_swarm = [], []
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        dex = m.get("dex", "")
        direction = m.get("direction", "")
        sm_pct = float(m.get("pct_of_top_traders_gain", 0) or 0)
        if token in MAJOR_ASSETS:
            continue
        if dex == "xyz":
            continue
        if sm_pct < MIN_SWARM_SM_PCT:
            continue
        entry = {
            "token": token,
            "direction": direction,
            "sm_pct": sm_pct,
            "contrib_change": m.get("contribution_pct_change_4h", 0) or 0,
            "price_change_4h": m.get("token_price_change_pct_4h", 0) or 0,
            "trader_count": m.get("trader_count", 0),
            "max_leverage": m.get("max_leverage", 3),
        }
        if direction == "short":
            short_swarm.append(entry)
        else:
            long_swarm.append(entry)
    if len(short_swarm) >= len(long_swarm) and len(short_swarm) >= MIN_SWARM_COUNT:
        return "SHORT", short_swarm
    if len(long_swarm) >= MIN_SWARM_COUNT:
        return "LONG", long_swarm
    return None, []


def score_target(target, swarm_size):
    token = target["token"]
    direction = target["direction"]
    sm_pct = target["sm_pct"]
    contrib = target["contrib_change"]
    price_4h = target["price_change_4h"]
    trader_count = target["trader_count"]

    score = 0
    breakdown = {
        "token": token,
        "direction": direction.upper(),
        "sm_pct": round(sm_pct, 2),
        "contrib_change": round(contrib, 2),
        "price_change_4h": round(price_4h, 3),
        "trader_count": trader_count,
        "swarm_size": swarm_size,
    }

    if swarm_size >= 7:
        score += 2
        breakdown["swarm_score"] = f"+2 (MASSIVE swarm: {swarm_size})"
    elif swarm_size >= 5:
        score += 1
        breakdown["swarm_score"] = f"+1 (CONFIRMED swarm: {swarm_size})"

    if sm_pct >= 10:
        score += 2
        breakdown["sm_score"] = f"+2 (DOMINANT {sm_pct:.1f}%)"
    elif sm_pct >= 5:
        score += 1
        breakdown["sm_score"] = f"+1 (STRONG {sm_pct:.1f}%)"
    else:
        breakdown["sm_score"] = f"+0 (IN SWARM {sm_pct:.1f}%)"

    if direction == "short" and price_4h < -1.0:
        score += 2
        breakdown["price_score"] = f"+2 (STRONG CONFIRM {price_4h:+.2f}%)"
    elif direction == "short" and price_4h < -0.5:
        score += 1
        breakdown["price_score"] = f"+1 (CONFIRMS {price_4h:+.2f}%)"
    elif direction == "long" and price_4h > 1.0:
        score += 2
        breakdown["price_score"] = f"+2 (STRONG CONFIRM {price_4h:+.2f}%)"
    elif direction == "long" and price_4h > 0.5:
        score += 1
        breakdown["price_score"] = f"+1 (CONFIRMS {price_4h:+.2f}%)"
    else:
        breakdown["price_score"] = f"+0 (NOT CONFIRMING {price_4h:+.2f}%)"

    if trader_count >= 50:
        score += 1
        breakdown["depth_score"] = f"+1 (DEEP: {trader_count})"
    else:
        breakdown["depth_score"] = f"+0 ({trader_count})"

    abs_contrib = abs(contrib)
    if abs_contrib >= 3:
        score += 1
        breakdown["contrib_score"] = f"+1 (VELOCITY {contrib:+.1f}%)"
    else:
        breakdown["contrib_score"] = f"+0 (SLOW {contrib:+.1f}%)"

    # Move-exhaustion penalty
    p4h = float(price_4h)
    d = direction.upper()
    if abs(p4h) >= 4.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 2
            breakdown["move_exhaustion"] = f"MOVE_EXHAUSTION {p4h:+.1f}%"
    elif abs(p4h) >= 2.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 1
            breakdown["move_exhaustion"] = f"MOVE_TIRING {p4h:+.1f}%"

    breakdown["total_score"] = score
    breakdown["passes"] = score >= MIN_SCORE
    return score, breakdown


def pick_best_target(swarm):
    swarm_size = len(swarm)
    candidates = []
    for target in swarm:
        token = target["token"]
        if cfg.is_asset_cooled_down(token, PER_ASSET_COOLDOWN_MINUTES):
            continue
        score, breakdown = score_target(target, swarm_size)
        if score >= MIN_SCORE:
            candidates.append({"target": target, "score": score, "breakdown": breakdown})
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["score"], c["target"]["sm_pct"]), reverse=True)
    return candidates[0]


def generate_entry(token, direction, leverage, margin):
    return {
        "coin": token,
        "direction": direction.upper(),
        "leverage": leverage,
        "leverageType": "CROSS",
        "marginAmount": margin,
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "feeOptimizedLimitOptions": {
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 30,
        },
    }


def generate_dsl_state(token, direction, leverage):
    return {
        "coin": token,
        "direction": direction.upper(),
        "leverage": leverage,
        "leverageType": "CROSS",
        "absoluteFloorRoe": None,
        "highWaterRoe": None,
        "highWaterPrice": None,
        "currentTier": 0,
        "consecutiveBreaches": 0,
        "consecutiveBreachesRequired": 3,
        "phase1MaxMinutes": 45,
        "deadWeightCutMin": 45,
        "phase1": {"maxLossPct": 20.0, "retraceThreshold": 10, "enabled": True},
        "phase2": {
            "enabled": True,
            "tiers": [
                {"triggerPct": 5, "lockHwPct": 20},
                {"triggerPct": 10, "lockHwPct": 40},
                {"triggerPct": 15, "lockHwPct": 55},
                {"triggerPct": 20, "lockHwPct": 70},
                {"triggerPct": 30, "lockHwPct": 82},
                {"triggerPct": 50, "lockHwPct": 90},
            ],
        },
        "hardTimeout": {"enabled": True, "intervalInMinutes": 240},
        "weakPeakCut": {"enabled": True, "intervalInMinutes": 90, "minValue": 3.0},
        "deadWeightCut": {"enabled": True, "intervalInMinutes": 45},
    }


def run():
    """Gates: wallet → account → positions → resting → daily → global cooldown → swarm → target → same-dir."""
    wallet, _ = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "cannot read account"})
        return

    if len(positions) >= MAX_POSITIONS:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": f"max positions ({len(positions)})"})
        return

    if has_resting_orders(wallet):
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "RESTING_ORDER"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("entries", 0) >= MAX_ENTRIES_PER_DAY:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": f"daily cap {tc.get('entries')}/{MAX_ENTRIES_PER_DAY}"})
        return

    last_ge = tc.get("last_global_entry_ts", 0)
    if last_ge and (time.time() - last_ge) < COOLDOWN_MINUTES * 60:
        rem = int((COOLDOWN_MINUTES * 60 - (time.time() - last_ge)) / 60)
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": f"global cooldown {rem}min"})
        return

    markets_list = fetch_markets()
    if not markets_list:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "failed to fetch markets"})
        return

    swarm_direction, swarm = detect_swarm(markets_list)
    if swarm_direction is None:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "no swarm", "swarm": {"detected": False}})
        return

    best = pick_best_target(swarm, True)
    if best is None:
        cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                    "reason": "no qualifying target", "swarm": {"detected": True, "n": len(swarm)}})
        return

    target = best["target"]
    token = target["token"]
    if XYZ_BANNED_TRADING:
        dex = next((m.get("dex", "") for m in markets_list
                    if isinstance(m, dict) and m.get("token") == token), "")
        if str(dex).lower() == "xyz" or str(token).lower().startswith("xyz:"):
            cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                        "reason": "XYZ_BANNED"})
            return

    direction = target["direction"].upper()
    tc = sync_last_win_from_chain(wallet, tc)
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts and direction == last_win_dir:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            rem = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
            cfg.output({"scanner": "scorpion", "version": "2.1", "action": "NONE",
                        "reason": f"SAME_DIR_COOLDOWN {rem}min"})
            return

    lev = LEVERAGE_OVERRIDES.get(token, min(LEVERAGE, int(target.get("max_leverage", 5))))
    entry = generate_entry(token, direction, lev, MARGIN_AMOUNT)
    dsl_state = generate_dsl_state(token, direction, lev)

    tc["entries"] = tc.get("entries", 0) + 1
    tc["last_global_entry_ts"] = time.time()
    cfg.save_trade_counter(tc)
    cfg.set_asset_cooldown(token, "entry")

    cfg.output({
        "scanner": "scorpion",
        "version": "2.1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "ENTER",
        "score": best["score"],
        "breakdown": best["breakdown"],
        "reason": f"SCORPION swarm {swarm_direction} n={len(swarm)} best={token}",
        "entry": entry,
        "dsl_state": dsl_state,
        "swarm": {"detected": True, "direction": swarm_direction, "count": len(swarm)},
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"scanner": "scorpion", "version": "2.1", "status": "error", "error": str(e)})
