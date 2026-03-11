#!/usr/bin/env python3
"""shark-conviction.py — SM Conviction Trader (every 15 min, main session).

Replaces cascade detection + movers with a single flow:
1. SCAN: leaderboard_get_markets → where is SM concentrated?
2. VALIDATE: leaderboard_get_top → are top traders holding this direction?
3. CONFIRM: 15m candles → is price moving in SM direction?
4. STRUCTURE: funding + OI → is the move structurally supported?

When ALL 4 align on an asset → enter. ALO maker. SL -5% ROE. No TP.
DSL trailing kicks in at 8% ROE (managed by risk guardian).

Runs on main session for position opening + notifications.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-conviction"

# ── Thresholds ──
MIN_SM_PCT = 5.0              # Minimum SM gain share for an asset to be interesting
MIN_SM_TRADERS = 30           # Minimum SM traders for consensus
MIN_TOP5_ALIGNMENT = 2        # At least 2 of top 5 traders profiting in same direction
FUNDING_CONFIRM_THRESHOLD = 0.00005  # Funding rate that confirms directional pressure
OI_GROWTH_MIN = 0.005         # 0.5% OI growth = new positions entering (conviction growing)


def scan_sm_markets() -> list[dict]:
    """Step 1: Find assets where SM is concentrated."""
    markets, err = cfg.fetch_sm_markets(100)
    if err or not markets:
        return []

    # Group by token, pick dominant direction
    by_token: dict[str, dict] = {}
    for m in markets:
        token = (m.get("token") or "").upper()
        dex = m.get("dex", "")
        if dex == "xyz":
            continue  # Skip xyz assets
        if not token:
            continue

        pct = float(m.get("pct_of_top_traders_gain", 0))
        direction = (m.get("direction") or "").upper()
        traders = int(m.get("trader_count", 0))
        price_chg = float(m.get("token_price_change_pct_4h", 0))

        key = token
        if key not in by_token or pct > by_token[key].get("pct", 0):
            by_token[key] = {
                "token": token,
                "direction": direction,
                "pct": pct,
                "traders": traders,
                "price_chg_4h": price_chg,
            }

    # Filter: need meaningful SM concentration + trader count
    candidates = []
    for token, data in by_token.items():
        if data["pct"] >= MIN_SM_PCT and data["traders"] >= MIN_SM_TRADERS:
            candidates.append(data)

    # Sort by SM concentration (highest first)
    candidates.sort(key=lambda x: x["pct"], reverse=True)
    return candidates


def validate_top_traders(asset: str, direction: str) -> tuple[bool, int, str]:
    """Step 2: Check if top Hyperfeed traders are aligned with this direction.

    Returns (validated, count_aligned, detail).
    """
    data, err = cfg.mcporter_call("leaderboard_get_top", {"limit": 5})
    if err or not data:
        return False, 0, f"leaderboard_get_top failed: {err}"

    leaderboard = data.get("leaderboard", {}).get("data", [])
    if not leaderboard:
        return False, 0, "no leaderboard data"

    aligned = 0
    total_delta = 0.0
    for trader in leaderboard:
        top_markets = trader.get("top_markets", [])
        # Check if this asset is in their top markets
        clean_asset = asset.replace("xyz:", "").upper()
        if clean_asset in [m.upper() for m in top_markets]:
            # They're profiting on this asset — fetch their position direction
            trader_id = trader.get("trader_id", "")
            if trader_id:
                pos_data, pos_err = cfg.mcporter_call(
                    "leaderboard_get_trader_positions", {"trader_id": trader_id}
                )
                if not pos_err and pos_data:
                    positions = pos_data.get("positions", {}).get("positions", [])
                    for p in positions:
                        if (p.get("market", "")).upper() == clean_asset:
                            pos_dir = (p.get("direction", "")).upper()
                            if pos_dir == direction.upper():
                                aligned += 1
                                total_delta += float(p.get("delta_pnl", 0))

    validated = aligned >= MIN_TOP5_ALIGNMENT
    detail = f"{aligned}/5 top traders aligned {direction} on {asset}, delta ${total_delta:,.0f}"
    return validated, aligned, detail


def confirm_candles(asset: str, direction: str) -> tuple[bool, str]:
    """Step 3: 15m candle confirmation — is price trending in our direction?"""
    return cfg.check_candle_confirmation(asset, direction)


def check_structure(asset: str, direction: str) -> tuple[bool, str]:
    """Step 4: Check funding + OI for structural confirmation.

    SHORT conviction: funding positive (longs paying shorts) + OI growing
    LONG conviction: funding negative (shorts paying longs) + OI growing
    """
    # Get OI history from our tracker
    strategies = cfg.load_all_strategies()
    if not strategies:
        return True, "no strategy — skip structure check"

    sk = strategies[0].get("strategyId", "")
    sd = cfg.state_dir(sk)
    history = cfg.load_json(os.path.join(sd, "shark-oi-history.json"), {})

    entries = history.get(asset, [])
    if len(entries) < 6:
        return True, f"insufficient OI history ({len(entries)} snapshots)"

    # Funding check
    latest_funding = entries[-1].get("funding", 0)
    funding_confirms = False
    if direction.upper() == "SHORT" and latest_funding > FUNDING_CONFIRM_THRESHOLD:
        funding_confirms = True  # Longs paying shorts = pressure on longs
    elif direction.upper() == "LONG" and latest_funding < -FUNDING_CONFIRM_THRESHOLD:
        funding_confirms = True  # Shorts paying longs = pressure on shorts

    # OI growth check (new positions entering = conviction growing)
    recent_oi = entries[-1].get("oi", 0)
    older_oi = entries[-6].get("oi", 0)  # ~30 min ago
    oi_change = (recent_oi - older_oi) / older_oi if older_oi > 0 else 0
    oi_growing = oi_change > OI_GROWTH_MIN

    # Need at least one structural confirmation
    if funding_confirms or oi_growing:
        parts = []
        if funding_confirms:
            parts.append(f"funding {latest_funding:.6f} confirms {direction}")
        if oi_growing:
            parts.append(f"OI +{oi_change:.2%} (new positions entering)")
        return True, " + ".join(parts)

    return False, f"no structural confirmation: funding {latest_funding:.6f}, OI change {oi_change:+.2%}"


def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)
        return

    for strat in strategies:
        sk = strat.get("strategyId")
        wallet = strat.get("wallet")
        if not sk or not wallet:
            continue

        sd = cfg.state_dir(sk)
        budget = strat.get("budget", 5000)
        margin_pct = strat.get("marginPct", 0.25)
        default_leverage = strat.get("defaultLeverage", 10)
        max_slots = strat.get("maxSlots", 2)

        # Load state
        state_path = os.path.join(sd, "shark-state.json")
        state = cfg.load_json(state_path, {"stalking": [], "strike": [], "active_positions": {}})
        active_positions = state.get("active_positions", {})

        # Reconcile state with on-chain
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

        # Check capacity
        if len(active_positions) >= max_slots:
            cfg.output({"status": "at_capacity", "script": SCRIPT,
                        "active": len(active_positions), "maxSlots": max_slots})
            return

        # Check risk gate
        counter_path = os.path.join(sd, "trade-counter.json")
        counter = cfg.load_json(counter_path, {"gate": "OPEN", "entries": 0, "maxEntriesPerDay": 4})

        today = time.strftime("%Y-%m-%d")
        if counter.get("date") != today:
            # Auto-reset if day changed
            ch_data, _ = cfg.fetch_clearinghouse(wallet)
            acct_val = budget
            if ch_data:
                for vk in ("main", "xyz"):
                    v = ch_data.get(vk, {})
                    ms = v.get("marginSummary") or v.get("crossMarginSummary") or {}
                    try:
                        acct_val = max(acct_val, float(ms.get("accountValue", 0)))
                    except (TypeError, ValueError):
                        pass

            counter = {
                "date": today,
                "accountValueStart": acct_val,
                "entries": 0,
                "realizedPnl": 0,
                "gate": "OPEN",
                "gateReason": None,
                "cooldownUntil": None,
                "lastResults": counter.get("lastResults", []),
                "maxEntriesPerDay": strat.get("maxEntriesPerDay", 4),
                "maxConsecutiveLosses": 4,
                "cooldownMinutes": 30,
            }
            cfg.atomic_write(counter_path, counter)

        gate = counter.get("gate", "OPEN")
        entries = counter.get("entries", 0)
        max_entries = counter.get("maxEntriesPerDay", 4)

        if gate != "OPEN":
            cfg.output({"status": "gate_closed", "script": SCRIPT, "gate": gate,
                        "reason": counter.get("gateReason")})
            return

        if entries >= max_entries:
            cfg.output({"status": "max_entries", "script": SCRIPT,
                        "entries": entries, "max": max_entries})
            return

        # ════════════════════════════════════════
        # STEP 1: Scan SM markets
        # ════════════════════════════════════════
        candidates = scan_sm_markets()
        if not candidates:
            cfg.heartbeat(SCRIPT)
            return

        entered = False

        for cand in candidates:
            if entered:
                break

            token = cand["token"]
            direction = cand["direction"]
            sm_pct = cand["pct"]
            sm_traders = cand["traders"]

            # Skip if already in this position
            if token in active_positions:
                continue

            # Skip if at capacity
            if len(active_positions) >= max_slots:
                break

            # ════════════════════════════════════════
            # STEP 2: Validate top trader alignment
            # ════════════════════════════════════════
            validated, top_aligned, val_detail = validate_top_traders(token, direction)
            if not validated:
                cfg.output({
                    "status": "top_traders_not_aligned",
                    "script": SCRIPT,
                    "asset": token,
                    "direction": direction,
                    "sm_pct": sm_pct,
                    "detail": val_detail,
                })
                continue

            # ════════════════════════════════════════
            # STEP 3: Candle confirmation
            # ════════════════════════════════════════
            candle_ok, candle_reason = confirm_candles(token, direction)
            if not candle_ok:
                cfg.output({
                    "status": "candle_rejected",
                    "script": SCRIPT,
                    "asset": token,
                    "direction": direction,
                    "sm_pct": sm_pct,
                    "reason": candle_reason,
                })
                continue

            # ════════════════════════════════════════
            # STEP 4: Structural confirmation
            # ════════════════════════════════════════
            struct_ok, struct_reason = check_structure(token, direction)
            if not struct_ok:
                cfg.output({
                    "status": "structure_rejected",
                    "script": SCRIPT,
                    "asset": token,
                    "direction": direction,
                    "sm_pct": sm_pct,
                    "reason": struct_reason,
                })
                continue

            # ════════════════════════════════════════
            # ALL 4 GATES PASSED → ENTER
            # ════════════════════════════════════════
            margin = round(budget * margin_pct, 2)
            leverage = default_leverage

            # Verify on-chain before opening
            positions, pos_err = cfg.get_active_positions(wallet)
            if pos_err:
                cfg.output_error(SCRIPT, f"clearinghouse check failed: {pos_err}")
                continue
            if token in positions:
                continue

            # Build entry reason with full context
            reason = (
                f"SHARK conviction: {direction} {token} — "
                f"SM {sm_pct:.1f}% ({sm_traders} traders), "
                f"{top_aligned}/5 top aligned, "
                f"{candle_reason}, "
                f"{struct_reason}"
            )

            # SL only, no TP — let winners run
            SL_ROE = 5.0
            result, err = cfg.create_position(
                wallet, token, direction, leverage, margin, reason,
                tp_pct=None, sl_pct=SL_ROE
            )

            if err:
                cfg.output_error(SCRIPT, f"create_position failed: {err}",
                                  strategyId=sk, asset=token, direction=direction)
                continue

            # Verify ALO fill
            filled, fill_err = cfg.verify_position_filled(wallet, token, max_wait=45)
            if not filled:
                cfg.cancel_open_orders(wallet, token)
                cfg.output({
                    "status": "alo_unfilled",
                    "script": SCRIPT,
                    "asset": token,
                    "direction": direction,
                    "reason": fill_err or "ALO maker did not fill in 45s — cancelled",
                })
                continue

            # Get actual entry price from clearinghouse
            positions_after, _ = cfg.get_active_positions(wallet)
            entry_price = 0.0
            if token in positions_after:
                entry_price = float(positions_after[token].get("entryPx", 0))

            # Record in state
            active_positions[token] = {
                "direction": direction,
                "entry_price": entry_price,
                "opened_at": cfg.now_iso(),
                "cascade_oi_at_entry": 0,
                "target_liq_zone": 0,
                "pattern": "SM_CONVICTION",
                "triggers": [
                    f"SM {sm_pct:.1f}% ({sm_traders} traders)",
                    f"{top_aligned}/5 top traders aligned",
                    candle_reason,
                    struct_reason,
                ],
                "margin": margin,
                "leverage": leverage,
                "source": "conviction",
                "sm_pct": sm_pct,
                "sm_traders": sm_traders,
                "top_aligned": top_aligned,
            }
            state["active_positions"] = active_positions
            state["updated_at"] = cfg.now_iso()
            cfg.atomic_write(state_path, state)

            # Update trade counter
            counter["entries"] = counter.get("entries", 0) + 1
            cfg.atomic_write(counter_path, counter)

            entered = True

            cfg.output({
                "status": "position_opened",
                "script": SCRIPT,
                "strategyId": sk,
                "asset": token,
                "direction": direction,
                "entry_price": entry_price,
                "margin": margin,
                "leverage": leverage,
                "sm_pct": sm_pct,
                "sm_traders": sm_traders,
                "top_aligned": top_aligned,
                "candle": candle_reason,
                "structure": struct_reason,
                "notify": True,
            })

        if not entered:
            # Log what was scanned but didn't pass
            cfg.output({
                "status": "no_conviction",
                "script": SCRIPT,
                "candidates_scanned": len(candidates),
                "top_candidate": candidates[0]["token"] if candidates else None,
                "top_sm_pct": candidates[0]["pct"] if candidates else 0,
            })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
