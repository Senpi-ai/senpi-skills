#!/usr/bin/env python3
"""
risk-guardian.py — WOLF v6.1.1 Risk Guardian (Account-Level Guard Rails)

6th cron: enforces three account-level guardrails across all strategies:
  G1: Daily loss halt (accountValue drop from start-of-day)
  G3: Max entries per day (with bypass-on-profit option)
  G4: Consecutive loss cooldown

Runs every 5 minutes. Budget model tier.

Usage:
  python3 risk-guardian.py
"""
import json, sys, os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (
    load_all_strategies, load_trade_counter, save_trade_counter,
    mcporter_call_safe, heartbeat, GUARD_RAIL_DEFAULTS, strategy_lock,
)

heartbeat("risk_guardian")


def fetch_closed_trades(wallet):
    """Fetch recent closed trades for a wallet via discovery_get_trader_history."""
    data = mcporter_call_safe(
        "discovery_get_trader_history",
        traderAddress=wallet,
        sort_by="CLOSED_TIME",
        sort_direction="DESC",
        limit=20,
        latest=True,
    )
    if not data:
        return []
    return data.get("closed_positions", data.get("closedPositions", []))


def get_account_value(wallet):
    """Fetch current total account value (main + xyz) from clearinghouse."""
    data = mcporter_call_safe("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not data:
        return None
    total = 0.0
    for section_key in ("main", "xyz"):
        section = data.get(section_key, {})
        av = section.get("marginSummary", {}).get("accountValue")
        if av is not None:
            total += float(av)
    return total


def record_new_closings(counter, closed_positions):
    """Process unrecorded closed trades, update counter. Returns list of newly recorded trades."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    processed = set(counter.get("processedOrderIds", []))
    new_closings = []

    for pos in reversed(closed_positions):
        closed_order_id = pos.get("closedOrderId") or pos.get("closed_order_id")
        if not closed_order_id:
            continue
        if closed_order_id in processed:
            continue

        # Check if trade was closed today
        close_time = pos.get("closeTime") or pos.get("close_time")
        if close_time:
            try:
                # closeTime is Unix milliseconds
                ct = int(close_time)
                close_date = datetime.fromtimestamp(ct / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                if close_date != today:
                    continue
            except (ValueError, TypeError, OSError):
                continue
        else:
            continue

        # Parse realizedPnl
        rpnl_raw = pos.get("realizedPnl") or pos.get("realized_pnl") or 0
        try:
            rpnl = float(rpnl_raw)
        except (ValueError, TypeError):
            rpnl = 0.0

        # Update counter
        result = "W" if rpnl >= 0 else "L"
        last_results = counter.get("lastResults", [])
        last_results.append(result)
        counter["lastResults"] = last_results[-20:]
        counter["realizedPnl"] = counter.get("realizedPnl", 0.0) + rpnl
        counter["closedTrades"] = counter.get("closedTrades", 0) + 1
        processed.add(closed_order_id)

        coin = pos.get("coin") or pos.get("token") or "?"
        new_closings.append({
            "coin": coin,
            "pnl": round(rpnl, 2),
            "result": result,
            "closedOrderId": closed_order_id,
        })

    counter["processedOrderIds"] = list(processed)
    return new_closings


def evaluate_guard_rails(counter, wallet, cfg):
    """Evaluate G1, G3, G4 guard rails. Mutates counter. Returns list of notifications."""
    notifications = []
    strategy_key = cfg.get("_key", "unknown")

    # If already CLOSED for today, stay closed (sticky)
    if counter.get("gate") == "CLOSED":
        return notifications

    # --- G1: Daily Loss Halt ---
    daily_loss_limit = cfg.get("dailyLossLimit", 0)
    account_value_start = counter.get("accountValueStart")

    if daily_loss_limit > 0 and account_value_start is not None:
        current_value = get_account_value(wallet)
        if current_value is not None:
            daily_pnl = current_value - account_value_start
            counter["_dailyPnl"] = round(daily_pnl, 2)
            counter["_currentAccountValue"] = round(current_value, 2)

            if daily_pnl <= -daily_loss_limit:
                counter["gate"] = "CLOSED"
                counter["gateReason"] = (
                    f"G1 daily loss halt: PnL ${daily_pnl:+.2f} exceeded "
                    f"-${daily_loss_limit:.2f} limit"
                )
                notifications.append(
                    f"\U0001f6d1 GATE CLOSED [{strategy_key}]: {counter['gateReason']}"
                )
                return notifications

    # --- G3: Max Entries Per Day ---
    max_entries = counter.get("maxEntriesPerDay", GUARD_RAIL_DEFAULTS["maxEntriesPerDay"])
    entries = counter.get("entries", 0)

    if entries >= max_entries:
        bypass = counter.get("bypassOnProfit", GUARD_RAIL_DEFAULTS["bypassOnProfit"])
        if bypass and account_value_start is not None:
            current_value = counter.get("_currentAccountValue")
            if current_value is None:
                current_value = get_account_value(wallet)
            if current_value is not None and current_value > account_value_start:
                pass  # bypass — profitable day
            else:
                counter["gate"] = "CLOSED"
                counter["gateReason"] = (
                    f"G3 max entries: {entries}/{max_entries} entries reached on losing day"
                )
                notifications.append(
                    f"\U0001f6d1 GATE CLOSED [{strategy_key}]: {counter['gateReason']}"
                )
                return notifications
        else:
            counter["gate"] = "CLOSED"
            counter["gateReason"] = (
                f"G3 max entries: {entries}/{max_entries} entries reached"
            )
            notifications.append(
                f"\U0001f6d1 GATE CLOSED [{strategy_key}]: {counter['gateReason']}"
            )
            return notifications

    # --- Check if cooldown just expired (must run before G4 to append "R" streak-breaker) ---
    last_results = counter.get("lastResults", [])
    if counter.get("gate") == "COOLDOWN" and counter.get("cooldownUntil"):
        try:
            cd_dt = datetime.fromisoformat(
                counter["cooldownUntil"].replace("Z", "+00:00")
            )
            if cd_dt <= datetime.now(timezone.utc):
                counter["gate"] = "OPEN"
                counter["gateReason"] = None
                counter["cooldownUntil"] = None
                last_results.append("R")
                counter["lastResults"] = last_results[-20:]
                notifications.append(
                    f"\u2705 COOLDOWN EXPIRED [{strategy_key}]: gate re-opened"
                )
        except (ValueError, TypeError):
            counter["gate"] = "OPEN"
            counter["gateReason"] = None
            counter["cooldownUntil"] = None

    # --- G4: Consecutive Losses Cooldown ---
    max_consec = counter.get("maxConsecutiveLosses", GUARD_RAIL_DEFAULTS["maxConsecutiveLosses"])
    cooldown_min = counter.get("cooldownMinutes", GUARD_RAIL_DEFAULTS["cooldownMinutes"])

    if len(last_results) >= max_consec:
        tail = last_results[-max_consec:]
        if all(r == "L" for r in tail):
            # Already in active cooldown?
            if counter.get("gate") == "COOLDOWN" and counter.get("cooldownUntil"):
                try:
                    cd_dt = datetime.fromisoformat(
                        counter["cooldownUntil"].replace("Z", "+00:00")
                    )
                    if cd_dt > datetime.now(timezone.utc):
                        return notifications  # already cooling down
                except (ValueError, TypeError):
                    pass
            # Set new cooldown
            expiry = datetime.now(timezone.utc) + timedelta(minutes=cooldown_min)
            counter["gate"] = "COOLDOWN"
            counter["cooldownUntil"] = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
            counter["gateReason"] = (
                f"G4 consecutive losses: {max_consec} losses in a row, "
                f"cooldown until {counter['cooldownUntil']}"
            )
            notifications.append(
                f"\u23f3 COOLDOWN [{strategy_key}]: {counter['gateReason']}"
            )
            return notifications

    return notifications


def main():
    now = datetime.now(timezone.utc)
    strategies = load_all_strategies()

    if not strategies:
        print(json.dumps({
            "status": "ok",
            "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "strategies": {},
            "notifications": [],
        }))
        return

    all_notifications = []
    strategy_results = {}

    for key, cfg in strategies.items():
        wallet = cfg.get("wallet", "")
        if not wallet:
            continue

        # 3. Fetch closed trades (outside lock — read-only network call)
        closed_positions = fetch_closed_trades(wallet)

        # Lock to serialize with open-position.py's increment_entry_counter
        with strategy_lock(key):
            # 1. Load trade counter (handles day rollover)
            counter = load_trade_counter(key)

            # 2. Set account value start (first run of day)
            if counter.get("accountValueStart") is None:
                av = get_account_value(wallet)
                if av is not None:
                    counter["accountValueStart"] = round(av, 2)

            # 4. Record new closings
            new_closings = record_new_closings(counter, closed_positions)

            # 5. Evaluate guard rails
            notifications = evaluate_guard_rails(counter, wallet, cfg)
            all_notifications.extend(notifications)

            # 6. Save counter
            save_trade_counter(key, counter)

        strategy_results[key] = {
            "gate": counter.get("gate", "OPEN"),
            "gateReason": counter.get("gateReason"),
            "entries": counter.get("entries", 0),
            "closedTrades": counter.get("closedTrades", 0),
            "realizedPnl": round(counter.get("realizedPnl", 0.0), 2),
            "lastResults": counter.get("lastResults", []),
            "dailyPnl": counter.get("_dailyPnl"),
            "newClosings": new_closings,
        }

    output = {
        "status": "ok",
        "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategies": strategy_results,
        "notifications": all_notifications,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
