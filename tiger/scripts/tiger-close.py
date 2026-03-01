#!/usr/bin/env python3
"""
tiger-close.py — Deterministic position close for TIGER.

Handles the full close lifecycle atomically:
  1. Execute close_position via mcporter
  2. Deactivate DSL trailing-stop state file
  3. Remove from tiger-state.json activePositions
  4. Log trade to trade-log.json
  5. Journal the event

Idempotent — safe to call even if position is already closed on-chain.

Usage:
  python3 tiger-close.py --coin SOL --reason "DSL Tier 2 breach"
"""

import argparse
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPTS_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "lib"))

from tiger_config import (
    load_config, load_state, save_state,
    load_dsl_state, save_dsl_state, log_trade, output,
)
from senpi_state.journal import TradeJournal
from senpi_state.positions import close_position_safe


def main():
    parser = argparse.ArgumentParser(description="TIGER deterministic position close")
    parser.add_argument("--coin", required=True, help="Asset symbol (e.g. SOL)")
    parser.add_argument("--reason", required=True, help="Close reason")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip MCP call, just update state")
    args = parser.parse_args()

    config = load_config()
    wallet = config.get("strategy_wallet", config.get("strategyWallet", ""))
    if not wallet:
        output({"success": False, "error": "No strategy wallet configured"})
        return

    instance_key = config.get("strategyId", config.get("strategy_id", "default"))
    max_slots = config.get("maxSlots", config.get("max_slots", 3))

    workspace = os.environ.get(
        "TIGER_WORKSPACE",
        os.environ.get("OPENCLAW_WORKSPACE", ""))
    journal_path = os.path.join(workspace, "state", "trade-journal.jsonl") if workspace else None
    journal = TradeJournal(journal_path) if journal_path else None

    result = close_position_safe(
        wallet=wallet,
        coin=args.coin.upper(),
        reason=args.reason,
        load_state=load_state,
        save_state=save_state,
        load_dsl=load_dsl_state,
        save_dsl=save_dsl_state,
        journal=journal,
        skill="tiger",
        instance_key=instance_key,
        max_slots=max_slots,
        log_trade_fn=log_trade,
        dry_run=args.dry_run,
    )
    output(result)


if __name__ == "__main__":
    main()
