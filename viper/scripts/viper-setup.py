#!/usr/bin/env python3
"""
viper-setup.py — Setup wizard for VIPER.
Creates config, initializes state directory.

Usage:
  python3 scripts/viper-setup.py --wallet 0x... --strategy-id UUID \
    --budget 5000 --chat-id 12345
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from viper_config import (
    CONFIG_FILE, STATE_DIR, HISTORY_DIR, MEMORY_DIR,
    DEFAULT_CONFIG, atomic_write, deep_merge
)


def main():
    parser = argparse.ArgumentParser(description="VIPER Setup Wizard")
    parser.add_argument("--wallet", required=True, help="Strategy wallet address")
    parser.add_argument("--strategy-id", required=True, help="Strategy UUID")
    parser.add_argument("--budget", type=float, required=True, help="Budget in USD")
    parser.add_argument("--chat-id", required=True, help="Telegram chat ID")
    args = parser.parse_args()

    config = deep_merge(DEFAULT_CONFIG, {
        "strategyWallet": args.wallet,
        "strategyId": args.strategy_id,
        "budget": args.budget,
        "telegramChatId": args.chat_id,
    })

    # Create directories
    instance_dir = os.path.join(STATE_DIR, args.strategy_id)
    for d in [instance_dir, HISTORY_DIR, MEMORY_DIR]:
        os.makedirs(d, exist_ok=True)

    # Write config
    atomic_write(CONFIG_FILE, config)

    # Initialize state
    state = {
        "version": 1,
        "active": True,
        "instanceKey": args.strategy_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "budget": args.budget,
        "startingEquity": args.budget,
        "ranges": {},
        "activePositions": {},
        "pendingOrders": {},
        "cooldown": {},
        "dailyStats": {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "bouncesTraded": 0, "bouncesWon": 0, "bouncesLost": 0,
            "breakExits": 0, "grossPnl": 0, "fees": 0, "netPnl": 0,
            "makerFillRate": 0, "avgHoldHours": 0
        },
        "safety": {
            "halted": False, "haltReason": None,
            "consecutiveStopsPerRange": {}, "dailyLossPct": 0, "tradesToday": 0
        }
    }
    atomic_write(os.path.join(instance_dir, "viper-state.json"), state)
    atomic_write(os.path.join(instance_dir, "trade-log.json"), [])

    print(json.dumps({
        "success": True,
        "message": f"VIPER initialized. Budget: ${args.budget}. Wallet: {args.wallet[:10]}...",
        "configPath": CONFIG_FILE,
        "statePath": instance_dir
    }))


if __name__ == "__main__":
    main()
