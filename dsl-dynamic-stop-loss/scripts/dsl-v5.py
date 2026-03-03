#!/usr/bin/env python3
"""DSL v5.1 — Strategy-scoped cron, MCP clearinghouse + price, delete on close.
Cron is per strategy (DSL_STATE_DIR + DSL_STRATEGY_ID only). Each run:
1. Check if strategy is active via MCP; if not, cleanup all state files and output strategy_inactive.
2. Get active positions from MCP clearinghouse; delete state files for positions that no longer exist.
3. For each remaining position with a state file: fetch price, update tiers, breach, close if needed, delete state on close.
Output: one JSON line per position (ndjson), or one line for strategy-level outcome (inactive / no_positions).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path & config
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = "/data/workspace/dsl"


def asset_to_filename(asset: str) -> str:
    """xyz:SILVER → xyz--SILVER (filesystem-safe)."""
    if asset.startswith("xyz:"):
        return asset.replace(":", "--", 1)
    return asset


def filename_to_asset(filename: str) -> str | None:
    """xyz--SILVER.json → xyz:SILVER; ETH.json → ETH."""
    if not filename.endswith(".json"):
        return None
    base = filename[:-5]
    if "--" in base and not base.startswith("xyz--"):
        return None
    if base.startswith("xyz--"):
        return "xyz:" + base[5:]
    return base


def resolve_state_file(state_dir: str, strategy_id: str, asset: str) -> tuple[str, str | None]:
    """Return (state_file_path, error_message). error_message is None if valid."""
    if not strategy_id or not asset:
        return "", "strategy_id and asset required"
    path = os.path.join(state_dir, strategy_id, f"{asset_to_filename(asset)}.json")
    if not os.path.isfile(path):
        return path, "state_file_not_found"
    return path, None


def list_strategy_state_files(state_dir: str, strategy_id: str) -> list[tuple[str, str]]:
    """Return list of (path, asset) for each .json in strategy dir. asset from filename."""
    out = []
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return out
    for name in os.listdir(strategy_dir):
        path = os.path.join(strategy_dir, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        asset = filename_to_asset(name)
        if asset is not None:
            out.append((path, asset))
    return out


def dex_and_lookup_symbol(asset: str) -> tuple[str, str]:
    """Return (dex, lookup_symbol) for MCP. Main dex: dex='', xyz: dex='xyz'."""
    if asset.startswith("xyz:"):
        return "xyz", asset.split(":", 1)[1]
    return "", asset


def normalize_asset_dex(asset: str, dex: str | None) -> tuple[str, str]:
    """Return (canonical_asset, dex). Canonical asset has 'xyz:' prefix for xyz dex.
    - asset xyz:SILVER → (xyz:SILVER, 'xyz'); dex arg inferred from prefix.
    - asset SILVER, dex 'xyz' → (xyz:SILVER, 'xyz').
    - asset ETH, dex '' or 'main' or None → (ETH, '').
    """
    asset = (asset or "").strip()
    dex_val = (dex or "").strip().lower() if dex else ""
    if asset.startswith("xyz:"):
        return asset, "xyz"
    if dex_val == "xyz":
        return f"xyz:{asset}", "xyz"
    return asset, ""


# ---------------------------------------------------------------------------
# Strategy & clearinghouse (MCP)
# ---------------------------------------------------------------------------

# Strategy statuses that allow DSL to run (Senpi MCP strategy_get).
DSL_ACTIVE_STATUSES = ("ACTIVE", "PAUSED")


def _unwrap_mcporter_response(stdout_str: str) -> dict | None:
    """Unwrap mcporter MCP response. May be { content: [{ type, text: '<json>' }] } or direct { success, data }.
    Returns the inner payload (parsed content[0].text or raw) for further use.
    """
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


def _mcp_strategy_get(strategy_id: str) -> tuple[dict | None, str | None]:
    """Call senpi strategy_get via mcporter. Returns (strategy dict with status, strategyWalletAddress, ...), error."""
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "strategy_get", "--args", json.dumps({"strategy_id": strategy_id})],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout)
        if not raw:
            return None, "strategy_get: invalid or empty response"
        if raw.get("success") is False:
            err = raw.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return None, msg
        data = raw.get("data") or raw
        strategy = data.get("strategy") if isinstance(data, dict) else None
        if not strategy or not isinstance(strategy, dict):
            return None, "strategy_get: no strategy in response"
        return strategy, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def get_strategy_active_and_wallet(strategy_id: str) -> tuple[bool, str | None, str | None, bool]:
    """Check if strategy is active via Senpi MCP strategy_get (not clearinghouse).
    Returns (active, wallet, message, confirmed_inactive).
    - active=True only when strategy.status is ACTIVE or PAUSED.
    - confirmed_inactive=True only when we got a successful response and strategy is not active
      (do not True on MCP/API errors — those must not trigger state cleanup).
    """
    strategy, err = _mcp_strategy_get(strategy_id)
    if err is not None:
        return False, None, err, False  # transient/API error: do not cleanup state
    status = (strategy.get("status") or "").strip().upper()
    if status not in DSL_ACTIVE_STATUSES:
        return False, None, f"strategy status is {status!r} (not ACTIVE/PAUSED)", True
    wallet = (strategy.get("strategyWalletAddress") or "").strip()
    if not wallet:
        return False, None, "strategy_get: no strategyWalletAddress", False  # data quality, not confirmed inactive
    return True, wallet, None, False


def _mcp_clearinghouse(wallet: str) -> tuple[dict | None, str | None]:
    """Call senpi strategy_get_clearinghouse_state via mcporter. Single call returns data.main + data.xyz.
    Returns (data dict with main/xyz and assetPositions, error)."""
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "strategy_get_clearinghouse_state", "--args", json.dumps({"strategy_wallet": wallet})],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout)
        if not raw:
            return None, "clearinghouse: invalid or empty response"
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        return data, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def get_active_position_coins(wallet: str) -> tuple[set[str], str | None]:
    """Get active position coins from clearinghouse. One call returns main + xyz (data.main, data.xyz)."""
    coins = set()
    data, err = _mcp_clearinghouse(wallet)
    if err is not None:
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


def get_position_from_clearinghouse(wallet: str, asset: str) -> tuple[dict | None, str | None]:
    """Get position dict for one asset from clearinghouse. Returns (position, error).
    position has entryPx, szi, coin. Error if position not found or clearinghouse failed.
    """
    data, err = _mcp_clearinghouse(wallet)
    if err is not None:
        return None, err
    for section in ("main", "xyz"):
        if not data or section not in data:
            continue
        for p in data.get(section, {}).get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin")
            if not coin:
                continue
            if coin != asset and not (asset.startswith("xyz:") and coin == asset.split(":", 1)[1]):
                continue
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            return pos, None
    return None, f"no open position for asset {asset!r}"


def cleanup_strategy_state_dir(state_dir: str, strategy_id: str) -> int:
    """Delete all .json state files in strategy dir. Return count deleted."""
    deleted = 0
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return 0
    for name in os.listdir(strategy_dir):
        path = os.path.join(strategy_dir, name)
        if name.endswith(".json") and os.path.isfile(path):
            try:
                os.remove(path)
                deleted += 1
            except OSError:
                pass
    return deleted


# ---------------------------------------------------------------------------
# Price fetch (MCP)
# ---------------------------------------------------------------------------

def _parse_price_from_response(data: dict, response_key: str) -> str | None:
    """Extract price string from MCP response (market_get_prices envelope or flat allMids).
    response_key: for main use bare symbol (e.g. ETH); for xyz use prefixed (e.g. xyz:SILVER).
    """
    if "prices" in data:
        return data["prices"].get(response_key)
    return data.get(response_key)


def _unwrap_mcp_response(raw: dict) -> dict | None:
    """Unwrap MCP envelope if present: { success, data: { prices, ... } } -> { prices, ... }."""
    if not raw or not isinstance(raw, dict):
        return None
    if "data" in raw and isinstance(raw.get("data"), dict):
        return raw["data"]
    return raw


def fetch_price_mcp(dex: str, lookup_symbol: str) -> tuple[float | None, str | None]:
    """Fetch mid price via MCP only: senpi market_get_prices then allMids fallback.
    MCP expects dex '' for main (passing 'main' causes INTERNAL error). xyz response uses keys like xyz:SILVER.
    """
    try:
        dex = dex.strip() if dex else ""
        if dex.lower() == "main":
            dex = ""
        is_xyz = dex.lower() == "xyz"
        response_key = f"xyz:{lookup_symbol}" if is_xyz else lookup_symbol

        args_mgp = {"assets": [response_key], "dex": dex}
        r = subprocess.run(
            ["mcporter", "call", "senpi", "market_get_prices", "--args", json.dumps(args_mgp)],
            capture_output=True, text=True, timeout=15,
        )
        data = None
        if r.returncode == 0 and r.stdout:
            raw = _unwrap_mcporter_response(r.stdout)
            if raw is not None:
                data = _unwrap_mcp_response(raw)
        price_str = _parse_price_from_response(data, response_key) if data else None

        if price_str is None:
            args_am = {"dex": dex} if dex else {}
            r = subprocess.run(
                ["mcporter", "call", "senpi", "allMids", "--args", json.dumps(args_am)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout:
                raw = _unwrap_mcporter_response(r.stdout)
                if raw is not None:
                    data = _unwrap_mcp_response(raw)
                    price_str = _parse_price_from_response(data, response_key)
            elif r.returncode != 0 and data is None:
                return None, (r.stderr or r.stdout or "non-zero exit")

        if price_str is None:
            return None, f"no price for {lookup_symbol} (dex={dex or 'main'})"
        return float(price_str), None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)
    except (TypeError, ValueError) as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# State normalization (backfill missing phase1/phase2 for older state files)
# ---------------------------------------------------------------------------

# Schema defaults for phase config (see references/state-schema.md)
DEFAULT_PHASE1_RETRACE = 0.03
DEFAULT_PHASE1_BREACHES = 1
DEFAULT_PHASE2_RETRACE = 0.015
DEFAULT_PHASE2_BREACHES = 1

DEFAULT_TIERS = [
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
    {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
    {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
    {"triggerPct": 100, "lockPct": 80, "retrace": 0.006},
]


def normalize_state_phase_config(state: dict) -> bool:
    """Ensure phase1 and phase2 exist with required fields. Backfills missing keys from schema defaults.
    Allows older state files (e.g. missing phase2) to run without KeyError.
    Returns True if any keys were backfilled (caller may persist state file).
    """
    changed = False
    if "phase1" not in state or not isinstance(state["phase1"], dict):
        state["phase1"] = {}
        changed = True
    p1 = state["phase1"]
    if "retraceThreshold" not in p1:
        p1["retraceThreshold"] = DEFAULT_PHASE1_RETRACE
        changed = True
    if "consecutiveBreachesRequired" not in p1:
        p1["consecutiveBreachesRequired"] = DEFAULT_PHASE1_BREACHES
        changed = True
    if "absoluteFloor" not in p1 or p1["absoluteFloor"] is None:
        entry = state.get("entryPrice")
        lev = max(1, state.get("leverage", 1))
        is_long = (state.get("direction", "LONG").upper() == "LONG")
        if entry is not None:
            if is_long:
                p1["absoluteFloor"] = round(entry * (1 - 0.03 / lev), 4)
            else:
                p1["absoluteFloor"] = round(entry * (1 + 0.03 / lev), 4)
        else:
            p1["absoluteFloor"] = 0.0
        changed = True

    if "phase2" not in state or not isinstance(state["phase2"], dict):
        state["phase2"] = {}
        changed = True
    p2 = state["phase2"]
    if "retraceThreshold" not in p2:
        p2["retraceThreshold"] = DEFAULT_PHASE2_RETRACE
        changed = True
    if "consecutiveBreachesRequired" not in p2:
        p2["consecutiveBreachesRequired"] = DEFAULT_PHASE2_BREACHES
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Trading logic: high water, tiers, floor, breach
# ---------------------------------------------------------------------------

def update_high_water(state: dict, price: float, is_long: bool) -> float:
    """Update state high water; return new hw."""
    hw = state["highWaterPrice"]
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw
    return hw


def apply_tier_upgrades(
    state: dict, upnl_pct: float, is_long: bool, hw: float
) -> tuple[int, float | None, bool, int]:
    """Apply tier upgrades based on ROE. Mutates state. Returns (tier_idx, tier_floor, tier_changed, previous_tier_idx).
    Tier floor = entry + (hw - entry) * lockPct/100 (LONG) or entry - (entry - hw) * lockPct/100 (SHORT):
    lockPct is the fraction of the entry→hw range to lock, not ROE %.
    """
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
            # Floor = entry + fraction of (entry → hw) range; lockPct = that fraction
            if is_long:
                tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
            else:
                tier_floor = round(entry - (entry - hw) * tier["lockPct"] / 100, 4)
            # Ratchet: never regress vs stored (e.g. v4 ROE-based floor may be higher for LONG)
            stored = state.get("tierFloorPrice")
            if stored is not None and isinstance(stored, (int, float)):
                if is_long:
                    tier_floor = max(tier_floor, float(stored))
                else:
                    tier_floor = min(tier_floor, float(stored))
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1 and tier_idx >= state.get("phase2TriggerTier", 0):
                state["phase"] = 2
                breach_count = 0
                state["currentBreachCount"] = 0
                phase = 2

    return tier_idx, tier_floor, tier_changed, previous_tier_idx


def compute_effective_floor(
    state: dict, phase: int, tier_idx: int, tier_floor: float | None, hw: float, is_long: bool
) -> tuple[float, float, int, float]:
    """Return (effective_floor, trailing_floor, breaches_needed, retrace).
    Retrace is stored as ROE fraction (e.g. 0.03 = 3% ROE); we convert to price via / leverage
    so that 3% means 3% ROE, not 3% price (which would be 30% ROE at 10x).
    """
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
        if tier_idx >= 0
        else state["phase2"]["retraceThreshold"]
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


def update_breach_count(state: dict, breached: bool, decay_mode: str) -> int:
    """Update state currentBreachCount; return new count."""
    count = state["currentBreachCount"]
    if breached:
        count += 1
    else:
        count = max(0, count - 1) if decay_mode == "soft" else 0
    state["currentBreachCount"] = count
    return count


# ---------------------------------------------------------------------------
# Edit position (sync SL) and open orders (MCP)
# ---------------------------------------------------------------------------

def _mcp_edit_position(
    wallet: str, coin: str, stop_loss_price: float, order_type: str = "LIMIT"
) -> tuple[bool, str | None, int | None]:
    """Call senpi edit_position to set/update SL at price. Returns (success, error_message, sl_order_id_from_response).
    sl_order_id_from_response is None if API does not return it (use strategy_get_open_orders to resolve).
    """
    args = {
        "strategyWalletAddress": wallet,
        "coin": coin,
        "stopLoss": {"price": round(stop_loss_price, 4), "orderType": order_type},
    }
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "edit_position", "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=30,
        )
        raw = _unwrap_mcporter_response(r.stdout) if r.stdout else None
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "non-zero exit"), None
        if not raw or not isinstance(raw, dict):
            return False, "edit_position: invalid or empty response", None
        if raw.get("success") is False:
            err = raw.get("error", {})
            msg = err.get("message", err.get("description", str(err))) if isinstance(err, dict) else str(err)
            return False, msg, None
        # MCP EditPosition returns data.ordersUpdated.stopLoss.orderId (or data = raw when unwrapped)
        data = raw.get("data") or raw
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
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, str(e), None


def _mcp_strategy_get_open_orders(wallet: str, dex: str = "") -> tuple[list[dict], str | None]:
    """Call senpi strategy_get_open_orders. Returns (list of orders with oid, coin, triggerPx, etc.), error.
    dex must match the position's dex (same as for market price): '' for main, 'xyz' for xyz assets."""
    args = {"strategy_wallet": wallet, "dex": dex}
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "strategy_get_open_orders", "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return [], (r.stderr or r.stdout or "non-zero exit")
        raw = _unwrap_mcporter_response(r.stdout) if r.stdout else None
        if not raw or not isinstance(raw, dict):
            return [], "strategy_get_open_orders: invalid or empty response"
        data = raw.get("data") or raw
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            return [], None
        return orders, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return [], str(e)


def _resolve_sl_order_id_after_edit(
    wallet: str, dex: str, coin: str, trigger_price: float, orders: list[dict]
) -> int | None:
    """From strategy_get_open_orders list, find the SL order for this coin matching trigger_price. Return oid or None."""
    for o in orders:
        if not isinstance(o, dict):
            continue
        o_coin = o.get("coin")
        if o_coin != coin:
            continue
        if not o.get("isTrigger", False) and not o.get("isPositionTpsl", False):
            continue
        try:
            tp = float(o.get("triggerPx", 0))
        except (TypeError, ValueError):
            continue
        if abs(tp - trigger_price) < 1e-6:
            oid = o.get("oid")
            if oid is not None:
                try:
                    return int(oid)
                except (TypeError, ValueError):
                    pass
    return None


def sync_sl_to_hyperliquid(
    state: dict,
    effective_floor: float,
    now: str,
    dex: str,
) -> tuple[bool, bool, str | None]:
    """Set or update SL on Hyperliquid at effective_floor via edit_position. Resolve slOrderId via open orders if needed.
    Mutates state: slOrderId, lastSyncedFloorPrice, slOrderIdUpdatedAt.
    Returns (success, sl_synced_this_tick, error_message)."""
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        return False, False, "no wallet in state"

    success, err, oid_from_api = _mcp_edit_position(
        wallet, coin, effective_floor, order_type="LIMIT"
    )
    if not success:
        return False, False, err

    oid = oid_from_api
    if oid is None:
        orders, orders_err = _mcp_strategy_get_open_orders(wallet, dex)
        if orders_err:
            return False, False, f"edit_ok_but_resolve_failed: {orders_err}"
        # Use rounded price to match what was sent to Hyperliquid (round(..., 4))
        oid = _resolve_sl_order_id_after_edit(wallet, dex, coin, round(effective_floor, 4), orders)

    state["lastSyncedFloorPrice"] = round(effective_floor, 4)
    state["slOrderIdUpdatedAt"] = now
    if oid is not None:
        state["slOrderId"] = oid
    return True, True, None


# ---------------------------------------------------------------------------
# Close position (MCP)
# ---------------------------------------------------------------------------

def try_close_position(
    state: dict,
    price: float,
    phase: int,
    breach_count: int,
    breaches_needed: int,
    effective_floor: float,
    now: str,
    close_retries: int,
    close_retry_delay: float,
) -> tuple[bool, str | None]:
    """Attempt close via senpi:close_position. Mutates state. Returns (closed, close_result)."""
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        state["pendingClose"] = True
        return False, "error: no wallet in state file"

    reason = (
        f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, "
        f"price {price}, floor {effective_floor}"
    )
    for attempt in range(close_retries):
        try:
            cr = subprocess.run(
                ["mcporter", "call", "senpi", "close_position", "--args",
                 json.dumps({"strategyWalletAddress": wallet, "coin": coin, "reason": reason})],
                capture_output=True, text=True, timeout=30,
            )
            result_text = cr.stdout.strip()
            if cr.returncode == 0 and "error" not in result_text.lower():
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = f"DSL breach: Phase {phase}, price {price}, floor {effective_floor}"
                return True, result_text
            close_result = f"api_error_attempt_{attempt+1}: {result_text}"
        except Exception as e:
            close_result = f"error_attempt_{attempt+1}: {str(e)}"
        if attempt < close_retries - 1:
            time.sleep(close_retry_delay)
    state["pendingClose"] = True
    return False, close_result


# ---------------------------------------------------------------------------
# Persist: save or delete state file
# ---------------------------------------------------------------------------

def save_or_delete_state(
    state: dict, state_file: str, closed: bool, now: str, close_result: str | None
) -> str | None:
    """Persist state (save or delete file). Caller must set state['lastPrice'] before calling.
    If closed but delete fails, writes state (active: False) so cleanup can proceed later.
    """
    state["lastCheck"] = now
    if closed:
        try:
            os.remove(state_file)
            return close_result
        except OSError as e:
            # Fallback: write state so dsl-cleanup sees active: false and can clean strategy
            try:
                with open(state_file, "w") as f:
                    json.dump(state, f, indent=2)
            except OSError:
                pass
            return (close_result or "") + f"; delete_failed: {e}"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
    return close_result


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------

def build_output(
    state: dict,
    *,
    price: float,
    direction: str,
    upnl: float,
    upnl_pct: float,
    phase: int,
    hw: float,
    effective_floor: float,
    trailing_floor: float,
    tier_floor: float | None,
    tier_idx: int,
    tiers: list,
    tier_changed: bool,
    previous_tier_idx: int,
    breach_count: int,
    breaches_needed: int,
    breached: bool,
    should_close: bool,
    closed: bool,
    close_result: str | None,
    now: str,
    sl_synced: bool = False,
    sl_initial_sync: bool = False,
) -> dict:
    """Build the single JSON object printed to stdout."""
    is_long = direction == "LONG"
    entry = state["entryPrice"]
    size = state["size"]

    retrace_from_hw = (
        (1 - price / hw) * 100 if hw > 0 else 0
    ) if is_long else (
        (price / hw - 1) * 100 if hw > 0 else 0
    )
    tier_name = (
        f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%→lock {tiers[tier_idx]['lockPct']}%)"
        if tier_idx >= 0 else "None"
    )
    previous_tier_name = None
    if tier_changed:
        if previous_tier_idx >= 0:
            t = tiers[previous_tier_idx]
            previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']}%→lock {t['lockPct']}%)"
        else:
            previous_tier_name = "None (Phase 1)"
    locked_profit = (
        round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)
        if tier_floor else 0
    )
    elapsed_minutes = 0
    if state.get("createdAt"):
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - created).total_seconds() / 60)
        except (ValueError, TypeError):
            pass
    distance_to_next_tier = None
    if tier_idx + 1 < len(tiers):
        distance_to_next_tier = round(tiers[tier_idx + 1]["triggerPct"] - upnl_pct, 2)

    status = "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active")
    out = {
        "status": status,
        "asset": state["asset"],
        "direction": direction,
        "price": price,
        "upnl": round(upnl, 2),
        "upnl_pct": round(upnl_pct, 2),
        "phase": phase,
        "hw": hw,
        "floor": effective_floor,
        "trailing_floor": trailing_floor,
        "tier_floor": tier_floor,
        "tier_name": tier_name,
        "locked_profit": locked_profit,
        "retrace_pct": round(retrace_from_hw, 2),
        "breach_count": breach_count,
        "breaches_needed": breaches_needed,
        "breached": breached,
        "should_close": should_close,
        "closed": closed,
        "close_result": close_result,
        "time": now,
        "tier_changed": tier_changed,
        "previous_tier": previous_tier_name,
        "elapsed_minutes": elapsed_minutes,
        "distance_to_next_tier_pct": distance_to_next_tier,
        "pending_close": state.get("pendingClose", False),
        "consecutive_failures": state.get("consecutiveFetchFailures", 0),
        "sl_synced": sl_synced,
        "sl_initial_sync": sl_initial_sync,
        "sl_order_id": state.get("slOrderId"),
    }
    out["preset"] = state.get("preset", "default")
    return out


# ---------------------------------------------------------------------------
# Per-position run
# ---------------------------------------------------------------------------

def process_one_position(state_file: str, strategy_id: str, now: str) -> None:
    """Load state, fetch price, update tiers/breach, close if needed, save or delete, print one JSON line."""
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        print(json.dumps({
            "status": "error", "error": "state_file_read_failed", "path": state_file,
            "strategy_id": strategy_id, "time": now,
        }))
        return

    if normalize_state_phase_config(state):
        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass

    if not state.get("active") and not state.get("pendingClose"):
        print(json.dumps({"status": "inactive", "asset": state.get("asset"), "strategy_id": strategy_id, "time": now}))
        return

    direction = state.get("direction", "LONG").upper()
    is_long = direction == "LONG"
    asset = state["asset"]
    dex, lookup_symbol = dex_and_lookup_symbol(asset)

    price, fetch_error = fetch_price_mcp(dex, lookup_symbol)
    if fetch_error is not None:
        fails = state.get("consecutiveFetchFailures", 0) + 1
        state["consecutiveFetchFailures"] = fails
        state["lastCheck"] = now
        max_failures = state.get("maxFetchFailures", 10)
        if fails >= max_failures:
            state["active"] = False
            state["closeReason"] = f"Auto-deactivated: {fails} consecutive fetch failures"
        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass
        print(json.dumps({
            "status": "error",
            "error": f"price_fetch_failed: {fetch_error}",
            "asset": state.get("asset"),
            "strategy_id": strategy_id,
            "consecutive_failures": fails,
            "deactivated": fails >= max_failures,
            "pending_close": state.get("pendingClose", False),
            "time": now,
        }))
        return

    state["consecutiveFetchFailures"] = 0
    state["lastPrice"] = price
    entry = state["entryPrice"]
    size = state["size"]
    leverage = state["leverage"]
    hw = update_high_water(state, price, is_long)

    upnl = (price - entry) * size if is_long else (entry - price) * size
    margin = state.get("margin")
    if margin is None or margin <= 0:
        margin = entry * size / leverage
    upnl_pct = upnl / margin * 100

    tier_idx, tier_floor, tier_changed, previous_tier_idx = apply_tier_upgrades(
        state, upnl_pct, is_long, hw
    )
    phase = state["phase"]
    breach_count = state["currentBreachCount"]

    effective_floor, trailing_floor, breaches_needed, _ = compute_effective_floor(
        state, phase, tier_idx, tier_floor, hw, is_long
    )
    state["floorPrice"] = round(effective_floor, 4)

    # Optional: if we have slOrderId, verify it still exists on HL; if not, re-sync (SL cancelled externally)
    last_synced = state.get("lastSyncedFloorPrice")
    if state.get("slOrderId") is not None and last_synced is not None:
        orders, _ = _mcp_strategy_get_open_orders(state.get("wallet", ""), dex)
        oids_for_coin = []
        for o in orders:
            if not isinstance(o, dict) or o.get("coin") != asset:
                continue
            oid = o.get("oid")
            if oid is not None:
                try:
                    oids_for_coin.append(int(oid))
                except (TypeError, ValueError):
                    pass  # ignore non-int OIDs
        if state["slOrderId"] not in oids_for_coin:
            state["lastSyncedFloorPrice"] = None  # force sync below

    had_sl_order_before = state.get("slOrderId") is not None
    effective_floor_rounded = round(effective_floor, 4)
    # Sync SL to Hyperliquid: never synced or floor changed (compare rounded to match stored lastSyncedFloorPrice)
    need_sync = (
        state.get("lastSyncedFloorPrice") is None
        or abs((state.get("lastSyncedFloorPrice") or 0) - effective_floor_rounded) > 1e-9
    )
    sl_synced_this_tick = False
    if need_sync:
        sync_ok, sl_synced_this_tick, sync_err = sync_sl_to_hyperliquid(state, effective_floor, now, dex)
        if not sync_ok and sync_err:
            # Log but continue; backup close on breach still available
            state["lastSlSyncError"] = sync_err
    sl_initial_sync = sl_synced_this_tick and not had_sl_order_before

    breached = price <= effective_floor if is_long else price >= effective_floor
    breach_count = update_breach_count(state, breached, state.get("breachDecay", "hard"))
    force_close = state.get("pendingClose", False)
    should_close = breach_count >= breaches_needed or force_close

    closed = False
    close_result = None
    if should_close:
        closed, close_result = try_close_position(
            state, price, phase, breach_count, breaches_needed, effective_floor, now,
            state.get("closeRetries", 2), state.get("closeRetryDelaySec", 3),
        )

    close_result = save_or_delete_state(state, state_file, closed, now, close_result)

    out = build_output(
        state,
        price=price,
        direction=direction,
        upnl=upnl,
        upnl_pct=upnl_pct,
        phase=phase,
        hw=hw,
        effective_floor=effective_floor,
        trailing_floor=trailing_floor,
        tier_floor=tier_floor,
        tier_idx=tier_idx,
        tiers=state["tiers"],
        tier_changed=tier_changed,
        previous_tier_idx=previous_tier_idx,
        breach_count=breach_count,
        breaches_needed=breaches_needed,
        breached=breached,
        should_close=should_close,
        closed=closed,
        close_result=close_result,
        now=now,
        sl_synced=sl_synced_this_tick,
        sl_initial_sync=sl_initial_sync,
    )
    out["strategy_id"] = strategy_id
    print(json.dumps(out))


# ---------------------------------------------------------------------------
# CLI subcommands (add-dsl, update-dsl, pause-dsl, resume-dsl, delete-dsl, status-dsl)
# ---------------------------------------------------------------------------

SUBCOMMANDS = {"add-dsl", "update-dsl", "pause-dsl", "resume-dsl", "delete-dsl", "status-dsl"}
CRON_SCHEDULE = "*/3 * * * *"


def _deep_merge_config(base: dict, override: dict, configurable_keys: set) -> None:
    """Merge override into base for configurable keys. phase1/phase2 deep-merge; tiers full replace."""
    for key in list(override.keys()):
        if key not in configurable_keys:
            continue
        val = override[key]
        if key == "tiers":
            if isinstance(val, list):
                base[key] = list(val)
            continue
        if key in ("phase1", "phase2") and isinstance(val, dict):
            if key not in base or not isinstance(base[key], dict):
                base[key] = {}
            for k, v in val.items():
                base[key][k] = v
            continue
        base[key] = val


def _add_dsl_build_state(
    asset: str,
    direction: str,
    leverage: float,
    margin: float,
    entry_price: float,
    size: float,
    wallet: str,
    strategy_id: str,
    preset: str,
    config_json: dict | None,
) -> dict:
    """Build state dict for add-dsl. Uses defaults and optional --config merge."""
    configurable = {
        "phase1", "phase2", "phase2TriggerTier", "tiers",
        "breachDecay", "closeRetries", "closeRetryDelaySec", "maxFetchFailures",
    }
    state = {
        "phase1": {
            "retraceThreshold": DEFAULT_PHASE1_RETRACE,
            "consecutiveBreachesRequired": DEFAULT_PHASE1_BREACHES,
            "absoluteFloor": 0.0,
        },
        "phase2": {
            "retraceThreshold": DEFAULT_PHASE2_RETRACE,
            "consecutiveBreachesRequired": DEFAULT_PHASE2_BREACHES,
        },
        "phase2TriggerTier": 0,
        "tiers": list(DEFAULT_TIERS),
        "breachDecay": "hard",
        "closeRetries": 2,
        "closeRetryDelaySec": 3,
        "maxFetchFailures": 10,
    }
    if config_json and isinstance(config_json, dict):
        _deep_merge_config(state, config_json, configurable)
    lev = max(1, leverage)
    is_long = direction.upper() == "LONG"
    retrace_roe = state["phase1"]["retraceThreshold"]
    if is_long:
        abs_floor = round(entry_price * (1 - retrace_roe / lev), 4)
    else:
        abs_floor = round(entry_price * (1 + retrace_roe / lev), 4)
    state["phase1"]["absoluteFloor"] = abs_floor
    state["active"] = True
    state["asset"] = asset
    state["direction"] = direction.upper()
    state["leverage"] = lev
    state["entryPrice"] = round(entry_price, 4)
    state["size"] = round(size, 4)
    state["margin"] = round(margin, 4)
    state["wallet"] = wallet
    state["strategyId"] = strategy_id
    state["preset"] = preset or "default"
    state["phase"] = 1
    state["highWaterPrice"] = round(entry_price, 4)
    state["floorPrice"] = abs_floor
    state["currentTierIndex"] = -1
    state["tierFloorPrice"] = None
    state["currentBreachCount"] = 0
    state["consecutiveFetchFailures"] = 0
    state["pendingClose"] = False
    state["lastCheck"] = None
    state["lastPrice"] = None
    state["createdAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return state


def cmd_add_dsl() -> None:
    parser = argparse.ArgumentParser(prog="dsl-v5.py add-dsl")
    parser.add_argument("preset", nargs="?", default="default", help="Preset name (e.g. dsl-tight)")
    parser.add_argument("--asset", required=True, help="Ticker: ETH (main) or xyz:SILVER (xyz) or SILVER with --dex xyz")
    parser.add_argument("--dex", type=str, default=None, help="DEX: main or '' for main, xyz for xyz. Required when asset has no prefix and position is on xyz (e.g. --asset SILVER --dex xyz). Inferred from xyz: prefix when omitted.")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"], help="LONG or SHORT")
    parser.add_argument("--leverage", type=float, required=True, help="Leverage (positive)")
    parser.add_argument("--margin", type=float, required=True, help="Position margin (collateral) in quote units; used for ROE. Must be > 0.")
    parser.add_argument("--config", type=str, default=None, help="Optional JSON config override")
    parser.add_argument("--state-dir", type=str, default=None, help="State directory (default: env or /data/workspace/dsl)")
    parser.add_argument("--strategy-id", type=str, default=None, help="Strategy ID (default: DSL_STRATEGY_ID)")
    args = parser.parse_args(sys.argv[2:])
    asset, _dex = normalize_asset_dex(args.asset, args.dex)
    state_dir = args.state_dir or os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "")).strip()
    if not strategy_id:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "strategy_id_required", "message": "Set --strategy-id or DSL_STRATEGY_ID"}))
        sys.exit(1)
    if args.leverage <= 0:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "invalid_leverage", "message": "leverage must be > 0"}))
        sys.exit(1)
    if args.margin <= 0:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "invalid_margin", "message": "margin must be > 0"}))
        sys.exit(1)
    config_json = None
    if args.config:
        try:
            config_json = json.loads(args.config)
            if not isinstance(config_json, dict):
                config_json = None
        except json.JSONDecodeError as e:
            print(json.dumps({"action": "add-dsl", "status": "error", "error": "invalid_config_json", "message": str(e)}))
            sys.exit(1)
    active, wallet, err, _ = get_strategy_active_and_wallet(strategy_id)
    if not active:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "strategy_get_failed", "message": err or "Strategy not active or not found"}))
        sys.exit(1)
    pos, pos_err = get_position_from_clearinghouse(wallet, asset)
    if pos is None:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "position_not_found", "message": pos_err, "asset": asset}))
        sys.exit(1)
    try:
        entry_price = float(pos.get("entryPx", 0))
        szi = float(pos.get("szi", 0))
        size = abs(szi)
    except (TypeError, ValueError):
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "invalid_position_data", "message": "entryPx/szi missing or invalid"}))
        sys.exit(1)
    if entry_price <= 0 or size <= 0:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "invalid_position_data", "message": "entryPx and size must be > 0"}))
        sys.exit(1)
    state = _add_dsl_build_state(
        asset, args.direction.upper(), args.leverage, args.margin,
        entry_price, size, wallet, strategy_id, args.preset, config_json,
    )
    state_file = os.path.join(state_dir, strategy_id, f"{asset_to_filename(asset)}.json")
    if os.path.isfile(state_file):
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "state_file_exists", "message": "State file already exists; overwrite not allowed", "state_file": state_file}))
        sys.exit(1)
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        print(json.dumps({"action": "add-dsl", "status": "error", "error": "write_failed", "message": str(e), "state_file": state_file}))
        sys.exit(1)
    existing = list_strategy_state_files(state_dir, strategy_id)
    is_first = len(existing) == 1
    out = {
        "action": "add-dsl",
        "status": "ok",
        "preset": state["preset"],
        "asset": asset,
        "strategy_id": strategy_id,
        "state_file": state_file,
        "is_first_position_for_strategy": is_first,
        "cron_needed": is_first,
        "cron_env": {"DSL_STATE_DIR": state_dir, "DSL_STRATEGY_ID": strategy_id, "DSL_PRESET": state["preset"]},
        "cron_schedule": CRON_SCHEDULE,
    }
    print(json.dumps(out))


def cmd_update_dsl() -> None:
    parser = argparse.ArgumentParser(prog="dsl-v5.py update-dsl")
    parser.add_argument("--asset", type=str, default=None, help="Omit to update all positions in strategy. Use xyz:SILVER or SILVER with --dex xyz for xyz.")
    parser.add_argument("--dex", type=str, default=None, help="DEX when asset has no prefix (e.g. --asset SILVER --dex xyz)")
    parser.add_argument("--config", type=str, required=True, help="JSON config to merge")
    parser.add_argument("--state-dir", type=str, default=None)
    parser.add_argument("--strategy-id", type=str, default=None)
    args = parser.parse_args(sys.argv[2:])
    state_dir = args.state_dir or os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "")).strip()
    if not strategy_id:
        print(json.dumps({"action": "update-dsl", "status": "error", "error": "strategy_id_required"}))
        sys.exit(1)
    try:
        config_json = json.loads(args.config)
        if not isinstance(config_json, dict):
            raise ValueError("config must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        print(json.dumps({"action": "update-dsl", "status": "error", "error": "invalid_config", "message": str(e)}))
        sys.exit(1)
    configurable = {"phase1", "phase2", "phase2TriggerTier", "tiers", "breachDecay", "closeRetries", "closeRetryDelaySec", "maxFetchFailures"}
    files = list_strategy_state_files(state_dir, strategy_id)
    if args.asset:
        canonical_asset, _ = normalize_asset_dex(args.asset, args.dex)
        files = [(p, a) for p, a in files if a == canonical_asset]
    if not files:
        print(json.dumps({"action": "update-dsl", "status": "error", "error": "no_state_files", "message": "No matching state file(s)"}))
        sys.exit(1)
    updated = 0
    for path, asset in files:
        try:
            with open(path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        _deep_merge_config(state, config_json, configurable)
        if "phase1" in config_json and "absoluteFloor" not in config_json.get("phase1", {}):
            entry = state.get("entryPrice")
            lev = max(1, state.get("leverage", 1))
            is_long = (state.get("direction", "LONG").upper() == "LONG")
            if entry is not None:
                retrace = state["phase1"].get("retraceThreshold", DEFAULT_PHASE1_RETRACE)
                if is_long:
                    state["phase1"]["absoluteFloor"] = round(entry * (1 - retrace / lev), 4)
                else:
                    state["phase1"]["absoluteFloor"] = round(entry * (1 + retrace / lev), 4)
        try:
            with open(path, "w") as f:
                json.dump(state, f, indent=2)
            updated += 1
        except OSError:
            pass
    print(json.dumps({"action": "update-dsl", "status": "ok", "strategy_id": strategy_id, "updated_count": updated}))


def _cmd_pause_resume_dsl(active: bool) -> None:
    name = "resume-dsl" if active else "pause-dsl"
    parser = argparse.ArgumentParser(prog=f"dsl-v5.py {name}")
    parser.add_argument("--asset", type=str, default=None, help="e.g. ETH or xyz:SILVER or SILVER with --dex xyz")
    parser.add_argument("--dex", type=str, default=None, help="DEX when asset has no prefix")
    parser.add_argument("--state-dir", type=str, default=None)
    parser.add_argument("--strategy-id", type=str, default=None)
    args = parser.parse_args(sys.argv[2:])
    state_dir = args.state_dir or os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "")).strip()
    if not strategy_id:
        print(json.dumps({"action": name, "status": "error", "error": "strategy_id_required"}))
        sys.exit(1)
    files = list_strategy_state_files(state_dir, strategy_id)
    if args.asset:
        canonical_asset, _ = normalize_asset_dex(args.asset, args.dex)
        files = [(p, a) for p, a in files if a == canonical_asset]
    for path, _ in files:
        try:
            with open(path) as f:
                state = json.load(f)
            state["active"] = active
            with open(path, "w") as f:
                json.dump(state, f, indent=2)
        except (OSError, json.JSONDecodeError):
            pass
    print(json.dumps({"action": name, "status": "ok", "strategy_id": strategy_id, "active": active}))


def cmd_delete_dsl() -> None:
    parser = argparse.ArgumentParser(prog="dsl-v5.py delete-dsl")
    parser.add_argument("--asset", type=str, default=None, help="e.g. ETH or xyz:SILVER or SILVER with --dex xyz")
    parser.add_argument("--dex", type=str, default=None, help="DEX when asset has no prefix")
    parser.add_argument("--state-dir", type=str, default=None)
    parser.add_argument("--strategy-id", type=str, default=None)
    args = parser.parse_args(sys.argv[2:])
    state_dir = args.state_dir or os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "")).strip()
    if not strategy_id:
        print(json.dumps({"action": "delete-dsl", "status": "error", "error": "strategy_id_required"}))
        sys.exit(1)
    files = list_strategy_state_files(state_dir, strategy_id)
    if args.asset:
        canonical_asset, _ = normalize_asset_dex(args.asset, args.dex)
        files = [(p, a) for p, a in files if a == canonical_asset]
    deleted = 0
    for path, _ in files:
        try:
            os.remove(path)
            deleted += 1
        except OSError:
            pass
    strategy_dir = os.path.join(state_dir, strategy_id)
    if os.path.isdir(strategy_dir) and not os.listdir(strategy_dir):
        try:
            os.rmdir(strategy_dir)
        except OSError:
            pass
    print(json.dumps({"action": "delete-dsl", "status": "ok", "strategy_id": strategy_id, "deleted_count": deleted}))


def cmd_status_dsl() -> None:
    parser = argparse.ArgumentParser(prog="dsl-v5.py status-dsl")
    parser.add_argument("--asset", type=str, default=None, help="e.g. ETH or xyz:SILVER or SILVER with --dex xyz")
    parser.add_argument("--dex", type=str, default=None, help="DEX when asset has no prefix")
    parser.add_argument("--state-dir", type=str, default=None)
    parser.add_argument("--strategy-id", type=str, default=None)
    args = parser.parse_args(sys.argv[2:])
    state_dir = args.state_dir or os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "")).strip()
    if not strategy_id:
        print(json.dumps({"action": "status-dsl", "status": "error", "error": "strategy_id_required"}))
        sys.exit(1)
    files = list_strategy_state_files(state_dir, strategy_id)
    if args.asset:
        canonical_asset, _ = normalize_asset_dex(args.asset, args.dex)
        files = [(p, a) for p, a in files if a == canonical_asset]
    if not files:
        print(json.dumps({"action": "status-dsl", "status": "ok", "strategy_id": strategy_id, "positions": []}))
        return
    for path, asset in files:
        try:
            with open(path) as f:
                state = json.load(f)
            if args.asset:
                print(json.dumps(state, indent=2))
            else:
                print(json.dumps(state))
        except (OSError, json.JSONDecodeError):
            pass


# ---------------------------------------------------------------------------
# Main (strategy-scoped monitor)
# ---------------------------------------------------------------------------

def main_monitor() -> None:
    state_dir = os.environ.get("DSL_STATE_DIR", DEFAULT_STATE_DIR)
    strategy_id = os.environ.get("DSL_STRATEGY_ID", "").strip()
    if not strategy_id:
        print(json.dumps({"status": "error", "error": "DSL_STRATEGY_ID required", "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Strategy active? From Senpi MCP strategy_get (not clearinghouse).
    active, wallet, active_error, confirmed_inactive = get_strategy_active_and_wallet(strategy_id)
    if not active:
        if confirmed_inactive:
            deleted = cleanup_strategy_state_dir(state_dir, strategy_id)
            dsl_preset = os.environ.get("DSL_PRESET", "") or "default"
            print(json.dumps({
                "status": "strategy_inactive",
                "strategy_id": strategy_id,
                "preset": dsl_preset,
                "message": "Strategy not active (Senpi MCP). State files cleaned. Agent: remove cron for this strategy.",
                "reason": active_error,
                "state_files_deleted": deleted,
                "time": now,
            }))
            sys.exit(0)
        else:
            dsl_preset = os.environ.get("DSL_PRESET", "") or "default"
            print(json.dumps({
                "status": "error",
                "error": "strategy_get_failed",
                "strategy_id": strategy_id,
                "preset": dsl_preset,
                "message": active_error,
                "time": now,
            }))
            sys.exit(1)

    # 2. Active positions from clearinghouse.
    coins, ch_error = get_active_position_coins(wallet)
    if ch_error is not None:
        dsl_preset = os.environ.get("DSL_PRESET", "") or "default"
        print(json.dumps({
            "status": "error",
            "error": "clearinghouse_failed",
            "strategy_id": strategy_id,
            "preset": dsl_preset,
            "message": ch_error,
            "time": now,
        }))
        sys.exit(1)

    state_files = list_strategy_state_files(state_dir, strategy_id)
    for path, asset in list(state_files):
        if asset not in coins:
            try:
                os.remove(path)
            except OSError:
                pass

    processed = 0
    for coin in sorted(coins):
        state_file, path_error = resolve_state_file(state_dir, strategy_id, coin)
        if path_error is None:
            process_one_position(state_file, strategy_id, now)
            processed += 1

    if processed == 0:
        dsl_preset = os.environ.get("DSL_PRESET", "") or "default"
        print(json.dumps({
            "status": "no_positions",
            "strategy_id": strategy_id,
            "preset": dsl_preset,
            "message": "Strategy active but no position state files to process. Agent: keep cron; next run may have positions or output strategy_inactive after cleanup.",
            "time": now,
        }))


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in SUBCOMMANDS:
        cmd = sys.argv[1]
        if cmd == "add-dsl":
            cmd_add_dsl()
        elif cmd == "update-dsl":
            cmd_update_dsl()
        elif cmd == "pause-dsl":
            _cmd_pause_resume_dsl(active=False)
        elif cmd == "resume-dsl":
            _cmd_pause_resume_dsl(active=True)
        elif cmd == "delete-dsl":
            cmd_delete_dsl()
        elif cmd == "status-dsl":
            cmd_status_dsl()
        return
    main_monitor()


if __name__ == "__main__":
    main()
