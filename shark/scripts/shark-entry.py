#!/usr/bin/env python3
"""shark-entry.py — Cascade entry logic (every 2 min, main session).

Phase 3 of the SHARK signal pipeline.
Only processes STRIKE assets. Detects cascade triggers and opens positions.

Cascade triggers (need >= 2 firing):
- OI drops >3% in one 5min interval (HIGH)
- Price breaks into liquidation zone (HIGH)
- Funding rate spiking in cascade direction (MEDIUM)
- Volume explosion — 5min volume > 3x average (MEDIUM)
- SM already positioned in cascade direction (HIGH)

Anti-patterns enforced:
- NEVER enter if OI is INCREASING toward zone
- NEVER chase a cascade already started (OI dropped 10%+, price moved 5%+)
- Max 1 BTC-correlated cascade trade
- Max 2 concurrent positions
- Respect risk guardian gate

Runs on MAIN session. Output: position opened or heartbeat.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-entry"

# Trigger thresholds
# PREDATOR MODE — more sensitive triggers
# Layer 3: Tighter filters — quality over quantity
OI_DROP_THRESHOLD = 0.03      # Back to 3% — 2% produced noise
OI_CHASE_THRESHOLD = 0.10     # 10% already dropped = too late
PRICE_CHASE_THRESHOLD = 0.05  # 5% through zone = too late (scaled by leverage at runtime)
VOLUME_EXPLOSION = 3.0        # Back to 3x — 2.5x was noise
FUNDING_SPIKE_THRESHOLD = 2.0 # Back to 2.0x
MIN_TRIGGERS = 2              # Need 2+ triggers
OI_INCREASING_BLOCK = 0.005   # Allow tiny OI increase (0.5%)


def check_risk_gate(strat: dict) -> tuple[bool, str | None]:
    """Check if risk guardian gate is open. Returns (open, reason)."""
    sk = strat.get("strategyId")
    sd = cfg.state_dir(sk)
    counter_path = os.path.join(sd, "trade-counter.json")
    counter = cfg.load_json(counter_path)
    if not counter:
        return True, None

    gate = counter.get("gate", "OPEN")
    if gate != "OPEN":
        return False, counter.get("gateReason", gate)

    # Check cooldown
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


def check_anti_patterns(asset: str, direction: str, oi_entries: list[dict],
                        current_price: float, zone_price: float,
                        active_positions: dict, leverage: int = 10) -> tuple[bool, str | None]:
    """Check anti-patterns. Returns (pass, rejection_reason)."""

    # Anti-pattern 1: OI INCREASING toward zone
    if len(oi_entries) >= 2:
        recent_oi = oi_entries[-1].get("oi", 0)
        prev_oi = oi_entries[-2].get("oi", 0)
        if prev_oi > 0 and recent_oi > prev_oi:
            oi_change = (recent_oi - prev_oi) / prev_oi
            if oi_change > OI_INCREASING_BLOCK:
                return False, f"OI increasing (+{oi_change:.3%}) — no liquidation imminent"

    # Anti-pattern 2: Cascade already started (chasing)
    if len(oi_entries) >= 6:
        lookback_oi = oi_entries[-6].get("oi", 0)  # ~30 min ago
        recent_oi = oi_entries[-1].get("oi", 0)
        if lookback_oi > 0:
            oi_drop = (lookback_oi - recent_oi) / lookback_oi
            if oi_drop > OI_CHASE_THRESHOLD:
                return False, f"cascade already underway (OI dropped {oi_drop:.1%}) — too late"

    # Check price chase — scale with leverage
    # At 10x: 0.5% price through zone = 5% ROE already happened = too late
    adjusted_chase = PRICE_CHASE_THRESHOLD / max(1, leverage)  # 0.05/10 = 0.005
    if zone_price > 0 and current_price > 0:
        price_through = abs(current_price - zone_price) / zone_price
        if direction == "SHORT" and current_price < zone_price:
            # Price already below liq zone for longs
            if price_through > adjusted_chase:
                return False, f"price already {price_through:.1%} through zone ({price_through * leverage:.1%} ROE) — cascade mostly done"
        elif direction == "LONG" and current_price > zone_price:
            # Price already above liq zone for shorts
            if price_through > adjusted_chase:
                return False, f"price already {price_through:.1%} through zone ({price_through * leverage:.1%} ROE) — cascade mostly done"

    # Anti-pattern 4: Max 1 BTC-correlated position
    if cfg.is_btc_correlated(asset):
        for pos_coin in active_positions:
            if cfg.is_btc_correlated(pos_coin):
                return False, f"already have BTC-correlated position ({pos_coin})"

    return True, None


def detect_triggers(asset: str, direction: str, oi_entries: list[dict],
                    current_price: float, zone_price: float,
                    sm_markets: list[dict], candles: list[dict],
                    funding_history: list[dict]) -> list[dict]:
    """Detect cascade triggers. Returns list of firing triggers."""
    triggers = []

    # Trigger 1: OI drops >3% in one 5min interval
    if len(oi_entries) >= 2:
        recent_oi = oi_entries[-1].get("oi", 0)
        prev_oi = oi_entries[-2].get("oi", 0)
        if prev_oi > 0:
            oi_change = (recent_oi - prev_oi) / prev_oi
            if oi_change < -OI_DROP_THRESHOLD:
                triggers.append({
                    "trigger": "oi_drop",
                    "confidence": "HIGH",
                    "value": f"{oi_change:.2%}",
                    "detail": "OI dropping — liquidations cascading",
                })

    # Trigger 2: Price breaks into liquidation zone
    if zone_price > 0 and current_price > 0:
        if direction == "SHORT" and current_price <= zone_price:
            triggers.append({
                "trigger": "zone_break",
                "confidence": "HIGH",
                "value": f"price {current_price} <= zone {zone_price}",
                "detail": "Price entered long liquidation zone",
            })
        elif direction == "LONG" and current_price >= zone_price:
            triggers.append({
                "trigger": "zone_break",
                "confidence": "HIGH",
                "value": f"price {current_price} >= zone {zone_price}",
                "detail": "Price entered short liquidation zone",
            })

    # Trigger 3: Funding rate spiking in cascade direction
    if funding_history and len(funding_history) >= 4:
        recent_funding = [abs(float(f.get("fundingRate", 0))) for f in funding_history[-3:]]
        older_funding = [abs(float(f.get("fundingRate", 0))) for f in funding_history[-12:-3]]
        if older_funding:
            avg_recent = sum(recent_funding) / len(recent_funding)
            avg_older = sum(older_funding) / len(older_funding)
            if avg_older > 0 and avg_recent / avg_older >= FUNDING_SPIKE_THRESHOLD:
                triggers.append({
                    "trigger": "funding_spike",
                    "confidence": "MEDIUM",
                    "value": f"{avg_recent:.6f} vs avg {avg_older:.6f}",
                    "detail": "Funding rate spiking — pressure building",
                })

    # Trigger 4: Volume explosion
    if candles and len(candles) >= 4:
        recent_vol = float(candles[-1].get("v", 0))
        avg_vol = sum(float(c.get("v", 0)) for c in candles[-12:-1]) / max(1, min(11, len(candles) - 1))
        if avg_vol > 0 and recent_vol / avg_vol >= VOLUME_EXPLOSION:
            triggers.append({
                "trigger": "volume_explosion",
                "confidence": "MEDIUM",
                "value": f"{recent_vol / avg_vol:.1f}x average",
                "detail": "Volume explosion — forced orders hitting",
            })

    # Trigger 5: SM already positioned in cascade direction
    clean_asset = asset.replace("xyz:", "").upper()
    for mkt in sm_markets:
        if mkt.get("token", "").upper() == clean_asset:
            sm_dir = mkt.get("direction", "").upper()
            if ((direction == "SHORT" and sm_dir == "SHORT") or
                (direction == "LONG" and sm_dir == "LONG")):
                pct = mkt.get("pct_of_top_traders_gain", 0)
                if pct > 3:  # Meaningful SM concentration
                    triggers.append({
                        "trigger": "sm_positioned",
                        "confidence": "HIGH",
                        "value": f"{pct:.1f}% SM concentration {sm_dir}",
                        "detail": "Smart money already positioned for cascade",
                    })
            break

    return triggers


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
        budget = strat.get("budget", 5000)
        margin_pct = strat.get("marginPct", 0.18)
        default_leverage = strat.get("defaultLeverage", 8)
        max_slots = strat.get("maxSlots", 2)

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"stalking": [], "strike": [], "active_positions": {}})
        strike_assets = state.get("strike", [])
        active_positions = state.get("active_positions", {})

        # Reconcile state with on-chain positions BEFORE checking capacity
        # This prevents phantom positions from blocking new entries
        on_chain, pos_err = cfg.get_active_positions(wallet)
        if not pos_err:
            on_chain_coins = set(on_chain.keys())
            phantoms = [a for a in active_positions if a not in on_chain_coins]
            if phantoms:
                for p in phantoms:
                    del active_positions[p]
                state["active_positions"] = active_positions
                state["updated_at"] = cfg.now_iso()
                cfg.atomic_write(state_path, state)

        if not strike_assets:
            cfg.heartbeat(SCRIPT)
            return

        # Check capacity
        if len(active_positions) >= max_slots:
            cfg.output({"status": "at_capacity", "script": SCRIPT, "strategyId": sk,
                        "active": len(active_positions), "maxSlots": max_slots})
            continue

        # Check risk gate
        gate_open, gate_reason = check_risk_gate(strat)
        if not gate_open:
            cfg.output({"status": "gate_closed", "script": SCRIPT, "strategyId": sk,
                        "reason": gate_reason})
            continue

        # Load liq map and OI history
        liq_map = cfg.load_json(os.path.join(sd, "shark-liq-map.json"), {})
        history = cfg.load_json(os.path.join(sd, "shark-oi-history.json"), {})

        # Fetch SM markets (one call for all assets)
        sm_markets, _ = cfg.fetch_sm_markets(50)

        entered = False

        for asset in strike_assets:
            if entered:
                break  # One entry per cycle

            if asset in active_positions:
                continue

            if len(active_positions) >= max_slots:
                break

            # Ban xyz assets — cost us $339 in losses, unreliable signals
            if asset.startswith("xyz:"):
                continue

            liq_entry = liq_map.get(asset, {})
            direction = liq_entry.get("stalking_direction")
            if not direction:
                continue

            current_price = liq_entry.get("current_price", 0)
            if current_price <= 0:
                continue

            # Get zone price
            if direction == "SHORT":
                zone = liq_entry.get("long_liq_zone", {})
            else:
                zone = liq_entry.get("short_liq_zone", {})

            zone_price = zone.get("price", 0)
            if zone_price <= 0:
                continue

            oi_entries = history.get(asset, [])

            # Anti-pattern checks
            ap_pass, ap_reason = check_anti_patterns(
                asset, direction, oi_entries, current_price, zone_price, active_positions, leverage=default_leverage
            )
            if not ap_pass:
                cfg.output({
                    "status": "anti_pattern_blocked",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "reason": ap_reason,
                })
                continue

            # Fetch detailed data for trigger detection
            dex = "xyz" if asset.startswith("xyz:") else ""
            asset_data, err = cfg.fetch_asset_data(
                asset, candle_intervals=["5m"], include_order_book=False,
                include_funding=True, dex=dex
            )

            candles = []
            funding_history = []
            if not err and asset_data:
                candles_data = asset_data.get("candles", {})
                candles = candles_data.get("5m", []) if isinstance(candles_data, dict) else []
                funding_history = asset_data.get("funding_history", [])

            # Detect triggers
            triggers = detect_triggers(
                asset, direction, oi_entries, current_price, zone_price,
                sm_markets, candles, funding_history
            )

            if len(triggers) < MIN_TRIGGERS:
                cfg.output({
                    "status": "insufficient_triggers",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "direction": direction,
                    "triggers_found": len(triggers),
                    "triggers_needed": MIN_TRIGGERS,
                    "triggers": triggers,
                })
                continue

            # ============================================================
            # ENTRY DECISION — All checks passed, >= 2 triggers firing
            # ============================================================

            margin = round(budget * margin_pct, 2)
            leverage = default_leverage

            # Layer 1: Smart money alignment check
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
                    "detail": f"SM is {sm_dir} ({sm_pct:.1f}% gain share) — refusing {direction}",
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

            # Verify we have enough in the wallet
            positions, pos_err = cfg.get_active_positions(wallet)
            if pos_err:
                cfg.output_error(SCRIPT, f"clearinghouse check failed: {pos_err}", strategyId=sk)
                continue

            # Check if position already exists for this coin
            if asset in positions:
                cfg.output({"status": "position_exists", "script": SCRIPT,
                            "strategyId": sk, "asset": asset})
                continue

            # Layer 4: SL only, NO TP — let winners run via DSL trailing
            SL_ROE = 5.0
            reason = f"SHARK cascade: {direction} {asset} — {len(triggers)} triggers, SM:{sm_dir} {sm_pct:.1f}%"
            result, err = cfg.create_position(wallet, asset, direction, leverage, margin, reason,
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

            # Record in state
            cascade_oi = oi_entries[-1].get("oi", 0) if oi_entries else 0
            active_positions[asset] = {
                "direction": direction,
                "entry_price": current_price,
                "opened_at": cfg.now_iso(),
                "cascade_oi_at_entry": cascade_oi,
                "target_liq_zone": zone_price,
                "pattern": "LONG_LIQUIDATION_CASCADE" if direction == "SHORT" else "SHORT_LIQUIDATION_CASCADE",
                "triggers": triggers,
                "margin": margin,
                "leverage": leverage,
            }
            state["active_positions"] = active_positions
            state["updated_at"] = cfg.now_iso()
            cfg.atomic_write(state_path, state)

            # Update trade counter
            counter_path = os.path.join(sd, "trade-counter.json")
            counter = cfg.load_json(counter_path, {
                "date": time.strftime("%Y-%m-%d"),
                "entries": 0,
                "realizedPnl": 0,
                "gate": "OPEN",
            })
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
                    "lastResults": [],
                    "maxEntriesPerDay": strat.get("maxEntriesPerDay", 6),
                    "maxConsecutiveLosses": 3,
                    "cooldownMinutes": 45,
                }
            counter["entries"] = counter.get("entries", 0) + 1
            cfg.atomic_write(counter_path, counter)

            # NO DSL on entry — native TP/SL handles initial risk
            # DSL trailing activates only after 10% ROE (risk guardian promotes)

            entered = True

            # Output entry notification
            cfg.output({
                "status": "position_opened",
                "script": SCRIPT,
                "strategyId": sk,
                "asset": asset,
                "direction": direction,
                "entry_price": current_price,
                "margin": margin,
                "leverage": leverage,
                "zone_price": zone_price,
                "triggers": triggers,
                "pattern": active_positions[asset]["pattern"],
                "notify": True,
            })

        if not entered:
            cfg.heartbeat(SCRIPT)


def _create_dsl_state(strat: dict, asset: str, direction: str,
                       entry_price: float, leverage: int, margin: float,
                       wallet: str) -> None:
    """Create DSL v5.3.1 state file for the new position."""
    strategy_id = strat.get("strategyId")
    dsl_dir = cfg.dsl_state_path(strategy_id)

    # Asset filename (xyz:SILVER → xyz--SILVER)
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

    # Calculate absolute floor
    # PREDATOR DSL: wider retrace, longer timeouts — let winners develop
    retrace = 0.035  # Widened from 0.025 — more room to breathe
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


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
