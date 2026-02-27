#!/usr/bin/env python3
"""
dsl-strategy-init.py — Create a strategy descriptor (strategy.json).

Usage:
  python dsl-strategy-init.py --strategy-key wolf-abc123 --owner-skill wolf-strategy --owner-ref abc123 \\
    [--max-positions 3] [--display-name "Wolf Strategy A"] [--state-dir /data/workspace]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsl_engine import create_strategy

def _data_dir() -> str:
    return os.environ.get(
        "DSL_STATE_DIR",
        os.environ.get("WOLF_WORKSPACE", os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")),
    )


def main():
    p = argparse.ArgumentParser(description="Create DSL strategy descriptor")
    p.add_argument("--strategy-key", required=True, help="Strategy key (e.g. wolf-abc123)")
    p.add_argument("--owner-skill", required=True, help="Owner skill name (e.g. wolf-strategy)")
    p.add_argument("--owner-ref", required=True, help="Owner reference (e.g. strategy id)")
    p.add_argument("--display-name", default=None, help="Display name")
    p.add_argument("--max-positions", type=int, default=3)
    p.add_argument("--state-dir", default=_data_dir())
    p.add_argument("--cron-interval", type=int, default=180)
    p.add_argument("--retention-days", type=int, default=30)
    p.add_argument("--cleanup-on-close", action="store_true")
    args = p.parse_args()
    config = {
        "maxPositions": args.max_positions,
        "model": {"primary": "<primary-model-id>"},
        "cron": {"intervalSeconds": args.cron_interval, "mode": "strategy"},
        "state": {
            "cleanupOnClose": args.cleanup_on_close,
            "retentionDays": args.retention_days,
            "maxHistoryPerPosition": 0,
        },
        "execution": {"preset": "moderate", "outputLevel": "full"},
    }
    desc = create_strategy(
        args.strategy_key,
        args.owner_skill,
        args.owner_ref,
        config,
        display_name=args.display_name,
        base=args.state_dir,
    )
    print(json.dumps(desc, indent=2))
    print(f"Created strategy.json at state/dsl/{args.strategy_key}/strategy.json", file=sys.stderr)


if __name__ == "__main__":
    main()
