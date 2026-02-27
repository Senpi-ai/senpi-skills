#!/usr/bin/env python3
"""
dsl-init.py — Generate a new DSL position state file from a preset.

Usage:
  python dsl-init.py --preset wolf --asset HYPE --entry 28.87 --size 100 --leverage 10 \\
    --direction long --wallet 0x... [--strategy-key wolf-abc123] [--state-dir /data/workspace]

If --strategy-key is set, writes to state/dsl/{strategy_key}/dsl-{ASSET}.json and optionally
checks strategy_has_slot() before creating.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsl_engine import strategy_has_slot, dsl_state_path
from dsl_presets import PRESETS, generate_state_file
from dsl_common import save_state


def _data_dir() -> str:
    return os.environ.get(
        "DSL_STATE_DIR",
        os.environ.get("WOLF_WORKSPACE", os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")),
    )


def main():
    p = argparse.ArgumentParser(description="Create DSL position state file from preset")
    p.add_argument("--preset", default="moderate", choices=list(PRESETS), help="Preset name")
    p.add_argument("--asset", required=True, help="Asset ticker (e.g. HYPE, ETH)")
    p.add_argument("--entry", type=float, required=True, help="Entry price")
    p.add_argument("--size", type=float, required=True, help="Position size in units")
    p.add_argument("--leverage", type=int, default=10)
    p.add_argument("--direction", default="long", choices=["long", "short"])
    p.add_argument("--wallet", default="")
    p.add_argument("--strategy-key", default=None, help="Strategy key (e.g. wolf-abc123)")
    p.add_argument("--owner-skill", default=None)
    p.add_argument("--owner-ref", default=None)
    p.add_argument("--state-dir", default=_data_dir())
    p.add_argument("--no-slot-check", action="store_true", help="Skip strategy slot check")
    p.add_argument("--dex", default=None, help="e.g. xyz for XYZ DEX")
    args = p.parse_args()

    base = args.state_dir
    if args.strategy_key and not args.no_slot_check:
        if not strategy_has_slot(args.strategy_key, base):
            print(
                f"Error: strategy {args.strategy_key} has no available slots (max positions reached).",
                file=sys.stderr,
            )
            sys.exit(1)

    state = generate_state_file(
        preset=args.preset,
        asset=args.asset,
        entry=args.entry,
        size=args.size,
        leverage=args.leverage,
        direction=args.direction,
        wallet=args.wallet,
        strategy_key=args.strategy_key,
        owner_skill=args.owner_skill,
        owner_ref=args.owner_ref,
        dex=args.dex,
    )

    if args.strategy_key:
        path = dsl_state_path(args.strategy_key, args.asset, base)
    else:
        path = os.path.join(base, "state", "dsl", "default", f"dsl-{args.asset}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)

    save_state(path, state)
    print(f"Created {path}")
    print(f"Run DSL with: DSL_STATE_FILE={path} python scripts/dsl.py")
    if args.strategy_key:
        print(f"Or: python scripts/dsl.py --strategy {args.strategy_key}")


if __name__ == "__main__":
    main()
