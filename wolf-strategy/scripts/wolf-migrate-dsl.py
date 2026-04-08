#!/usr/bin/env python3
"""
One-time migration: move DSL state from {workspace}/state/{strategyKey}/dsl-{ASSET}.json
to {workspace}/dsl/{strategyId_UUID}/{asset}.json (DSL v5.2 path convention).

Run before switching to per-strategy DSL v5 crons. For each active DSL file:
- Copies state to new path (atomic write).
- Sets active: false in old path (tombstone; do not delete).

Usage: python3 wolf-migrate-dsl.py [--dry-run]
"""
import json
import os
import sys
import glob
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (
    load_all_strategies,
    DSL_STATE_DIR,
    WORKSPACE,
    asset_to_filename,
    atomic_write,
)


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no files will be written")

    migrated = []
    skipped = []
    errors = []

    for strategy_key, cfg in load_all_strategies().items():
        strategy_uuid = cfg.get("strategyId")
        if not strategy_uuid:
            errors.append(f"{strategy_key}: no strategyId in config")
            continue
        old_dir = os.path.join(WORKSPACE, "state", strategy_key)
        if not os.path.isdir(old_dir):
            continue
        pattern = os.path.join(old_dir, "dsl-*.json")
        for old_path in glob.glob(pattern):
            basename = os.path.basename(old_path)
            asset = basename.replace("dsl-", "").replace(".json", "")
            if not asset:
                continue
            try:
                with open(old_path) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{old_path}: read failed — {e}")
                continue
            if not state.get("active"):
                skipped.append((strategy_key, asset, "inactive"))
                continue
            new_filename = f"{asset_to_filename(asset)}.json"
            new_dir = os.path.join(DSL_STATE_DIR, strategy_uuid)
            new_path = os.path.join(new_dir, new_filename)
            if not dry_run:
                os.makedirs(new_dir, exist_ok=True)
                atomic_write(new_path, state)
                state["active"] = False
                state["migratedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                atomic_write(old_path, state)
            migrated.append((strategy_key, asset, old_path, new_path))

    out = {"migrated": len(migrated), "skipped": len(skipped), "errors": len(errors)}
    if migrated:
        out["migrated_files"] = [{"strategyKey": k, "asset": a, "new_path": np} for k, a, op, np in migrated]
    if skipped:
        out["skipped_list"] = [{"strategyKey": k, "asset": a, "reason": r} for k, a, r in skipped]
    if errors:
        out["error_messages"] = errors
    print(json.dumps(out, indent=2))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
