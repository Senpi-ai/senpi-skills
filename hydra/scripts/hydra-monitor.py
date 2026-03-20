# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

"""
HYDRA v1.0 — Monitor Watchdog
===============================
Independent 3rd cron that runs every 5 minutes.

Checks:
  1. Account health (drawdown cap)
  2. Capital exposure (deployed margin threshold)
  3. Signal reversal (FDD re-check)
  4. Daily loss limit
  5. Consecutive losses → cooldown gate

Can force-close positions or activate cooldown gate.
"""

import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydra_config import (
    load_config, load_runtime, save_runtime,
    get_positions, get_wallet_balance, get_deployed_margin,
    list_active_dsl_states, mcporter_call, output, log,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════════
# Check 1: Account Health (Drawdown Cap)
# ═══════════════════════════════════════════════════════════════════════════

def check_account_health(wallet_balance: float, config: dict) -> dict:
    """Check if account has breached drawdown cap."""
    monitor_cfg = config.get("monitor", {})
    drawdown_cap = monitor_cfg.get("drawdownCapPct", 0.25)

    # Get initial budget from config (set at deploy time)
    initial_budget = safe_float(config.get("sizing", {}).get("initialBudget",
                                config.get("wallet", {}).get("budget", 0)))

    if initial_budget <= 0:
        return {"status": "ok", "note": "No initial budget set — cannot check drawdown"}

    drawdown = (initial_budget - wallet_balance) / initial_budget
    if drawdown >= drawdown_cap:
        return {
            "status": "ALERT",
            "action": "FORCE_CLOSE_ALL",
            "reason": f"Drawdown {drawdown:.1%} >= cap {drawdown_cap:.1%}. "
                      f"Balance: ${wallet_balance:.2f}, Initial: ${initial_budget:.2f}",
        }

    return {"status": "ok", "drawdown": drawdown, "cap": drawdown_cap}


# ═══════════════════════════════════════════════════════════════════════════
# Check 2: Capital Exposure
# ═══════════════════════════════════════════════════════════════════════════

def check_exposure(positions: list, wallet_balance: float, config: dict) -> dict:
    """Check if total deployed margin exceeds threshold."""
    monitor_cfg = config.get("monitor", {})
    threshold = monitor_cfg.get("exposureThresholdPct", 0.60)

    deployed = get_deployed_margin(positions)
    if wallet_balance <= 0:
        return {"status": "ok", "note": "Cannot determine balance"}

    exposure = deployed / wallet_balance
    if exposure >= threshold:
        return {
            "status": "WARNING",
            "action": "GATE_CLOSE",
            "reason": f"Exposure {exposure:.1%} >= threshold {threshold:.1%}. "
                      f"Deployed: ${deployed:.2f}, Balance: ${wallet_balance:.2f}",
        }

    return {"status": "ok", "exposure": exposure, "threshold": threshold}


# ═══════════════════════════════════════════════════════════════════════════
# Check 3: Signal Reversal (FDD Re-check)
# ═══════════════════════════════════════════════════════════════════════════

def check_signal_reversal(positions: list, active_states: list, config: dict) -> list:
    """
    Re-run FDD check for each open position.
    If original thesis has flipped, flag for force-close.
    """
    monitor_cfg = config.get("monitor", {})
    if not monitor_cfg.get("checkSignalReversal", True):
        return []

    reversals = []
    for coin, state in active_states:
        direction = state.get("direction", "").lower()
        if not direction:
            continue

        # Re-check funding
        data = mcporter_call("market_get_asset_data", {"asset": coin})
        if not data or not isinstance(data, dict):
            continue

        funding = safe_float(data.get("funding", data.get("fundingRate", 0)))

        # Original thesis: SHORT_CROWDING (negative funding) → went long
        # Reversal: funding flipped positive significantly
        if direction == "long" and funding > 0.0003:
            reversals.append({
                "asset": coin,
                "reason": f"FDD reversal: entered LONG on SHORT_CROWDING but funding now +{funding:.6f} (longs crowded)",
                "action": "FORCE_CLOSE" if monitor_cfg.get("forceCloseOnReversal", True) else "WARNING",
            })
        elif direction == "short" and funding < -0.0003:
            reversals.append({
                "asset": coin,
                "reason": f"FDD reversal: entered SHORT on LONG_CROWDING but funding now {funding:.6f} (shorts crowded)",
                "action": "FORCE_CLOSE" if monitor_cfg.get("forceCloseOnReversal", True) else "WARNING",
            })

    return reversals


# ═══════════════════════════════════════════════════════════════════════════
# Check 4: Daily Loss Limit
# ═══════════════════════════════════════════════════════════════════════════

def check_daily_loss(runtime: dict, wallet_balance: float, config: dict) -> dict:
    """Check cumulative realized losses today against daily limit."""
    monitor_cfg = config.get("monitor", {})
    daily_limit_pct = monitor_cfg.get("dailyLossLimitPct", 0.10)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trade_log = runtime.get("tradeLog", [])

    daily_losses = 0.0
    for trade in trade_log:
        if not isinstance(trade, dict):
            continue
        ts = trade.get("timestamp", "")
        if ts.startswith(today):
            roe = safe_float(trade.get("roe", 0))
            if roe < 0:
                daily_losses += abs(roe)

    # Convert to percentage of wallet
    if wallet_balance <= 0:
        return {"status": "ok", "note": "Cannot determine balance"}

    # daily_losses is in ROE% terms — approximate dollar impact
    # This is rough but directionally correct
    if daily_losses >= daily_limit_pct * 100:
        return {
            "status": "ALERT",
            "action": "GATE_CLOSE",
            "reason": f"Daily losses {daily_losses:.1f}% ROE >= limit {daily_limit_pct * 100:.1f}%",
        }

    return {"status": "ok", "dailyLosses": daily_losses,
            "limit": daily_limit_pct * 100}


# ═══════════════════════════════════════════════════════════════════════════
# Check 5: Consecutive Losses
# ═══════════════════════════════════════════════════════════════════════════

def check_consecutive_losses(runtime: dict, config: dict) -> dict:
    """Check if consecutive losses trigger cooldown."""
    gate_cfg = config.get("gate", {})
    max_losses = gate_cfg.get("maxConsecutiveLosses", 3)
    cooldown_min = gate_cfg.get("cooldownMinutes", 30)

    consec = runtime.get("consecutiveLosses", 0)
    if consec >= max_losses:
        return {
            "status": "ALERT",
            "action": "GATE_COOLDOWN",
            "reason": f"{consec} consecutive losses >= {max_losses}. Cooldown {cooldown_min}min.",
            "cooldownMinutes": cooldown_min,
        }

    return {"status": "ok", "consecutiveLosses": consec, "limit": max_losses}


# ═══════════════════════════════════════════════════════════════════════════
# Orphan Recovery
# ═══════════════════════════════════════════════════════════════════════════

def check_orphans(positions: list, active_states: list) -> list:
    """Find on-chain positions with no DSL state managing them."""
    managed_coins = set(coin.upper() for coin, _ in active_states)
    orphans = []

    for pos in positions:
        coin = str(pos.get("coin", pos.get("asset", ""))).upper()
        if coin and coin not in managed_coins:
            orphans.append({
                "asset": coin,
                "size": safe_float(pos.get("szi", pos.get("size", 0))),
                "entryPrice": safe_float(pos.get("entryPx", pos.get("avgEntryPrice", 0))),
            })

    return orphans


# ═══════════════════════════════════════════════════════════════════════════
# Main Monitor
# ═══════════════════════════════════════════════════════════════════════════

def run():
    log("=" * 60)
    log(f"Monitor run — {datetime.now(timezone.utc).isoformat()}")
    log("=" * 60)

    config = load_config()
    runtime = load_runtime()

    wallet_balance = get_wallet_balance()
    positions = get_positions()
    active_states = list_active_dsl_states()

    alerts = []
    actions = []

    # ── Check 1: Account Health ──────────────────────────────────────────
    health = check_account_health(wallet_balance, config)
    if health["status"] != "ok":
        log(f"ALERT: {health['reason']}")
        alerts.append(health)
        if health.get("action") == "FORCE_CLOSE_ALL":
            actions.append({"action": "FORCE_CLOSE_ALL", "reason": health["reason"]})

    # ── Check 2: Exposure ────────────────────────────────────────────────
    exposure = check_exposure(positions, wallet_balance, config)
    if exposure["status"] != "ok":
        log(f"WARNING: {exposure['reason']}")
        alerts.append(exposure)
        if exposure.get("action") == "GATE_CLOSE":
            actions.append({"action": "GATE_CLOSE", "reason": exposure["reason"]})

    # ── Check 3: Signal Reversal ─────────────────────────────────────────
    reversals = check_signal_reversal(positions, active_states, config)
    for rev in reversals:
        log(f"REVERSAL: {rev['asset']} — {rev['reason']}")
        alerts.append(rev)
        if rev.get("action") == "FORCE_CLOSE":
            actions.append({"action": "FORCE_CLOSE", "asset": rev["asset"],
                           "reason": rev["reason"]})

    # ── Check 4: Daily Loss ──────────────────────────────────────────────
    daily = check_daily_loss(runtime, wallet_balance, config)
    if daily["status"] != "ok":
        log(f"ALERT: {daily['reason']}")
        alerts.append(daily)
        if daily.get("action") == "GATE_CLOSE":
            runtime["gate"] = "CLOSED"
            actions.append({"action": "GATE_CLOSE", "reason": daily["reason"]})

    # ── Check 5: Consecutive Losses ──────────────────────────────────────
    consec = check_consecutive_losses(runtime, config)
    if consec["status"] != "ok":
        log(f"ALERT: {consec['reason']}")
        alerts.append(consec)
        if consec.get("action") == "GATE_COOLDOWN":
            runtime["gate"] = "COOLDOWN"
            runtime["gateExpiresAt"] = time.time() + (consec["cooldownMinutes"] * 60)
            actions.append({"action": "GATE_COOLDOWN", "reason": consec["reason"]})

    # ── Orphan Check ─────────────────────────────────────────────────────
    orphans = check_orphans(positions, active_states)
    if orphans:
        for orph in orphans:
            log(f"ORPHAN: {orph['asset']} — no DSL state managing this position")
        alerts.append({"status": "WARNING", "action": "ORPHAN_RECOVERY",
                       "orphans": orphans})
        actions.append({"action": "ORPHAN_RECOVERY", "orphans": orphans})

    # ── Save runtime ─────────────────────────────────────────────────────
    save_runtime(runtime)

    # ── Output ───────────────────────────────────────────────────────────
    result = {
        "status": "ok" if not alerts else "alerts",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "walletBalance": wallet_balance,
            "positions": len(positions),
            "activeStates": len(active_states),
            "gate": runtime.get("gate", "UNKNOWN"),
            "alerts": len(alerts),
        },
        "alerts": alerts,
        "actions": actions,
    }

    if alerts:
        log(f"Monitor found {len(alerts)} alert(s), {len(actions)} action(s).")
    else:
        log("Monitor: all clear.")

    output(result)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        output({"status": "error", "error": str(e)})
        sys.exit(1)
