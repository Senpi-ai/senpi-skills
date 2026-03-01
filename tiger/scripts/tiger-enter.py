#!/usr/bin/env python3
"""
tiger-enter.py — Deterministic position entry for TIGER.

Handles the full entry lifecycle atomically:
  1. Guard checks (halted, slots, duplicates)
  2. Execute create_position via mcporter
  3. Create DSL trailing-stop state file
  4. Update tiger-state.json activePositions
  5. Journal the event

Usage:
  python3 tiger-enter.py --coin SOL --direction SHORT --leverage 7 \
    --margin 400 --pattern MOMENTUM_BREAKOUT --score 0.65
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
    save_dsl_state, create_dsl_state, output,
)
from senpi_state.journal import TradeJournal
from senpi_state.positions import enter_position


def main():
    parser = argparse.ArgumentParser(description="TIGER deterministic position entry")
    parser.add_argument("--coin", required=True, help="Asset symbol (e.g. SOL)")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"])
    parser.add_argument("--leverage", type=int, required=True)
    parser.add_argument("--margin", type=float, required=True)
    parser.add_argument("--pattern", required=True,
                        help="Signal pattern (e.g. MOMENTUM_BREAKOUT)")
    parser.add_argument("--score", type=float, default=0.0,
                        help="Confluence score")
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

    def _create_dsl(asset, direction, entry_price, size, margin, leverage, pattern):
        return create_dsl_state(asset, direction, entry_price, size,
                                margin, leverage, pattern, config)

    result = enter_position(
        wallet=wallet,
        coin=args.coin.upper(),
        direction=args.direction.upper(),
        leverage=args.leverage,
        margin=args.margin,
        pattern=args.pattern,
        score=args.score,
        load_state=load_state,
        save_state=save_state,
        create_dsl=_create_dsl,
        save_dsl=save_dsl_state,
        journal=journal,
        skill="tiger",
        instance_key=instance_key,
        max_slots=max_slots,
        dry_run=args.dry_run,
    )
    output(result)


if __name__ == "__main__":
    main()
