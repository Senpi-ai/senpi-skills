#!/usr/bin/env python3
"""
tiger-setup.py — Setup wizard for TIGER.
Creates config, initializes state directory.

Usage:
  python3 scripts/tiger-setup.py --wallet 0x... --strategy-id UUID \
    --budget 1000 --target 2000 --deadline-days 7 --chat-id 12345
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from tiger_config import (
    WORKSPACE, DEFAULT_CONFIG, atomic_write, deep_merge, _instance_dir
)


def main():
    parser = argparse.ArgumentParser(description="TIGER Setup Wizard")
    parser.add_argument("--wallet", required=True, help="Strategy wallet address")
    parser.add_argument("--strategy-id", required=True, help="Strategy UUID")
    parser.add_argument("--budget", type=float, required=True, help="Starting budget USD")
    parser.add_argument("--target", type=float, required=True, help="Profit target USD")
    parser.add_argument("--deadline-days", type=int, required=True, help="Timeframe in days")
    parser.add_argument("--chat-id", required=True, help="Telegram chat ID")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()

    config = deep_merge(DEFAULT_CONFIG, {
        "strategyWallet": args.wallet,
        "strategyId": args.strategy_id,
        "budget": args.budget,
        "target": args.target,
        "deadlineDays": args.deadline_days,
        "startTime": now,
        "telegramChatId": args.chat_id,
    })

    # Create directories
    instance_dir = os.path.join(WORKSPACE, "state", args.strategy_id)
    scan_hist_dir = os.path.join(instance_dir, "scan-history")
    memory_dir = os.path.join(WORKSPACE, "memory")
    for d in [instance_dir, scan_hist_dir, memory_dir]:
        os.makedirs(d, exist_ok=True)

    # Write config
    config_path = os.path.join(WORKSPACE, "tiger-config.json")
    atomic_write(config_path, config)

    # Initialize state
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
        "dailyRateNeeded": 0,
        "daysRemaining": args.deadline_days,
        "dayNumber": 1,
        "activePositions": {},
        "safety": {
            "halted": False,
            "haltReason": None,
            "dailyLossPct": 0,
            "tradesToday": 0
        },
        "lastGoalRecalc": None,
        "lastBtcPrice": None,
        "lastBtcCheck": None,
    }
    atomic_write(os.path.join(instance_dir, "tiger-state.json"), state)
    atomic_write(os.path.join(instance_dir, "trade-log.json"), [])
    atomic_write(os.path.join(instance_dir, "oi-history.json"), {})

    print(json.dumps({
        "success": True,
        "message": f"TIGER initialized. Budget: ${args.budget}, Target: ${args.target}, Deadline: {args.deadline_days}d",
        "configPath": config_path,
        "statePath": instance_dir
    }))


if __name__ == "__main__":
    main()
