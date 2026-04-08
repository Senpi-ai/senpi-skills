#!/usr/bin/env python3
"""HAWK Risk Guardian — Account-level risk enforcement.

Runs every 5 minutes. Enforces:
  G1: Daily loss halt (max_daily_loss_pct from config)
  G2: Drawdown halt (max_drawdown_pct from config)
  G3: Max entries per day (max_entries_per_day from config)
  G4: Consecutive loss cooldown (3 losses → 60min pause)
  G5: Per-position loss cap (max_single_loss_pct → force close)
"""

import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hype_lib as lib


def get_positions(wallet):
    """Fetch account value and positions."""
    ch = lib.get_clearinghouse_state(wallet)
    if not ch:
        return None, []
    data = ch.get("data", ch)
    positions = []
    account_value = 0
    for section_key in ("main", "xyz"):
        section = data.get(section_key, {})
        if not isinstance(section, dict):
            continue
        ms = section.get("marginSummary", {})
        account_value += float(ms.get("accountValue", 0))
        for ap in section.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "roe": float(pos.get("returnOnEquity", 0)) * 100,
                "margin": float(pos.get("marginUsed", 0)),
            })
    return account_value, positions


def run():
    config = lib.load_config()
    wallet = config.get("strategy_wallet", "")
    risk = config.get("risk", {})

    if not wallet:
        lib.output_json({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    counter = lib.load_trade_counter()
    peak_data = lib.load_peak()
    account_value, positions = get_positions(wallet)

    if account_value is None:
        lib.output_json({"success": False, "error": "clearinghouse_failed"})
        return

    # Initialize start-of-day balance
    if counter.get("accountValueStart", 0) == 0:
        counter["accountValueStart"] = account_value

    # Update peak
    if account_value > peak_data.get("peak", 0):
        peak_data["peak"] = account_value
        peak_data["updatedAt"] = lib.now_iso()

    alerts = []
    gate = "OPEN"

    # G1: Daily loss halt
    daily_loss_limit_pct = risk.get("max_daily_loss_pct", 5)
    start_value = counter.get("accountValueStart", account_value)
    daily_loss_limit = start_value * (daily_loss_limit_pct / 100)
    unrealized = sum(p["upnl"] for p in positions)
    realized = counter.get("realizedPnl", 0)
    daily_pnl = realized + unrealized

    if daily_pnl <= -daily_loss_limit:
        gate = "CLOSED"
        alerts.append(f"G1 DAILY LOSS HALT: P&L ${daily_pnl:+,.2f} hit {daily_loss_limit_pct}% limit (${-daily_loss_limit:,.2f})")

    # G2: Drawdown halt
    drawdown_pct_limit = risk.get("max_drawdown_pct", 15)
    peak = peak_data.get("peak", 0)
    if peak > 0:
        drawdown_pct = (account_value - peak) / peak * 100
        if drawdown_pct <= -drawdown_pct_limit:
            gate = "CLOSED"
            alerts.append(f"G2 DRAWDOWN HALT: Account ${account_value:,.2f} is {drawdown_pct:+.1f}% from peak ${peak:,.2f}")

    # G3: Max entries
    max_entries = risk.get("max_entries_per_day", 10)
    if counter.get("entries", 0) >= max_entries:
        # Bypass if profitable day
        if daily_pnl <= 0:
            gate = "CLOSED"
            alerts.append(f"G3 MAX ENTRIES: {counter['entries']}/{max_entries} trades today, not profitable")

    # G4: Consecutive loss cooldown
    max_consec = risk.get("max_consecutive_losses", 3)
    cooldown_min = risk.get("cooldown_minutes", 60)
    last_results = counter.get("lastResults", [])

    # Check active cooldown
    cooldown_until = counter.get("cooldownUntil")
    if cooldown_until:
        try:
            cd = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < cd:
                remaining = (cd - datetime.now(timezone.utc)).total_seconds() / 60
                if gate == "OPEN":
                    gate = "COOLDOWN"
                alerts.append(f"G4 COOLDOWN: {remaining:.0f}min remaining")
            else:
                counter["cooldownUntil"] = None
        except (ValueError, TypeError):
            counter["cooldownUntil"] = None

    if gate == "OPEN" and not cooldown_until:
        consec = 0
        for r in reversed(last_results):
            if r == "L":
                consec += 1
            else:
                break
        if consec >= max_consec:
            gate = "COOLDOWN"
            until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_min)
            counter["cooldownUntil"] = until.isoformat()
            alerts.append(f"G4 COOLDOWN TRIGGERED: {consec} consecutive losses. Pausing {cooldown_min}min.")

    # G5: Per-position loss cap
    max_loss_pct = risk.get("max_single_loss_pct", 10)
    if account_value > 0:
        threshold = account_value * (max_loss_pct / 100)
        for p in positions:
            if p["upnl"] < 0 and abs(p["upnl"]) > threshold:
                lib.close_position(wallet, p["coin"], order_type="MARKET")
                alerts.append(
                    f"G5 FORCE CLOSE: {p['coin']} {p['direction']} "
                    f"loss ${p['upnl']:,.2f} ({abs(p['upnl'])/account_value*100:.1f}% of account) "
                    f"exceeds {max_loss_pct}% cap"
                )

    # Save state
    counter["gate"] = gate
    counter["gateReason"] = "; ".join(alerts) if alerts else None
    lib.save_trade_counter(counter)
    lib.save_peak(peak_data)

    lib.output_json({
        "success": True,
        "gate": gate,
        "account_value": round(account_value, 2),
        "daily_pnl": round(daily_pnl, 2),
        "peak": round(peak, 2),
        "entries_today": counter.get("entries", 0),
        "alerts": alerts,
        "heartbeat": "NO_REPLY" if not alerts else None,
    })


if __name__ == "__main__":
    run()
