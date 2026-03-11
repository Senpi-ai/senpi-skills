#!/usr/bin/env python3
"""shark-health.py — Health check (every 10 min, isolated).

Validates:
- Orphan DSL state files (DSL state exists but no matching active position)
- State file consistency (shark-state.json active_positions vs clearinghouse)
- OI history freshness (warn if stale > 15 min)
- Strategy active status via Senpi MCP
- Liq map freshness
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-health"

OI_STALE_SECONDS = 900  # 15 min
LIQ_MAP_STALE_SECONDS = 900


def check_orphan_dsl(strategy_id: str, active_coins: set[str]) -> list[dict]:
    """Check for DSL state files with no matching active position."""
    issues = []
    dsl_dir = cfg.dsl_state_path(strategy_id)

    if not os.path.isdir(dsl_dir):
        return issues

    for name in os.listdir(dsl_dir):
        if not name.endswith(".json") or "_archived" in name:
            continue

        # Convert filename back to asset
        base = name[:-5]
        if base.startswith("xyz--"):
            asset = "xyz:" + base[5:]
        else:
            asset = base

        if asset not in active_coins:
            issues.append({
                "type": "orphan_dsl",
                "asset": asset,
                "file": name,
                "detail": f"DSL state exists for {asset} but no active position found",
            })

    return issues


def check_state_consistency(state: dict, active_coins: set[str]) -> list[dict]:
    """Check shark-state.json active_positions vs clearinghouse."""
    issues = []
    active_positions = state.get("active_positions", {})

    # Positions in state but not on chain
    for asset in active_positions:
        if asset not in active_coins:
            issues.append({
                "type": "phantom_position",
                "asset": asset,
                "detail": f"{asset} in shark-state but not in clearinghouse — may have been closed externally",
            })

    return issues


def check_data_freshness(sd: str) -> list[dict]:
    """Check if OI history and liq map are fresh."""
    issues = []
    now = cfg.now_ts()

    # OI history
    history_path = os.path.join(sd, "shark-oi-history.json")
    history = cfg.load_json(history_path, {})
    if history:
        latest_ts = 0
        for entries in history.values():
            if entries:
                ts = entries[-1].get("ts", 0)
                if ts > latest_ts:
                    latest_ts = ts
        if latest_ts > 0 and (now - latest_ts) > OI_STALE_SECONDS:
            age_min = (now - latest_ts) / 60
            issues.append({
                "type": "stale_oi_history",
                "age_minutes": round(age_min, 1),
                "detail": f"OI history is {age_min:.0f} min old — OI tracker may not be running",
            })
    else:
        issues.append({
            "type": "missing_oi_history",
            "detail": "No OI history file — OI tracker has not run yet",
        })

    # Liq map
    liq_map_path = os.path.join(sd, "shark-liq-map.json")
    liq_map = cfg.load_json(liq_map_path, {})
    if liq_map:
        latest_update = None
        for entry in liq_map.values():
            upd = entry.get("updated_at")
            if upd and (latest_update is None or upd > latest_update):
                latest_update = upd
        if latest_update:
            try:
                from datetime import datetime, timezone
                upd_time = datetime.fromisoformat(latest_update.replace("Z", "+00:00"))
                age_sec = (datetime.now(timezone.utc) - upd_time).total_seconds()
                if age_sec > LIQ_MAP_STALE_SECONDS:
                    issues.append({
                        "type": "stale_liq_map",
                        "age_minutes": round(age_sec / 60, 1),
                        "detail": f"Liquidation map is {age_sec / 60:.0f} min old",
                    })
            except (ValueError, TypeError):
                pass
    else:
        issues.append({
            "type": "missing_liq_map",
            "detail": "No liquidation map file — mapper has not run yet",
        })

    return issues


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)

    for strat in strategies:
        sk = strat.get("strategyId")
        wallet = strat.get("wallet")
        if not sk or not wallet:
            continue

        sd = cfg.state_dir(sk)
        all_issues = []

        # Check strategy is active
        strategy, err = cfg.fetch_strategy(sk)
        if err:
            all_issues.append({
                "type": "strategy_check_failed",
                "detail": f"Could not verify strategy status: {err}",
            })
        elif strategy:
            status = (strategy.get("status") or "").upper()
            if status not in ("ACTIVE", "PAUSED"):
                all_issues.append({
                    "type": "strategy_not_active",
                    "status": status,
                    "detail": f"Strategy status is {status} — SHARK should not be running",
                    "notify": True,
                })

        # Get active positions from clearinghouse
        positions, pos_err = cfg.get_active_positions(wallet)
        active_coins = set(positions.keys()) if positions else set()

        if pos_err:
            all_issues.append({
                "type": "clearinghouse_error",
                "detail": f"Could not fetch positions: {pos_err}",
            })

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"active_positions": {}})

        # Check orphan DSL
        all_issues.extend(check_orphan_dsl(sk, active_coins))

        # Check state consistency
        all_issues.extend(check_state_consistency(state, active_coins))

        # Clean phantom positions from state
        phantom_assets = [asset for asset in state.get("active_positions", {})
                         if asset not in active_coins]
        if phantom_assets:
            for asset in phantom_assets:
                del state["active_positions"][asset]
            state["updated_at"] = cfg.now_iso()
            cfg.atomic_write(state_path, state)

        # Check data freshness
        all_issues.extend(check_data_freshness(sd))

        if all_issues:
            cfg.output({
                "status": "issues_found",
                "script": SCRIPT,
                "strategyId": sk,
                "issue_count": len(all_issues),
                "issues": all_issues,
            })
        else:
            cfg.output({
                "status": "ok",
                "script": SCRIPT,
                "strategyId": sk,
                "active_positions": len(active_coins),
                "state_positions": len(state.get("active_positions", {})),
            })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
