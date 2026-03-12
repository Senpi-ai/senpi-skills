#!/usr/bin/env python3
"""DSL v5.3 — Strategy-scoped cron, MCP clearinghouse + price, archive on close.
Cron is per strategy (DSL_STATE_DIR + DSL_STRATEGY_ID only). Each run:
1. Check if strategy is active via MCP; if not, cleanup active state files and output strategy_inactive.
2. For each state file with slOrderId: call execution_get_order_status; if filled, rename to {asset}_archived_sl_{epoch}.json.
3. Get active positions from MCP clearinghouse; rename state files for positions no longer present to {asset}_archived_external_{epoch}.json.
4. For each remaining position: fetch price, update tiers, breach, close if needed; on close rename to {asset}_archived_{epoch}.json.
Phase 1 SL uses MARKET (fast exit); Phase 2 (tiered) SL uses LIMIT. Phase 2 default: 1 breach to close.
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


def _safe_int(value, default: int = 0) -> int:
    """Convert to int without raising; return default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    """Return list of (path, asset) for each active .json in strategy dir (excludes *_archived_* files)."""
    out = []
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return out
    for name in os.listdir(strategy_dir):
        if name.startswith("strategy-") or "_archived" in name or ".archived" in name:
            continue
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


# ---------------------------------------------------------------------------
# Strategy & clearinghouse (MCP)
# ---------------------------------------------------------------------------

# Strategy statuses that allow DSL to run (Senpi MCP strategy_get).
DSL_ACTIVE_STATUSES = ("ACTIVE", "PAUSED")


def _mcp_result_ok(result) -> bool:
    """2-tuple (result, error): success when error is None; 3-tuple (success, *rest): success when first is True."""
    if result is None or not isinstance(result, tuple) or len(result) < 2:
        return False
    if len(result) == 2:
        return result[1] is None
    return result[0] is True


def _retry_mcp_call(fn, *args, max_attempts=4, delay_seconds=1.0, on_exception=None, **kwargs):
    """Run fn(*args, **kwargs); on failure, retry up to max_attempts (1 initial + 3 retries).
    If on_exception is set, it must be a callable (e) -> result used when an exception is
    caught, so the returned value matches the wrapped function's tuple size (e.g. 3-tuple).
    """
    last_result = None
    for attempt in range(max_attempts):
        try:
            last_result = fn(*args, **kwargs)
            if _mcp_result_ok(last_result):
                return last_result
        except Exception as e:
            last_result = (on_exception(e)) if on_exception else (None, str(e))
        if attempt + 1 < max_attempts:
            time.sleep(delay_seconds)
    return last_result


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


def _mcp_strategy_get_once(strategy_id: str) -> tuple[dict | None, str | None]:
    """Single attempt: (strategy dict, error)."""
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


def _mcp_strategy_get(strategy_id: str) -> tuple[dict | None, str | None]:
    """Call senpi strategy_get via mcporter. Returns (strategy dict, error). 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_mcp_strategy_get_once, strategy_id)


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


def _mcp_clearinghouse_once(wallet: str) -> tuple[dict | None, str | None]:
    """Single attempt: (data dict with main/xyz and assetPositions), error."""
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


def _mcp_clearinghouse(wallet: str) -> tuple[dict | None, str | None]:
    """Call senpi strategy_get_clearinghouse_state via mcporter. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_mcp_clearinghouse_once, wallet)


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


def cleanup_strategy_state_dir(state_dir: str, strategy_id: str) -> int:
    """Archive only active position .json state files (rename to _archived_inactive_{epoch}.json).
    Skips strategy-*.json (config), *_archived_*, and *.archived* files. Never deletes; never touches archives.
    Returns count archived.
    """
    archived = 0
    strategy_dir = os.path.join(state_dir, strategy_id)
    if not os.path.isdir(strategy_dir):
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name in os.listdir(strategy_dir):
        if name.startswith("strategy-") or "_archived" in name or ".archived" in name:
            continue
        path = os.path.join(strategy_dir, name)
        if name.endswith(".json") and os.path.isfile(path):
            try:
                dest = _archived_state_filename(path, now, "archived-inactive")
                os.rename(path, dest)
                archived += 1
            except OSError:
                pass
    return archived


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


def _fetch_price_mcp_once(dex: str, lookup_symbol: str) -> tuple[float | None, str | None]:
    """Single attempt: fetch mid price via MCP (market_get_prices then allMids fallback)."""
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


def fetch_price_mcp(dex: str, lookup_symbol: str) -> tuple[float | None, str | None]:
    """Fetch mid price via MCP only. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_fetch_price_mcp_once, dex, lookup_symbol)


# ---------------------------------------------------------------------------
# State normalization (backfill missing phase1/phase2 for older state files)
# ---------------------------------------------------------------------------

# Schema defaults for phase config (see references/state-schema.md)
DEFAULT_PHASE1_RETRACE = 0.03
DEFAULT_PHASE1_BREACHES = 3
DEFAULT_PHASE2_RETRACE = 0.015
DEFAULT_PHASE2_BREACHES = 1


def _high_water_roe(state: dict, hw: float) -> float:
    """Compute high-water ROE % from high-water price. Used for pct_of_high_water floor."""
    entry = state.get("entryPrice") or 0.0
    leverage = max(1, state.get("leverage", 1))
    is_long = (state.get("direction", "LONG").upper() == "LONG")
    if entry <= 0:
        return 0.0
    if is_long:
        return (hw - entry) / entry * leverage * 100
    return (entry - hw) / entry * leverage * 100


def normalize_state_phase_config(state: dict) -> bool:
    """Ensure phase1 and phase2 exist with required fields and enabled flags. Backfills missing keys.
    At most two phases; phase1 = capital protection, phase2 = tier-based.
    Returns True if any keys were backfilled (caller may persist state file).
    """
    changed = False
    if "phase1" not in state or not isinstance(state["phase1"], dict):
        state["phase1"] = {}
        changed = True
    p1 = state["phase1"]
    if "enabled" not in p1:
        p1["enabled"] = True
        changed = True
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
    if "enabled" not in p2:
        p2["enabled"] = True
        changed = True
    if "retraceThreshold" not in p2:
        p2["retraceThreshold"] = DEFAULT_PHASE2_RETRACE
        changed = True
    if "consecutiveBreachesRequired" not in p2:
        p2["consecutiveBreachesRequired"] = DEFAULT_PHASE2_BREACHES
        changed = True

    # Backfill createdAt so elapsed-time logic always has a value (use lastCheck or leave for caller).
    if not state.get("createdAt"):
        state["createdAt"] = state.get("lastCheck") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = True

    # Backfill cronIntervalMinutes (default 3) for phase1 time-based cut minimum.
    if state.get("cronIntervalMinutes") is None:
        state["cronIntervalMinutes"] = 3
        changed = True

    # Phase 1 time-based cut objects: if not present, treated as disabled (no backfill).
    # Only migrate legacy flat keys into the object structure when present.
    cron_min = max(1, int(state.get("cronIntervalMinutes", 3)))
    if p1.get("phase1MaxMinutes") is not None:
        if p1.get("hardTimeout") is None or not isinstance(p1["hardTimeout"], dict):
            p1["hardTimeout"] = {}
        ht = p1["hardTimeout"]
        val = _safe_int(p1.get("phase1MaxMinutes"), 0)
        if val > 0:
            ht["enabled"] = True
            ht["intervalInMinutes"] = max(val, cron_min)
            changed = True
        p1.pop("phase1MaxMinutes", None)
    if p1.get("weakPeakCutMinutes") is not None:
        if p1.get("weakPeakCut") is None or not isinstance(p1["weakPeakCut"], dict):
            p1["weakPeakCut"] = {}
        wpc = p1["weakPeakCut"]
        val = _safe_int(p1.get("weakPeakCutMinutes"), 0)
        if val > 0:
            wpc["enabled"] = True
            wpc["intervalInMinutes"] = max(val, cron_min)
            try:
                wpc["minValue"] = float(p1.get("weakPeakThreshold", 3.0))
            except (TypeError, ValueError):
                wpc["minValue"] = 3.0
            changed = True
        p1.pop("weakPeakCutMinutes", None)
        p1.pop("weakPeakThreshold", None)
    if p1.get("deadWeightCutMin") is not None:
        if p1.get("deadWeightCut") is None or not isinstance(p1["deadWeightCut"], dict):
            p1["deadWeightCut"] = {}
        dwc = p1["deadWeightCut"]
        val = _safe_int(p1.get("deadWeightCutMin"), 0)
        if val > 0:
            dwc["enabled"] = True
            dwc["intervalInMinutes"] = max(val, cron_min)
            changed = True
        p1.pop("deadWeightCutMin", None)

    # Backfill highWaterRoe when highWaterPrice exists (for pct_of_high_water floor calc).
    if "highWaterPrice" in state and state.get("highWaterRoe") is None:
        try:
            state["highWaterRoe"] = round(
                _high_water_roe(state, float(state["highWaterPrice"])), 4
            )
            changed = True
        except (TypeError, ValueError):
            state["highWaterRoe"] = 0.0
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Trading logic: high water, tiers, floor, breach
# ---------------------------------------------------------------------------

def update_high_water(state: dict, price: float, is_long: bool) -> float:
    """Update state high water; return new hw. Also keeps highWaterRoe in sync for pct_of_high_water mode."""
    hw = state["highWaterPrice"]
    if is_long and price > hw:
        hw = price
        state["highWaterPrice"] = hw
    elif not is_long and price < hw:
        hw = price
        state["highWaterPrice"] = hw
    state["highWaterRoe"] = round(_high_water_roe(state, hw), 4)
    return hw


def _tier_floor_from_roe(state: dict, tier_floor_roe: float, is_long: bool) -> float:
    """Convert tier floor ROE %% to price. LONG: entry*(1+roe/100/lev), SHORT: entry*(1-roe/100/lev)."""
    entry = state["entryPrice"]
    leverage = max(1, state.get("leverage", 1))
    if is_long:
        return round(entry * (1 + tier_floor_roe / 100 / leverage), 4)
    return round(entry * (1 - tier_floor_roe / 100 / leverage), 4)


def apply_tier_upgrades(
    state: dict, upnl_pct: float, is_long: bool, hw: float
) -> tuple[int, float | None, bool, int]:
    """Apply tier upgrades based on ROE. Mutates state. Returns (tier_idx, tier_floor, tier_changed, previous_tier_idx).
    Tier floor: fixed_roe uses lockPct as fraction of (entry→hw) range; pct_of_high_water uses lockHwPct %% of highWaterRoe.
    """
    tiers = state["tiers"]
    tier_idx = state["currentTierIndex"]
    tier_floor = state["tierFloorPrice"]
    phase = state["phase"]
    breach_count = state["currentBreachCount"]
    entry = state["entryPrice"]
    previous_tier_idx = tier_idx
    tier_changed = False
    lock_mode = state.get("lockMode", "fixed_roe")

    for i, tier in enumerate(tiers):
        if i <= tier_idx:
            continue
        if upnl_pct >= tier["triggerPct"]:
            tier_idx = i
            tier_changed = True
            if lock_mode == "pct_of_high_water" and "lockHwPct" in tier:
                hw_roe = state.get("highWaterRoe") or 0.0
                tier_floor_roe = hw_roe * tier["lockHwPct"] / 100
                tier_floor = _tier_floor_from_roe(state, tier_floor_roe, is_long)
            else:
                lock_pct = tier.get("lockPct", 0)
                if is_long:
                    tier_floor = round(entry + (hw - entry) * lock_pct / 100, 4)
                else:
                    tier_floor = round(entry - (entry - hw) * lock_pct / 100, 4)
            stored = state.get("tierFloorPrice")
            if stored is not None and isinstance(stored, (int, float)):
                if is_long:
                    tier_floor = max(tier_floor, float(stored))
                else:
                    tier_floor = min(tier_floor, float(stored))
            state["currentTierIndex"] = tier_idx
            state["tierFloorPrice"] = tier_floor
            if phase == 1 and tier_idx >= state.get("phase2TriggerTier", 0) and state.get("phase2", {}).get("enabled", True):
                state["phase"] = 2
                breach_count = 0
                state["currentBreachCount"] = 0
                phase = 2

    # High Water mode: recalc floor every tick for current tier (floor trails high water).
    if phase == 2 and tier_idx >= 0 and lock_mode == "pct_of_high_water":
        current_tier = tiers[tier_idx]
        if "lockHwPct" in current_tier:
            hw_roe = state.get("highWaterRoe") or 0.0
            tier_floor_roe = hw_roe * current_tier["lockHwPct"] / 100
            new_floor = _tier_floor_from_roe(state, tier_floor_roe, is_long)
            stored = state.get("tierFloorPrice")
            if stored is not None and isinstance(stored, (int, float)):
                if is_long:
                    new_floor = max(new_floor, float(stored))
                else:
                    new_floor = min(new_floor, float(stored))
            tier_floor = new_floor
            state["tierFloorPrice"] = tier_floor

    # Recalculate floor for CURRENT tier using latest HW (lockPct as % of range) — per-tick trailing.
    # In pct_of_high_water mode when tier uses lockPct (not lockHwPct), floor must move every tick.
    if tier_idx >= 0 and tier_idx < len(tiers) and lock_mode == "pct_of_high_water" and "lockHwPct" not in tiers[tier_idx]:
        tier = tiers[tier_idx]
        lock_pct = tier.get("lockPct", 0)
        if is_long:
            new_floor = round(entry + (hw - entry) * lock_pct / 100, 4)
            tier_floor = max(new_floor, float(state.get("tierFloorPrice", 0)))
        else:
            new_floor = round(entry - (entry - hw) * lock_pct / 100, 4)
            tier_floor = min(new_floor, float(state.get("tierFloorPrice", float("inf"))))
        state["tierFloorPrice"] = tier_floor

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
    phase2 = state.get("phase2") or {}
    breaches_needed = phase2.get("consecutiveBreachesRequired", 1)
    if tier_idx >= 0 and tier_idx < len(tiers) and isinstance(tiers[tier_idx], dict):
        tier = tiers[tier_idx]
        raw = tier.get("consecutiveBreachesRequired", tier.get("breachesRequired", breaches_needed))
        try:
            breaches_needed = int(raw)
        except (TypeError, ValueError):
            pass  # keep previous breaches_needed so max(1, breaches_needed) does not see non-numeric
    breaches_needed = max(1, breaches_needed)
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

def _mcp_edit_position_once(
    wallet: str, coin: str, stop_loss_price: float, order_type: str = "LIMIT"
) -> tuple[bool, str | None, int | None]:
    """Single attempt: (success, error_message, sl_order_id_from_response)."""
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


def _mcp_edit_position(
    wallet: str, coin: str, stop_loss_price: float, order_type: str = "LIMIT"
) -> tuple[bool, str | None, int | None]:
    """Call senpi edit_position to set/update SL at price. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(
        _mcp_edit_position_once,
        wallet,
        coin,
        stop_loss_price,
        order_type,
        on_exception=lambda e: (False, str(e), None),
    )


def _mcp_strategy_get_open_orders_once(wallet: str, dex: str = "") -> tuple[list[dict], str | None]:
    """Single attempt: (list of orders, error)."""
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


def _mcp_strategy_get_open_orders(wallet: str, dex: str = "") -> tuple[list[dict], str | None]:
    """Call senpi strategy_get_open_orders. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(_mcp_strategy_get_open_orders_once, wallet, dex)


def _mcp_execution_get_order_status_once(wallet: str, order_id: int) -> tuple[bool, str | None, str | None]:
    """Single attempt: (success, order_status, error)."""
    args = {"user": wallet, "orderId": order_id}
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", "execution_get_order_status", "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=15,
        )
        raw = _unwrap_mcporter_response(r.stdout) if r.stdout else None
        if r.returncode != 0:
            return False, None, (r.stderr or r.stdout or "non-zero exit")
        if not raw or not isinstance(raw, dict):
            return False, None, "execution_get_order_status: invalid or empty response"
        if raw.get("success") is False:
            err = raw.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return False, None, msg
        data = raw.get("data") or raw
        if data.get("status") == "unknownOid":
            return True, None, None
        if data.get("status") == "order":
            order = data.get("order")
            if isinstance(order, dict):
                return True, (order.get("status") or "").strip().lower(), None
        return False, None, "execution_get_order_status: unexpected response shape"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, None, str(e)


def _mcp_execution_get_order_status(wallet: str, order_id: int) -> tuple[bool, str | None, str | None]:
    """Call senpi execution_get_order_status. 4 attempts (1 initial + 3 retries) on failure."""
    return _retry_mcp_call(
        _mcp_execution_get_order_status_once,
        wallet,
        order_id,
        on_exception=lambda e: (False, None, str(e)),
    )


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
    phase: int,
) -> tuple[bool, bool, str | None]:
    """Set or update SL on Hyperliquid at effective_floor via edit_position. Resolve slOrderId via open orders if needed.
    Phase 1 (initial/absolute floor): MARKET for fast exit; Phase 2 (tiered): LIMIT.
    Mutates state: slOrderId, lastSyncedFloorPrice, slOrderIdUpdatedAt.
    Returns (success, sl_synced_this_tick, error_message)."""
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        return False, False, "no wallet in state"
    order_type = "MARKET" if phase == 1 else "LIMIT"

    success, err, oid_from_api = _mcp_edit_position(
        wallet, coin, effective_floor, order_type=order_type
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

def _close_response_no_position(raw: dict | None, result_text: str) -> bool:
    """True if the close_position response indicates the position was already closed (e.g. CLOSE_NO_POSITION)."""
    text_lower = (result_text or "").lower()
    if "close_no_position" in text_lower or "no position" in text_lower or "no_position" in text_lower:
        return True
    if not raw or not isinstance(raw, dict):
        return False
    err = raw.get("error")
    msg = ""
    if isinstance(err, dict):
        msg = (err.get("message") or "").lower()
    elif isinstance(err, str):
        msg = err.lower()
    if "close_no_position" in msg or "no position" in msg or "no_position" in msg:
        return True
    top_msg = (raw.get("message") or "").lower()
    return "close_no_position" in top_msg or "no position" in top_msg or "no_position" in top_msg


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
    """Attempt close via senpi:close_position. Mutates state. Returns (closed, close_result).
    Uses state['closeReason'] if set (e.g. Phase 1 timeout / weak peak); otherwise breach reason.
    Treats CLOSE_NO_POSITION (position already closed) as success so we clear pendingClose and archive."""
    wallet = state.get("wallet", "")
    coin = state["asset"]
    if not wallet:
        state["pendingClose"] = True
        return False, "error: no wallet in state file"

    reason = state.get("closeReason") or (
        f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed} breaches, "
        f"price {price}, floor {effective_floor}"
    )
    close_result: str | None = None
    for attempt in range(close_retries):
        try:
            cr = subprocess.run(
                ["mcporter", "call", "senpi", "close_position", "--args",
                 json.dumps({"strategyWalletAddress": wallet, "coin": coin, "reason": reason})],
                capture_output=True, text=True, timeout=30,
            )
            result_text = (cr.stdout or "").strip()
            raw = _unwrap_mcporter_response(cr.stdout or "")
            # Position already closed (e.g. SL filled or manual): treat as success and archive
            if _close_response_no_position(raw, result_text):
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                # Use the reason that triggered the close (e.g. hardTimeout, weak peak, breach); else manual close
                state["closeReason"] = (
                    (state.get("closeReason") or reason or "").strip() or "manual close"
                )
                return True, result_text or "close_no_position (position already closed)"
            if cr.returncode == 0 and "error" not in result_text.lower():
                state["active"] = False
                state["pendingClose"] = False
                state["closedAt"] = now
                state["closeReason"] = reason
                return True, result_text
            close_result = f"api_error_attempt_{attempt+1}: {result_text}"
        except Exception as e:
            close_result = f"error_attempt_{attempt+1}: {str(e)}"
        if attempt < close_retries - 1:
            time.sleep(close_retry_delay)
    state["pendingClose"] = True
    return False, close_result


# ---------------------------------------------------------------------------
# Persist: save or rename state file on close (archive instead of delete)
# ---------------------------------------------------------------------------

def _archived_state_filename(state_file: str, now: str, suffix: str = "archived") -> str:
    """Build archived filename with epoch and underscores: e.g. ETH.json -> ETH_archived_1709722800.json."""
    epoch = int(time.time())
    suffix_safe = suffix.replace("-", "_")
    base, ext = os.path.splitext(state_file)
    return f"{base}_{suffix_safe}_{epoch}{ext}"


def _write_state_and_archive(
    state_file: str, state: dict, now: str, close_reason: str, filename_suffix: str
) -> None:
    """Set closeReason, closedAt, active=False on state; write to file; rename to archived filename.
    Lets the agent know why the position was closed (sl_filled, external, or in-script reason)."""
    state["closeReason"] = close_reason
    state["closedAt"] = now
    state["active"] = False
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        dest = _archived_state_filename(state_file, now, filename_suffix)
        os.rename(state_file, dest)
    except OSError:
        pass


def save_or_rename_state(
    state: dict, state_file: str, closed: bool, now: str, close_result: str | None
) -> str | None:
    """Persist state: save file, or if closed write state (with closeReason/closedAt) then rename to {base}_archived_{epoch}.json (archive).
    Caller must set state['lastPrice'] before calling. Caller sets state['closeReason'] and state['closedAt'] when closing.
    If closed but rename fails, state is already written so dsl-cleanup sees active: false and can clean strategy.
    """
    state["lastCheck"] = now
    if closed:
        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
            dest = _archived_state_filename(state_file, now, "archived")
            os.rename(state_file, dest)
            return close_result
        except OSError as e:
            return (close_result or "") + f"; rename_failed: {e}"
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
    def _lock_label(t):
        if t.get("lockHwPct") is not None:
            return f"lock {t['lockHwPct']}%hw"
        return f"lock {t.get('lockPct', 0)}%"
    tier_name = (
        f"Tier {tier_idx+1} ({tiers[tier_idx]['triggerPct']}%→{_lock_label(tiers[tier_idx])})"
        if 0 <= tier_idx < len(tiers) else "None"
    )
    previous_tier_name = None
    if tier_changed:
        if 0 <= previous_tier_idx < len(tiers):
            t = tiers[previous_tier_idx]
            previous_tier_name = f"Tier {previous_tier_idx+1} ({t['triggerPct']}%→{_lock_label(t)})"
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
    # tier_idx -1 (Phase 1, no tier reached) → next tier is first tier (index 0); allow so monitoring can show distance to first tier
    if tier_idx >= -1 and tier_idx + 1 < len(tiers):
        distance_to_next_tier = round(tiers[tier_idx + 1]["triggerPct"] - upnl_pct, 2)

    status = "inactive" if closed else ("pending_close" if state.get("pendingClose") else "active")
    return {
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
        "close_reason": state.get("closeReason") if closed else None,
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
        sync_ok, sl_synced_this_tick, sync_err = sync_sl_to_hyperliquid(
            state, effective_floor, now, dex, phase
        )
        if not sync_ok and sync_err:
            # Log but continue; backup close on breach still available
            state["lastSlSyncError"] = sync_err
    sl_initial_sync = sl_synced_this_tick and not had_sl_order_before

    breached = price <= effective_floor if is_long else price >= effective_floor
    breach_count = update_breach_count(state, breached, "hard")
    force_close = state.get("pendingClose", False)
    should_close = breach_count >= breaches_needed or force_close

    # Phase 1 time-based auto-cut (optional; only when phase==1, using extensible objects)
    if not should_close and phase == 1 and state.get("createdAt"):
        try:
            created_dt = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            elapsed_min = (now_dt - created_dt).total_seconds() / 60.0
        except (ValueError, TypeError):
            elapsed_min = 0.0
        cron_interval = max(1, int(state.get("cronIntervalMinutes", 3)))
        p1 = state.get("phase1") or {}
        entry_f = float(state.get("entryPrice", 0))
        lev_f = max(1, float(state.get("leverage", 1)))

        # hardTimeout: { enabled, intervalInMinutes }
        ht = p1.get("hardTimeout") or {}
        if isinstance(ht, dict) and ht.get("enabled"):
            interval = max(_safe_int(ht.get("intervalInMinutes"), 0), cron_interval)
            if interval > 0 and elapsed_min >= interval:
                should_close = True
                state["closeReason"] = f"Phase 1 timeout {elapsed_min:.0f}min"

        # weakPeakCut: { enabled, intervalInMinutes, minValue } — minValue is ROE % (leverage-based)
        if not should_close:
            wpc = p1.get("weakPeakCut") or {}
            if isinstance(wpc, dict) and wpc.get("enabled"):
                interval = max(_safe_int(wpc.get("intervalInMinutes"), 0), cron_interval)
                if interval > 0 and elapsed_min >= interval and entry_f > 0:
                    try:
                        min_val = float(wpc.get("minValue", 3.0))
                    except (TypeError, ValueError):
                        min_val = 3.0
                    # ROE % = (price_change / entry) * leverage * 100 (same as upnl_pct)
                    if is_long:
                        peak_roe_pct = (hw - entry_f) / entry_f * lev_f * 100
                    else:
                        peak_roe_pct = (entry_f - hw) / entry_f * lev_f * 100
                    if peak_roe_pct < min_val and upnl_pct < peak_roe_pct:
                        should_close = True
                        state["closeReason"] = "Weak peak early cut"

        # deadWeightCut: { enabled, intervalInMinutes } — ROE was never positive for X min → close
        if not should_close:
            dwc = p1.get("deadWeightCut") or {}
            if isinstance(dwc, dict) and dwc.get("enabled"):
                interval = _safe_int(dwc.get("intervalInMinutes"), 0)
                if interval > 0:
                    interval = max(interval, cron_interval)
                    if elapsed_min >= interval:
                        # Never gone positive: LONG → highWater <= entry; SHORT → highWater >= entry
                        never_positive = (is_long and hw <= entry_f) or (not is_long and hw >= entry_f)
                        if never_positive:
                            should_close = True
                            state["closeReason"] = "Dead weight cut (never positive)"

    # All closes (breach, phase1 hardTimeout/weakPeakCut/deadWeightCut, pending retry) use the same path:
    # MCP close_position(strategyWalletAddress, coin, reason) via try_close_position.
    closed = False
    close_result = None
    if should_close:
        closed, close_result = try_close_position(
            state, price, phase, breach_count, breaches_needed, effective_floor, now,
            state.get("closeRetries", 2), state.get("closeRetryDelaySec", 3),
        )

    close_result = save_or_rename_state(state, state_file, closed, now, close_result)

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
# Main (strategy-scoped)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DSL v5.3 — strategy-scoped trailing stop cron.")
    parser.add_argument("--strategy-id", default="", help="Strategy UUID (overrides DSL_STRATEGY_ID env)")
    parser.add_argument("--state-dir", default="", help="DSL state directory (overrides DSL_STATE_DIR env)")
    args = parser.parse_args()

    # CLI > env > default (backward compatible)
    strategy_id = (args.strategy_id or os.environ.get("DSL_STRATEGY_ID", "") or "").strip()
    state_dir = (args.state_dir or os.environ.get("DSL_STATE_DIR", "") or DEFAULT_STATE_DIR).strip() or DEFAULT_STATE_DIR

    if not strategy_id:
        print(json.dumps({"status": "error", "error": "strategy_id required (use --strategy-id or DSL_STRATEGY_ID)", "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Strategy active? From Senpi MCP strategy_get (not clearinghouse).
    active, wallet, active_error, confirmed_inactive = get_strategy_active_and_wallet(strategy_id)
    if not active:
        if confirmed_inactive:
            archived = cleanup_strategy_state_dir(state_dir, strategy_id)
            print(json.dumps({
                "status": "strategy_inactive",
                "strategy_id": strategy_id,
                "message": "Strategy not active (Senpi MCP). State files archived. Agent: remove OpenClaw cron for this strategy.",
                "reason": active_error,
                "state_files_archived": archived,
                "time": now,
            }))
            sys.exit(0)
        else:
            # Transient/API error (timeout, mcporter down, malformed response): do not delete state.
            print(json.dumps({
                "status": "error",
                "error": "strategy_get_failed",
                "strategy_id": strategy_id,
                "message": active_error,
                "time": now,
            }))
            sys.exit(1)

    # 2. List state files and check SL order status (before reconcile): if SL already filled, archive and skip.
    state_files = list_strategy_state_files(state_dir, strategy_id)
    for path, asset in list(state_files):
        try:
            with open(path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        oid = state.get("slOrderId")
        if oid is None:
            continue
        try:
            oid = int(oid)
        except (TypeError, ValueError):
            continue
        ok, order_status, _ = _mcp_execution_get_order_status(wallet, oid)
        if ok and order_status == "filled":
            _write_state_and_archive(path, state, now, "sl_filled", "archived-sl")

    # 3. Active positions from clearinghouse.
    coins, ch_error = get_active_position_coins(wallet)
    if ch_error is not None:
        print(json.dumps({
            "status": "error",
            "error": "clearinghouse_failed",
            "strategy_id": strategy_id,
            "message": ch_error,
            "time": now,
        }))
        sys.exit(1)

    # 4. Reconcile: archive state files for positions no longer in clearinghouse (closed externally).
    state_files = list_strategy_state_files(state_dir, strategy_id)
    for path, asset in list(state_files):
        if asset not in coins:
            try:
                with open(path) as f:
                    state = json.load(f)
            except (OSError, json.JSONDecodeError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            if state:
                _write_state_and_archive(path, state, now, "external", "archived-external")
            else:
                # Read failed: move file only so we don't overwrite with minimal state
                try:
                    dest = _archived_state_filename(path, now, "archived-external")
                    os.rename(path, dest)
                except OSError:
                    pass

    processed = 0
    for coin in sorted(coins):
        state_file, path_error = resolve_state_file(state_dir, strategy_id, coin)
        if path_error is None:
            process_one_position(state_file, strategy_id, now)
            processed += 1

    if processed == 0:
        print(json.dumps({
            "status": "no_positions",
            "strategy_id": strategy_id,
            "message": "Strategy active but no position state files to process. Agent: keep cron; next run may have positions or output strategy_inactive after cleanup.",
            "time": now,
        }))


if __name__ == "__main__":
    main()
