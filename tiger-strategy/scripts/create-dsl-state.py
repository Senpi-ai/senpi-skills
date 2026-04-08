#!/usr/bin/env python3
"""
create-dsl-state.py — Helper to create properly formatted DSL v5.3.1 state files.
Avoids repeated schema mistakes (coin vs asset, phase string vs int, missing fields).

Usage:
  python3 create-dsl-state.py --asset SEI --direction LONG --leverage 10 --entry 0.068914 --size 86634
  python3 create-dsl-state.py --asset OP --direction SHORT --leverage 10 --entry 0.12926 --size 38688

Optional:
  --wallet ADDR          (default: reads from tiger-config.json)
  --strategy-id ID       (default: reads from tiger-config.json)
  --state-dir DIR        (default: /data/workspace/dsl)
  --dry-run              Print state JSON without writing file
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def load_tiger_config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "tiger-config.json")
    config_path = os.path.normpath(config_path)
    if not os.path.isfile(config_path):
        print(f"ERROR: tiger-config.json not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Create DSL v5.3.1 state file")
    parser.add_argument("--asset", required=True, help="Asset symbol (e.g. SEI, OP, xyz:SILVER)")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"])
    parser.add_argument("--leverage", required=True, type=int)
    parser.add_argument("--entry", required=True, type=float, help="Entry price")
    parser.add_argument("--size", required=True, type=float, help="Position size in units")
    parser.add_argument("--wallet", default=None, help="Strategy wallet address")
    parser.add_argument("--strategy-id", default=None, help="Strategy ID")
    parser.add_argument("--state-dir", default="/data/workspace/dsl", help="DSL state directory")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without writing")
    args = parser.parse_args()

    config = load_tiger_config()
    wallet = args.wallet or config.get("strategy_wallet", "")
    strategy_id = args.strategy_id or config.get("strategy_id", "")

    if not wallet:
        print("ERROR: No wallet. Pass --wallet or set strategy_wallet in tiger-config.json", file=sys.stderr)
        sys.exit(1)
    if not strategy_id:
        print("ERROR: No strategy ID. Pass --strategy-id or set strategy_id in tiger-config.json", file=sys.stderr)
        sys.exit(1)

    tiers = config.get("dsl_tiers", [])
    if not tiers:
        print("ERROR: No dsl_tiers in tiger-config.json", file=sys.stderr)
        sys.exit(1)

    is_long = args.direction == "LONG"
    leverage = args.leverage
    entry = args.entry

    # absoluteFloor: SL% ROE loss → price move = SL% / leverage
    # Use tiered SL if available: high conviction (score >= threshold) gets wider SL
    tiered_sl = config.get("tiered_sl", {})
    floor_retrace = config.get("dsl_retrace", {}).get("phase1", 0.03)
    min_hold = config.get("min_hold_minutes", 15)
    if is_long:
        absolute_floor = round(entry * (1 - floor_retrace / leverage), 6)
    else:
        absolute_floor = round(entry * (1 + floor_retrace / leverage), 6)

    # Phase 2 trigger tier: first tier index (0-based) where we transition
    phase2_trigger_tier = min(1, len(tiers) - 1)  # default: after T1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state = {
        "active": True,
        "asset": args.asset,  # NOT "coin" — DSL v5.3.1 uses "asset"
        "direction": args.direction,
        "entryPrice": entry,
        "size": args.size,
        "leverage": leverage,
        "wallet": wallet,
        "strategyId": strategy_id,
        "highWaterPrice": entry,
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "floorPrice": absolute_floor,
        "currentBreachCount": 0,
        "consecutiveFetchFailures": 0,
        "phase": 1,  # INTEGER, not string
        "phase2TriggerTier": phase2_trigger_tier,  # INTEGER
        "phase1": {
            "retraceThreshold": floor_retrace,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": absolute_floor,
        },
        "phase2": {
            "retraceThreshold": 0.015,
            "consecutiveBreachesRequired": 2,
        },
        "tiers": tiers,
        "breachDecay": "hard",
        "pendingClose": False,
        "closeRetries": 2,
        "closeRetryDelaySec": 3,
        "maxFetchFailures": 10,
        "minHoldMinutes": min_hold,
        "createdAt": now,
        "lastCheck": None,
        "lastPrice": None,
        "lastSyncedFloorPrice": None,
        "slOrderId": None,
        "slOrderIdUpdatedAt": None,
    }

    state_json = json.dumps(state, indent=2)

    if args.dry_run:
        print(state_json)
        return

    # Write to correct path: {state_dir}/{strategy_id}/{asset}.json
    asset_filename = args.asset.replace(":", "--", 1) if ":" in args.asset else args.asset
    strategy_dir = os.path.join(args.state_dir, strategy_id)
    os.makedirs(strategy_dir, exist_ok=True)
    out_path = os.path.join(strategy_dir, f"{asset_filename}.json")

    import tempfile
    if os.path.exists(out_path):
        print(f"WARNING: State file already exists at {out_path}", file=sys.stderr)
        print(f"Overwriting...", file=sys.stderr)

    dir_name = os.path.dirname(out_path) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(state_json)
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    print(f"Created DSL state file: {out_path}")
    print(f"  Asset: {args.asset} {args.direction} {leverage}x")
    print(f"  Entry: {entry}, Size: {args.size}")
    print(f"  Absolute floor: {absolute_floor}")
    print(f"  Tiers: {len(tiers)}")


if __name__ == "__main__":
    main()
