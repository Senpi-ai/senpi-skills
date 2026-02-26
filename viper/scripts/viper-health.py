#!/usr/bin/env python3
"""
viper-health.py — VIPER health monitoring and reporting.
Runs every 10 min. Tier 1 cron.

MANDATE: Check margin, orphans, stale orders. Hourly + daily reports.
"""

import sys
import os
from datetime import timezone
sys.path.insert(0, os.path.dirname(__file__))

from viper_config import (
    load_config, load_state, save_state, get_clearinghouse,
    now_utc, hours_since,
    output, output_heartbeat, output_error
)


def main():
    try:
        config = load_config()
        state = load_state(config)
    except Exception as e:
        output_error(f"config_load_failed: {e}")

    wallet = config.get("strategyWallet")
    if not wallet:
        output_error("no_wallet_configured")

    alerts = []

    # Fetch clearinghouse state
    try:
        ch = get_clearinghouse(wallet)
        ch_data = ch.get("data", ch)
        margin_summary = ch_data.get("marginSummary", ch_data.get("crossMarginSummary", {}))
        account_value = float(margin_summary.get("accountValue", state.get("budget", 5000)))
        total_margin = float(margin_summary.get("totalMarginUsed", 0))
        available_pct = ((account_value - total_margin) / account_value * 100) if account_value > 0 else 100
    except Exception as e:
        alerts.append({"level": "WARNING", "msg": f"clearinghouse_fetch_failed: {e}"})
        account_value = state.get("budget", 5000)
        available_pct = 100

    # 1. Margin checks (conservative thresholds per guide §12.1)
    margin_warning = config.get("marginWarningPct", 50)
    margin_critical = config.get("marginCriticalPct", 30)

    if available_pct < margin_critical:
        alerts.append({
            "level": "CRITICAL",
            "msg": f"Margin critical: {available_pct:.0f}% available (limit {margin_critical}%). Reduce next size 50%."
        })
    elif available_pct < margin_warning:
        alerts.append({
            "level": "WARNING",
            "msg": f"Margin warning: {available_pct:.0f}% available (limit {margin_warning}%)."
        })

    # 2. Drawdown check
    starting = state.get("startingEquity", config.get("budget", 5000))
    peak = max(starting, account_value)
    drawdown = ((peak - account_value) / peak * 100) if peak > 0 else 0
    max_dd = config.get("maxDrawdownPct", 10)

    if drawdown >= max_dd:
        state["safety"]["halted"] = True
        state["safety"]["haltReason"] = f"Max drawdown {drawdown:.1f}% (limit {max_dd}%)"
        alerts.append({"level": "CRITICAL", "msg": state["safety"]["haltReason"]})

    # 3. Daily loss check
    daily_stats = state.get("dailyStats", {})
    today = now_utc().strftime("%Y-%m-%d")
    if daily_stats.get("date") != today:
        # Day rollover — reset daily stats
        state["dailyStats"] = {
            "date": today,
            "bouncesTraded": 0, "bouncesWon": 0, "bouncesLost": 0,
            "breakExits": 0, "grossPnl": 0, "fees": 0, "netPnl": 0,
            "makerFillRate": 0, "avgHoldHours": 0
        }
        state["safety"]["tradesToday"] = 0
        state["safety"]["dailyLossPct"] = 0
        state["safety"]["consecutiveStopsPerRange"] = {}

    daily_pnl = daily_stats.get("netPnl", 0)
    if daily_pnl < 0:
        daily_loss_pct = abs(daily_pnl) / starting * 100 if starting > 0 else 0
        state["safety"]["dailyLossPct"] = round(daily_loss_pct)
        max_daily = config.get("maxDailyLossPct", 6)
        if daily_loss_pct >= max_daily:
            state["safety"]["halted"] = True
            state["safety"]["haltReason"] = f"Daily loss {daily_loss_pct:.1f}% (limit {max_daily}%)"
            alerts.append({"level": "CRITICAL", "msg": state["safety"]["haltReason"]})

    # 4. Orphan position check
    active = state.get("activePositions", {})
    ranges = state.get("ranges", {})
    for coin in list(active.keys()):
        range_asset = active[coin].get("rangeAsset", coin)
        if range_asset not in ranges or ranges[range_asset].get("phase") not in ("TRADING",):
            alerts.append({
                "level": "WARNING",
                "msg": f"Orphan position: {coin} has no active range. Consider closing."
            })

    # 5. Stale pending orders
    pending = state.get("pendingOrders", {})
    for asset, order in list(pending.items()):
        posted_at = order.get("postedAt")
        if posted_at and hours_since(posted_at) * 60 > 30:
            alerts.append({
                "level": "INFO",
                "msg": f"Stale order: {asset} {order.get('side')} at {order.get('price')} posted {hours_since(posted_at) * 60:.0f}min ago. Cancel."
            })

    # Build report
    now = now_utc()
    hourly_due = now.minute < 10  # Report if within first 10 min of the hour
    daily_due = now.hour == 0 and now.minute < 10

    report = None
    if hourly_due or daily_due or alerts:
        report = {
            "type": "daily" if daily_due else "hourly",
            "accountValue": round(account_value, 2),
            "availableMarginPct": round(available_pct),
            "drawdownPct": round(drawdown, 1),
            "activeRanges": len([r for r in ranges.values() if r.get("phase") == "TRADING"]),
            "activePositions": len(active),
            "pendingOrders": len(pending),
            "dailyStats": daily_stats,
            "halted": state["safety"]["halted"],
        }

    save_state(config, state)

    if not alerts and not report:
        output_heartbeat()
        return

    result = {"success": True, "alerts": alerts}
    if report:
        result["report"] = report
        result["summary"] = (
            f"Balance: ${account_value:.0f} | "
            f"Ranges: {report['activeRanges']} | "
            f"Positions: {report['activePositions']} | "
            f"Daily PnL: ${daily_stats.get('netPnl', 0):.0f}"
        )
    else:
        result["summary"] = f"{len(alerts)} alerts"

    output(result)


if __name__ == "__main__":
    main()
