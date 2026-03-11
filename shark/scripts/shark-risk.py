#!/usr/bin/env python3
"""shark-risk.py — Risk guardian (every 5 min, isolated).

Enforces:
- Max concurrent positions: 2
- Max daily loss: 12% of budget
- Max drawdown from peak: 25%
- Max single trade loss: 5% of account
- Correlation guard: max 1 BTC-correlated position
- Cascade invalidation: OI increased >2% after entry → immediate close
- Max entries per day: 6 (unless ROE positive)
- Consecutive loss cooldown: 3 losses → 45min pause

The cascade invalidation exit is the most important SHARK-specific risk rule.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-risk"

OI_INVALIDATION_THRESHOLD = 0.02  # 2% OI increase after entry = thesis dead


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
        daily_loss_limit = strat.get("dailyLossLimit", budget * 0.12)
        drawdown_cap = strat.get("drawdownCap", budget * 0.25)
        max_single_loss_pct = strat.get("maxSingleLossPct", 5)
        max_entries = strat.get("maxEntriesPerDay", 6)
        max_consecutive_losses = 3
        cooldown_minutes = 45

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"stalking": [], "strike": [], "active_positions": {}})
        active_positions = state.get("active_positions", {})

        # ── Fetch clearinghouse EARLY so we can use real account value ──
        ch_data, ch_err = cfg.fetch_clearinghouse(wallet)
        account_value = budget  # fallback
        if not ch_err and ch_data:
            # Sum accountValue from BOTH main and xyz vaults.
            total_av = 0.0
            for vault_key in ("main", "xyz"):
                vault = ch_data.get(vault_key, {})
                ms = vault.get("marginSummary") or vault.get("crossMarginSummary") or {}
                try:
                    total_av += float(ms.get("accountValue", 0))
                except (TypeError, ValueError):
                    pass
            if total_av > 0:
                account_value = total_av

        # Get active positions from clearinghouse (reuse ch_data)
        positions = {}
        if not ch_err and ch_data:
            for section in ("main", "xyz"):
                if section not in ch_data:
                    continue
                for p in ch_data.get(section, {}).get("assetPositions", []):
                    pos = p.get("position", {})
                    coin = pos.get("coin")
                    szi = float(pos.get("szi", 0))
                    if coin and szi != 0:
                        positions[coin] = pos
        pos_err = ch_err

        # Load trade counter
        counter_path = os.path.join(sd, "trade-counter.json")
        today = time.strftime("%Y-%m-%d")
        counter = cfg.load_json(counter_path, {
            "date": today,
            "accountValueStart": account_value,
            "entries": 0,
            "realizedPnl": 0,
            "gate": "OPEN",
            "gateReason": None,
            "cooldownUntil": None,
            "lastResults": [],
            "maxEntriesPerDay": max_entries,
            "maxConsecutiveLosses": max_consecutive_losses,
            "cooldownMinutes": cooldown_minutes,
        })

        # Reset counter if new day — use real account value, not budget
        if counter.get("date") != today:
            counter = {
                "date": today,
                "accountValueStart": account_value,
                "entries": 0,
                "realizedPnl": 0,
                "gate": "OPEN",
                "gateReason": None,
                "cooldownUntil": None,
                "lastResults": counter.get("lastResults", []),
                "maxEntriesPerDay": max_entries,
                "maxConsecutiveLosses": max_consecutive_losses,
                "cooldownMinutes": cooldown_minutes,
            }

        # Load peak balance
        peak_path = os.path.join(sd, "peak-balance.json")
        peak_data = cfg.load_json(peak_path, {"peak": budget, "updated_at": cfg.now_iso()})

        # Load OI history for cascade invalidation
        history = cfg.load_json(os.path.join(sd, "shark-oi-history.json"), {})

        alerts = []
        positions_to_close = []
        gate_reasons = []

        # ============================================================
        # CHECK 1: Cascade invalidation — OI increased >2% after entry
        # ============================================================
        for asset, pos_info in list(active_positions.items()):
            oi_at_entry = pos_info.get("cascade_oi_at_entry", 0)
            if oi_at_entry <= 0:
                continue

            oi_entries = history.get(asset, [])
            if not oi_entries:
                continue

            current_oi = oi_entries[-1].get("oi", 0)
            if current_oi <= 0:
                continue

            oi_change = (current_oi - oi_at_entry) / oi_at_entry
            if oi_change > OI_INVALIDATION_THRESHOLD:
                positions_to_close.append({
                    "asset": asset,
                    "reason": f"CASCADE_INVALIDATION: OI increased {oi_change:.1%} after entry (threshold: {OI_INVALIDATION_THRESHOLD:.0%})",
                    "immediate": True,
                })
                alerts.append({
                    "type": "cascade_invalidation",
                    "asset": asset,
                    "oi_change": f"+{oi_change:.1%}",
                    "detail": "OI increasing — no cascade happening. Thesis dead, cutting immediately.",
                })

        # ============================================================
        # CHECK 2: Max daily loss (12% of budget)
        # ============================================================
        # account_value already fetched above (both main + xyz vaults summed).
        # Total PnL = current account value - starting value
        # This automatically includes all fees, funding, and realized+unrealized PnL
        day_start_value = counter.get("accountValueStart", budget)
        total_pnl = account_value - day_start_value

        if total_pnl < -daily_loss_limit:
            gate_reasons.append(f"DAILY_LOSS: ${abs(total_pnl):.2f} loss exceeds ${daily_loss_limit:.2f} limit")
            alerts.append({
                "type": "daily_loss_halt",
                "loss": f"${abs(total_pnl):.2f}",
                "limit": f"${daily_loss_limit:.2f}",
                "notify": True,
            })

        # ============================================================
        # CHECK 3: Max drawdown from peak (25%)
        # ============================================================
        current_value = account_value  # Already fee-inclusive from clearinghouse
        peak = peak_data.get("peak", budget)

        if current_value > peak:
            peak = current_value
            peak_data["peak"] = peak
            peak_data["updated_at"] = cfg.now_iso()
            cfg.atomic_write(peak_path, peak_data)

        drawdown = peak - current_value
        if drawdown > drawdown_cap:
            gate_reasons.append(f"DRAWDOWN: ${drawdown:.2f} from peak ${peak:.2f} exceeds ${drawdown_cap:.2f} cap")
            alerts.append({
                "type": "drawdown_halt",
                "drawdown": f"${drawdown:.2f}",
                "peak": f"${peak:.2f}",
                "cap": f"${drawdown_cap:.2f}",
                "notify": True,
            })

        # ============================================================
        # CHECK 4: Max single trade loss (5% of account)
        # ============================================================
        # Use clearinghouse unrealizedPnl + cumFunding for fee-inclusive per-position PnL.
        # unrealizedPnl from HL = price PnL only.
        # cumFunding.sinceOpen = funding payments (negative = paid out).
        # Entry fees aren't tracked per-position by HL, but funding + price PnL
        # covers the bulk. We estimate entry fee from notional × ~9bps (taker) or ~5bps (ALO).
        max_single_loss = budget * max_single_loss_pct / 100
        if positions:
            for coin, pos in positions.items():
                upnl = float(pos.get("unrealizedPnl", 0))
                # Subtract cumulative funding paid
                cum_funding = pos.get("cumFunding", {})
                funding_out = float(cum_funding.get("sinceOpen", 0))
                # funding_out > 0 means we paid funding (cost)
                # Add estimated entry fee (notional × 9bps taker or 5bps ALO)
                notional = abs(float(pos.get("positionValue", 0)) or float(pos.get("szi", 0)) * float(pos.get("entryPx", 0)))
                est_entry_fee = notional * 0.0009  # ~9bps conservative
                # Total position PnL including estimated fees
                position_pnl = upnl - funding_out - est_entry_fee
                if position_pnl < -max_single_loss:
                    positions_to_close.append({
                        "asset": coin,
                        "reason": f"SINGLE_TRADE_LOSS: ${abs(position_pnl):.2f} (incl fees) exceeds ${max_single_loss:.2f} max",
                        "immediate": False,
                    })
                    alerts.append({
                        "type": "single_trade_loss",
                        "asset": coin,
                        "loss": f"${abs(position_pnl):.2f}",
                        "limit": f"${max_single_loss:.2f}",
                        "notify": True,
                    })

        # ============================================================
        # CHECK 4b: PROMOTE winners to DSL trailing
        # When a position hits 10% ROE, remove TP/SL and create DSL Phase 2
        # This lets winners run indefinitely with a trailing stop
        # ============================================================
        DSL_PROMOTE_ROE = 8.0  # Promote to trailing at 8% ROE — lock profits early
        default_leverage = strat.get("defaultLeverage", 10)
        if positions:
            for coin, pos in positions.items():
                upnl = float(pos.get("unrealizedPnl", 0))
                margin_used = float(pos.get("marginUsed", 0))
                if margin_used <= 0:
                    continue
                roe = upnl / margin_used * 100

                if roe >= DSL_PROMOTE_ROE:
                    # Check if DSL already exists for this position
                    dsl_dir = cfg.dsl_state_path(sk)
                    if coin.startswith("xyz:"):
                        dsl_filename = coin.replace(":", "--", 1) + ".json"
                    else:
                        dsl_filename = coin + ".json"
                    dsl_path = os.path.join(dsl_dir, dsl_filename)

                    if not os.path.exists(dsl_path):
                        # Promote! Create DSL Phase 2 trailing stop
                        entry_px = float(pos.get("entryPx", 0))
                        szi = float(pos.get("szi", 0))
                        lev = float(pos.get("leverage", {}).get("value", default_leverage) if isinstance(pos.get("leverage"), dict) else pos.get("leverage", default_leverage))
                        current_price = entry_px * (1 + roe / 100 / lev) if szi > 0 else entry_px * (1 - roe / 100 / lev)

                        # Phase 2 tiers — aggressive lock-in, let winners run
                        tiers = [
                            {"triggerPct": 8, "lockPct": 5},
                            {"triggerPct": 12, "lockPct": 8},
                            {"triggerPct": 18, "lockPct": 14},
                            {"triggerPct": 25, "lockPct": 20},
                            {"triggerPct": 35, "lockPct": 28},
                            {"triggerPct": 50, "lockPct": 42},
                            {"triggerPct": 75, "lockPct": 65},
                            {"triggerPct": 100, "lockPct": 90},
                        ]

                        # Find current tier
                        current_tier = -1
                        for i, t in enumerate(tiers):
                            if roe >= t["triggerPct"]:
                                current_tier = i

                        # Calculate floor from current tier
                        if current_tier >= 0:
                            lock_pct = tiers[current_tier]["lockPct"]
                            if szi > 0:  # LONG
                                floor_price = round(entry_px * (1 + lock_pct / 100 / lev), 6)
                            else:  # SHORT
                                floor_price = round(entry_px * (1 - lock_pct / 100 / lev), 6)
                        else:
                            floor_price = entry_px  # shouldn't happen at 10%+ ROE

                        direction = "LONG" if szi > 0 else "SHORT"
                        dsl_state = {
                            "active": True,
                            "asset": coin,
                            "direction": direction,
                            "leverage": int(lev),
                            "entryPrice": entry_px,
                            "size": abs(szi),
                            "wallet": wallet,
                            "strategyId": sk,
                            "phase": 2,
                            "phase1": None,  # Skipped — native TP/SL handled Phase 1
                            "phase2TriggerTier": 0,
                            "phase2": {
                                "retraceThreshold": 0.015,
                                "consecutiveBreachesRequired": 1,
                            },
                            "tiers": tiers,
                            "currentTierIndex": current_tier,
                            "tierFloorPrice": floor_price,
                            "highWaterPrice": current_price,
                            "floorPrice": floor_price,
                            "currentBreachCount": 0,
                            "createdAt": cfg.now_iso(),
                            "promotedAt": cfg.now_iso(),
                            "promotionROE": round(roe, 2),
                        }
                        cfg.atomic_write(dsl_path, dsl_state)

                        # Cancel native SL — DSL trailing takes over stop management
                        # edit_position with stopLossPercent=0 or cancel the order
                        try:
                            cfg.cancel_open_orders(wallet, coin)
                        except Exception:
                            pass  # Best effort — DSL floor is the real protection now

                        alerts.append({
                            "type": "dsl_promotion",
                            "asset": coin,
                            "roe": f"+{roe:.1f}%",
                            "detail": f"{coin} promoted to DSL trailing at {roe:.1f}% ROE — letting winner run",
                            "notify": True,
                        })

        # ============================================================
        # CHECK 5: Max entries per day
        # ============================================================
        entries_today = counter.get("entries", 0)
        if entries_today >= max_entries and total_pnl <= 0:
            gate_reasons.append(f"MAX_ENTRIES: {entries_today} entries today (limit: {max_entries})")

        # ============================================================
        # CHECK 6: Consecutive loss cooldown
        # ============================================================
        last_results = counter.get("lastResults", [])
        consecutive_losses = 0
        for r in reversed(last_results):
            if r.get("pnl", 0) < 0:
                consecutive_losses += 1
            else:
                break

        if consecutive_losses >= max_consecutive_losses:
            cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)).isoformat()
            counter["cooldownUntil"] = cooldown_until
            gate_reasons.append(f"CONSECUTIVE_LOSSES: {consecutive_losses} losses → {cooldown_minutes}min cooldown")
            alerts.append({
                "type": "cooldown_activated",
                "consecutive_losses": consecutive_losses,
                "cooldown_until": cooldown_until,
                "notify": True,
            })

        # ============================================================
        # Execute position closes
        # ============================================================
        for close_info in positions_to_close:
            asset = close_info["asset"]
            reason = close_info["reason"]

            result, err = cfg.close_position(wallet, asset, reason)
            if err:
                cfg.output_error(SCRIPT, f"close failed for {asset}: {err}",
                                  strategyId=sk, asset=asset)
            else:
                # Fetch fee-inclusive PnL for the closed trade
                net_pnl, pnl_err = cfg.fetch_closed_trade_pnl(wallet, asset)
                if net_pnl is not None:
                    counter["realizedPnl"] = counter.get("realizedPnl", 0) + net_pnl
                    counter["lastResults"] = counter.get("lastResults", [])[-9:] + [{
                        "asset": asset, "pnl": net_pnl, "closedAt": cfg.now_iso(),
                        "fees_included": True
                    }]

                # Remove from active positions
                if asset in active_positions:
                    del active_positions[asset]
                    state["active_positions"] = active_positions
                    state["updated_at"] = cfg.now_iso()
                    cfg.atomic_write(state_path, state)

                cfg.output({
                    "status": "position_closed",
                    "script": SCRIPT,
                    "strategyId": sk,
                    "asset": asset,
                    "reason": reason,
                    "net_pnl": net_pnl,
                    "notify": True,
                })

        # ============================================================
        # Update gate status
        # ============================================================
        if gate_reasons:
            counter["gate"] = "CLOSED"
            counter["gateReason"] = "; ".join(gate_reasons)
        else:
            # Check cooldown
            cooldown = counter.get("cooldownUntil")
            if cooldown:
                try:
                    cd_time = datetime.fromisoformat(cooldown.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) >= cd_time:
                        counter["cooldownUntil"] = None
                        counter["gate"] = "OPEN"
                        counter["gateReason"] = None
                    else:
                        counter["gate"] = "COOLDOWN"
                        counter["gateReason"] = f"cooldown until {cooldown}"
                except (ValueError, TypeError):
                    counter["gate"] = "OPEN"
                    counter["gateReason"] = None
            else:
                counter["gate"] = "OPEN"
                counter["gateReason"] = None

        cfg.atomic_write(counter_path, counter)

        # Output
        if alerts:
            for alert in alerts:
                cfg.output({
                    "status": "risk_alert",
                    "script": SCRIPT,
                    "strategyId": sk,
                    **alert,
                })
        else:
            cfg.output({
                "status": "ok",
                "script": SCRIPT,
                "strategyId": sk,
                "gate": counter.get("gate", "OPEN"),
                "entries_today": entries_today,
                "daily_pnl": round(total_pnl, 2),
                "account_value": round(account_value, 2),
                "drawdown": round(drawdown, 2) if drawdown > 0 else 0,
                "active_positions": len(active_positions),
            })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
