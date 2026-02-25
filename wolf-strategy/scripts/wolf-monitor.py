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
import subprocess, json, sys, os, glob
from datetime import datetime, timezone

# Add scripts dir to path for wolf_config import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (load_all_strategies, state_dir, dsl_state_glob,
                         WORKSPACE)

EMERGING_HISTORY = os.path.join(WORKSPACE, "history", "emerging-movers.json")
# Fallback to legacy location
if not os.path.exists(EMERGING_HISTORY):
    EMERGING_HISTORY = os.path.join(WORKSPACE, "emerging-movers-history.json")


def mcporter_call(tool, **kwargs):
    args = [f"{k}={v}" for k, v in kwargs.items()]
    cmd = ["mcporter", "call", f"senpi.{tool}"] + args + ["--output", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout)
        if d.get("content"):
            return json.loads(d["content"][0]["text"])
        return d
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_clearinghouse(wallet):
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_dsl_state_for_strategy(strategy_key, asset):
    """Read DSL state file for a specific strategy+asset."""
    path = os.path.join(WORKSPACE, "state", strategy_key, f"dsl-{asset}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def analyze_strategy(strategy_key, cfg):
    """Analyze a single strategy's positions and health."""
    wallet = cfg.get("wallet", "")
    xyz_wallet = cfg.get("xyzWallet")
    results = {"strategyKey": strategy_key, "name": cfg.get("name", ""), "positions": [], "alerts": [], "summary": {}}

    if not wallet:
        results["alerts"].append({"level": "ERROR", "msg": f"Strategy {strategy_key}: no wallet configured"})
        return results

    # Crypto wallet
    ch = get_clearinghouse(wallet)
    if not ch.get("success") and not ch.get("data"):
        results["alerts"].append({"level": "ERROR", "msg": f"Strategy {strategy_key}: failed to fetch crypto clearinghouse"})
    else:
        data = ch.get("data", ch)
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

        for ap in main.get("assetPositions", []):
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

            # DSL floor from strategy-scoped state
            dsl = get_dsl_state_for_strategy(strategy_key, coin)
            dsl_floor = float(dsl["floorPrice"]) if dsl and dsl.get("active") else None

            # Distance calculations
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
                "wallet_type": "crypto", "margin": round(float(pos["marginUsed"]), 2),
                "strategyKey": strategy_key
            }
            results["positions"].append(p)

            # Alert: liq closer than DSL floor
            if liq_dist_pct is not None and dsl_dist_pct is not None:
                if liq_dist_pct < dsl_dist_pct:
                    results["alerts"].append({
                        "level": "CRITICAL",
                        "strategyKey": strategy_key,
                        "msg": f"[{strategy_key}] {coin} {direction}: Liquidation ({liq_dist_pct}% away) CLOSER than DSL floor ({dsl_dist_pct}% away)!"
                    })

            # Alert: ROE below -15%
            if roe < -15:
                results["alerts"].append({
                    "level": "WARNING",
                    "strategyKey": strategy_key,
                    "msg": f"[{strategy_key}] {coin} {direction}: ROE at {round(roe, 1)}% -- approaching danger zone"
                })

            # Alert: liq distance < 30%
            if liq_dist_pct is not None and liq_dist_pct < 30:
                results["alerts"].append({
                    "level": "WARNING",
                    "strategyKey": strategy_key,
                    "msg": f"[{strategy_key}] {coin} {direction}: Liquidation only {liq_dist_pct}% away"
                })

        # Cross-margin alert
        buf = results["summary"].get("crypto_liq_buffer_pct", 100)
        if buf < 30:
            results["alerts"].append({
                "level": "CRITICAL" if buf < 15 else "WARNING",
                "strategyKey": strategy_key,
                "msg": f"[{strategy_key}] Cross-margin buffer: {buf}% (account ${round(acct_value, 2)}, maint margin ${round(maint_margin, 2)})"
            })

    # XYZ wallet (if configured)
    if xyz_wallet:
        ch2 = get_clearinghouse(xyz_wallet)
        if ch2.get("success") or ch2.get("data"):
            data2 = ch2.get("data", ch2)
            xyz = data2.get("xyz", {})
            xyz_acct = float(xyz.get("marginSummary", {}).get("accountValue", "0"))
            results["summary"]["xyz_account"] = xyz_acct
            for ap in xyz.get("assetPositions", []):
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

                liq_dist_pct = None
                if liq and direction == "LONG":
                    liq_dist_pct = round((price - liq) / price * 100, 1)
                elif liq and direction == "SHORT":
                    liq_dist_pct = round((liq - price) / price * 100, 1)

                p = {
                    "coin": coin, "direction": direction, "entry": entry,
                    "price": round(price, 4), "liq": liq, "upnl": round(upnl, 2),
                    "roe_pct": round(roe, 2), "liq_distance_pct": liq_dist_pct,
                    "dsl_floor": None, "dsl_distance_pct": None,
                    "wallet_type": "xyz", "margin": round(float(pos["marginUsed"]), 2),
                    "strategyKey": strategy_key
                }
                results["positions"].append(p)

                # XYZ isolated -- liq distance warning
                if liq_dist_pct is not None and liq_dist_pct < 25:
                    results["alerts"].append({
                        "level": "WARNING",
                        "strategyKey": strategy_key,
                        "msg": f"[{strategy_key}] {coin} {direction}: Liquidation only {liq_dist_pct}% away (isolated)"
                    })

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

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
