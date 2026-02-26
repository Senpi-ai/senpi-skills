#!/usr/bin/env python3
"""
risk-guardian.py — TIGER's risk management watchdog.
Runs every 5min. Enforces daily loss budget, drawdown limits, deadline management,
and OI-based position adjustments.

MANDATE: Run TIGER risk guardian. Check daily loss budget, drawdown, positions, and deadline.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, load_state, save_state, get_clearinghouse,
    load_oi_history, close_position, edit_position,
    get_all_instruments,
    days_remaining, day_number, now_utc, output, shorten_address
)


def check_daily_loss(config, state, current_balance):
    """Check if daily loss budget is exceeded."""
    day_start = state.get("day_start_balance", config["budget"])
    daily_loss = day_start - current_balance
    daily_loss_pct = (daily_loss / day_start) * 100 if day_start > 0 else 0

    if daily_loss_pct >= config["max_daily_loss_pct"]:
        return {
            "breach": True,
            "type": "DAILY_LOSS",
            "message": f"Daily loss limit breached: -{daily_loss_pct:.1f}% (limit {config['max_daily_loss_pct']}%)",
            "loss_usd": round(daily_loss, 2),
            "loss_pct": round(daily_loss_pct, 1)
        }
    return {"breach": False, "loss_pct": round(daily_loss_pct, 1)}


def check_drawdown(config, state, current_balance):
    """Check max drawdown from peak."""
    peak = state.get("peak_balance", current_balance)
    if peak <= 0:
        return {"breach": False}
    dd_pct = ((peak - current_balance) / peak) * 100
    if dd_pct >= config["max_drawdown_pct"]:
        return {
            "breach": True,
            "type": "MAX_DRAWDOWN",
            "message": f"Max drawdown breached: {dd_pct:.1f}% from peak ${peak:.2f} (limit {config['max_drawdown_pct']}%)",
            "drawdown_pct": round(dd_pct, 1)
        }
    return {"breach": False, "drawdown_pct": round(dd_pct, 1)}


def check_deadline(config, state):
    """Check deadline proximity and enforce auto-close."""
    remaining = days_remaining(config)
    alerts = []

    if remaining <= 0:
        alerts.append({
            "type": "DEADLINE_REACHED",
            "message": "Deadline reached. Close all positions now.",
            "action": "CLOSE_ALL"
        })
    elif remaining <= 0.5:  # Last 12 hours
        alerts.append({
            "type": "DEADLINE_IMMINENT",
            "message": f"Only {remaining * 24:.0f}h remaining. Tighten all stops to 90% lock.",
            "action": "TIGHTEN_STOPS"
        })
    elif remaining <= 1:
        alerts.append({
            "type": "DEADLINE_APPROACHING",
            "message": f"Final day. {remaining * 24:.0f}h remaining. Lock 85%+ on all positions.",
            "action": "TIGHTEN_STOPS"
        })

    return alerts


def check_oi_shifts(config, state):
    """Check OI changes for active positions. Flag if OI drops >10%."""
    oi_history = load_oi_history()
    alerts = []

    for coin, pos in state.get("active_positions", {}).items():
        if coin not in oi_history:
            continue
        history = oi_history[coin]
        if len(history) < 12:  # Need at least 1 hour of history
            continue

        current_oi = history[-1]["oi"]
        hour_ago_oi = history[-12]["oi"] if len(history) >= 12 else history[0]["oi"]

        if hour_ago_oi <= 0:
            continue

        oi_change = ((current_oi - hour_ago_oi) / hour_ago_oi) * 100

        if oi_change < -25:
            alerts.append({
                "type": "OI_COLLAPSE",
                "coin": coin,
                "oi_change_pct": round(oi_change, 1),
                "message": f"{coin} OI collapsed {oi_change:.1f}% in 1h. Full exit recommended.",
                "action": "CLOSE"
            })
        elif oi_change < -10:
            alerts.append({
                "type": "OI_DROP",
                "coin": coin,
                "oi_change_pct": round(oi_change, 1),
                "message": f"{coin} OI dropped {oi_change:.1f}% in 1h. Reduce to 50% size.",
                "action": "REDUCE"
            })
        elif oi_change > 10 and pos.get("direction") in ("LONG", "SHORT"):
            alerts.append({
                "type": "OI_BUILDING",
                "coin": coin,
                "oi_change_pct": round(oi_change, 1),
                "message": f"{coin} OI building +{oi_change:.1f}% in 1h. Position confirmed.",
                "action": "HOLD"
            })

    return alerts


def check_funding_reversal(config, state):
    """Check if funding rate has flipped for FUNDING_ARB positions.
    If we entered to collect funding and funding has reversed direction, exit."""
    alerts = []
    instruments = get_all_instruments()
    inst_map = {i["name"]: i for i in instruments if not i.get("is_delisted")}

    for coin, pos in state.get("active_positions", {}).items():
        if pos.get("pattern") != "FUNDING_ARB":
            continue

        inst = inst_map.get(coin)
        if not inst:
            continue

        ctx = inst.get("context", {})
        current_funding = float(ctx.get("funding", 0))
        direction = pos.get("direction", "")

        # We went SHORT to collect positive funding, or LONG to collect negative funding
        # If funding has flipped, we're now PAYING instead of collecting
        funding_flipped = (
            (direction == "SHORT" and current_funding < 0) or
            (direction == "LONG" and current_funding > 0)
        )

        # Also flag if funding has weakened below profitable threshold
        # Original entry required >30% annualized. If now <10%, the trade thesis is dead.
        funding_annualized = abs(current_funding) * 3 * 365 * 100
        funding_weak = funding_annualized < 10  # Below 10% annualized = not worth holding

        if funding_flipped:
            alerts.append({
                "type": "FUNDING_REVERSED",
                "coin": coin,
                "current_funding_ann": round(current_funding * 3 * 365 * 100, 1),
                "message": f"{coin} funding REVERSED — now paying instead of collecting. Exit FUNDING_ARB position.",
                "action": "CLOSE"
            })
        elif funding_weak:
            alerts.append({
                "type": "FUNDING_WEAK",
                "coin": coin,
                "current_funding_ann": round(funding_annualized, 1),
                "message": f"{coin} funding dropped to {funding_annualized:.0f}% annualized (was >30%). Thesis weakened. Consider closing.",
                "action": "REDUCE"
            })

    return alerts


def check_position_pnl(config, state, positions_data):
    """Check individual position P&L against limits."""
    alerts = []

    for pos in positions_data:
        coin = pos.get("coin", "")
        unrealized_pnl = float(pos.get("unrealizedPnl", 0))
        margin = float(pos.get("marginUsed", 1))
        roe_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0

        # Check single trade loss limit
        loss_of_balance = abs(unrealized_pnl) / state["current_balance"] * 100 if state["current_balance"] > 0 else 0

        if unrealized_pnl < 0 and loss_of_balance >= config["max_single_loss_pct"]:
            alerts.append({
                "type": "SINGLE_LOSS_LIMIT",
                "coin": coin,
                "loss_pct": round(loss_of_balance, 1),
                "roe_pct": round(roe_pct, 1),
                "message": f"{coin} losing {loss_of_balance:.1f}% of balance (limit {config['max_single_loss_pct']}%). Close immediately.",
                "action": "CLOSE"
            })

        # Check if position hit daily target (for take-profit)
        daily_rate = state.get("daily_rate_needed", 10)
        daily_target_usd = state["current_balance"] * (daily_rate / 100)
        if unrealized_pnl > 0 and unrealized_pnl >= daily_target_usd:
            alerts.append({
                "type": "DAILY_TARGET_HIT",
                "coin": coin,
                "pnl": round(unrealized_pnl, 2),
                "roe_pct": round(roe_pct, 1),
                "message": f"{coin} hit daily target: +${unrealized_pnl:.2f} ({roe_pct:.1f}% ROE). Take profit.",
                "action": "TAKE_PROFIT"
            })

    return alerts


def main():
    config = load_config()
    state = load_state()

    if not config.get("strategy_wallet"):
        output({"error": "TIGER not set up."})
        return

    wallet = config["strategy_wallet"]

    # Fetch current state
    ch = get_clearinghouse(wallet)
    if ch.get("error"):
        output({"error": f"Clearinghouse failed: {ch['error']}"})
        return

    ch_data = ch.get("data", ch)
    margin_summary = ch_data.get("marginSummary", ch_data.get("crossMarginSummary", {}))
    current_balance = float(margin_summary.get("accountValue", state["current_balance"]))
    positions = ch_data.get("assetPositions", [])

    # Parse positions
    parsed_positions = []
    for p in positions:
        item = p.get("position", p)
        parsed_positions.append(item)

    # Run all checks
    all_alerts = []

    # 1. Daily loss
    daily_check = check_daily_loss(config, state, current_balance)
    if daily_check.get("breach"):
        all_alerts.append(daily_check)

    # 2. Drawdown
    dd_check = check_drawdown(config, state, current_balance)
    if dd_check.get("breach"):
        all_alerts.append(dd_check)

    # 3. Deadline
    deadline_alerts = check_deadline(config, state)
    all_alerts.extend(deadline_alerts)

    # 4. OI shifts
    oi_alerts = check_oi_shifts(config, state)
    all_alerts.extend(oi_alerts)

    # 5. Funding reversal (for FUNDING_ARB positions)
    funding_alerts = check_funding_reversal(config, state)
    all_alerts.extend(funding_alerts)

    # 6. Position P&L
    pnl_alerts = check_position_pnl(config, state, parsed_positions)
    all_alerts.extend(pnl_alerts)

    # Determine if we need to halt
    critical_alerts = [a for a in all_alerts if a.get("type") in ("DAILY_LOSS", "MAX_DRAWDOWN", "DEADLINE_REACHED")]
    if critical_alerts:
        state["halted"] = True
        state["halt_reason"] = critical_alerts[0].get("message", "Critical risk limit breached")

    # Determine actions needed
    close_coins = [a["coin"] for a in all_alerts if a.get("action") == "CLOSE" and "coin" in a]
    reduce_coins = [a["coin"] for a in all_alerts if a.get("action") == "REDUCE" and "coin" in a]
    tp_coins = [a["coin"] for a in all_alerts if a.get("action") == "TAKE_PROFIT" and "coin" in a]

    # Update state
    state["current_balance"] = current_balance
    save_state(state)

    report = {
        "action": "risk_check",
        "current_balance": round(current_balance, 2),
        "daily_loss_pct": daily_check.get("loss_pct", 0),
        "drawdown_pct": dd_check.get("drawdown_pct", 0),
        "days_remaining": round(days_remaining(config), 1),
        "active_positions": len(parsed_positions),
        "alerts": all_alerts,
        "alert_count": len(all_alerts),
        "critical": len(critical_alerts) > 0,
        "halted": state["halted"],
        "actions_needed": {
            "close": close_coins,
            "reduce": reduce_coins,
            "take_profit": tp_coins,
            "close_all": any(a.get("action") == "CLOSE_ALL" for a in all_alerts)
        }
    }

    output(report)


if __name__ == "__main__":
    main()
