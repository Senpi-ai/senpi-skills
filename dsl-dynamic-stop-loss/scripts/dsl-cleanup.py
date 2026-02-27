#!/usr/bin/env python3
"""DSL v5 strategy-level cleanup.
When all positions in a strategy are closed, removes the strategy directory
(no archiving). Run after disabling all crons for the strategy.

Usage:
  DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 python3 scripts/dsl-cleanup.py

Output: single JSON line to stdout (status=cleaned | blocked).
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

DSL_STATE_DIR = os.environ.get("DSL_STATE_DIR", "/data/workspace/dsl")
DSL_STRATEGY_ID = os.environ.get("DSL_STRATEGY_ID", "").strip()

if not DSL_STRATEGY_ID:
    print(json.dumps({"status": "error", "error": "DSL_STRATEGY_ID required"}), file=sys.stderr)
    sys.exit(1)

strategy_dir = os.path.join(DSL_STATE_DIR, DSL_STRATEGY_ID)
if not os.path.isdir(strategy_dir):
    print(json.dumps({
        "status": "cleaned",
        "strategy_id": DSL_STRATEGY_ID,
        "positions_deleted": 0,
        "blocked_by_active": [],
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "strategy_dir_missing"
    }))
    sys.exit(0)

blocked = []
deleted_count = 0
for name in os.listdir(strategy_dir):
    path = os.path.join(strategy_dir, name)
    if not name.endswith(".json") or not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            state = json.load(f)
        if state.get("active"):
            # Use asset from state; fallback to filename without .json
            asset = state.get("asset", name[:-5])
            blocked.append(asset)
    except (json.JSONDecodeError, OSError):
        # Corrupted or unreadable: treat as potentially active so we don't delete strategy dir
        blocked.append(name[:-5] if name.endswith(".json") else name)

if blocked:
    print(json.dumps({
        "status": "blocked",
        "strategy_id": DSL_STRATEGY_ID,
        "blocked_by_active": blocked,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }))
    sys.exit(1)

# All closed or empty: delete strategy directory
for name in os.listdir(strategy_dir):
    path = os.path.join(strategy_dir, name)
    if name.endswith(".json") and os.path.isfile(path):
        deleted_count += 1
shutil.rmtree(strategy_dir, ignore_errors=False)

print(json.dumps({
    "status": "cleaned",
    "strategy_id": DSL_STRATEGY_ID,
    "positions_deleted": deleted_count,
    "blocked_by_active": [],
    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
}))
