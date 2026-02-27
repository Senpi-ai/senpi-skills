#!/usr/bin/env python3
"""
DSL cron entry point. Supports single (DSL_STATE_FILE), strategy (--strategy key), and multi (--mode multi) modes.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_single_mode():
    """Delegate to dsl-v4.py for single-position mode."""
    dsl_v4_path = os.path.join(script_dir, "dsl-v4.py")
    os.execv(sys.executable, [sys.executable, dsl_v4_path] + sys.argv[1:])


def run_strategy_mode(strategy_key: str) -> dict:
    """Load strategy, glob positions, batch fetch prices, process each, update strategy runtime. Returns output dict."""
    from dsl_common import fetch_all_prices, load_state, save_state, close_position, is_xyz_state
    from dsl_engine import (
        process_position,
        load_strategy,
        save_strategy,
        dsl_state_glob,
        emit_event,
    )

    base = os.environ.get("DSL_STATE_DIR", os.environ.get("WOLF_WORKSPACE", "/data/workspace"))
    desc = load_strategy(strategy_key, base)
    if not desc:
        return {"status": "error", "error": f"strategy not found: {strategy_key}", "time": _now_iso()}

    cfg = desc.get("config", {})
    wallet_override = cfg.get("wallet")  # strategy-level wallet if set
    state_files = sorted(glob.glob(dsl_state_glob(strategy_key, base)))
    now = _now_iso()
    all_mids = fetch_all_prices()
    xyz_mids = fetch_all_prices(dex="xyz")
    results = []
    closed_count = 0
    tier_changed_count = 0
    total_roe = 0.0
    active_count = 0

    def close_fn(wallet: str, coin: str, reason: str):
        return close_position(wallet, coin, reason)

    for path in state_files:
        try:
            state = load_state(path)
        except (json.JSONDecodeError, IOError):
            continue
        if not state.get("active") and not state.get("pendingClose"):
            continue
        asset = state.get("asset", state.get("config", {}).get("asset", ""))
        is_xyz = is_xyz_state(state)
        if is_xyz:
            key = asset if (isinstance(asset, str) and asset.startswith("xyz:")) else f"xyz:{asset}"
            price = xyz_mids.get(key) or xyz_mids.get(asset)
        else:
            price = all_mids.get(asset)
        if price is None:
            fails = state.get("consecutiveFetchFailures", 0) + 1
            state["consecutiveFetchFailures"] = fails
            state["lastCheck"] = now
            save_state(path, state)
            continue
        state["consecutiveFetchFailures"] = 0
        strategy_wallet = wallet_override or state.get("wallet", "")
        result = process_position(state, float(price), now_iso=now, close_fn=close_fn, strategy_wallet=strategy_wallet)
        save_state(path, state)
        results.append(result.to_dict())
        if result.closed:
            closed_count += 1
            emit_event(strategy_key, "position.closed", {
                "asset": result.asset,
                "reason": result.close_reason or "breach",
                "roe": result.upnl_pct,
                "phase": result.phase,
            }, base)
        if result.tier_changed:
            tier_changed_count += 1
        if result.status == "active":
            active_count += 1
            total_roe += result.upnl_pct

    runtime = desc.get("runtime", {})
    runtime["activePositions"] = active_count
    runtime["slotsAvailable"] = max(0, cfg.get("maxPositions", 3) - active_count)
    runtime["totalUnrealizedROE"] = round(total_roe, 2) if results else 0
    runtime["lastRunAt"] = now
    runtime["lastRunStatus"] = "HEARTBEAT_OK" if not results else ("CLOSED" if closed_count else "TIER_CHANGED" if tier_changed_count else "HEARTBEAT_OK")
    runtime["consecutiveErrors"] = 0
    desc["runtime"] = runtime
    save_strategy(strategy_key, desc, base)

    if closed_count and active_count == 0:
        emit_event(strategy_key, "strategy.all_closed", {"strategyKey": strategy_key, "position_count": len(results)}, base)
    if closed_count:
        emit_event(strategy_key, "strategy.slot_freed", {
            "strategyKey": strategy_key,
            "slots_available": runtime["slotsAvailable"],
            "slots_total": cfg.get("maxPositions", 3),
        }, base)

    return {
        "status": "ok",
        "time": now,
        "strategyKey": strategy_key,
        "positions": len(results),
        "active": active_count,
        "closed_this_run": closed_count,
        "results": results,
    }


def run_multi_mode(registry_path: str) -> dict:
    """Load registry, run each enabled strategy. Returns aggregated output."""
    if not os.path.isfile(registry_path):
        return {"status": "error", "error": f"registry not found: {registry_path}", "time": _now_iso()}
    with open(registry_path) as f:
        registry = json.load(f)
    strategies = registry.get("strategies", {})
    strategy_outputs = []
    for key, scfg in strategies.items():
        if scfg.get("enabled", True) is False:
            continue
        strategy_outputs.append(run_strategy_mode(key))
    return {
        "status": "ok",
        "time": _now_iso(),
        "strategies": list(strategies.keys()),
        "runs": strategy_outputs,
    }


def main():
    parser = argparse.ArgumentParser(description="DSL cron entry")
    parser.add_argument("--strategy", metavar="KEY", help="Run in strategy mode for this key")
    parser.add_argument("--mode", choices=["single", "strategy", "multi"], default="single")
    parser.add_argument("--registry", default=None, help="Registry JSON path (multi mode)")
    args, _ = parser.parse_known_args()

    if args.strategy:
        out = run_strategy_mode(args.strategy)
        print(json.dumps(out))
        if out.get("status") == "error":
            sys.exit(1)
        return
    if args.mode == "multi":
        from dsl_config import get_registry_path
        out = run_multi_mode(args.registry or get_registry_path())
        print(json.dumps(out))
        if out.get("status") == "error":
            sys.exit(1)
        return
    run_single_mode()


if __name__ == "__main__":
    main()
