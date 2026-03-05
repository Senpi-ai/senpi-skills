#!/usr/bin/env python3
"""
open-position.py — Atomic position open + DSL state creation for WOLF v6

Replaces hand-crafted DSL JSON by the agent. Opens a position via mcporter,
fetches actual fill data, and creates a correct DSL state file atomically.

Usage:
  python3 open-position.py --strategy wolf-abc123 --asset HYPE --direction LONG --leverage 10
  python3 open-position.py --strategy wolf-abc123 --asset HYPE --direction SHORT --leverage 5 --margin 200
"""
import json, sys, os, argparse, glob
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (load_strategy, dsl_state_path, dsl_state_glob,
                         dsl_state_template, atomic_write, mcporter_call,
                         mcporter_call_safe, calculate_leverage, strategy_lock,
                         check_gate, increment_entry_counter,
                         WORKSPACE, ROTATION_COOLDOWN_MINUTES)


def fail(msg, **extra):
    """Print error JSON and exit."""
    print(json.dumps({"success": False, "error": msg, **extra}))
    sys.exit(1)


def load_max_leverage():
    """Load max-leverage.json if it exists."""
    path = os.path.join(WORKSPACE, "max-leverage.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


APPROX_GRACE_MINUTES = 10  # approximate DSLs older than this don't count toward slots


def count_active_dsls(strategy_key):
    """Count active DSL state files for a strategy.

    Excludes approximate DSLs older than APPROX_GRACE_MINUTES — these are
    likely orphans from unfilled orders and shouldn't block new entries.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    count = 0
    for sf in glob.glob(dsl_state_glob(strategy_key)):
        try:
            with open(sf) as f:
                state = json.load(f)
            if not state.get("active"):
                continue
            # Skip stale approximate DSLs from slot count
            if state.get("approximate") and state.get("createdAt"):
                try:
                    created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
                    age_min = (now - created).total_seconds() / 60
                    if age_min > APPROX_GRACE_MINUTES:
                        continue
                except (ValueError, TypeError):
                    pass
            count += 1
        except (json.JSONDecodeError, IOError, AttributeError):
            continue
    return count


def has_active_dsl(strategy_key, asset):
    """Check if an active DSL already exists for this asset in this strategy."""
    path = dsl_state_path(strategy_key, asset)
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            state = json.load(f)
        return state.get("active", False)
    except (json.JSONDecodeError, IOError, AttributeError):
        return False


def extract_position(clearinghouse_data, coin, dex=None):
    """Extract a specific position from clearinghouse data."""
    section_key = "xyz" if dex == "xyz" else "main"
    section = clearinghouse_data.get(section_key, {})
    for p in section.get("assetPositions", []):
        if not isinstance(p, dict):
            continue
        pos = p.get("position", {})
        if pos.get("coin") == coin:
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            margin_used = float(pos.get("marginUsed", 0))
            pos_value = float(pos.get("positionValue", 0))
            return {
                "entryPx": float(pos.get("entryPx", 0)),
                "size": abs(szi),
                "leverage": round(pos_value / margin_used, 1) if margin_used > 0 else None,
                "direction": "SHORT" if szi < 0 else "LONG",
            }
    return None


def main():
    parser = argparse.ArgumentParser(
        description="WOLF v6 — Atomic position open + DSL creation")
    parser.add_argument("--strategy", required=True,
                        help="Strategy key (e.g. wolf-abc123)")
    parser.add_argument("--asset", required=True,
                        help="Asset symbol (e.g. HYPE, BTC, xyz:AAPL)")
    parser.add_argument("--direction", required=False, default=None, choices=["LONG", "SHORT"],
                        help="Trade direction")
    parser.add_argument("--leverage", required=False, type=float, default=None,
                        help="Leverage multiplier (optional — auto-calculated from tradingRisk if omitted)")
    parser.add_argument("--conviction", type=float, default=0.5,
                        help="Signal conviction 0.0-1.0 for leverage calculation (default: 0.5)")
    parser.add_argument("--margin", type=float, default=None,
                        help="Margin override (default: strategy marginPerSlot)")
    parser.add_argument("--scanner", action="store_true", default=False,
                        help="Scanner mode: use reduced retries/timeouts for faster execution")
    parser.add_argument("--close-asset", default=None, dest="close_asset",
                        help="Asset to close before opening (rotation). Handled atomically under lock.")
    parser.add_argument("--signal-index", type=int, default=None, dest="signal_index",
                        help="Read direction/conviction from Nth alert in scanner output. Overrides --direction/--conviction.")
    args = parser.parse_args()

    strategy_key = args.strategy
    asset = args.asset
    leverage = args.leverage
    margin_override = args.margin

    # Resolve direction/conviction from scanner output if --signal-index provided
    if args.signal_index is not None:
        scanner_output_file = os.path.join(WORKSPACE, "emerging-movers-output.json")
        try:
            with open(scanner_output_file) as f:
                scanner_output = json.load(f)
            signal = scanner_output["alerts"][args.signal_index]
            direction = signal["direction"].upper()
            conviction = signal.get("conviction", 0.5)
        except (FileNotFoundError, json.JSONDecodeError, IndexError, KeyError) as e:
            fail("signal_index_failed", detail=str(e), signalIndex=args.signal_index)
    else:
        if not args.direction:
            fail("missing_direction", detail="--direction is required when --signal-index is not used")
        direction = args.direction.upper()
        conviction = args.conviction

    # 1. Load strategy config
    try:
        cfg = load_strategy(strategy_key)
    except SystemExit:
        # load_strategy calls _fail which exits — re-raise context
        sys.exit(1)

    wallet = cfg.get("wallet", "")
    if not wallet:
        fail("no_wallet_configured", strategyKey=strategy_key)

    margin = margin_override if margin_override else cfg.get("marginPerSlot", 0)
    if margin <= 0:
        fail("invalid_margin", margin=margin, strategyKey=strategy_key)

    # 2a. Guard rail gate check
    gate_status, gate_reason = check_gate(strategy_key)
    if gate_status != "OPEN":
        fail("strategy_gated", gate=gate_status, gateReason=gate_reason, strategyKey=strategy_key)

    # 2. Resolve leverage — auto-calculate or validate explicit value
    max_lev_data = load_max_leverage()
    clean_asset = asset.replace("xyz:", "")
    lookup_key = asset if asset in max_lev_data else clean_asset
    max_lev = max_lev_data.get(lookup_key)
    leverage_capped = False
    leverage_auto = False

    if leverage is None:
        # Auto-calculate from tradingRisk + maxLeverage + conviction
        trading_risk = cfg.get("tradingRisk", "moderate")
        if max_lev is not None:
            leverage = calculate_leverage(max_lev, trading_risk, conviction)
            leverage_auto = True
        else:
            # Fallback to defaultLeverage when max-leverage data unavailable
            leverage = cfg.get("defaultLeverage", 10)
    else:
        # Explicit --leverage provided: cap against max as before
        if max_lev is not None and leverage > max_lev:
            original_leverage = leverage
            leverage = max_lev
            leverage_capped = True

    # Scanner mode: 2 retries x 15s for faster execution within cron timeout
    api_retries = 2 if args.scanner else 3
    api_timeout = 15 if args.scanner else 30

    # Acquire strategy lock to serialize slot check + position open + DSL write.
    # This prevents two concurrent open-position calls from both reading
    # the same slot count and exceeding the limit.
    try:
        lock_ctx = strategy_lock(strategy_key, timeout=120)
        lock_ctx.__enter__()
    except RuntimeError as e:
        fail("lock_timeout", detail=str(e), strategyKey=strategy_key)

    try:
        # 2.5 Handle rotation close (--close-asset) inside the lock
        just_closed_coin = None
        rotation_notif = None
        if args.close_asset:
            close_clean = args.close_asset.replace("xyz:", "")

            # Enforce rotation cooldown — refuse to close positions younger than threshold
            close_dsl_path = dsl_state_path(strategy_key, close_clean)
            if os.path.exists(close_dsl_path):
                try:
                    with open(close_dsl_path) as f:
                        close_state = json.load(f)
                    if close_state.get("createdAt"):
                        created = datetime.fromisoformat(close_state["createdAt"].replace("Z", "+00:00"))
                        age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
                        if age_min < ROTATION_COOLDOWN_MINUTES:
                            fail("rotation_cooldown",
                                 detail=f"{close_clean} is {round(age_min, 1)}min old, cooldown is {ROTATION_COOLDOWN_MINUTES}min",
                                 closeAsset=close_clean, strategyKey=strategy_key)
                except (json.JSONDecodeError, IOError, ValueError, TypeError):
                    pass  # if we can't read the DSL, allow the rotation (edge case)

            # Determine on-chain coin name for close
            close_is_xyz = args.close_asset.startswith("xyz:") or cfg.get("dex") == "xyz"
            if not close_is_xyz:
                xyz_key = f"xyz:{args.close_asset}"
                if xyz_key in max_lev_data and args.close_asset not in max_lev_data:
                    close_is_xyz = True
            close_coin = (args.close_asset if args.close_asset.startswith("xyz:")
                          else (f"xyz:{args.close_asset}" if close_is_xyz
                                else args.close_asset))

            # Close the position on-chain
            try:
                mcporter_call("close_position",
                              retries=api_retries, timeout=api_timeout,
                              strategyWalletAddress=wallet,
                              coin=close_coin, reason="rotation_for_stronger_signal")
            except RuntimeError as e:
                fail("rotation_close_failed", detail=str(e),
                     closeAsset=close_clean, strategyKey=strategy_key)

            # Deactivate the DSL state file
            close_dsl_path = dsl_state_path(strategy_key, close_clean)
            if os.path.exists(close_dsl_path):
                try:
                    with open(close_dsl_path) as f:
                        close_state = json.load(f)
                    close_state["active"] = False
                    close_state["closeReason"] = "rotation_for_stronger_signal"
                    close_state["closedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    atomic_write(close_dsl_path, close_state)
                except (json.JSONDecodeError, IOError):
                    pass

            just_closed_coin = close_coin
            rotation_notif = f"🔄 ROTATION [{strategy_key}]: Closing {close_clean} for {clean_asset}"

        # 3. Check slot availability (cross-check DSL files with on-chain positions)
        max_slots = cfg.get("slots", 2)
        dsl_count = count_active_dsls(strategy_key)
        on_chain_count = 0
        if wallet:
            ch_data = mcporter_call_safe("strategy_get_clearinghouse_state",
                                          strategy_wallet=wallet)
            if ch_data:
                for section_key in ("main", "xyz"):
                    section = ch_data.get(section_key, {})
                    for p in section.get("assetPositions", []):
                        if not isinstance(p, dict):
                            continue
                        pos = p.get("position", {})
                        szi = float(pos.get("szi", 0))
                        if szi != 0:
                            on_chain_count += 1
        # Adjust on_chain_count for position we just closed (may still appear on-chain)
        if just_closed_coin and on_chain_count > 0 and ch_data:
            for section_key in ("main", "xyz"):
                section = ch_data.get(section_key, {})
                for p in section.get("assetPositions", []):
                    if not isinstance(p, dict):
                        continue
                    pos = p.get("position", {})
                    if pos.get("coin") == just_closed_coin and float(pos.get("szi", 0)) != 0:
                        on_chain_count -= 1
                        break

        active_count = max(dsl_count, on_chain_count)
        if active_count >= max_slots:
            fail("no_slots_available", used=active_count, max=max_slots,
                 dslCount=dsl_count, onChainCount=on_chain_count,
                 strategyKey=strategy_key)

        # 4. Check no existing active DSL for this asset
        if has_active_dsl(strategy_key, clean_asset):
            fail("position_already_exists", asset=clean_asset,
                 strategyKey=strategy_key)

        # 5. Detect dex (with max-leverage fallback for XYZ assets passed without prefix)
        is_xyz = asset.startswith("xyz:") or cfg.get("dex") == "xyz"
        if not is_xyz and not asset.startswith("xyz:"):
            xyz_key = f"xyz:{asset}"
            if xyz_key in max_lev_data and asset not in max_lev_data:
                is_xyz = True
        dex = "xyz" if is_xyz else "hl"
        coin = asset if asset.startswith("xyz:") else (f"xyz:{asset}" if is_xyz else asset)

        # 6. Open position via mcporter
        order = {
            "coin": coin,
            "direction": direction,
            "leverage": int(leverage),
            "marginAmount": margin,
            "orderType": "MARKET",
        }
        if is_xyz:
            order["leverageType"] = "ISOLATED"

        try:
            open_result = mcporter_call(
                "create_position",
                retries=api_retries, timeout=api_timeout,
                strategyWalletAddress=wallet,
                orders=[order],
            )
        except RuntimeError as e:
            fail("position_open_failed", detail=str(e), strategyKey=strategy_key)

        # 7. Fetch actual fill data from clearinghouse
        approximate = False
        try:
            ch_data = mcporter_call("strategy_get_clearinghouse_state",
                                    retries=api_retries, timeout=api_timeout,
                                    strategy_wallet=wallet)
            pos_data = extract_position(ch_data, coin, dex=("xyz" if is_xyz else None))
            if pos_data:
                entry_price = pos_data["entryPx"]
                size = pos_data["size"]
                actual_leverage = pos_data["leverage"] or leverage
                # entryPx can be 0 during fill race window — treat as approximate
                if not entry_price:
                    approximate = True
            else:
                # Position not found in clearinghouse — use approximate values
                approximate = True
                entry_price = 0
                size = round(margin * leverage, 6)
                actual_leverage = leverage
        except RuntimeError:
            # Clearinghouse fetch failed — use approximate values
            approximate = True
            entry_price = 0
            size = round(margin * leverage, 6)
            actual_leverage = leverage

        # 8. Create DSL state
        tiers = None
        dsl_cfg = cfg.get("dsl", {})
        if isinstance(dsl_cfg.get("tiers"), list) and len(dsl_cfg["tiers"]) > 0:
            tiers = dsl_cfg["tiers"]

        dsl_state = dsl_state_template(
            asset=clean_asset,
            direction=direction,
            entry_price=entry_price,
            size=size,
            leverage=actual_leverage,
            strategy_key=strategy_key,
            tiers=tiers,
            created_by="open_position_script",
        )
        dsl_state["wallet"] = wallet
        dsl_state["dex"] = dex
        if approximate:
            dsl_state["approximate"] = True

        # 9. Write DSL state atomically
        dsl_path = dsl_state_path(strategy_key, clean_asset)
        atomic_write(dsl_path, dsl_state)

        # 10. Increment guard rail entry counter
        try:
            increment_entry_counter(strategy_key)
        except Exception:
            pass  # Never fail the open for counter bookkeeping
    finally:
        lock_ctx.__exit__(None, None, None)

    # 10. Output result
    result = {
        "success": True,
        "asset": clean_asset,
        "direction": direction,
        "entryPrice": entry_price,
        "size": size,
        "leverage": actual_leverage,
        "dslFile": dsl_path,
        "strategyKey": strategy_key,
    }
    if approximate:
        result["approximate"] = True
        result["warning"] = "Fill data unavailable, DSL uses approximate values. Health check will reconcile."
    if leverage_capped:
        result["leverageCapped"] = True
        result["requestedLeverage"] = original_leverage
        result["maxLeverage"] = max_lev
    if leverage_auto:
        result["leverageAutoCalculated"] = True
        result["tradingRisk"] = cfg.get("tradingRisk", "moderate")
        result["conviction"] = conviction
        if max_lev is not None:
            result["maxLeverage"] = max_lev

    # Build pre-formatted notification message
    notif_parts = [f"🟢 OPENED {clean_asset} {direction} [{strategy_key}]"]
    notif_parts.append(f"Entry: ${entry_price:.4g}" if entry_price else "Entry: pending fill")
    notif_parts.append(f"Size: {size:.4g}")
    notif_parts.append(f"Leverage: {actual_leverage}x")
    if leverage_auto:
        notif_parts.append(f"(auto: {cfg.get('tradingRisk', 'moderate')} risk, {conviction} conviction)")
    if leverage_capped:
        notif_parts.append(f"(capped from {original_leverage}x, max {max_lev}x)")
    result["notifications"] = [" | ".join(notif_parts)]
    if rotation_notif:
        result["notifications"].insert(0, rotation_notif)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
