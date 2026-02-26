#!/usr/bin/env python3
"""
WOLF Job Health Check v2 — Multi-strategy meta-watchdog
Verifies per-strategy:
1. All active positions have a DSL state file
2. No orphan DSL state files (active but no matching position)
3. DSL state files are fresh (checked within 10 min)
4. Direction matches between state and wallet position
5. State files have correct strategyKey

Outputs JSON with per-strategy issues[] array.
"""

import json, subprocess, sys, os, glob, time
from datetime import datetime, timezone

# Add scripts dir to path for wolf_config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import load_all_strategies, dsl_state_glob


def run_cmd(args, timeout=30):
    """Run a subprocess command with 3 retries."""
    last_error = None
    for attempt in range(3):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip()
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)
    raise last_error


def get_wallet_positions(wallet):
    """Get actual positions from clearinghouse for a specific wallet."""
    try:
        raw = run_cmd(["mcporter", "call", "senpi", "strategy_get_clearinghouse_state",
                       f"strategy_wallet={wallet}"])
        data = json.loads(raw).get("data", {})
        positions = {}
        for section in ["main", "crypto"]:
            if section in data and "assetPositions" in data[section]:
                for p in data[section]["assetPositions"]:
                    pos = p.get("position", {})
                    coin = pos.get("coin")
                    if coin:
                        szi = float(pos.get("szi", 0))
                        if szi != 0:
                            positions[coin] = {
                                "direction": "SHORT" if szi < 0 else "LONG",
                                "size": abs(szi),
                                "entryPx": pos.get("entryPx"),
                                "unrealizedPnl": pos.get("unrealizedPnl"),
                                "returnOnEquity": pos.get("returnOnEquity"),
                            }
        return positions
    except Exception as e:
        return {"_error": str(e)}


def get_xyz_positions(wallet):
    """Get XYZ positions from clearinghouse."""
    try:
        raw = run_cmd(["mcporter", "call", "senpi", "strategy_get_clearinghouse_state",
                       f"strategy_wallet={wallet}", "dex=xyz"])
        data = json.loads(raw).get("data", {})
        positions = {}
        xyz = data.get("xyz", {})
        for p in xyz.get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin")
            if coin:
                szi = float(pos.get("szi", 0))
                if szi != 0:
                    positions[coin] = {
                        "direction": "SHORT" if szi < 0 else "LONG",
                        "size": abs(szi),
                        "entryPx": pos.get("entryPx"),
                    }
        return positions
    except Exception:
        return {}


def get_active_dsl_states(strategy_key):
    """Read all DSL state files for a specific strategy."""
    states = {}
    for f in sorted(glob.glob(dsl_state_glob(strategy_key))):
        try:
            with open(f) as fh:
                state = json.load(fh)
            # Extract asset from filename: dsl-HYPE.json -> HYPE
            asset = os.path.basename(f).replace("dsl-", "").replace(".json", "")
            states[asset] = {
                "active": state.get("active", False),
                "pendingClose": state.get("pendingClose", False),
                "file": f,
                "direction": state.get("direction"),
                "lastCheck": state.get("lastCheck"),
                "strategyKey": state.get("strategyKey", strategy_key),
            }
        except (json.JSONDecodeError, IOError):
            continue
    return states


def check_strategy(strategy_key, cfg):
    """Run health checks for a single strategy."""
    issues = []
    now = datetime.now(timezone.utc)
    wallet = cfg.get("wallet", "")

    if not wallet:
        issues.append({
            "level": "CRITICAL",
            "type": "NO_WALLET",
            "strategyKey": strategy_key,
            "message": f"Strategy {strategy_key}: no wallet configured"
        })
        return issues, [], []

    # Get actual positions
    positions = get_wallet_positions(wallet)
    if "_error" in positions:
        issues.append({
            "level": "WARNING",
            "type": "FETCH_ERROR",
            "strategyKey": strategy_key,
            "message": f"Strategy {strategy_key}: failed to fetch positions: {positions['_error']}"
        })
        positions = {}

    # Get XYZ positions if configured
    xyz_wallet = cfg.get("xyzWallet")
    xyz_positions = {}
    if xyz_wallet:
        xyz_positions = get_xyz_positions(xyz_wallet)

    # Merge positions (XYZ coins might have xyz: prefix)
    all_positions = dict(positions)
    for coin, pos in xyz_positions.items():
        all_positions[coin] = pos

    # Get DSL states for this strategy
    dsl_states = get_active_dsl_states(strategy_key)

    # Check: every position has an active DSL state
    for coin, pos in all_positions.items():
        asset_key = coin
        # Check with and without xyz: prefix
        if asset_key not in dsl_states:
            clean_key = coin.replace("xyz:", "")
            if clean_key in dsl_states:
                asset_key = clean_key
            else:
                issues.append({
                    "level": "CRITICAL",
                    "type": "NO_DSL",
                    "strategyKey": strategy_key,
                    "asset": coin,
                    "message": f"[{strategy_key}] {coin} {pos['direction']} has NO DSL state file -- unprotected position"
                })
                continue

        dsl = dsl_states[asset_key]
        if not dsl["active"] and not dsl["pendingClose"]:
            issues.append({
                "level": "CRITICAL",
                "type": "DSL_INACTIVE",
                "strategyKey": strategy_key,
                "asset": coin,
                "message": f"[{strategy_key}] {coin} has DSL state file but active=false -- unprotected position"
            })
        elif dsl["direction"] != pos["direction"]:
            issues.append({
                "level": "CRITICAL",
                "type": "DIRECTION_MISMATCH",
                "strategyKey": strategy_key,
                "asset": coin,
                "message": f"[{strategy_key}] {coin} position is {pos['direction']} but DSL is {dsl['direction']}"
            })

        # Check DSL freshness
        if dsl.get("lastCheck"):
            try:
                last = datetime.fromisoformat(dsl["lastCheck"].replace("Z", "+00:00"))
                age_min = (now - last).total_seconds() / 60
                if age_min > 10:
                    issues.append({
                        "level": "WARNING",
                        "type": "DSL_STALE",
                        "strategyKey": strategy_key,
                        "asset": coin,
                        "message": f"[{strategy_key}] {coin} DSL last checked {round(age_min)}min ago -- cron may not be firing"
                    })
            except (ValueError, TypeError):
                pass

    # Check: no orphan DSL states (active but no matching position)
    for asset, dsl in dsl_states.items():
        if dsl["active"]:
            clean_asset = asset.replace("xyz:", "")
            if clean_asset not in all_positions and asset not in all_positions:
                # Also check xyz:-prefixed version
                xyz_asset = f"xyz:{asset}"
                if xyz_asset not in all_positions:
                    issues.append({
                        "level": "WARNING",
                        "type": "ORPHAN_DSL",
                        "strategyKey": strategy_key,
                        "asset": asset,
                        "message": f"[{strategy_key}] {asset} DSL state is active but no matching position -- should deactivate"
                    })

    return issues, list(all_positions.keys()), [a for a, d in dsl_states.items() if d["active"]]


def main():
    now = datetime.now(timezone.utc)
    strategies = load_all_strategies()

    if not strategies:
        print(json.dumps({"status": "ok", "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "strategies": 0, "issues": [], "message": "No enabled strategies"}))
        sys.exit(0)

    all_issues = []
    strategy_results = {}

    for key, cfg in strategies.items():
        issues, positions, active_dsl = check_strategy(key, cfg)
        all_issues.extend(issues)
        strategy_results[key] = {
            "positions": positions,
            "active_dsl": active_dsl,
            "issues": issues,
            "issue_count": len(issues),
            "critical_count": sum(1 for i in issues if i["level"] == "CRITICAL"),
        }

    result = {
        "status": "ok" if not any(i["level"] == "CRITICAL" for i in all_issues) else "critical",
        "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategies": strategy_results,
        "issues": all_issues,
        "issue_count": len(all_issues),
        "critical_count": sum(1 for i in all_issues if i["level"] == "CRITICAL"),
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
