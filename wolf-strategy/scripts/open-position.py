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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_config import (load_strategy, dsl_state_path, dsl_state_glob,
                         dsl_state_template, atomic_write, mcporter_call,
                         WORKSPACE)


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


def count_active_dsls(strategy_key):
    """Count active DSL state files for a strategy."""
    count = 0
    for sf in glob.glob(dsl_state_glob(strategy_key)):
        try:
            with open(sf) as f:
                state = json.load(f)
            if state.get("active"):
                count += 1
        except (json.JSONDecodeError, IOError):
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
    except (json.JSONDecodeError, IOError):
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
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"],
                        help="Trade direction")
    parser.add_argument("--leverage", required=True, type=float,
                        help="Leverage multiplier")
    parser.add_argument("--margin", type=float, default=None,
                        help="Margin override (default: strategy marginPerSlot)")
    args = parser.parse_args()

    strategy_key = args.strategy
    asset = args.asset
    direction = args.direction.upper()
    leverage = args.leverage
    margin_override = args.margin

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

    # 2. Validate leverage against max-leverage.json
    max_lev_data = load_max_leverage()
    clean_asset = asset.replace("xyz:", "")
    lookup_key = asset if asset in max_lev_data else clean_asset
    max_lev = max_lev_data.get(lookup_key)
    leverage_capped = False
    if max_lev is not None and leverage > max_lev:
        original_leverage = leverage
        leverage = max_lev
        leverage_capped = True

    # 3. Check slot availability
    max_slots = cfg.get("slots", 2)
    active_count = count_active_dsls(strategy_key)
    if active_count >= max_slots:
        fail("no_slots_available", used=active_count, max=max_slots,
             strategyKey=strategy_key)

    # 4. Check no existing active DSL for this asset
    if has_active_dsl(strategy_key, clean_asset):
        fail("position_already_exists", asset=clean_asset,
             strategyKey=strategy_key)

    # 5. Detect dex
    is_xyz = asset.startswith("xyz:") or cfg.get("dex") == "xyz"
    dex = "xyz" if is_xyz else "hl"
    coin = asset if asset.startswith("xyz:") else (f"xyz:{asset}" if is_xyz else asset)

    # 6. Open position via mcporter
    try:
        open_result = mcporter_call(
            "create_position",
            strategyWalletAddress=wallet,
            coin=coin,
            direction=direction,
            leverage=leverage,
            margin=margin,
        )
    except RuntimeError as e:
        fail("position_open_failed", detail=str(e), strategyKey=strategy_key)

    # 7. Fetch actual fill data from clearinghouse
    approximate = False
    try:
        ch_data = mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=wallet)
        pos_data = extract_position(ch_data, coin, dex=("xyz" if is_xyz else None))
        if pos_data:
            entry_price = pos_data["entryPx"]
            size = pos_data["size"]
            actual_leverage = pos_data["leverage"] or leverage
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

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
