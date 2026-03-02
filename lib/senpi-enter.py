#!/usr/bin/env python3
"""
senpi-enter.py — Generic deterministic position entry for any Senpi trading skill.

Works with any skill that provides a get_lifecycle_adapter() function in its
config module. The adapter returns the callbacks that positions.enter_position()
needs — wallet, state loaders, DSL creators, etc.

Usage:
  python3 senpi-enter.py --skill wolf --config-dir wolf-strategy/scripts \
    --strategy wolf-abc123 --coin HYPE --direction LONG --leverage 10 \
    --margin 500 --pattern FIRST_JUMP --score 0.80
"""

import argparse
import importlib.util
import json
import os
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LIB_DIR)

from senpi_state.journal import TradeJournal
from senpi_state.positions import enter_position


def _import_config(skill, config_dir):
    """Dynamically import {skill}_config from config_dir."""
    module_name = f"{skill}_config"
    module_path = os.path.join(config_dir, f"{module_name}.py")

    if not os.path.isfile(module_path):
        return None, f"{module_name}.py not found in {config_dir}"

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod

    if config_dir not in sys.path:
        sys.path.insert(0, config_dir)

    spec.loader.exec_module(mod)
    return mod, None


def _output(data):
    print(json.dumps(data))


def main():
    parser = argparse.ArgumentParser(
        description="Senpi generic deterministic position entry")
    parser.add_argument("--skill", required=True,
                        help="Skill name (tiger, wolf, lion, viper)")
    parser.add_argument("--config-dir", required=True,
                        help="Path to directory containing {skill}_config.py")
    parser.add_argument("--strategy", default=None,
                        help="Strategy key (for multi-strategy skills like wolf)")
    parser.add_argument("--coin", required=True, help="Asset symbol (e.g. SOL)")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"])
    parser.add_argument("--leverage", type=int, required=True)
    parser.add_argument("--margin", type=float, required=True)
    parser.add_argument("--pattern", required=True,
                        help="Signal pattern (e.g. MOMENTUM_BREAKOUT, FIRST_JUMP)")
    parser.add_argument("--score", type=float, default=0.0,
                        help="Confluence score")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip MCP call, just update state")
    args = parser.parse_args()

    config_dir = os.path.abspath(args.config_dir)
    mod, err = _import_config(args.skill, config_dir)
    if err:
        _output({"success": False, "error": err})
        return

    if not hasattr(mod, "get_lifecycle_adapter"):
        _output({"success": False,
                 "error": f"{args.skill}_config has no get_lifecycle_adapter()"})
        return

    adapter_kwargs = {}
    if args.strategy:
        adapter_kwargs["strategy_key"] = args.strategy
    adapter = mod.get_lifecycle_adapter(**adapter_kwargs)

    journal = None
    if adapter.get("journal_path"):
        journal = TradeJournal(adapter["journal_path"])

    out_fn = adapter.get("output", _output)

    result = enter_position(
        wallet=adapter["wallet"],
        coin=args.coin.upper(),
        direction=args.direction.upper(),
        leverage=args.leverage,
        margin=args.margin,
        pattern=args.pattern,
        score=args.score,
        load_state=adapter["load_state"],
        save_state=adapter["save_state"],
        create_dsl=adapter["create_dsl"],
        save_dsl=adapter["save_dsl"],
        journal=journal,
        skill=adapter["skill"],
        instance_key=adapter["instance_key"],
        max_slots=adapter["max_slots"],
        dry_run=args.dry_run,
    )
    out_fn(result)


if __name__ == "__main__":
    main()
