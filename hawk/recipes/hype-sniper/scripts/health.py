#!/usr/bin/env python3
"""HYPE Momentum Sniper — Health Check & Reporting (runs every 10min)."""

import sys
import time
import json

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import hype_lib as lib


def run():
    config = lib.load_config()
    wallet = config.get("strategy_wallet", "")

    if not wallet:
        lib.output_json({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet configured"})
        return

    # Load health state
    health_state = lib.load_state("health_state.json")

    # Get clearinghouse state
    ch = lib.get_clearinghouse_state(wallet)
    if not ch:
        lib.output_json({"success": False, "error": "failed to get clearinghouse state"})
        return

    # Extract account info — clearinghouse returns nested data.main / data.xyz
    data = ch.get("data", ch)
    account_value = 0.0
    total_margin = 0.0
    withdrawable = 0.0
    positions = []

    for section in ("main", "xyz"):
        section_data = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(section_data, dict):
            continue
        ms = section_data.get("marginSummary", {})
        if ms:
            account_value += float(ms.get("accountValue", 0))
            total_margin += float(ms.get("totalMarginUsed", 0))
        withdrawable += float(section_data.get("withdrawable", 0))

        for p in section_data.get("assetPositions", []):
            pos = p.get("position", p) if isinstance(p, dict) else {}
            coin = pos.get("coin", "")
            szi = float(pos.get("szi", pos.get("size", 0)))
            if coin and szi != 0:
                entry_px = float(pos.get("entryPx", pos.get("entryPrice", 0)))
                upnl = float(pos.get("unrealizedPnl", 0))
                positions.append({
                    "coin": coin,
                    "size": szi,
                    "entry": entry_px,
                    "upnl": round(upnl, 2)
                })

    # Fallback: if data wasn't nested, try top-level marginSummary
    if account_value == 0 and "marginSummary" in data:
        ms = data["marginSummary"]
        account_value = float(ms.get("accountValue", 0))
        total_margin = float(ms.get("totalMarginUsed", 0))
        withdrawable = float(data.get("withdrawable", 0))

    margin_usage_pct = (total_margin / account_value * 100) if account_value > 0 else 0

    # Track daily PnL
    today = lib.now_iso()[:10]
    daily = health_state.get("daily", {})
    if daily.get("date") != today:
        daily = {"date": today, "start_value": account_value, "alerts_sent": []}
    daily_pnl = account_value - daily.get("start_value", account_value)
    daily_pnl_pct = (daily_pnl / daily["start_value"] * 100) if daily["start_value"] > 0 else 0
    health_state["daily"] = daily

    # Build report
    report = {
        "account_value": round(account_value, 2),
        "margin_used": round(total_margin, 2),
        "margin_usage_pct": round(margin_usage_pct, 1),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "positions": positions,
        "timestamp": lib.now_iso()
    }

    alerts = []

    # Alert: margin usage > 80%
    max_margin = config["risk"]["max_margin_usage_pct"]
    if margin_usage_pct > 80:
        alerts.append(f"⚠️ HIGH MARGIN USAGE: {margin_usage_pct:.1f}% (max: {max_margin}%)")

    # Alert: daily loss > threshold
    max_loss = config["risk"]["max_daily_loss_pct"]
    if daily_pnl_pct < -max_loss:
        alerts.append(f"🚨 DAILY LOSS LIMIT: {daily_pnl_pct:.2f}% (max: -{max_loss}%)")

    # Hourly report check
    last_report_ts = health_state.get("last_report_ts", 0)
    should_report = (time.time() - last_report_ts) >= 3600  # hourly

    report["alerts"] = alerts

    # Save health state
    health_state["last_check"] = report
    health_state["last_check_ts"] = time.time()
    lib.save_state(health_state, "health_state.json")

    # If alerts or hourly report due, flag for telegram
    if alerts or should_report:
        health_state["last_report_ts"] = time.time()
        lib.save_state(health_state, "health_state.json")

        # Build telegram message text
        lines = ["📊 *HYPE Sniper Health Report*", ""]
        lines.append(f"💰 Account: ${account_value:.2f}")
        lines.append(f"📊 Margin: {margin_usage_pct:.1f}%")
        lines.append(f"📈 Daily PnL: ${daily_pnl:+.2f} ({daily_pnl_pct:+.2f}%)")
        if positions:
            lines.append("")
            lines.append("*Positions:*")
            for p in positions:
                direction = "LONG" if p["size"] > 0 else "SHORT"
                lines.append(f"  {p['coin']} {direction} | uPnL: ${p['upnl']}")
        if alerts:
            lines.append("")
            for a in alerts:
                lines.append(a)

        report["telegram_message"] = "\n".join(lines)
        report["send_telegram"] = True

    lib.output_json({"success": True, **report})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        lib.error(f"Health check error: {e}")
        lib.output_json({"success": False, "error": str(e)})
