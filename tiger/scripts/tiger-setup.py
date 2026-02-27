#!/usr/bin/env python3
"""
tiger-setup.py — Setup wizard for TIGER.
Creates config from user parameters, validates wallet, and initializes state.

Usage:
  python3 tiger-setup.py --wallet 0x... --strategy-id UUID --budget 5000 \
    --target 10000 --days 7 --chat-id 12345 [--max-slots 3] [--max-leverage 10]
"""

import sys
import os
import argparse
import json
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    WORKSPACE, DEFAULT_CONFIG, deep_merge, atomic_write, save_config
)


def main():
    parser = argparse.ArgumentParser(description="TIGER Setup Wizard")
    parser.add_argument("--wallet", required=True, help="Strategy wallet address")
    parser.add_argument("--strategy-id", required=True, help="Senpi strategy ID")
    parser.add_argument("--budget", type=float, required=True, help="Starting budget in USD")
    parser.add_argument("--target", type=float, required=True, help="Target balance in USD")
    parser.add_argument("--days", type=int, default=7, help="Days to hit target (default: 7)")
    parser.add_argument("--chat-id", required=True, help="Telegram chat ID for notifications")
    parser.add_argument("--max-slots", type=int, default=3, help="Max concurrent positions (default: 3)")
    parser.add_argument("--max-leverage", type=int, default=10, help="Max leverage (default: 10)")
    parser.add_argument("--min-leverage", type=int, default=5, help="Min leverage (default: 5)")

    args = parser.parse_args()

    # Validate
    if args.budget <= 0:
        print(json.dumps({"error": "Budget must be positive"}))
        sys.exit(1)
    if args.target <= args.budget:
        print(json.dumps({"error": "Target must be greater than budget"}))
        sys.exit(1)
    if args.days < 1 or args.days > 30:
        print(json.dumps({"error": "Days must be between 1 and 30"}))
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    daily_rate = (math.pow(args.target / args.budget, 1 / args.days) - 1) * 100

    # Build config via deep_merge (preserves nested defaults)
    config = deep_merge(DEFAULT_CONFIG, {
        "strategyWallet": args.wallet,
        "strategyId": args.strategy_id,
        "budget": args.budget,
        "target": args.target,
        "deadlineDays": args.days,
        "startTime": now,
        "telegramChatId": args.chat_id,
        "maxSlots": args.max_slots,
        "maxLeverage": args.max_leverage,
        "minLeverage": args.min_leverage,
    })

    # Create directories
    instance_dir = os.path.join(WORKSPACE, "state", args.strategy_id)
    for d in [instance_dir, os.path.join(instance_dir, "scan-history"),
              os.path.join(WORKSPACE, "memory")]:
        os.makedirs(d, exist_ok=True)

    # Write config (atomic)
    save_config(config)

    # Initialize state (atomic)
    state = {
        "version": 1,
        "active": True,
        "instanceKey": args.strategy_id,
        "createdAt": now,
        "updatedAt": now,
        "currentBalance": args.budget,
        "peakBalance": args.budget,
        "dayStartBalance": args.budget,
        "dailyPnl": 0,
        "totalPnl": 0,
        "tradesToday": 0,
        "winsToday": 0,
        "totalTrades": 0,
        "totalWins": 0,
        "aggression": "NORMAL",
        "dailyRateNeeded": round(daily_rate, 2),
        "daysRemaining": args.days,
        "dayNumber": 1,
        "activePositions": {},
        "safety": {
            "halted": False,
            "haltReason": None,
            "dailyLossPct": 0,
            "tradesToday": 0,
        },
        "lastGoalRecalc": None,
        "lastBtcPrice": None,
        "lastBtcCheck": None,
    }
    atomic_write(os.path.join(instance_dir, "tiger-state.json"), state)
    atomic_write(os.path.join(instance_dir, "trade-log.json"), [])
    atomic_write(os.path.join(instance_dir, "oi-history.json"), {})

    # Summary
    print(json.dumps({
        "success": True,
        "status": "TIGER configured",
        "budget": f"${args.budget:,.0f}",
        "target": f"${args.target:,.0f}",
        "returnNeeded": f"{((args.target / args.budget) - 1) * 100:.0f}%",
        "days": args.days,
        "dailyCompoundRate": f"{daily_rate:.1f}%",
        "strategyId": args.strategy_id,
        "wallet": args.wallet,
        "maxSlots": args.max_slots,
        "maxLeverage": args.max_leverage,
        "statePath": instance_dir,
        "nextSteps": [
            "Create 10 OpenClaw crons from references/cron-templates.md",
            "OI tracker needs ~1h before scanners can use OI data",
            "TIGER will start hunting on next cron cycle",
        ]
    }, indent=2))


if __name__ == "__main__":
    main()
