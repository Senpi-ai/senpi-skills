#!/usr/bin/env python3
"""shark-movers.py — Emerging Movers integration for SHARK (every 3 min, main session).

Runs the Emerging Movers scanner and acts on IMMEDIATE signals by opening
positions through SHARK's pipeline. Uses SHARK's risk guardian, DSL v5.3.1,
and all existing safety rails.

Entry criteria (stricter than standalone Emerging Movers):
- IMMEDIATE_MOVER signal (10+ rank jump from #25+)
- NOT erratic, NOT low velocity
- Trader count >= 15 (higher floor than EM's 10)
- 4h price change aligned with direction (momentum confirmation)
- SHARK risk gate is OPEN
- Not already in an active position for this asset
- Not at max capacity (2 slots)
- Correlation guard (max 1 BTC-correlated)

Position sizing uses SHARK's config (18% margin, 8x leverage).
DSL v5.3.1 state file created automatically.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-movers"

# Layer 3: Tighter filters — quality over quantity
MIN_TRADER_COUNT = 20         # Need real consensus (was 10)
MIN_CONTRIB_VELOCITY = 0.04   # Stronger momentum (was 0.02)
MIN_PRICE_CHANGE_4H = 0.5    # Meaningful move (was 0.3%)
MAX_RANK = 25                 # Top of leaderboard only (was 40)


def run_emerging_movers() -> dict | None:
    """Run the Emerging Movers scanner and return its output."""
    em_script = os.path.expanduser("~/.agents/skills/emerging-movers/scripts/emerging-movers.py")
    if not os.path.isfile(em_script):
        return None

    try:
        r = subprocess.run(
            ["python3", em_script],
            capture_output=True, text=True, timeout=45,
            env={**os.environ}
        )
        if r.returncode != 0:
            cfg.output_error(SCRIPT, f"emerging-movers failed: {r.stderr[:200]}")
            return None
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        cfg.output_error(SCRIPT, f"emerging-movers error: {e}")
        return None


def check_risk_gate(strat: dict) -> tuple[bool, str | None]:
    """Check if risk guardian gate is open."""
    sk = strat.get("strategyId")
    sd = cfg.state_dir(sk)
    counter_path = os.path.join(sd, "trade-counter.json")
    counter = cfg.load_json(counter_path)
    if not counter:
        return True, None

    gate = counter.get("gate", "OPEN")
    if gate != "OPEN":
        return False, counter.get("gateReason", gate)

    cooldown = counter.get("cooldownUntil")
    if cooldown:
        try:
            from datetime import datetime, timezone
            cd_time = datetime.fromisoformat(cooldown.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < cd_time:
                return False, f"cooldown until {cooldown}"
        except (ValueError, TypeError):
            pass

    return True, None


def filter_signal(alert: dict, active_positions: dict, strat: dict) -> tuple[bool, str | None]:
    """Apply SHARK-grade filters to an Emerging Movers signal.
    Returns (pass, rejection_reason).
    """
    token = alert.get("token", "")
    direction = alert.get("direction", "").upper()
    rank = alert.get("currentRank", 99)
    traders = alert.get("traders", 0)
    contrib_vel = alert.get("contribVelocity", 0)
    price_chg = alert.get("priceChg4h", 0)
    erratic = alert.get("erratic", False)
    low_vel = alert.get("lowVelocity", False)

    # IMMEDIATE only — higher conviction entries
    if not alert.get("isImmediate"):
        return False, "not IMMEDIATE signal"

    if erratic:
        return False, f"{token} erratic rank history"

    if low_vel:
        return False, f"{token} low velocity ({contrib_vel:.4f})"

    # Trader count floor (stricter than EM's 10)
    if traders < MIN_TRADER_COUNT:
        return False, f"{token} only {traders} traders (need {MIN_TRADER_COUNT})"

    # Must have climbed into top N
    if rank > MAX_RANK:
        return False, f"{token} rank #{rank} too deep (need top {MAX_RANK})"

    # Velocity gate
    if contrib_vel < MIN_CONTRIB_VELOCITY:
        return False, f"{token} velocity {contrib_vel:.4f} < {MIN_CONTRIB_VELOCITY}"

    # Price must be moving in signal direction (4h confirmation)
    if direction == "LONG" and price_chg < MIN_PRICE_CHANGE_4H:
        return False, f"{token} LONG but 4h change only {price_chg:.2f}% (need +{MIN_PRICE_CHANGE_4H}%)"
    if direction == "SHORT" and price_chg > -MIN_PRICE_CHANGE_4H:
        return False, f"{token} SHORT but 4h change only {price_chg:.2f}% (need -{MIN_PRICE_CHANGE_4H}%)"

    # Require sustained contribution growth — 4+ consecutive rank improvements
    # Zigzag ranks = noise. Consistent climbing = real momentum.
    rank_history = alert.get("rankHistory", [])
    nums = [r for r in rank_history if r is not None]
    if len(nums) >= 4:
        # Check last 4 moves are consistently climbing (each rank <= previous)
        recent = nums[-4:]
        climbing = all(recent[i] >= recent[i+1] for i in range(len(recent)-1))
        if not climbing:
            return False, f"{token} rank not climbing 4+ consecutive: {recent}"
    elif len(nums) < 4:
        return False, f"{token} only {len(nums)} rank checks — need 4+ for confidence"

    # Already in position
    # Check both exact token and xyz: prefixed version
    if token in active_positions:
        return False, f"already in {token}"
    if f"xyz:{token}" in active_positions:
        return False, f"already in xyz:{token}"

    # Max capacity
    max_slots = strat.get("maxSlots", 2)
    if len(active_positions) >= max_slots:
        return False, f"at capacity ({len(active_positions)}/{max_slots})"

    # Correlation guard
    if cfg.is_btc_correlated(token):
        for pos_coin in active_positions:
            if cfg.is_btc_correlated(pos_coin):
                return False, f"already have BTC-correlated position ({pos_coin})"

    return True, None


def create_dsl_state(strat: dict, asset: str, direction: str,
                     entry_price: float, leverage: int, margin: float,
                     wallet: str) -> None:
    """Create DSL v5.3.1 state file for a movers-sourced position."""
    strategy_id = strat.get("strategyId")
    dsl_dir = cfg.dsl_state_path(strategy_id)

    if asset.startswith("xyz:"):
        filename = asset.replace(":", "--", 1) + ".json"
    else:
        filename = asset + ".json"

    dsl_config = strat.get("dsl", {})
    tiers = dsl_config.get("tiers", [
        {"triggerPct": 5, "lockPct": 2},
        {"triggerPct": 10, "lockPct": 5},
        {"triggerPct": 20, "lockPct": 14},
        {"triggerPct": 30, "lockPct": 24},
        {"triggerPct": 40, "lockPct": 34},
        {"triggerPct": 50, "lockPct": 44},
        {"triggerPct": 65, "lockPct": 56},
        {"triggerPct": 80, "lockPct": 72},
        {"triggerPct": 100, "lockPct": 90},
    ])

    # PREDATOR DSL: wider retrace, longer timeouts — let winners develop
    retrace = 0.035
    if direction.upper() == "LONG":
        absolute_floor = round(entry_price * (1 - retrace / leverage), 6)
    else:
        absolute_floor = round(entry_price * (1 + retrace / leverage), 6)

    state = {
        "active": True,
        "asset": asset,
        "direction": direction.upper(),
        "leverage": leverage,
        "entryPrice": entry_price,
        "size": round(margin * leverage / entry_price, 6) if entry_price > 0 else 0,
        "wallet": wallet,
        "strategyId": strategy_id,
        "phase": 1,
        "phase1": {
            "retraceThreshold": 0.035,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": absolute_floor,
            "hardTimeoutMinutes": 60,
            "weakPeakCutMinutes": 25,
            "weakPeakThreshold": 3.0,
            "deadWeightCutMinutes": 20,
        },
        "phase2TriggerTier": 1,
        "phase2": {
            "retraceThreshold": 0.015,
            "consecutiveBreachesRequired": 1,
        },
        "tiers": tiers,
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "highWaterPrice": entry_price,
        "floorPrice": absolute_floor,
        "currentBreachCount": 0,
        "createdAt": cfg.now_iso(),
    }

    dsl_path = os.path.join(dsl_dir, filename)
    cfg.atomic_write(dsl_path, state)


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)

    # Run Emerging Movers scanner
    em_output = run_emerging_movers()
    if not em_output or em_output.get("status") != "ok":
        cfg.heartbeat(SCRIPT)
        return

    immediate = em_output.get("immediateMovers", [])
    if not immediate:
        # No immediate signals — silent
        cfg.output({
            "status": "ok",
            "script": SCRIPT,
            "alerts": len(em_output.get("alerts", [])),
            "immediate": 0,
            "scans": em_output.get("scansInHistory", 0),
        })
        return

    for strat in strategies:
        sk = strat.get("strategyId")
        wallet = strat.get("wallet")
        if not sk or not wallet:
            continue

        sd = cfg.state_dir(sk)
        budget = strat.get("budget", 5000)
        margin_pct = strat.get("marginPct", 0.18)
        default_leverage = strat.get("defaultLeverage", 8)

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"stalking": [], "strike": [], "active_positions": {}})
        active_positions = state.get("active_positions", {})

        # Reconcile state with on-chain positions BEFORE checking capacity
        on_chain, recon_err = cfg.get_active_positions(wallet)
        if not recon_err:
            on_chain_coins = set(on_chain.keys())
            phantoms = [a for a in active_positions if a not in on_chain_coins]
            if phantoms:
                for p in phantoms:
                    del active_positions[p]
                state["active_positions"] = active_positions
                state["updated_at"] = cfg.now_iso()
                cfg.atomic_write(state_path, state)

        # Check risk gate
        gate_open, gate_reason = check_risk_gate(strat)
        if not gate_open:
            cfg.output({"status": "gate_closed", "script": SCRIPT, "strategyId": sk,
                        "reason": gate_reason, "immediate_signals": len(immediate)})
            continue

        entered = False

        for alert in immediate:
            if entered:
                break

            token = alert.get("token", "")
            direction = alert.get("direction", "").upper()
            dex = alert.get("dex", "")

            # Build full asset name
            asset = f"xyz:{token}" if dex == "xyz" else token

            # Ban xyz assets — unreliable signals, cost us $339
            if asset.startswith("xyz:"):
                continue

            # Apply filters
            passes, reason = filter_signal(alert, active_positions, strat)
            if not passes:
                cfg.output({
                    "status": "signal_filtered",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "direction": direction,
                    "reason": reason,
                    "rank": alert.get("currentRank"),
                    "traders": alert.get("traders"),
                })
                continue

            # Get current price
            prices, err = cfg.fetch_prices([asset])
            if err or not prices:
                cfg.output_error(SCRIPT, f"price fetch failed for {asset}: {err}", strategyId=sk)
                continue

            current_price = float(prices.get(asset, 0))
            if current_price <= 0:
                continue

            # Check position doesn't already exist on chain
            positions, pos_err = cfg.get_active_positions(wallet)
            if pos_err:
                cfg.output_error(SCRIPT, f"clearinghouse check failed: {pos_err}", strategyId=sk)
                continue
            if asset in positions:
                continue

            # Layer 1: Smart money alignment
            sm_aligned, sm_pct, sm_dir = cfg.check_sm_alignment(asset, direction)
            if not sm_aligned:
                cfg.output({
                    "status": "sm_misaligned",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "our_direction": direction,
                    "sm_direction": sm_dir,
                    "sm_pct": round(sm_pct, 2),
                })
                continue

            # Layer 2: Candle confirmation
            candle_ok, candle_reason = cfg.check_candle_confirmation(asset, direction)
            if not candle_ok:
                cfg.output({
                    "status": "candle_rejected",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "direction": direction,
                    "reason": candle_reason,
                })
                continue

            # Calculate margin
            margin = round(budget * margin_pct, 2)
            leverage = default_leverage

            reason_str = (f"SHARK/Movers: {direction} {asset} — "
                         f"rank #{alert.get('currentRank')} "
                         f"vel {alert.get('contribVelocity', 0):.4f} "
                         f"SM:{sm_dir} {sm_pct:.1f}%")

            # Layer 4: SL only, NO TP — let winners run via DSL trailing
            SL_ROE = 5.0
            result, err = cfg.create_position(wallet, asset, direction, leverage, margin, reason_str,
                                               tp_pct=None, sl_pct=SL_ROE)

            if err:
                cfg.output_error(SCRIPT, f"create_position failed: {err}",
                                  strategyId=sk, asset=asset, direction=direction)
                continue

            # Verify the ALO order filled (maker-only, may rest)
            filled, fill_err = cfg.verify_position_filled(wallet, asset, max_wait=45)
            if not filled:
                # Cancel the resting order — didn't get maker fill
                cfg.cancel_open_orders(wallet, asset)
                cfg.output({
                    "status": "alo_unfilled",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "direction": direction,
                    "reason": fill_err or "ALO maker order did not fill in 45s — cancelled",
                })
                continue

            # Record in shark state
            active_positions[asset] = {
                "direction": direction,
                "entry_price": current_price,
                "opened_at": cfg.now_iso(),
                "cascade_oi_at_entry": 0,  # Not cascade-sourced
                "target_liq_zone": 0,
                "pattern": "EMERGING_MOVER_IMMEDIATE",
                "triggers": alert.get("reasons", []),
                "margin": margin,
                "leverage": leverage,
                "source": "emerging-movers",
                "em_rank": alert.get("currentRank"),
                "em_velocity": alert.get("contribVelocity"),
                "em_traders": alert.get("traders"),
            }
            state["active_positions"] = active_positions
            state["updated_at"] = cfg.now_iso()
            cfg.atomic_write(state_path, state)

            # Update trade counter
            counter_path = os.path.join(sd, "trade-counter.json")
            counter = cfg.load_json(counter_path, {"date": time.strftime("%Y-%m-%d"), "entries": 0})
            today = time.strftime("%Y-%m-%d")
            if counter.get("date") != today:
                counter = {
                    "date": today,
                    "accountValueStart": budget,
                    "entries": 0,
                    "realizedPnl": 0,
                    "gate": "OPEN",
                    "gateReason": None,
                    "cooldownUntil": None,
                    "lastResults": counter.get("lastResults", []),
                    "maxEntriesPerDay": strat.get("maxEntriesPerDay", 6),
                    "maxConsecutiveLosses": 3,
                    "cooldownMinutes": 45,
                }
            counter["entries"] = counter.get("entries", 0) + 1
            cfg.atomic_write(counter_path, counter)

            # NO DSL on entry — native TP/SL handles initial risk
            # DSL trailing activates only after 10% ROE (risk guardian promotes)

            entered = True

            cfg.output({
                "status": "position_opened",
                "script": SCRIPT,
                "strategyId": sk,
                "asset": asset,
                "direction": direction,
                "entry_price": current_price,
                "margin": margin,
                "leverage": leverage,
                "source": "emerging-movers",
                "em_rank": alert.get("currentRank"),
                "em_velocity": alert.get("contribVelocity"),
                "em_traders": alert.get("traders"),
                "reasons": alert.get("reasons", []),
                "notify": True,
            })

        if not entered and immediate:
            cfg.output({
                "status": "signals_filtered",
                "script": SCRIPT,
                "strategyId": sk,
                "immediate_count": len(immediate),
                "all_filtered": True,
            })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
