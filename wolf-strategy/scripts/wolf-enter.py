#!/usr/bin/env python3
"""
wolf-enter.py — Deterministic position entry for WOLF.

Handles the full entry lifecycle atomically:
  1. Guard checks (slots, duplicates)
  2. Execute create_position via mcporter
  3. Create DSL trailing-stop state file
  4. Journal the event

Usage:
  python3 wolf-enter.py --strategy-key wolf-abc123 --coin HYPE \
    --direction LONG --leverage 7 --margin 400 \
    --entry-price 31.67 --size 88.44
"""

import argparse
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPTS_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "lib"))

from wolf_config import (
    load_strategy, dsl_state_template, save_dsl_state,
    load_dsl_state, mcporter_call, atomic_write, state_dir,
    get_all_active_positions,
)
from senpi_state.journal import TradeJournal


def main():
    parser = argparse.ArgumentParser(description="WOLF deterministic position entry")
    parser.add_argument("--strategy-key", required=True, help="Strategy key (e.g. wolf-abc123)")
    parser.add_argument("--coin", required=True, help="Asset symbol (e.g. HYPE)")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"])
    parser.add_argument("--leverage", type=int, required=True)
    parser.add_argument("--margin", type=float, required=True)
    parser.add_argument("--entry-price", type=float, default=0, help="Entry price (0 = fetch from market)")
    parser.add_argument("--size", type=float, default=0, help="Position size (0 = calculate from margin)")
    parser.add_argument("--pattern", default="SM_ENTRY", help="Signal pattern name")
    parser.add_argument("--score", type=float, default=0, help="Signal score")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_strategy(args.strategy_key)
    wallet = cfg.get("wallet", "")
    if not wallet:
        print(json.dumps({"success": False, "error": "No wallet in strategy config"}))
        return

    coin = args.coin.upper()
    active = get_all_active_positions()
    if coin in active:
        for pos in active[coin]:
            if pos["strategyKey"] == args.strategy_key:
                print(json.dumps({"success": False, "error": "DUPLICATE",
                                  "reason": f"{coin} already active in {args.strategy_key}"}))
                return

    max_slots = cfg.get("slots", 2)
    current_count = len([
        f for f in os.listdir(state_dir(args.strategy_key))
        if f.startswith("dsl-") and f.endswith(".json")
        and _is_active(os.path.join(state_dir(args.strategy_key), f))
    ]) if os.path.exists(state_dir(args.strategy_key)) else 0

    if current_count >= max_slots:
        print(json.dumps({"success": False, "error": "NO_SLOTS",
                          "reason": f"All {max_slots} slots occupied ({current_count} active)"}))
        return

    entry_price = args.entry_price
    size = args.size
    order_id = ""

    if not args.dry_run:
        order = {
            "coin": coin,
            "direction": args.direction.upper(),
            "leverage": args.leverage,
            "marginAmount": args.margin,
            "orderType": "MARKET",
        }
        try:
            result = mcporter_call(
                "create_position",
                strategyWalletAddress=wallet,
                orders=[order],
                reason=f"WOLF {args.pattern} score={args.score:.2f}",
            )
            if isinstance(result, dict):
                statuses = result.get("statuses", [result])
                if statuses:
                    st = statuses[0] if isinstance(statuses, list) else statuses
                    filled = st.get("filled", st)
                    if isinstance(filled, dict):
                        entry_price = float(filled.get("avgPx", filled.get("px", entry_price)))
                        size = abs(float(filled.get("totalSz", filled.get("sz", size))))
                        order_id = str(filled.get("oid", ""))
        except Exception as e:
            print(json.dumps({"success": False, "error": "MCP_FAILED", "reason": str(e)}))
            return

    if not entry_price:
        entry_price = 1.0
    if not size and entry_price > 0:
        size = round(args.margin * args.leverage / entry_price, 4)

    dsl_tiers = cfg.get("dsl", {}).get("tiers")
    dsl = dsl_state_template(
        asset=coin,
        direction=args.direction.upper(),
        entry_price=entry_price,
        size=size,
        leverage=args.leverage,
        strategy_key=args.strategy_key,
        tiers=dsl_tiers,
        created_by="wolf-enter",
    )
    save_dsl_state(args.strategy_key, coin, dsl)

    workspace = os.environ.get("WOLF_WORKSPACE",
                               os.environ.get("OPENCLAW_WORKSPACE", ""))
    journal_path = os.path.join(workspace, "state", "trade-journal.jsonl") if workspace else None
    if journal_path:
        journal = TradeJournal(journal_path)
        journal.record_entry(
            skill="wolf", instance_key=args.strategy_key, asset=coin,
            direction=args.direction.upper(), leverage=args.leverage,
            margin=args.margin, entry_price=entry_price, size=size,
            pattern=args.pattern, score=args.score, order_id=order_id,
            source="wolf-enter",
        )
        journal.record_dsl(
            skill="wolf", instance_key=args.strategy_key, asset=coin,
            event_type="DSL_CREATED", source="wolf-enter",
            details={"pattern": args.pattern},
        )

    print(json.dumps({
        "success": True,
        "action": "POSITION_OPENED",
        "coin": coin,
        "direction": args.direction.upper(),
        "leverage": args.leverage,
        "margin": args.margin,
        "entryPrice": entry_price,
        "size": size,
        "pattern": args.pattern,
        "orderId": order_id,
        "strategyKey": args.strategy_key,
    }))


def _is_active(path):
    try:
        with open(path) as f:
            return json.load(f).get("active", False)
    except Exception:
        return False


if __name__ == "__main__":
    main()
