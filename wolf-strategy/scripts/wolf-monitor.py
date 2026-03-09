#!/usr/bin/env python3
"""
WOLF Strategy Monitor v2 — Multi-strategy
- Iterates all enabled strategies from wolf-strategies.json
- Checks all positions across each strategy's wallets (crypto + XYZ)
- Computes liquidation distance vs DSL floor distance
- Flags positions where liq is closer than DSL
- Checks emerging movers for rotation candidates
- Per-strategy alerts and summary
- Outputs JSON with per-strategy results
"""
import json, sys, os, glob, subprocess
from datetime import datetime, timezone

# Add scripts dir to path for wolf_config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (load_all_strategies, state_dir, dsl_state_path,
                         dsl_position_state_files, resolve_dsl_cli_path,
                         DSL_STATE_DIR, WORKSPACE, mcporter_call_safe,
                         mcporter_call, heartbeat)

heartbeat("watchdog")

EMERGING_HISTORY = os.path.join(WORKSPACE, "history", "emerging-movers.json")
# Fallback to legacy location
if not os.path.exists(EMERGING_HISTORY):
    EMERGING_HISTORY = os.path.join(WORKSPACE, "emerging-movers-history.json")


def get_clearinghouse(wallet):
    """Fetch full clearinghouse state (main + xyz) in a single call."""
    return mcporter_call_safe("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_dsl_state_for_strategy(strategy_key, asset):
    """Read DSL state file for a specific strategy+asset (DSL v5.2 path)."""
    path = dsl_state_path(strategy_key, asset)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _process_positions(section_data, strategy_key, wallet_type, results):
    """Extract positions from a clearinghouse section (main or xyz)."""
    for ap in section_data.get("assetPositions", []):
        pos = ap["position"]
        coin = pos["coin"]
        szi = float(pos["szi"])
        if szi == 0:
            continue
        direction = "LONG" if szi > 0 else "SHORT"
        entry = float(pos["entryPx"])
        liq = float(pos["liquidationPx"]) if pos.get("liquidationPx") else None
        upnl = float(pos["unrealizedPnl"])
        roe = float(pos["returnOnEquity"]) * 100
        price = float(pos["positionValue"]) / abs(szi)

        state_coin = coin.replace("xyz:", "") if coin.startswith("xyz:") else coin
        dsl = get_dsl_state_for_strategy(strategy_key, state_coin)
        dsl_floor = float(dsl["floorPrice"]) if dsl and dsl.get("active") else None

        liq_dist_pct = None
        dsl_dist_pct = None
        if liq and direction == "LONG":
            liq_dist_pct = round((price - liq) / price * 100, 1)
        elif liq and direction == "SHORT":
            liq_dist_pct = round((liq - price) / price * 100, 1)

        if dsl_floor and direction == "LONG":
            dsl_dist_pct = round((price - dsl_floor) / price * 100, 1)
        elif dsl_floor and direction == "SHORT":
            dsl_dist_pct = round((dsl_floor - price) / price * 100, 1)

        p = {
            "coin": coin, "direction": direction, "entry": entry,
            "price": round(price, 4), "liq": liq, "upnl": round(upnl, 2),
            "roe_pct": round(roe, 2), "liq_distance_pct": liq_dist_pct,
            "dsl_floor": dsl_floor, "dsl_distance_pct": dsl_dist_pct,
            "wallet_type": wallet_type, "margin": round(float(pos["marginUsed"]), 2),
            "strategyKey": strategy_key
        }
        results["positions"].append(p)

        if liq_dist_pct is not None and dsl_dist_pct is not None:
            if liq_dist_pct < dsl_dist_pct:
                results["alerts"].append({
                    "level": "CRITICAL",
                    "strategyKey": strategy_key,
                    "msg": f"[{strategy_key}] {coin} {direction}: Liquidation ({liq_dist_pct}% away) CLOSER than DSL floor ({dsl_dist_pct}% away)!"
                })

        if roe < -15:
            results["alerts"].append({
                "level": "WARNING",
                "strategyKey": strategy_key,
                "msg": f"[{strategy_key}] {coin} {direction}: ROE at {round(roe, 1)}% -- approaching danger zone"
            })

        if liq_dist_pct is not None and liq_dist_pct < 30:
            results["alerts"].append({
                "level": "WARNING",
                "strategyKey": strategy_key,
                "msg": f"[{strategy_key}] {coin} {direction}: Liquidation only {liq_dist_pct}% away"
                       + (" (isolated)" if wallet_type == "xyz" else "")
            })


def analyze_strategy(strategy_key, cfg):
    """Analyze a single strategy's positions and health."""
    wallet = cfg.get("wallet", "")
    results = {"strategyKey": strategy_key, "name": cfg.get("name", ""), "positions": [], "alerts": [], "summary": {}}

    if not wallet:
        results["alerts"].append({"level": "ERROR", "msg": f"Strategy {strategy_key}: no wallet configured"})
        return results

    # Single clearinghouse call returns both main (crypto) and xyz (equities)
    data = get_clearinghouse(wallet)
    if not data:
        results["alerts"].append({"level": "ERROR", "msg": f"Strategy {strategy_key}: failed to fetch clearinghouse"})
        return results

    # --- Main (crypto) positions ---
    main = data.get("main", {})
    margin_summary = main.get("marginSummary", {})
    acct_value = float(margin_summary.get("accountValue", 0))
    total_margin = float(margin_summary.get("totalMarginUsed", 0))
    maint_margin = float(main.get("crossMaintenanceMarginUsed", 0))

    results["summary"]["crypto_account"] = acct_value
    results["summary"]["crypto_margin_used"] = total_margin
    results["summary"]["crypto_margin_pct"] = round(total_margin / acct_value * 100, 1) if acct_value > 0 else 0
    results["summary"]["crypto_maint_margin"] = maint_margin
    results["summary"]["crypto_liq_buffer_pct"] = round((acct_value - maint_margin) / acct_value * 100, 1) if acct_value > 0 else 0

    _process_positions(main, strategy_key, "crypto", results)

    buf = results["summary"].get("crypto_liq_buffer_pct", 100)
    if buf < 50:
        results["alerts"].append({
            "level": "CRITICAL" if buf < 30 else "WARNING",
            "strategyKey": strategy_key,
            "msg": f"[{strategy_key}] Cross-margin buffer: {buf}% (account ${round(acct_value, 2)}, maint margin ${round(maint_margin, 2)})"
        })

    # --- XYZ (equities) positions from same response ---
    xyz = data.get("xyz", {})
    xyz_acct = float(xyz.get("marginSummary", {}).get("accountValue", "0"))
    results["summary"]["xyz_account"] = xyz_acct

    _process_positions(xyz, strategy_key, "xyz", results)

    # Total P&L for this strategy
    total_upnl = sum(p["upnl"] for p in results["positions"])
    results["summary"]["total_upnl"] = round(total_upnl, 2)
    results["summary"]["total_account"] = round(
        results["summary"].get("crypto_account", 0) + results["summary"].get("xyz_account", 0), 2
    )
    results["summary"]["slots_used"] = len(results["positions"])
    results["summary"]["slots_max"] = cfg.get("slots", 3)

    return results


def main():
    strategies = load_all_strategies()

    if not strategies:
        print(json.dumps({"status": "ok", "strategies": {}, "alerts": [], "message": "No enabled strategies"}))
        sys.exit(0)

    output = {"strategies": {}, "alerts": [], "summary": {}}
    all_held_coins = set()

    for key, cfg in strategies.items():
        strategy_result = analyze_strategy(key, cfg)
        output["strategies"][key] = strategy_result
        output["alerts"].extend(strategy_result.get("alerts", []))
        for p in strategy_result.get("positions", []):
            all_held_coins.add(p["coin"])

    # Check emerging movers for rotation candidates (shared across strategies)
    try:
        with open(EMERGING_HISTORY) as f:
            history = json.load(f)
        scans = history.get("scans", history) if isinstance(history, dict) else history
        if isinstance(scans, list) and len(scans) >= 2:
            latest = scans[-1].get("markets", scans[-1].get("top_movers", []))
            prev = scans[-2].get("markets", scans[-2].get("top_movers", []))
            climbers = []
            for m in latest[:10]:
                asset = m.get("token", m.get("asset", ""))
                if asset not in all_held_coins:
                    prev_ranks = {pm.get("token", pm.get("asset")): pm.get("rank", 99) for pm in prev}
                    prev_rank = prev_ranks.get(asset, 99)
                    curr_rank = m.get("rank", 99)
                    if curr_rank < prev_rank and curr_rank <= 15:
                        climbers.append(f"{asset} #{prev_rank}->#{curr_rank}")
            if climbers:
                output["alerts"].append({
                    "level": "INFO",
                    "msg": f"Emerging rotation candidates (not held in any strategy): {', '.join(climbers[:3])}"
                })
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # Global summary
    total_account = sum(
        s.get("summary", {}).get("total_account", 0)
        for s in output["strategies"].values()
    )
    total_upnl = sum(
        s.get("summary", {}).get("total_upnl", 0)
        for s in output["strategies"].values()
    )
    output["summary"] = {
        "total_strategies": len(strategies),
        "total_account": round(total_account, 2),
        "total_upnl": round(total_upnl, 2),
        "total_positions": sum(len(s.get("positions", [])) for s in output["strategies"].values()),
        "total_alerts": len(output["alerts"]),
    }

    # --- Phase 1 auto-cut (DSL v5.2: moved from dsl-combined to Watchdog) ---
    PHASE1_MAX_MINUTES = 90
    WEAK_PEAK_CUT_MINUTES = 45
    WEAK_PEAK_THRESHOLD = 3.0
    dsl_cron_to_remove_out = None
    action_required = []

    for strat_key, cfg in strategies.items():
        dsl_cfg = cfg.get("dsl", {})
        phase1_max = dsl_cfg.get("phase1MaxMinutes", PHASE1_MAX_MINUTES)
        weak_peak_min = dsl_cfg.get("weakPeakCutMinutes", WEAK_PEAK_CUT_MINUTES)
        weak_peak_thresh = dsl_cfg.get("weakPeakThreshold", WEAK_PEAK_THRESHOLD)
        strategy_uuid = cfg.get("strategyId", "")
        wallet = cfg.get("wallet", "")

        for state_path in dsl_position_state_files(strat_key):
            try:
                with open(state_path) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            if not state.get("active") or state.get("phase") != 1:
                continue
            asset = state.get("asset", "")
            if not asset:
                bn = os.path.basename(state_path)
                asset = bn.replace(".json", "").replace("--", ":", 1) if "xyz--" in bn else bn.replace(".json", "")
            created_at_s = state.get("createdAt")
            if not created_at_s:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_s.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            now = datetime.now(timezone.utc)
            elapsed_min = (now - created_at).total_seconds() / 60
            entry = float(state.get("entryPrice", 0))
            lev = float(state.get("leverage", 1))
            hw = float(state.get("highWaterPrice", entry))
            last_px = float(state.get("lastPrice", state.get("entryPrice", entry)))
            if entry <= 0 or lev <= 0:
                continue
            if state.get("direction") == "LONG":
                peak_roe = (hw - entry) / entry * lev * 100
                current_roe = (last_px - entry) / entry * lev * 100
            else:
                peak_roe = (entry - hw) / entry * lev * 100
                current_roe = (entry - last_px) / entry * lev * 100

            autocut, reason = False, ""
            if elapsed_min >= phase1_max:
                autocut, reason = True, f"Phase 1 timeout {elapsed_min:.0f}min"
            elif (elapsed_min >= weak_peak_min and peak_roe < weak_peak_thresh and current_roe < peak_roe):
                autocut, reason = True, "Weak peak early cut"

            if not autocut:
                continue

            coin = asset if asset.startswith("xyz:") else asset
            dex = "xyz" if asset.startswith("xyz:") else "main"
            try:
                mcporter_call("close_position", strategyWalletAddress=wallet, coin=coin, reason=reason)
            except Exception:
                continue
            try:
                r = subprocess.run(
                    ["python3", resolve_dsl_cli_path(),
                     "delete-dsl", strategy_uuid, asset, dex,
                     "--state-dir", DSL_STATE_DIR],
                    capture_output=True, text=True, timeout=20,
                )
                if r.returncode == 0 and r.stdout:
                    try:
                        cli_out = json.loads(r.stdout)
                        if cli_out.get("cron_to_remove"):
                            dsl_cron_to_remove_out = cli_out["cron_to_remove"]
                    except json.JSONDecodeError:
                        pass
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            action_required.append({
                "asset": asset, "strategyKey": strat_key, "reason": reason,
                "closed_by_script": True,
            })

    # --- Actionable outputs for LLM mandate ---
    notifications = []  # empty — LLM sends notification after closing

    for strat_key, strat_data in output["strategies"].items():
        for alert in strat_data.get("alerts", []):
            if alert.get("level") == "CRITICAL" and "buffer" in alert.get("msg", "").lower():
                # Find weakest ROE position in this strategy
                positions = strat_data.get("positions", [])
                if positions:
                    weakest = min(positions, key=lambda p: p.get("roe_pct", 0))
                    action_required.append({
                        "action": "close_position",
                        "strategyKey": strat_key,
                        "coin": weakest["coin"],
                        "direction": weakest["direction"],
                        "roe_pct": weakest["roe_pct"],
                        "reason": alert["msg"]
                    })

    if dsl_cron_to_remove_out:
        output["dsl_cron_to_remove"] = dsl_cron_to_remove_out
    output["notifications"] = notifications
    output["action_required"] = action_required

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
