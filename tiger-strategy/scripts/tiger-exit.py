#!/usr/bin/env python3
"""
tiger-exit.py — Target-aware exit management for TIGER.
Runs every 5min alongside risk guardian. Manages trailing exits based on
goal progress, not fixed ROE tiers like WOLF.

MANDATE: Run TIGER exit manager. Check positions for target-aware exits. Report actions needed.
"""

import sys
from datetime import datetime, timezone
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, save_state, get_clearinghouse,
    edit_position, days_remaining, output
)


def evaluate_position(pos_data: dict, active_pos: dict, config: dict, state: dict) -> dict:
    """Evaluate a single position for exit signals."""
    coin = pos_data.get("coin", "")
    entry_price = float(pos_data.get("entryPx", 0))
    current_price = float(pos_data.get("positionValue", 0))  # Will recalc from mark
    unrealized_pnl = float(pos_data.get("unrealizedPnl", 0))
    margin = float(pos_data.get("marginUsed", 0))
    size = float(pos_data.get("szi", 0))
    leverage = float(pos_data.get("leverage", {}).get("value", 1) if isinstance(pos_data.get("leverage"), dict) else pos_data.get("leverage", 1))

    if margin <= 0:
        return None

    roe_pct = (unrealized_pnl / margin) * 100
    is_long = size > 0

    # Get tracked state for this position
    tracked = active_pos.get(coin, {})
    high_water_roe = max(tracked.get("high_water_roe", roe_pct), roe_pct)
    opened_at = tracked.get("opened_at", "")
    pattern = tracked.get("pattern", "unknown")

    # Update high water mark and track DSL tier
    prev_tier_idx = tracked.get("current_tier_idx", -1)
    if coin in active_pos:
        active_pos[coin]["high_water_roe"] = high_water_roe

    # ─── Exit Logic ───

    aggression = state.get("aggression", "NORMAL")
    remaining = state.get("days_remaining", 7)
    daily_rate = state.get("daily_rate_needed", 10)

    # Calculate daily target in USD
    daily_target_usd = state["current_balance"] * (daily_rate / 100) if daily_rate else 0

    actions = []
    lock_roe = None  # Will be set by DSL tier logic or fallback

    # 0. FALSE BREAKOUT CHECK (Compression Breakout only)
    # If price re-entered BB range within 2 candles of breakout, cut immediately
    if pattern == "COMPRESSION_BREAKOUT" and tracked.get("breakout_candle_index"):
        candles_since_breakout = tracked.get("candles_since_breakout", 0)
        bb_reentry = tracked.get("bb_reentry", False)
        if candles_since_breakout <= 2 and bb_reentry and roe_pct < 3:
            actions.append({
                "type": "FALSE_BREAKOUT",
                "action": "CLOSE",
                "reason": f"Price re-entered BB range within {candles_since_breakout} candles — false breakout. Cut immediately.",
                "priority": "HIGH"
            })

    # 1. Daily target hit → take profit
    if unrealized_pnl > 0 and daily_target_usd > 0 and unrealized_pnl >= daily_target_usd:
        actions.append({
            "type": "DAILY_TARGET_HIT",
            "action": "CLOSE",
            "reason": f"Position +${unrealized_pnl:.2f} hit daily target ${daily_target_usd:.2f}. Take profit.",
            "priority": "HIGH"
        })

    # 2. DSL Tiered Trailing Stop
    # Uses dsl_tiers from config (T1-T9). Falls back to continuous lock if no tiers defined.
    dsl_tiers = config.get("dsl_tiers", [])

    # Deadline override lock floors
    deadline_lock = 0
    if remaining <= 1:
        deadline_lock = 0.85
    elif remaining <= 2:
        deadline_lock = 0.75

    if high_water_roe > 5 and roe_pct > 0 and dsl_tiers:
        # Find the highest tier the high_water_roe has reached
        # Tiers are {triggerPct: 0.05, lockPct: 0.02} where values are ROE fractions (5% = 0.05)
        active_tier = None
        for tier in sorted(dsl_tiers, key=lambda t: t["triggerPct"], reverse=True):
            trigger_roe = tier["triggerPct"] * 100  # Convert to ROE %
            if high_water_roe >= trigger_roe:
                active_tier = tier
                break

        if active_tier:
            lock_roe = active_tier["lockPct"] * 100  # Convert to ROE %
            lock_roe = max(lock_roe, high_water_roe * deadline_lock) if deadline_lock else lock_roe
            tier_trigger = active_tier["triggerPct"] * 100

            # Track tier index for upgrade notifications
            sorted_tiers = sorted(dsl_tiers, key=lambda t: t["triggerPct"])
            current_tier_idx = sorted_tiers.index(active_tier)
            if coin in active_pos:
                active_pos[coin]["current_tier_idx"] = current_tier_idx
                if current_tier_idx > prev_tier_idx:
                    # New tier reached — flag for notification
                    tier_num = current_tier_idx + 1
                    next_tier = sorted_tiers[current_tier_idx + 1] if current_tier_idx + 1 < len(sorted_tiers) else None
                    active_pos[coin]["tier_upgraded"] = {
                        "tier_num": tier_num,
                        "trigger_pct": tier_trigger,
                        "lock_pct": lock_roe,
                        "next_trigger": next_tier["triggerPct"] * 100 if next_tier else None,
                        "next_lock": next_tier["lockPct"] * 100 if next_tier else None,
                        "exchange_sl_price": active_pos.get(coin, {}).get("exchange_sl_price"),
                        "exchange_sl_set": active_pos.get(coin, {}).get("exchange_sl_set"),
                    }

            # Set exchange-level SL at DSL lock price (enforced in real-time by Hyperliquid)
            # T1 warmup: don't set exchange SL until 5min after T1 first triggers (let it breathe)
            t1_warmup_s = config.get("t1_warmup_seconds", 300)  # default 5min
            skip_sl_for_warmup = False
            if current_tier_idx == 0 and prev_tier_idx < 0:
                # Just hit T1 for first time — record timestamp
                if coin in active_pos:
                    active_pos[coin]["t1_hit_at"] = datetime.now(timezone.utc).isoformat()
                skip_sl_for_warmup = True
            elif current_tier_idx == 0:
                # Still at T1 — check if warmup elapsed
                t1_hit = active_pos.get(coin, {}).get("t1_hit_at")
                if t1_hit:
                    t1_time = datetime.fromisoformat(t1_hit)
                    elapsed = (datetime.now(timezone.utc) - t1_time).total_seconds()
                    if elapsed < t1_warmup_s:
                        skip_sl_for_warmup = True

            if current_tier_idx > prev_tier_idx and entry_price > 0 and leverage > 0 and not skip_sl_for_warmup:
                lock_price_pct = lock_roe / leverage  # ROE% → price%
                if is_long:
                    sl_price = round(entry_price * (1 + lock_price_pct / 100), 6)
                else:
                    sl_price = round(entry_price * (1 - lock_price_pct / 100), 6)

                if coin in active_pos:
                    active_pos[coin]["exchange_sl_price"] = sl_price
                    active_pos[coin]["exchange_sl_tier"] = current_tier_idx

                # Actually set the SL on the exchange
                wallet = config.get("strategy_wallet", "")
                if wallet:
                    try:
                        sl_result = edit_position(wallet, coin,
                            stopLoss={"price": sl_price, "orderType": "MARKET"})
                        sl_ok = sl_result.get("success", False) or \
                                sl_result.get("data", {}).get("success", False)
                        if coin in active_pos:
                            active_pos[coin]["exchange_sl_set"] = sl_ok
                            if not sl_ok:
                                active_pos[coin]["exchange_sl_error"] = str(
                                    sl_result.get("data", {}).get("error",
                                    sl_result.get("error", "unknown")))
                    except Exception as e:
                        if coin in active_pos:
                            active_pos[coin]["exchange_sl_set"] = False
                            active_pos[coin]["exchange_sl_error"] = str(e)

            if roe_pct < lock_roe:
                actions.append({
                    "type": "TRAILING_LOCK",
                    "action": "CLOSE",
                    "reason": f"ROE {roe_pct:.1f}% fell below DSL tier lock {lock_roe:.1f}% (tier triggered at {tier_trigger:.0f}%, peak {high_water_roe:.1f}%)",
                    "priority": "HIGH"
                })
    elif high_water_roe > 5 and roe_pct > 0:
        # Fallback: continuous lock if no tiers configured
        lock_pct_map = config.get("trailing_lock_pct", {})
        lock_pct = lock_pct_map.get(aggression, 0.60)
        lock_pct = max(lock_pct, deadline_lock) if deadline_lock else lock_pct
        lock_roe = lock_pct * 100
        locked_level = high_water_roe * lock_pct
        if roe_pct < locked_level:
            actions.append({
                "type": "TRAILING_LOCK",
                "action": "CLOSE",
                "reason": f"ROE {roe_pct:.1f}% fell below {lock_pct*100:.0f}% lock of peak {high_water_roe:.1f}% (floor: {locked_level:.1f}%)",
                "priority": "HIGH"
            })

    # 3. Ahead of pace + positive = conservative take at 50%+ daily target
    if aggression == "CONSERVATIVE" and unrealized_pnl > daily_target_usd * 0.5:
        actions.append({
            "type": "CONSERVATIVE_TP",
            "action": "PARTIAL_75",
            "reason": f"Ahead of pace, take 75% of +${unrealized_pnl:.2f} position",
            "priority": "MEDIUM"
        })

    # 4. Stagnation: positive ROE but no new high in 2+ hours
    # (simplified: if high_water hasn't changed significantly and we're in profit)
    if roe_pct > 3 and abs(high_water_roe - roe_pct) < 1 and tracked.get("stagnant_checks", 0) >= 24:
        actions.append({
            "type": "STAGNATION",
            "action": "CLOSE",
            "reason": f"ROE stagnant at ~{roe_pct:.1f}% for 2+ hours. Take profit and rotate.",
            "priority": "MEDIUM"
        })

    # Track stagnation
    if coin in active_pos:
        prev_hw = tracked.get("prev_high_water", 0)
        if abs(high_water_roe - prev_hw) < 0.5:
            active_pos[coin]["stagnant_checks"] = tracked.get("stagnant_checks", 0) + 1
        else:
            active_pos[coin]["stagnant_checks"] = 0
        active_pos[coin]["prev_high_water"] = high_water_roe

    # 4b. CORRELATION_LAG early exit: if never went positive after 10min, thesis failed
    if pattern == "CORRELATION_LAG" and high_water_roe <= 0.5 and roe_pct < -5 and opened_at:
        try:
            opened = datetime.fromisoformat(opened_at)
            elapsed_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed_min >= 10:
                actions.append({
                    "type": "CORR_LAG_FAILED",
                    "action": "CLOSE",
                    "reason": f"Correlation lag thesis failed — never went green after {elapsed_min:.0f}min, ROE {roe_pct:.1f}%. Cut early.",
                    "priority": "HIGH"
                })
        except:
            pass

    # 5. Losing position past time limit (30 min + negative = cut)
    if opened_at and roe_pct < -2:
        try:
            opened = datetime.fromisoformat(opened_at)
            elapsed_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed_min > 30 and roe_pct < 0:
                actions.append({
                    "type": "TIME_STOP",
                    "action": "CLOSE",
                    "reason": f"Negative ROE ({roe_pct:.1f}%) after {elapsed_min:.0f}min. Cut loss.",
                    "priority": "MEDIUM"
                })
        except:
            pass

    # 6. Deadline close: Day 7+ = close everything
    if remaining <= 0:
        actions.append({
            "type": "DEADLINE",
            "action": "CLOSE",
            "reason": "Deadline reached. Closing all positions.",
            "priority": "CRITICAL"
        })

    if not actions:
        return None

    # Return highest priority action
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "LOW"), 3))

    return {
        "coin": coin,
        "roe_pct": round(roe_pct, 1),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "high_water_roe": round(high_water_roe, 1),
        "lock_pct": lock_roe,
        "pattern": pattern,
        "actions": actions,
        "primary_action": actions[0]
    }


def main():
    config = load_config()
    state = load_state()

    if not config.get("strategy_wallet"):
        output({"error": "TIGER not set up."})
        return

    wallet = config["strategy_wallet"]
    ch = get_clearinghouse(wallet)
    if ch.get("error"):
        output({"error": f"Clearinghouse failed: {ch['error']}"})
        return

    ch_data = ch.get("data", ch)
    # Positions may be nested under data.main.assetPositions or data.assetPositions
    main_data = ch_data.get("main", ch_data)
    positions = main_data.get("assetPositions", ch_data.get("assetPositions", []))

    active_pos = state.get("active_positions", {})
    exit_signals = []

    for p in positions:
        pos = p.get("position", p)
        coin = pos.get("coin", "")
        result = evaluate_position(pos, active_pos, config, state)
        if result:
            exit_signals.append(result)

    # Update state with high water marks
    state["active_positions"] = active_pos
    save_state(state)

    # Categorize actions
    close_needed = [e for e in exit_signals if e["primary_action"]["action"] == "CLOSE"]
    partial_needed = [e for e in exit_signals if "PARTIAL" in e["primary_action"]["action"]]

    # Collect tier upgrades
    tier_upgrades = []
    for coin, pos_state in active_pos.items():
        if "tier_upgraded" in pos_state:
            tier_upgrades.append({"coin": coin, **pos_state.pop("tier_upgraded")})
    # Re-save after popping tier_upgraded flags
    if tier_upgrades:
        state["active_positions"] = active_pos
        save_state(state)

    output({
        "action": "exit_check",
        "positions_checked": len(positions),
        "exit_signals": len(exit_signals),
        "close_needed": [{"coin": e["coin"], "reason": e["primary_action"]["reason"]} for e in close_needed],
        "partial_needed": [{"coin": e["coin"], "reason": e["primary_action"]["reason"]} for e in partial_needed],
        "tier_upgrades": tier_upgrades,
        "all_signals": exit_signals,
        "aggression": state.get("aggression", "NORMAL"),
        "days_remaining": round(state.get("days_remaining", 7), 1)
    })


if __name__ == "__main__":
    main()
