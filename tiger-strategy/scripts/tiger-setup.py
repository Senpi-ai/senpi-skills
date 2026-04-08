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
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from tiger_config import (
    load_config, save_config, save_state, DEFAULT_CONFIG, DEFAULT_STATE,
    CONFIG_FILE, STATE_FILE, WORKSPACE
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
    parser.add_argument("--min-leverage", type=int, default=7, help="Min leverage (default: 7)")

    args = parser.parse_args()

    # Validate
    if args.budget <= 0:
        print("Error: Budget must be positive")
        sys.exit(1)
    if args.target <= args.budget:
        print("Error: Target must be greater than budget")
        sys.exit(1)
    if args.days < 1 or args.days > 30:
        print("Error: Days must be between 1 and 30")
        sys.exit(1)

    # Calculate required daily return
    import math
    daily_rate = (math.pow(args.target / args.budget, 1 / args.days) - 1) * 100

    # Build config
    config = dict(DEFAULT_CONFIG)
    config.update({
        "budget": args.budget,
        "target": args.target,
        "deadline_days": args.days,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "strategy_id": args.strategy_id,
        "strategy_wallet": args.wallet,
        "telegram_chat_id": args.chat_id,
        "max_slots": args.max_slots,
        "max_leverage": args.max_leverage,
        "min_leverage": args.min_leverage,
    })

    save_config(config)

    # Initialize state
    state = dict(DEFAULT_STATE)
    state["active_positions"] = {}
    state.update({
        "current_balance": args.budget,
        "peak_balance": args.budget,
        "day_start_balance": args.budget,
        "daily_rate_needed": daily_rate,
        "days_remaining": args.days,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_state(state)

    # Summary
    summary = {
        "status": "TIGER configured",
        "config_file": CONFIG_FILE,
        "state_file": STATE_FILE,
        "budget": f"${args.budget:,.0f}",
        "target": f"${args.target:,.0f}",
        "return_needed": f"{((args.target / args.budget) - 1) * 100:.0f}%",
        "days": args.days,
        "daily_compound_rate": f"{daily_rate:.1f}%",
        "strategy_id": args.strategy_id,
        "wallet": args.wallet,
        "max_slots": args.max_slots,
        "max_leverage": args.max_leverage,
        "next_steps": [
            "Create 7 OpenClaw crons from references/cron-templates.md",
            "TIGER will start hunting on next cron cycle"
        ]
    }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
