#!/usr/bin/env python3
"""
lion-setup.py — Setup wizard for LION.
Creates config, initializes state and history directories.

Usage:
  python3 scripts/lion-setup.py --wallet 0x... --strategy-id UUID \
    --budget 5000 --chat-id 12345
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from lion_config import (
    SKILL_DIR, STATE_DIR, HISTORY_DIR, MEMORY_DIR,
    DEFAULT_CONFIG, atomic_write, deep_merge
)


def main():
    parser = argparse.ArgumentParser(description="LION Setup Wizard")
    parser.add_argument("--wallet", required=True, help="Strategy wallet address")
    parser.add_argument("--strategy-id", required=True, help="Strategy UUID")
    parser.add_argument("--budget", type=float, required=True, help="Budget in USD")
    parser.add_argument("--chat-id", required=True, help="Telegram chat ID")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()

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
    config_path = os.path.join(SKILL_DIR, "lion-config.json")
    atomic_write(config_path, config)

    # Initialize state
    state = {
        "version": 1,
        "active": True,
        "instanceKey": args.strategy_id,
        "createdAt": now,
        "updatedAt": now,
        "budget": args.budget,
        "startingEquity": args.budget,
        "activePositions": {},
        "watchlist": {"squeeze": {}, "preCascade": {}},
        "dailyStats": {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "trades": 0, "wins": 0,
            "cascadesDetected": 0, "cascadesTraded": 0,
            "squeezesDetected": 0, "squeezesTraded": 0,
            "imbalancesDetected": 0, "imbalancesTraded": 0,
            "grossPnl": 0, "fees": 0, "netPnl": 0
        },
        "safety": {
            "halted": False, "haltReason": None,
            "tradesToday": 0, "thresholdMultiplier": 100
        }
    }
    atomic_write(os.path.join(instance_dir, "lion-state.json"), state)
    atomic_write(os.path.join(instance_dir, "trade-log.json"), [])
    atomic_write(os.path.join(HISTORY_DIR, "oi-history.json"), {})

    print(json.dumps({
        "success": True,
        "message": f"LION initialized. Budget: ${args.budget}. Wallet: {args.wallet[:10]}...",
        "configPath": config_path,
        "statePath": instance_dir,
        "note": "OI monitor needs ~1 hour to build baseline before cascade detection is reliable."
    }))


if __name__ == "__main__":
    main()
