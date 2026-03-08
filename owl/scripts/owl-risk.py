#!/usr/bin/env python3
"""OWL Risk Guardian — Monitors active positions for contrarian-specific risk signals.
Checks: re-crowding, OI recovery, funding floor adjustment, daily loss limits.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from owl_config import (
    get_strategy_dirs, load_config, load_state, save_state,
    load_crowding_history, mcporter_call, output,
)


def check_position_risk(asset: str, position: dict, history: dict, config: dict) -> dict:
    """Check risk signals for a single active position."""
    risk_cfg = config.get("risk", {})
    actions = []

    crowded_dir = position.get("crowdedDirection", "LONG")
    entry_time = position.get("enteredAt", "")

    # Fetch current instrument data
    instruments = mcporter_call("market_list_instruments", timeout=15)
    current_oi = None
    current_funding = None

    asset_list = []
    if isinstance(instruments, dict):
        data = instruments.get("data", instruments)
        if isinstance(data, dict):
            asset_list = data.get("instruments", data.get("assets", []))
        elif isinstance(data, list):
            asset_list = data

    for inst in asset_list:
        name = inst.get("name", inst.get("coin", ""))
        if name == asset:
            current_oi = float(inst.get("openInterest", inst.get("oi", 0)))
            current_funding = float(inst.get("fundingRate", inst.get("funding", 0)))
            break

    # 1. RE-CROWDING CHECK (CRITICAL)
    # If the crowding score is INCREASING since entry, the crowd is growing not unwinding
    snapshots = history.get("snapshots", {}).get(asset, [])
    entry_oi = position.get("oiAtEntry")
    if current_oi and entry_oi:
        oi_change_pct = (current_oi - entry_oi) / entry_oi * 100
        if oi_change_pct > 5:  # OI grew 5%+ since entry
            actions.append({
                "type": "reCrowding",
                "priority": "CRITICAL",
                "reason": f"OI increased {oi_change_pct:.1f}% since entry — crowd growing, not unwinding",
                "asset": asset,
            })

    # Check if funding is getting MORE extreme in the crowded direction
    funding_at_entry = position.get("fundingRateAtEntry", 0)
    if current_funding is not None and funding_at_entry:
        if crowded_dir == "LONG" and current_funding > funding_at_entry * 1.3:
            actions.append({
                "type": "reCrowding",
                "priority": "CRITICAL",
                "reason": f"Funding intensified from {funding_at_entry:.6f} to {current_funding:.6f} — crowd growing",
                "asset": asset,
            })
        elif crowded_dir == "SHORT" and current_funding < funding_at_entry * 1.3:
            actions.append({
                "type": "reCrowding",
                "priority": "CRITICAL",
                "reason": f"Funding intensified — crowd growing",
                "asset": asset,
            })

    # 2. OI RECOVERY CHECK (HIGH)
    # If OI dropped (triggering our entry) but then recovered back to pre-drop levels
    if current_oi and entry_oi:
        recovery_timeout = risk_cfg.get("oiRecoveryExitMin", 30)
        if entry_time:
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                minutes_held = (now - entry_dt).total_seconds() / 60
                if minutes_held <= recovery_timeout:
                    oi_recovery = current_oi / entry_oi
                    if oi_recovery >= 0.98:  # OI back to entry level
                        actions.append({
                            "type": "oiRecovery",
                            "priority": "HIGH",
                            "reason": f"OI recovered to {oi_recovery:.1%} of entry level within {minutes_held:.0f}min",
                            "asset": asset,
                        })
            except (ValueError, TypeError):
                pass

    # 3. FUNDING FLIP CHECK (MEDIUM)
    # If funding flipped to our side, we're no longer contrarian
    if current_funding is not None:
        our_dir = position.get("direction", "SHORT")
        if our_dir == "LONG" and current_funding < 0:
            # We're long and funding is negative (shorts pay longs) — funding now FAVORS longs
            # This means the crowd has shifted; we're no longer contrarian
            actions.append({
                "type": "fundingFlip",
                "priority": "MEDIUM",
                "reason": "Funding flipped — we're no longer contrarian. Tighten to breakeven.",
                "asset": asset,
            })
        elif our_dir == "SHORT" and current_funding > 0:
            actions.append({
                "type": "fundingFlip",
                "priority": "MEDIUM",
                "reason": "Funding flipped — we're no longer contrarian. Tighten to breakeven.",
                "asset": asset,
            })

    # 4. FUNDING FLOOR ADJUSTMENT (LOW)
    # Tighten SL by accumulated funding income
    if risk_cfg.get("fundingFloorAdjustEnabled", True):
        funding_earned = position.get("fundingEarned", 0)
        margin = position.get("marginUsed", 0)
        min_pct = risk_cfg.get("fundingFloorMinPct", 0.10) / 100
        if margin > 0 and funding_earned / margin >= min_pct:
            actions.append({
                "type": "fundingFloorAdjust",
                "priority": "LOW",
                "reason": f"Funding income ${funding_earned:.2f} ({funding_earned/margin*100:.2f}% of margin) — tighten SL",
                "asset": asset,
                "fundingEarned": funding_earned,
            })

    return {"asset": asset, "actions": actions}


def check_portfolio_risk(state: dict, config: dict) -> list:
    """Check portfolio-level risk limits."""
    risk_cfg = config.get("risk", {})
    actions = []
    daily = state.get("dailyStats", {})

    # Daily loss halt
    max_daily_loss = risk_cfg.get("maxDailyLossPct", 10)
    day_start = daily.get("dayStartBalance", config.get("budget", 2000))
    realized_pnl = daily.get("realizedPnl", 0)
    if day_start > 0 and realized_pnl < 0:
        loss_pct = abs(realized_pnl) / day_start * 100
        if loss_pct >= max_daily_loss:
            actions.append({
                "type": "dailyLossHalt",
                "priority": "CRITICAL",
                "reason": f"Daily loss {loss_pct:.1f}% >= {max_daily_loss}% limit — halting new entries",
            })

    return actions


def run():
    dirs = get_strategy_dirs()
    if not dirs:
        output({"success": True, "heartbeat": "HEARTBEAT_OK"})
        return

    all_actions = []

    for state_dir in dirs:
        config = load_config(state_dir)
        state = load_state(state_dir)
        history = load_crowding_history(state_dir)
        active = state.get("activePositions", {})

        if not active:
            continue

        # Check each position
        for asset, position in active.items():
            try:
                result = check_position_risk(asset, position, history, config)
                all_actions.extend(result["actions"])
            except Exception:
                continue

        # Portfolio-level checks
        portfolio_actions = check_portfolio_risk(state, config)
        all_actions.extend(portfolio_actions)

        # Apply daily loss halt if needed
        for action in portfolio_actions:
            if action["type"] == "dailyLossHalt":
                state.setdefault("safetyFlags", {})["dailyLossHalted"] = True
                save_state(state_dir, state)

    if all_actions:
        # Sort by priority
        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        all_actions.sort(key=lambda a: priority_order.get(a.get("priority", "LOW"), 99))
        output({
            "success": True,
            "actions": all_actions,
            "actionCount": len(all_actions),
        })
    else:
        output({"success": True, "heartbeat": "HEARTBEAT_OK"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        output({"success": False, "error": str(e)})
