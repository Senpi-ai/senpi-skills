#!/usr/bin/env python3
"""DSL v5.2 — Upgraded trailing stops with time decay and partial take-profit.

Upgrades over v5.0:
- Time decay: if trade doesn't reach profit targets within time windows, tighten SL
- Partial TP: close 25% at Tier 3 via ALO, let rest ride
- Wider initial stops: 8% ROE floor, 4% retrace, 5 breaches
- Phase 2: 2% retrace, 2 breaches (more confirmation before exit)
- Scanner loss feedback: updates scanner_state.json on SL to trigger longer cooldown

Architecture:
1. Check strategy active via MCP
2. Check if SL orders filled → archive
3. Reconcile with clearinghouse → archive orphans
4. For each position: price → time decay → tiers → breach → partial TP → close
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STATE_DIR = "/data/workspace/dsl"
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SCANNER_STATE_PATH = os.path.join(WORKSPACE, "recipes", "hype-sniper", "state", "scanner_state.json")


def atomic_write(path, data):
    """Write JSON atomically via tmp file + rename."""
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def asset_to_filename(asset: str) -> str:
    if asset.startswith("xyz:"):
        return asset.replace(":", "--", 1)
    return asset


def filename_to_asset(filename: str) -> str | None:
    if not filename.endswith(".json"):
        return None
    base = filename[:-5]
    if "--" in base and not base.startswith("xyz--"):
        return None
    if base.startswith("xyz--"):
        return "xyz:" + base[5:]
    return base


def resolve_state_file(state_dir: str, strategy_id: str, asset: str) -> tuple[str, str | None]:
    if not strategy_id or not asset:
        return "", "strategy_id and asset required"
    path = os.path.join(state_dir, strategy_id, f"{asset_to_filename(asset)}.json")
    if not os.path.isfile(path):
        return path, "state_file_not_found"
    return path, None


def list_strategy_state_files(state_dir: str, strategy_id: str) -> list[tuple[str, str]]:
    out = []
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return out
    for name in os.listdir(strategy_dir):
        if "_archived" in name or ".archived" in name:
            continue
        path = os.path.join(strategy_dir, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        asset = filename_to_asset(name)
        if asset is not None:
            out.append((path, asset))
    return out


def dex_and_lookup_symbol(asset: str) -> tuple[str, str]:
    if asset.startswith("xyz:"):
        return "xyz", asset.split(":", 1)[1]
    return "", asset


# ── MCP helpers ───────────────────────────────────────────────────────────

DSL_ACTIVE_STATUSES = ("ACTIVE", "PAUSED")


def _unwrap_mcporter_response(stdout_str: str) -> dict | None:
    try:
        raw = json.loads(stdout_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, list) and len(content) > 0:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
    return raw


def _mcp_call(tool: str, args: dict, timeout: int = 20) -> tuple[dict | None, str | None]:
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", tool, "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout)
        if not raw:
            return None, f"{tool}: invalid or empty response"
        if raw.get("success") is False:
            err = raw.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return None, msg
        data = raw.get("data") or raw
        return data, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def get_strategy_active_and_wallet(strategy_id: str) -> tuple[bool, str | None, str | None, bool]:
    data, err = _mcp_call("strategy_get", {"strategy_id": strategy_id})
    if err:
        return False, None, err, False
    strategy = data.get("strategy") if isinstance(data, dict) else None
    if not strategy or not isinstance(strategy, dict):
        return False, None, "no strategy in response", False
    status = (strategy.get("status") or "").strip().upper()
    if status not in DSL_ACTIVE_STATUSES:
        return False, None, f"status={status}", True
    wallet = (strategy.get("strategyWalletAddress") or "").strip()
    if not wallet:
        return False, None, "no wallet", False
    return True, wallet, None, False


def get_active_position_coins(wallet: str) -> tuple[set[str], str | None]:
    coins = set()
    data, err = _mcp_call("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if err:
        return set(), err
    for section in ("main", "xyz"):
        if not data or section not in data:
            continue
        for p in data.get(section, {}).get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin")
            if coin and float(pos.get("szi", 0)) != 0:
                coins.add(coin)
    return coins, None


def cleanup_strategy_state_dir(state_dir: str, strategy_id: str) -> int:
    deleted = 0
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return 0
    for name in os.listdir(strategy_dir):
        if "_archived" in name or ".archived" in name:
            continue
        path = os.path.join(strategy_dir, name)
        if name.endswith(".json") and os.path.isfile(path):
            try:
                os.remove(path)
                deleted += 1
            except OSError:
                pass
    return deleted


# ── Price fetch ───────────────────────────────────────────────────────────

def fetch_price_mcp(dex: str, lookup_symbol: str) -> tuple[float | None, str | None]:
    try:
        dex = dex.strip() if dex else ""
        if dex.lower() == "main":
            dex = ""
        is_xyz = dex.lower() == "xyz"
        response_key = f"xyz:{lookup_symbol}" if is_xyz else lookup_symbol

        data, err = _mcp_call("market_get_prices", {"assets": [response_key], "dex": dex}, timeout=15)
        price_str = None
        if data and isinstance(data, dict):
            price_str = data.get("prices", data).get(response_key) if "prices" in data else data.get(response_key)

        if price_str is None:
            data2, err2 = _mcp_call("allMids", {"dex": dex} if dex else {}, timeout=15)
            if data2 and isinstance(data2, dict):
                price_str = data2.get("prices", data2).get(response_key) if "prices" in data2 else data2.get(response_key)
            if price_str is None:
                return None, f"no price for {lookup_symbol}"

        return float(price_str), None
    except (TypeError, ValueError) as e:
        return None, str(e)


# ── SL order management ──────────────────────────────────────────────────

def _mcp_edit_position(wallet: str, coin: str, stop_loss_price: float, order_type: str = "LIMIT") -> tuple[bool, str | None, int | None]:
    args = {
        "strategyWalletAddress": wallet,
        "coin": coin,
        "stopLoss": {"price": round(stop_loss_price, 4), "orderType": order_type},
    }
    data, err = _mcp_call("edit_position", args, timeout=30)
    if err:
        return False, err, None
    oid = None
    if isinstance(data, dict):
        ou = data.get("ordersUpdated") or data.get("orders_updated")
        if isinstance(ou, dict):
            sl = ou.get("stopLoss") or ou.get("stop_loss")
            if isinstance(sl, dict):
                oid = sl.get("orderId") or sl.get("order_id")
        if oid is None:
            oid = data.get("stopLossOrderId") or data.get("stop_loss_order_id")
        if oid is not None:
            try:
                oid = int(oid)
            except (TypeError, ValueError):
                oid = None
    return True, None, oid


def _mcp_get_open_orders(wallet: str, dex: str = "") -> tuple[list[dict], str | None]:
    data, err = _mcp_call("strategy_get_open_orders", {"strategy_wallet": wallet, "dex": dex})
    if err:
        return [], err
    orders = data.get("orders") if isinstance(data, dict) else None
    return orders if isinstance(orders, list) else [], None


def _mcp_get_order_status(wallet: str, order_id: int) -> tuple[bool, str | None, str | None]:
    args = {"user": wallet, "orderId": order_id}
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "execution_get_order_status", "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=15,
        )
        raw = _unwrap_mcporter_response(r.stdout) if r.stdout else None
        if r.returncode != 0:
            return False, None, (r.stderr or r.stdout)
        if not raw or not isinstance(raw, dict):
            return False, None, "invalid response"
        if raw.get("success") is False:
            err = raw.get("error", {})
            return False, None, str(err)
        data = raw.get("data") or raw
        if data.get("status") == "unknownOid":
            return True, None, None
        if data.get("status") == "order":
            order = data.get("order")
            if isinstance(order, dict):
                return True, (order.get("status") or "").strip().lower(), None
        return False, None, "unexpected shape"
    except Exception as e:
        return False, None, str(e)


def sync_sl_to_hyperliquid(state, effective_floor, now, dex, phase):
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        return False, False, "no wallet"
    order_type = "MARKET" if phase == 1 else "LIMIT"

    success, err, oid = _mcp_edit_position(wallet, coin, effective_floor, order_type)
    if not success:
        return False, False, err

    if oid is None:
        orders, _ = _mcp_get_open_orders(wallet, dex)
        for o in orders:
            if not isinstance(o, dict) or o.get("coin") != coin:
                continue
            if not o.get("isTrigger", False) and not o.get("isPositionTpsl", False):
                continue
            try:
                tp = float(o.get("triggerPx", 0))
            except (TypeError, ValueError):
                continue
            if abs(tp - round(effective_floor, 4)) < 1e-6:
                oid_str = o.get("oid")
                if oid_str:
                    try:
                        oid = int(oid_str)
                    except (TypeError, ValueError):
                        pass
                break

    state["lastSyncedFloorPrice"] = round(effective_floor, 4)
    state["slOrderIdUpdatedAt"] = now
    if oid is not None:
        state["slOrderId"] = oid
    return True, True, None


def sync_native_tp(state, now):
    """Set a native take-profit on Hyperliquid via edit_position.
    
    ONLY set TP in Phase 2 at Tier 3+ (25% ROE or higher).
    Phase 1 and early Phase 2: let DSL trailing stops manage the exit.
    Setting TP too early kills winners — learned from HYPE SHORT that
    exited at 8% ROE right before a 23% move.
    
    TP target: skip 2 tiers ahead (not the next immediate tier).
    This gives the position room to run while still having a safety net.
    """
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        return False

    phase = state.get("phase", 1)
    tier_idx = state.get("currentTierIndex", -1)

    # Don't set TP in Phase 1 — let the trade breathe
    if phase < 2:
        return False

    # Don't set TP until Tier 3 (index 2, 25% ROE)
    # This ensures we only cap upside on confirmed big moves
    if tier_idx < 2:
        return False

    tiers = state.get("tiers", [])
    entry = state["entryPrice"]
    leverage = max(1, state.get("leverage", 1))
    is_long = state.get("direction", "LONG").upper() == "LONG"

    # Target: 2 tiers ahead of current (gives room to run)
    tp_tier_idx = min(tier_idx + 2, len(tiers) - 1)
    if tp_tier_idx <= tier_idx:
        return False  # already at or near max

    tp_trigger_roe = tiers[tp_tier_idx]["triggerPct"]

    # Convert ROE % to price
    if is_long:
        tp_price = round(entry * (1 + tp_trigger_roe / 100 / leverage), 4)
    else:
        tp_price = round(entry * (1 - tp_trigger_roe / 100 / leverage), 4)

    # Only set if different from what we last synced
    last_tp = state.get("lastSyncedTpPrice")
    if last_tp is not None and abs(last_tp - tp_price) < 1e-6:
        return False

    args = {
        "strategyWalletAddress": wallet,
        "coin": coin,
        "takeProfit": {"price": tp_price, "orderType": "LIMIT"},
    }
    data, err = _mcp_call("edit_position", args, timeout=30)
    if err:
        state["lastTpSyncError"] = err
        return False

    state["lastSyncedTpPrice"] = tp_price
    state["lastTpSyncAt"] = now
    return True


# ── Close position ────────────────────────────────────────────────────────

def try_close_position(state, price, reason_str, now, retries=2, delay=3):
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        state["pendingClose"] = True
        return False, "no wallet"

    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["mcporter", "call", "senpi", "close_position", "--args",
                 json.dumps({"strategyWalletAddress": wallet, "coin": coin, "reason": reason_str})],
                capture_output=True, text=True, timeout=30,
            )
            result_text = r.stdout.strip()
            if r.returncode == 0 and "error" not in result_text.lower():
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = reason_str

                # Record trade result for risk guardian
                try:
                    entry = state.get("entryPrice", 0)
                    size = state.get("size", 0)
                    is_long = state.get("direction", "").upper() == "LONG"
                    pnl = (price - entry) * size if is_long else (entry - price) * size
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import hype_lib
                    counter = hype_lib.load_trade_counter()
                    hype_lib.record_trade_result(counter, pnl)
                except Exception:
                    pass  # Don't let counter failure block the close

                return True, result_text
        except Exception as e:
            result_text = str(e)
        if attempt < retries - 1:
            time.sleep(delay)

    state["pendingClose"] = True
    return False, result_text


# ── Partial take-profit ───────────────────────────────────────────────────

def execute_pyramid_add(state, price, upnl_pct, now):
    """Add to a winning position via edit_position(targetMargin).
    
    Pyramid rules:
    - Only add once per tier upgrade (prevents over-adding)
    - Add 15% of current margin (small pyramid, not doubling down)
    - Only if position is in Phase 2 (confirmed winner)
    - Max 2 adds total per trade
    - Uses ALO for fee optimization
    """
    pyramid = state.get("pyramid", {
        "enabled": True,
        "addPct": 15,          # add 15% margin per pyramid
        "maxAdds": 2,          # max 2 pyramids per trade
        "minTier": 1,          # only after Tier 2 (index 1)
        "addsExecuted": 0,
        "lastAddTier": -1,
        "history": []
    })
    state["pyramid"] = pyramid

    if not pyramid.get("enabled", True):
        return False, None

    adds_done = pyramid.get("addsExecuted", 0)
    max_adds = pyramid.get("maxAdds", 2)
    if adds_done >= max_adds:
        return False, None

    # Only pyramid in Phase 2 (confirmed trend)
    if state.get("phase", 1) < 2:
        return False, None

    tier_idx = state.get("currentTierIndex", -1)
    min_tier = pyramid.get("minTier", 1)
    if tier_idx < min_tier:
        return False, None

    # Only add once per tier (don't re-add at same tier)
    last_add_tier = pyramid.get("lastAddTier", -1)
    if tier_idx <= last_add_tier:
        return False, None

    wallet = state.get("wallet", "")
    coin = state["asset"]
    entry = state["entryPrice"]
    size = state["size"]
    leverage = state.get("leverage", 10)

    current_margin = entry * size / leverage
    add_pct = pyramid.get("addPct", 15)
    target_margin = round(current_margin * (1 + add_pct / 100), 2)

    args = {
        "strategyWalletAddress": wallet,
        "coin": coin,
        "targetMargin": target_margin,
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "ensureExecutionAsTaker": True,
    }

    data, err = _mcp_call("edit_position", args, timeout=30)
    if err:
        return False, f"pyramid add failed: {err}"

    actions = data.get("actionsPerformed", []) if isinstance(data, dict) else []
    if "POSITION_ADDED" in actions:
        current_state = data.get("currentState", {}) if isinstance(data, dict) else {}
        new_size = current_state.get("size")
        if new_size is not None:
            state["size"] = abs(float(new_size))

        # Update entry price (blended average from currentState)
        new_entry = current_state.get("entryPrice")
        if new_entry is not None:
            state["entryPrice"] = float(new_entry)

        pyramid["addsExecuted"] = adds_done + 1
        pyramid["lastAddTier"] = tier_idx
        pyramid["history"].append({
            "tier": tier_idx,
            "price": price,
            "addedMargin": round(target_margin - current_margin, 2),
            "newSize": state["size"],
            "at": now
        })
        state["pyramid"] = pyramid
        return True, f"pyramided +{add_pct}% margin at Tier {tier_idx+1}, new size={state['size']}"

    return False, f"no add: actions={actions}"


def execute_partial_tp(state, price, now):
    """Close a portion of the position via edit_position(targetMargin).
    
    Uses targetMargin to reduce position size — Senpi handles the math.
    Also sets native TP on the remaining position for the next tier.
    """
    ptp = state.get("partialTp", {})
    if not ptp.get("enabled") or ptp.get("executed"):
        return False, None

    wallet = state.get("wallet", "")
    coin = state["asset"]
    size = state["size"]
    entry = state["entryPrice"]
    leverage = state.get("leverage", 10)
    close_pct = ptp.get("closePct", 25)

    # Current margin = entry * size / leverage
    current_margin = entry * size / leverage
    # Target margin after reducing by close_pct
    target_margin = round(current_margin * (1 - close_pct / 100), 2)

    if target_margin < 10:
        return False, "target margin too small"

    args = {
        "strategyWalletAddress": wallet,
        "coin": coin,
        "targetMargin": target_margin,
        "orderType": ptp.get("orderType", "FEE_OPTIMIZED_LIMIT"),
        "ensureExecutionAsTaker": True,
    }

    data, err = _mcp_call("edit_position", args, timeout=30)
    if err:
        return False, f"edit_position failed: {err}"

    # Check if reduction actually happened
    actions = data.get("actionsPerformed", []) if isinstance(data, dict) else []
    if "POSITION_REDUCED" in actions:
        # Update state with new size from response
        current_state = data.get("currentState", {}) if isinstance(data, dict) else {}
        new_size = current_state.get("size")
        if new_size is not None:
            state["size"] = abs(float(new_size))
        else:
            state["size"] = round(size * (1 - close_pct / 100), 2)

        ptp["executed"] = True
        ptp["executedAt"] = now
        ptp["executedPrice"] = price
        ptp["closedSize"] = round(size - state["size"], 2)
        ptp["targetMarginUsed"] = target_margin
        state["partialTp"] = ptp
        return True, f"reduced to {state['size']} sz (was {size}), actions={actions}"

    return False, f"no reduction: actions={actions}"


# ── Scanner feedback ──────────────────────────────────────────────────────

def notify_scanner_of_loss(was_loss: bool):
    """Update scanner_state.json so it uses longer cooldown after a loss."""
    try:
        if os.path.isfile(SCANNER_STATE_PATH):
            with open(SCANNER_STATE_PATH) as f:
                scanner_state = json.load(f)
        else:
            scanner_state = {}
        scanner_state["last_trade_was_loss"] = was_loss
        scanner_state["last_trade_ts"] = time.time()
        atomic_write(SCANNER_STATE_PATH, scanner_state)
    except Exception as e:
        print(f"Warning: could not update scanner state: {e}", file=sys.stderr)


# ── State normalization ───────────────────────────────────────────────────

def normalize_state(state):
    changed = False
    if "phase1" not in state or not isinstance(state["phase1"], dict):
        state["phase1"] = {}
        changed = True
    p1 = state["phase1"]
    if "retraceThreshold" not in p1:
        p1["retraceThreshold"] = 0.04
        changed = True
    if "consecutiveBreachesRequired" not in p1:
        p1["consecutiveBreachesRequired"] = 5
        changed = True
    if "absoluteFloor" not in p1 or p1["absoluteFloor"] is None:
        entry = state.get("entryPrice")
        lev = max(1, state.get("leverage", 1))
        is_long = state.get("direction", "LONG").upper() == "LONG"
        if entry:
            p1["absoluteFloor"] = round(entry * (1 - 0.08 / lev) if is_long else entry * (1 + 0.08 / lev), 4)
        else:
            p1["absoluteFloor"] = 0.0
        changed = True

    if "phase2" not in state or not isinstance(state["phase2"], dict):
        state["phase2"] = {}
        changed = True
    p2 = state["phase2"]
    if "retraceThreshold" not in p2:
        p2["retraceThreshold"] = 0.02
        changed = True
    if "consecutiveBreachesRequired" not in p2:
        p2["consecutiveBreachesRequired"] = 2
        changed = True
    return changed


# ── Time decay logic ──────────────────────────────────────────────────────

def apply_time_decay(state, upnl_pct, now_dt):
    """Check time-based rules and tighten floor or force close.
    
    Returns: (new_abs_floor or None, force_close: bool, reason: str or None)
    """
    td = state.get("timeDecay", {})
    if not td.get("enabled"):
        return None, False, None

    created_str = state.get("createdAt")
    if not created_str:
        return None, False, None

    try:
        created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None, False, None

    elapsed_min = (now_dt - created).total_seconds() / 60
    entry = state["entryPrice"]
    leverage = max(1, state.get("leverage", 1))
    is_long = state.get("direction", "LONG").upper() == "LONG"

    rules = td.get("rules", [])
    # Apply rules in order — later rules override earlier ones
    new_floor = None
    force_close = False
    reason = None

    for rule in rules:
        after_min = rule.get("afterMinutes", 999)
        min_roe = rule.get("minRoePct", 0)
        tighten_roe = rule.get("tightenFloorRoePct", 0)
        fc = rule.get("forceClose", False)

        if elapsed_min >= after_min and upnl_pct < min_roe:
            if fc:
                force_close = True
                reason = f"time decay: {elapsed_min:.0f}min elapsed, ROE {upnl_pct:.1f}% < {min_roe}% → force close"
            elif tighten_roe > 0:
                # Tighten absolute floor
                if is_long:
                    new_floor = round(entry * (1 - tighten_roe / 100 / leverage), 4)
                else:
                    new_floor = round(entry * (1 + tighten_roe / 100 / leverage), 4)
                reason = f"time decay: {elapsed_min:.0f}min, ROE {upnl_pct:.1f}% < {min_roe}% → tighten to {tighten_roe}% ROE floor"

    return new_floor, force_close, reason


# ── Core trading logic ────────────────────────────────────────────────────

def update_high_water(state, price, is_long):
    hw = state["highWaterPrice"]
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw
    return hw


def apply_tier_upgrades(state, upnl_pct, is_long, hw):
    tiers = state["tiers"]
    tier_idx = state["currentTierIndex"]
    tier_floor = state["tierFloorPrice"]
    phase = state["phase"]
    breach_count = state["currentBreachCount"]
    entry = state["entryPrice"]
    previous_tier_idx = tier_idx
    tier_changed = False

    for i, tier in enumerate(tiers):
        if i <= tier_idx:
            continue
        if upnl_pct >= tier["triggerPct"]:
            tier_idx = i
            tier_changed = True
            if is_long:
                tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
            else:
                tier_floor = round(entry - (entry - hw) * tier["lockPct"] / 100, 4)
            stored = state.get("tierFloorPrice")
            if stored is not None and isinstance(stored, (int, float)):
                tier_floor = max(tier_floor, float(stored)) if is_long else min(tier_floor, float(stored))
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1 and tier_idx >= state.get("phase2TriggerTier", 0):
                state["phase"] = 2
                breach_count = 0
                state["currentBreachCount"] = 0
                phase = 2

    return tier_idx, tier_floor, tier_changed, previous_tier_idx


def compute_effective_floor(state, phase, tier_idx, tier_floor, hw, is_long):
    tiers = state["tiers"]
    leverage = max(1, state.get("leverage", 1))

    if phase == 1:
        retrace_roe = state["phase1"]["retraceThreshold"]
        retrace_price = retrace_roe / leverage
        breaches_needed = state["phase1"]["consecutiveBreachesRequired"]
        abs_floor = state["phase1"]["absoluteFloor"]
        if is_long:
            trailing_floor = round(hw * (1 - retrace_price), 4)
            effective_floor = max(abs_floor, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + retrace_price), 4)
            effective_floor = min(abs_floor, trailing_floor)
        return effective_floor, trailing_floor, breaches_needed, retrace_roe

    retrace_roe = (
        tiers[tier_idx].get("retrace", state["phase2"]["retraceThreshold"])
        if tier_idx >= 0 else state["phase2"]["retraceThreshold"]
    )
    retrace_price = retrace_roe / leverage
    breaches_needed = state["phase2"]["consecutiveBreachesRequired"]
    if is_long:
        trailing_floor = round(hw * (1 - retrace_price), 4)
        effective_floor = max(tier_floor or 0, trailing_floor)
    else:
        trailing_floor = round(hw * (1 + retrace_price), 4)
        effective_floor = min(tier_floor or float("inf"), trailing_floor)
    return effective_floor, trailing_floor, breaches_needed, retrace_roe


# ── Per-position processing ───────────────────────────────────────────────

def _auto_adopt_position(state_dir, strategy_id, wallet, coin, now):
    """Auto-create a DSL state file for an orphan position found in clearinghouse.
    
    This handles positions opened externally (Senpi strategy manager, manual trades, etc.)
    so DSL can protect them with trailing stops immediately.
    """
    # Get position details from clearinghouse
    data, err = _mcp_call("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if err:
        return

    entry_px = None
    szi = 0
    leverage_val = 10
    for section in ("main", "xyz"):
        if not data or section not in data:
            continue
        for p in data.get(section, {}).get("assetPositions", []):
            pos = p.get("position", {})
            if pos.get("coin") == coin:
                szi = float(pos.get("szi", 0))
                entry_px = float(pos.get("entryPx", 0))
                lev = pos.get("leverage", {})
                if isinstance(lev, dict):
                    leverage_val = int(lev.get("value", 10))
                elif lev:
                    leverage_val = int(lev)
                break

    if entry_px is None or szi == 0:
        return

    direction = "LONG" if szi > 0 else "SHORT"
    size = abs(szi)
    is_long = direction == "LONG"

    abs_floor = round(entry_px * (1 - 0.08 / leverage_val) if is_long else entry_px * (1 + 0.08 / leverage_val), 4)

    state = {
        "active": True,
        "asset": coin,
        "direction": direction,
        "leverage": leverage_val,
        "entryPrice": entry_px,
        "size": size,
        "wallet": wallet,
        "strategyId": strategy_id,
        "phase": 1,
        "phase1": {
            "retraceThreshold": 0.04,
            "consecutiveBreachesRequired": 5,
            "absoluteFloor": abs_floor
        },
        "phase2TriggerTier": 1,
        "phase2": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 2
        },
        "tiers": [
            {"triggerPct": 8,   "lockPct": 3,  "retrace": 0.020},
            {"triggerPct": 15,  "lockPct": 10, "retrace": 0.018},
            {"triggerPct": 25,  "lockPct": 18, "retrace": 0.015},
            {"triggerPct": 40,  "lockPct": 30, "retrace": 0.012},
            {"triggerPct": 60,  "lockPct": 48, "retrace": 0.010},
            {"triggerPct": 80,  "lockPct": 65, "retrace": 0.008},
            {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
        ],
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "highWaterPrice": entry_px,
        "floorPrice": abs_floor,
        "currentBreachCount": 0,
        "createdAt": now,
        "consecutiveFetchFailures": 0,
        "adoptedAt": now,
        "adoptedFrom": "clearinghouse",
        "timeDecay": {
            "enabled": True,
            "rules": [
                {"afterMinutes": 5,  "minRoePct": 3.0, "tightenFloorRoePct": 6.0},
                {"afterMinutes": 20, "minRoePct": 3.0, "tightenFloorRoePct": 4.0},
                {"afterMinutes": 45, "minRoePct": 5.0, "tightenFloorRoePct": 0.0, "forceClose": True}
            ]
        },
        "partialTp": {
            "enabled": True,
            "triggerTier": 3,       # index 3 = Tier 4 (40% ROE) — let winners run
            "closePct": 25,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "executed": False
        }
    }

    strategy_dir = os.path.join(state_dir, strategy_id)
    os.makedirs(strategy_dir, exist_ok=True)
    filename = asset_to_filename(coin) + ".json"
    path = os.path.join(strategy_dir, filename)

    atomic_write(path, state)

    print(json.dumps({
        "status": "adopted",
        "asset": coin,
        "direction": direction,
        "entry": entry_px,
        "size": size,
        "floor": abs_floor,
        "time": now,
        "strategy_id": strategy_id,
    }), file=sys.stderr)


def process_one_position(state_file, strategy_id, now):
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"status": "error", "error": "read_failed", "path": state_file, "time": now}))
        return

    if normalize_state(state):
        try:
            atomic_write(state_file, state)
        except OSError:
            pass

    if not state.get("active") and not state.get("pendingClose"):
        print(json.dumps({"status": "inactive", "asset": state.get("asset"), "time": now}))
        return

    direction = state.get("direction", "LONG").upper()
    is_long = direction == "LONG"
    asset = state["asset"]
    dex, lookup_symbol = dex_and_lookup_symbol(asset)

    # Fetch price
    price, fetch_error = fetch_price_mcp(dex, lookup_symbol)
    if fetch_error:
        fails = state.get("consecutiveFetchFailures", 0) + 1
        state["consecutiveFetchFailures"] = fails
        state["lastCheck"] = now
        if fails >= 10:
            state["active"] = False
            state["closeReason"] = f"deactivated: {fails} fetch failures"
        atomic_write(state_file, state)
        print(json.dumps({"status": "error", "error": fetch_error, "failures": fails, "time": now}))
        return

    state["consecutiveFetchFailures"] = 0
    state["lastPrice"] = price

    entry = state["entryPrice"]
    size = state["size"]
    leverage = state["leverage"]
    hw = update_high_water(state, price, is_long)

    upnl = (price - entry) * size if is_long else (entry - price) * size
    margin = entry * size / leverage
    upnl_pct = upnl / margin * 100

    # ── Time decay ────────────────────────────────────────────────────
    now_dt = datetime.now(timezone.utc)
    td_floor, td_force_close, td_reason = apply_time_decay(state, upnl_pct, now_dt)

    if td_floor is not None:
        # Tighten the absolute floor
        abs_floor = state["phase1"]["absoluteFloor"]
        if is_long:
            state["phase1"]["absoluteFloor"] = max(abs_floor, td_floor)
        else:
            state["phase1"]["absoluteFloor"] = min(abs_floor, td_floor)
        state["timeDecayApplied"] = td_reason

    if td_force_close and state.get("phase", 1) == 1:
        # Force close — trade went nowhere
        closed, result = try_close_position(state, price, td_reason, now)
        if closed:
            notify_scanner_of_loss(upnl < 0)
            dest = _archived_name(state_file, "timedecay")
            try:
                os.rename(state_file, dest)
            except OSError:
                atomic_write(state_file, state)
        else:
            state["lastCheck"] = now
            atomic_write(state_file, state)
        print(json.dumps({
            "status": "closed_time_decay" if closed else "pending_close",
            "asset": asset, "direction": direction,
            "price": price, "upnl": round(upnl, 2), "upnl_pct": round(upnl_pct, 2),
            "reason": td_reason, "closed": closed, "time": now
        }))
        return

    # ── Tier upgrades ─────────────────────────────────────────────────
    tier_idx, tier_floor, tier_changed, prev_tier = apply_tier_upgrades(state, upnl_pct, is_long, hw)
    phase = state["phase"]

    # ── Native TP sync (set TP at next tier target on HL) ───────────
    if not state.get("pendingClose"):
        sync_native_tp(state, now)

    # ── Pyramid: add to winners on tier upgrades ──────────────────────
    pyramid_added = False
    if tier_changed and not state.get("pendingClose"):
        pyramid_added, pyramid_result = execute_pyramid_add(state, price, upnl_pct, now)
        if pyramid_added:
            size = state["size"]  # updated
            entry = state["entryPrice"]  # may be blended now

    # ── Partial take-profit check ─────────────────────────────────────
    ptp = state.get("partialTp", {})
    ptp_executed_now = False
    if ptp.get("enabled") and not ptp.get("executed"):
        trigger_tier = ptp.get("triggerTier", 2)
        if tier_idx >= trigger_tier:
            ok, ptp_result = execute_partial_tp(state, price, now)
            if ok:
                ptp_executed_now = True
                size = state["size"]  # updated by partial TP

    # ── Compute effective floor ───────────────────────────────────────
    effective_floor, trailing_floor, breaches_needed, retrace_roe = compute_effective_floor(
        state, phase, tier_idx, tier_floor, hw, is_long
    )
    state["floorPrice"] = round(effective_floor, 4)

    # ── Verify SL order still exists ──────────────────────────────────
    last_synced = state.get("lastSyncedFloorPrice")
    if state.get("slOrderId") is not None and last_synced is not None:
        orders, _ = _mcp_get_open_orders(state.get("wallet", ""), dex)
        oids = set()
        for o in orders:
            if isinstance(o, dict) and o.get("coin") == asset:
                oid = o.get("oid")
                if oid:
                    try:
                        oids.add(int(oid))
                    except (TypeError, ValueError):
                        pass
        if state["slOrderId"] not in oids:
            state["lastSyncedFloorPrice"] = None

    # ── Sync SL to Hyperliquid ────────────────────────────────────────
    # Don't sync native SL for first 5 min — let entry settle.
    # DSL still tracks breaches in software during this window.
    min_age_for_sl_sync = 5  # minutes
    position_age_min = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            position_age_min = (now_dt - created).total_seconds() / 60
        except (ValueError, TypeError):
            position_age_min = 999  # assume old enough

    effective_floor_r = round(effective_floor, 4)
    need_sync = (
        state.get("lastSyncedFloorPrice") is None
        or abs((state.get("lastSyncedFloorPrice") or 0) - effective_floor_r) > 1e-9
    )
    sl_synced = False
    if need_sync and position_age_min >= min_age_for_sl_sync:
        ok, sl_synced, sync_err = sync_sl_to_hyperliquid(state, effective_floor, now, dex, phase)
        if not ok and sync_err:
            state["lastSlSyncError"] = sync_err

    # ── Breach detection ──────────────────────────────────────────────
    breached = price <= effective_floor if is_long else price >= effective_floor
    count = state["currentBreachCount"]
    count = count + 1 if breached else 0
    state["currentBreachCount"] = count

    force_close = state.get("pendingClose", False)
    should_close = count >= breaches_needed or force_close

    # ── Close if needed ───────────────────────────────────────────────
    closed = False
    close_result = None
    if should_close:
        reason = f"DSL v5.2: Phase {phase}, {count}/{breaches_needed} breaches, price {price}, floor {effective_floor}"
        closed, close_result = try_close_position(state, price, reason, now)
        if closed:
            notify_scanner_of_loss(upnl < 0)

    # ── Persist ───────────────────────────────────────────────────────
    state["lastCheck"] = now
    if closed:
        dest = _archived_name(state_file, "archived")
        try:
            os.rename(state_file, dest)
        except OSError:
            atomic_write(state_file, state)
    else:
        atomic_write(state_file, state)

    # ── Output ────────────────────────────────────────────────────────
    elapsed_min = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            elapsed_min = round((now_dt - created).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    tier_name = f"Tier {tier_idx+1}" if tier_idx >= 0 else "None"
    locked = round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2) if tier_floor else 0

    print(json.dumps({
        "status": "closed" if closed else ("pending_close" if state.get("pendingClose") else "active"),
        "asset": asset, "direction": direction,
        "price": price, "entry": entry,
        "upnl": round(upnl, 2), "upnl_pct": round(upnl_pct, 2),
        "phase": phase, "hw": hw,
        "floor": effective_floor, "tier": tier_name,
        "locked_profit": locked,
        "breach": f"{count}/{breaches_needed}",
        "elapsed_min": elapsed_min,
        "sl_synced": sl_synced,
        "partial_tp": ptp_executed_now,
        "pyramid_add": pyramid_added,
        "time_decay": td_reason,
        "closed": closed,
        "time": now,
        "strategy_id": strategy_id,
    }))


def _archived_name(state_file, suffix):
    epoch = int(time.time())
    base, ext = os.path.splitext(state_file)
    return f"{base}_{suffix}_{epoch}{ext}"


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    state_dir = os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = os.environ.get("DSL_STRATEGY_ID", "").strip()
    if not strategy_id:
        print(json.dumps({"status": "error", "error": "DSL_STRATEGY_ID required"}))
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Strategy check
    active, wallet, err, confirmed_inactive = get_strategy_active_and_wallet(strategy_id)
    if not active:
        if confirmed_inactive:
            deleted = cleanup_strategy_state_dir(state_dir, strategy_id)
            print(json.dumps({"status": "strategy_inactive", "strategy_id": strategy_id,
                              "reason": err, "cleaned": deleted, "time": now}))
            sys.exit(0)
        print(json.dumps({"status": "error", "error": err, "time": now}))
        sys.exit(1)

    # Check SL fills
    state_files = list_strategy_state_files(state_dir, strategy_id)
    for path, asset in list(state_files):
        try:
            with open(path) as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        oid = st.get("slOrderId")
        if oid is None:
            continue
        try:
            oid = int(oid)
        except (TypeError, ValueError):
            continue
        ok, status, _ = _mcp_get_order_status(wallet, oid)
        if ok and status == "filled":
            # SL was hit — notify scanner of loss
            notify_scanner_of_loss(True)
            dest = _archived_name(path, "archived_sl")
            try:
                os.rename(path, dest)
            except OSError:
                pass

    # Reconcile with clearinghouse
    coins, ch_err = get_active_position_coins(wallet)
    if ch_err:
        print(json.dumps({"status": "error", "error": ch_err, "time": now}))
        sys.exit(1)

    state_files = list_strategy_state_files(state_dir, strategy_id)
    for path, asset in list(state_files):
        if asset not in coins:
            # Position gone — figure out why before archiving
            suffix = "archived_external"
            try:
                with open(path) as f:
                    st = json.load(f)
                
                # Check if our native TP order filled
                tp_price = st.get("lastSyncedTpPrice")
                if tp_price is not None:
                    # TP was set by DSL — this was likely a TP fill
                    suffix = "archived_tp"
                    notify_scanner_of_loss(False)  # profitable close
                
                # Check if our SL order filled (backup check)
                sl_oid = st.get("slOrderId")
                if sl_oid is not None:
                    try:
                        ok, order_status, _ = _mcp_get_order_status(wallet, int(sl_oid))
                        if ok and order_status == "filled":
                            suffix = "archived_sl"
                            notify_scanner_of_loss(True)
                    except (TypeError, ValueError):
                        pass
            except (OSError, json.JSONDecodeError):
                pass

            dest = _archived_name(path, suffix)
            try:
                os.rename(path, dest)
            except OSError:
                pass

    # Auto-adopt orphan positions (in clearinghouse but no state file)
    state_files = list_strategy_state_files(state_dir, strategy_id)
    managed_assets = {asset for _, asset in state_files}
    for coin in coins:
        if coin not in managed_assets:
            _auto_adopt_position(state_dir, strategy_id, wallet, coin, now)

    # Process active positions
    processed = 0
    for coin in sorted(coins):
        sf, err = resolve_state_file(state_dir, strategy_id, coin)
        if err is None:
            process_one_position(sf, strategy_id, now)
            processed += 1

    if processed == 0:
        print(json.dumps({"status": "no_positions", "strategy_id": strategy_id, "time": now}))


if __name__ == "__main__":
    main()
