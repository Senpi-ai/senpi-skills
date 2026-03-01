#!/usr/bin/env python3
"""
wolf-close.py — Deterministic position close for WOLF.

Handles the full close lifecycle atomically:
  1. Execute close_position via mcporter
  2. Deactivate DSL trailing-stop state file
  3. Journal the event

Idempotent — safe to call even if position is already closed on-chain.

Usage:
  python3 wolf-close.py --strategy-key wolf-abc123 --coin HYPE --reason "SM flip"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPTS_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "lib"))

from wolf_config import (
    load_strategy, load_dsl_state, save_dsl_state, mcporter_call,
)
from senpi_state.journal import TradeJournal


def main():
    parser = argparse.ArgumentParser(description="WOLF deterministic position close")
    parser.add_argument("--strategy-key", required=True, help="Strategy key")
    parser.add_argument("--coin", required=True, help="Asset symbol")
    parser.add_argument("--reason", required=True, help="Close reason")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_strategy(args.strategy_key)
    wallet = cfg.get("wallet", "")
    if not wallet:
        print(json.dumps({"success": False, "error": "No wallet in strategy config"}))
        return

    coin = args.coin.upper()
    already_closed = False

    if not args.dry_run:
        try:
            mcporter_call(
                "close_position",
                strategyWalletAddress=wallet,
                coin=coin,
                reason=args.reason,
            )
        except RuntimeError as e:
            err_str = str(e).lower()
            if "no_position" in err_str or "not found" in err_str:
                already_closed = True
            else:
                print(json.dumps({"success": False, "error": "MCP_FAILED", "reason": str(e)}))
                return

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dsl = load_dsl_state(args.strategy_key, coin)
    entry_price = 0
    direction = ""

    if dsl:
        entry_price = dsl.get("entryPrice", 0)
        direction = dsl.get("direction", "")
        dsl["active"] = False
        dsl["closedAt"] = now_iso
        dsl["closeReason"] = args.reason
        dsl["deactivatedBy"] = "wolf-close"
        save_dsl_state(args.strategy_key, coin, dsl)

    workspace = os.environ.get("WOLF_WORKSPACE",
                               os.environ.get("OPENCLAW_WORKSPACE", ""))
    journal_path = os.path.join(workspace, "state", "trade-journal.jsonl") if workspace else None
    if journal_path:
        journal = TradeJournal(journal_path)
        journal.record_exit(
            skill="wolf", instance_key=args.strategy_key, asset=coin,
            direction=direction, reason=args.reason,
            entry_price=entry_price, source="wolf-close",
        )
        journal.record_dsl(
            skill="wolf", instance_key=args.strategy_key, asset=coin,
            event_type="DSL_DEACTIVATED", source="wolf-close",
            details={"reason": args.reason},
        )

    print(json.dumps({
        "success": True,
        "action": "POSITION_CLOSED",
        "coin": coin,
        "direction": direction,
        "reason": args.reason,
        "entryPrice": entry_price,
        "alreadyClosed": already_closed,
        "strategyKey": args.strategy_key,
    }))


if __name__ == "__main__":
    main()
