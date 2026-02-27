#!/usr/bin/env python3
"""
dsl-migrate.py — One-shot migration: flat v1/v2 state files → v3 (meta + config + runtime).

Usage:
  python dsl-migrate.py [--state-dir /path/to/workspace]
  Migrates all state files under state-dir/state/dsl/ (or state-dir if it contains dsl-*.json).

If a file already has meta.schemaVersion == 3, it is skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Run from dsl/scripts
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsl_engine import migrate_state
from dsl_common import save_state


def _data_dir() -> str:
    return os.environ.get(
        "DSL_STATE_DIR",
        os.environ.get("WOLF_WORKSPACE", os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")),
    )


def main():
    p = argparse.ArgumentParser(description="Migrate DSL state files to v3")
    p.add_argument(
        "--state-dir",
        default=_data_dir(),
        help="Workspace root (default: DSL_STATE_DIR / WOLF_WORKSPACE / /data/workspace)",
    )
    args = p.parse_args()
    root = args.state_dir
    state_dsl = os.path.join(root, "state", "dsl")
    if not os.path.isdir(state_dsl):
        # Allow migrating a single dir that contains dsl-*.json
        state_dsl = root
    migrated = 0
    errors = []
    for strategy_key in os.listdir(state_dsl) if os.path.isdir(state_dsl) else []:
        strategy_dir = os.path.join(state_dsl, strategy_key)
        if not os.path.isdir(strategy_dir):
            continue
        for name in os.listdir(strategy_dir):
            if not name.startswith("dsl-") or not name.endswith(".json") or name.endswith(".tmp"):
                continue
            path = os.path.join(strategy_dir, name)
            try:
                with open(path) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{path}: {e}")
                continue
            if state.get("meta", {}).get("schemaVersion") == 3:
                continue
            try:
                state = migrate_state(state, strategy_key=strategy_key)
                save_state(path, state)
                migrated += 1
                print(f"Migrated {path}")
            except Exception as e:
                errors.append(f"{path}: {e}")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print(f"Done. Migrated {migrated} file(s).")


if __name__ == "__main__":
    main()
