#!/usr/bin/env python3
"""
lion-health.py — LION health monitoring and reporting.
Runs every 10min. Margin checks, drawdown tracking, orphan detection,
hourly/daily reporting. Tier 1 cron.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lion_config import (
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

    # Get clearinghouse state
    ch = get_clearinghouse(wallet)
    if ch.get("error"):
        output_error(f"clearinghouse_error: {ch['error']}")

    ch_data = ch.get("data", ch)

    # Parse balance and margin info
    balance = float(ch_data.get("accountValue", ch_data.get("balance", 0)))
    margin_used = float(ch_data.get("marginUsed", ch_data.get("totalMarginUsed", 0)))
    available_margin = balance - margin_used if balance > 0 else 0
    margin_pct = (available_margin / balance * 100) if balance > 0 else 100

    alerts = []

    # ─── Margin checks ───
    margin_warn = config.get("marginWarningPct", 50)
    margin_crit = config.get("marginCriticalPct", 30)

    if margin_pct < margin_crit:
        alerts.append({
            "level": "CRITICAL",
            "type": "MARGIN",
            "message": f"Available margin {margin_pct:.0f}% < {margin_crit}% — reduce positions 50%"
        })
    elif margin_pct < margin_warn:
        alerts.append({
            "level": "WARNING",
            "type": "MARGIN",
            "message": f"Available margin {margin_pct:.0f}% < {margin_warn}%"
        })

    # ─── Drawdown check ───
    peak = max(state.get("peakBalance", balance), balance)
    state["peakBalance"] = peak
    drawdown_pct = ((peak - balance) / peak * 100) if peak > 0 else 0
    max_drawdown = config.get("maxDrawdownPct", 15)

    if drawdown_pct >= max_drawdown:
        alerts.append({
            "level": "CRITICAL",
            "type": "DRAWDOWN",
            "message": f"Drawdown {drawdown_pct:.1f}% >= {max_drawdown}% limit — HALTING"
        })
        state["safety"]["halted"] = True
        state["safety"]["haltReason"] = f"drawdown_{drawdown_pct:.1f}pct"

    # ─── Daily loss check ───
    day_start = state.get("dayStartBalance", balance)
    if day_start > 0:
        daily_loss = ((day_start - balance) / day_start * 100) if balance < day_start else 0
    else:
        daily_loss = 0
    max_daily = config.get("maxDailyLossPct", 8)

    if daily_loss >= max_daily:
        alerts.append({
            "level": "CRITICAL",
            "type": "DAILY_LOSS",
            "message": f"Daily loss {daily_loss:.1f}% >= {max_daily}% — HALTING"
        })
        state["safety"]["halted"] = True
        state["safety"]["haltReason"] = f"daily_loss_{daily_loss:.1f}pct"

    state["safety"]["dailyLossPct"] = round(daily_loss, 2)

    # ─── Day boundary rollover ───
    now = now_utc()
    last_update = state.get("updatedAt")
    if last_update:
        try:
            from datetime import datetime
            last_dt = datetime.fromisoformat(last_update)
            if last_dt.date() < now.date():
                state["dayStartBalance"] = balance
                state["safety"]["tradesToday"] = 0
                state["safety"]["dailyLossPct"] = 0
                alerts.append({
                    "level": "INFO",
                    "type": "DAY_ROLLOVER",
                    "message": f"New day. Start balance: ${balance:.2f}"
                })
                # Remove halt if it was daily-loss based
                if state["safety"].get("halted") and "daily_loss" in (state["safety"].get("haltReason") or ""):
                    state["safety"]["halted"] = False
                    state["safety"]["haltReason"] = None
        except (ValueError, TypeError):
            pass

    # ─── Orphan detection ───
    positions = state.get("activePositions", {})
    ch_positions = ch_data.get("assetPositions", [])
    ch_assets = set()
    for cp in ch_positions:
        p = cp.get("position", cp)
        coin = p.get("coin", "")
        szi = float(p.get("szi", 0))
        if abs(szi) > 0:
            ch_assets.add(coin)

    for asset in list(positions.keys()):
        if asset not in ch_assets:
            alerts.append({
                "level": "WARNING",
                "type": "ORPHAN",
                "message": f"Position {asset} in state but not in clearinghouse — removing"
            })
            del positions[asset]

    # ─── Update state ───
    state["currentBalance"] = balance

    # ─── Reporting ───
    num_positions = len(positions)
    daily_pnl = balance - day_start if day_start > 0 else 0

    # Hourly/daily report determination
    report_type = None
    last_report_h = hours_since(state.get("lastReportAt"))
    if last_report_h >= 1:
        report_type = "hourly"
        state["lastReportAt"] = now.isoformat()

    # Check for daily report
    if last_report_h >= 24 or (last_update and now.hour == 0 and now.minute < 15):
        report_type = "daily"

    save_state(config, state)

    # Build output
    if not alerts and report_type is None:
        output_heartbeat()
        return

    result = {
        "success": True,
        "balance": round(balance, 2),
        "marginAvailablePct": round(margin_pct, 1),
        "drawdownPct": round(drawdown_pct, 1),
        "dailyPnl": round(daily_pnl, 2),
        "positions": num_positions,
        "halted": state["safety"]["halted"],
    }

    if alerts:
        result["alerts"] = alerts

    if report_type:
        result["reportType"] = report_type
        result["report"] = {
            "balance": round(balance, 2),
            "peakBalance": round(peak, 2),
            "drawdown": f"{drawdown_pct:.1f}%",
            "dailyPnl": f"${daily_pnl:+.2f}",
            "positions": num_positions,
            "tradesToday": state["safety"].get("tradesToday", 0),
            "halted": state["safety"]["halted"],
        }

    output(result)


if __name__ == "__main__":
    main()
