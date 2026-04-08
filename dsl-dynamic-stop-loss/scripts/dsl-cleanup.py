#!/usr/bin/env python3
"""DSL v5 strategy-level cleanup.
When all positions in a strategy are closed, reports status only. Does NOT delete
the strategy directory or any DSL state/archive files (retention policy: archive only).

Usage:
  DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 python3 scripts/dsl-cleanup.py

Output: single JSON line to stdout (status=cleaned | blocked).
"""
import json
import os
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
        "blocked_by_active": [],
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "strategy_dir_missing"
    }))
    sys.exit(0)

blocked = []
for name in os.listdir(strategy_dir):
    path = os.path.join(strategy_dir, name)
    if not name.endswith(".json") or not os.path.isfile(path):
        continue
    # Skip archived files (never treat as blocking)
    if "_archived" in name or ".archived" in name:
        continue
    try:
        with open(path) as f:
            state = json.load(f)
        if state.get("active"):
            asset = state.get("asset", name[:-5])
            blocked.append(asset)
    except (json.JSONDecodeError, OSError):
        blocked.append(name[:-5] if name.endswith(".json") else name)

if blocked:
    print(json.dumps({
        "status": "blocked",
        "strategy_id": DSL_STRATEGY_ID,
        "blocked_by_active": blocked,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }))
    sys.exit(1)

# All closed or empty: do NOT delete; retain all state and archive files
print(json.dumps({
    "status": "cleaned",
    "strategy_id": DSL_STRATEGY_ID,
    "blocked_by_active": [],
    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "note": "directory_retained_no_deletion"
}))
