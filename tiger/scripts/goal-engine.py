#!/usr/bin/env python3
"""
goal-engine.py — Hourly goal recalculation for TIGER.
Recalculates daily targets, aggression mode, position sizing, and resizes positions if needed.

MANDATE: Run TIGER goal engine. Recalculate targets and aggression. Report changes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    resolve_dependencies
)
from tiger_lib import required_daily_return, aggression_mode, kelly_fraction


def recalculate_goal(config, state, deps):
    """Recalculate all goal metrics."""
    get_clearinghouse = deps["get_clearinghouse"]
    days_remaining = deps["days_remaining"]
    day_number = deps["day_number"]
    now_utc = deps["now_utc"]
    set_halt_state = deps["set_halt_state"]
    get_active_positions = deps["get_active_positions"]
    wallet = config["strategyWallet"]
    if not wallet:
        return {"error": "No strategy wallet configured"}

    # Fetch current balance from clearinghouse
    ch = get_clearinghouse(wallet)
    if ch.get("error"):
        return {"error": f"Clearinghouse fetch failed: {ch['error']}"}

    # Parse account value from clearinghouse
    margin_summary = ch.get("marginSummary", ch.get("crossMarginSummary", {}))
    current_balance = float(margin_summary.get("accountValue", state.get("currentBalance", config["budget"])))

    # Update state
    old_aggression = state["aggression"]
    state["currentBalance"] = current_balance
    state["peakBalance"] = max(state.get("peakBalance", current_balance), current_balance)
    state["totalPnl"] = current_balance - config["budget"]

    # Day tracking
    remaining = days_remaining(config)
    state["daysRemaining"] = remaining
    new_day_number = day_number(config)
    
    # Day boundary reset: if day changed, reset daily tracking
    if new_day_number != state.get("dayNumber", 1):
        state["dayStartBalance"] = current_balance
        state["dailyPnl"] = 0
        state["tradesToday"] = 0
        state["winsToday"] = 0
    
    state["dayNumber"] = new_day_number

    # Calculate required daily return
    daily_rate = required_daily_return(current_balance, config["target"], max(remaining, 0.5))
    state["dailyRateNeeded"] = daily_rate if daily_rate else 999

    # Determine aggression
    new_aggression = aggression_mode(daily_rate)
    state["aggression"] = new_aggression

    # Adaptive leverage based on required daily return
    max_lev = config["maxLeverage"]
    if daily_rate is not None:
        if daily_rate < 3:
            adaptive_leverage = min(max_lev, 7)
        elif daily_rate < 8:
            adaptive_leverage = min(max_lev, 10)
        elif daily_rate < 15:
            adaptive_leverage = min(max_lev, 15)
        else:
            adaptive_leverage = max_lev  # Last resort before ABORT
    else:
        adaptive_leverage = min(max_lev, 10)
    state["adaptiveLeverage"] = adaptive_leverage

    # Calculate position sizing (half-Kelly)
    # Use historical win rate or default 60%
    total_trades = state.get("totalTrades", 0)
    total_wins = state.get("totalWins", 0)
    win_rate = total_wins / total_trades if total_trades >= 5 else 0.60
    avg_win = 1.5  # Default assumption: 1.5:1 reward:risk
    avg_loss = 1.0
    kelly = kelly_fraction(win_rate, avg_win, avg_loss)
    state["kellyFraction"] = round(kelly, 4)

    # Per-slot budget
    max_slots = config["maxSlots"]
    per_slot = current_balance * kelly
    state["perSlotBudget"] = round(per_slot, 2)

    # Drawdown check
    drawdown_pct = 0
    if state["peakBalance"] > 0:
        drawdown_pct = ((state["peakBalance"] - current_balance) / state["peakBalance"]) * 100

    # Check halt conditions
    halt = False
    halt_reason = None

    if new_aggression == "ABORT":
        halt = True
        halt_reason = f"Target requires {daily_rate:.1f}%/day — mathematically improbable. Recommend: extend timeline, reduce target, or accept current gains."

    if drawdown_pct >= config["maxDrawdownPct"]:
        halt = True
        halt_reason = f"Max drawdown breached: {drawdown_pct:.1f}% (limit {config['maxDrawdownPct']}%)"

    if remaining <= 0:
        halt = True
        halt_reason = "Deadline reached. Closing all positions."

    set_halt_state(state, halt, halt_reason)
    state["lastGoalRecalc"] = now_utc().isoformat()

    # Build report
    progress_pct = ((current_balance - config["budget"]) / (config["target"] - config["budget"])) * 100
    progress_pct = max(0, min(100, progress_pct))

    report = {
        "action": "goal_recalc",
        "day": state["dayNumber"],
        "days_remaining": round(remaining, 1),
        "budget": config["budget"],
        "target": config["target"],
        "current_balance": round(current_balance, 2),
        "total_pnl": round(state["totalPnl"], 2),
        "total_pnl_pct": round((state["totalPnl"] / config["budget"]) * 100, 1),
        "progress_pct": round(progress_pct, 1),
        "daily_rate_needed": round(daily_rate, 1) if daily_rate else None,
        "aggression": new_aggression,
        "aggression_changed": old_aggression != new_aggression,
        "old_aggression": old_aggression if old_aggression != new_aggression else None,
        "adaptive_leverage": adaptive_leverage,
        "kelly_fraction": state["kellyFraction"],
        "per_slot_budget": state["perSlotBudget"],
        "peak_balance": round(state["peakBalance"], 2),
        "drawdown_pct": round(drawdown_pct, 1),
        "win_rate": round(win_rate * 100, 1),
        "total_trades": total_trades,
        "halted": halt,
        "halt_reason": halt_reason,
        "active_positions": len(get_active_positions(state)),
        "max_slots": max_slots
    }

    # Check if target is hit
    if current_balance >= config["target"]:
        report["TARGET_HIT"] = True
        report["halt_reason"] = f"🎯 TARGET HIT! ${config['budget']} → ${current_balance:.2f} (target was ${config['target']})"
        set_halt_state(state, True, report["halt_reason"])

    return report


def main(deps=None):
    deps = deps or resolve_dependencies()
    load_config = deps["load_config"]
    load_state = deps["load_state"]
    save_state = deps["save_state"]
    output = deps["output"]

    config = load_config()
    state = load_state(config=config)

    if not config.get("strategyWallet"):
        output({"error": "TIGER not set up. Run tiger-setup.py first."})
        return

    report = recalculate_goal(config, state, deps)
    save_state(config, state)
    output(report)


if __name__ == "__main__":
    main()
