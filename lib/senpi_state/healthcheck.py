"""
Self-healing DSL health check — shared across all Senpi trading skills.

Detects and auto-fixes:
  ORPHAN_DSL         — active DSL but no on-chain position → auto-deactivate
  NO_DSL             — on-chain position with no DSL state → auto-create
  DIRECTION_MISMATCH — DSL/position direction differ → auto-replace
  STATE_RECONCILED   — size/entry/leverage drift → auto-update DSL
  DSL_STALE          — DSL not checked recently → alert
  DSL_INACTIVE       — DSL exists but active=false → alert

Each issue includes an ``action`` field:
  auto_deactivated, auto_created, auto_replaced, updated_state,
  skipped_fetch_error, or alert_only.

Skills provide a ``get_healthcheck_adapter()`` (or extend ``get_lifecycle_adapter()``)
that returns wallet, DSL path helpers, state loaders, and an optional
``dsl_state_template`` factory.  The generic CLI script (senpi-healthcheck.py)
dynamically imports the skill's config module and delegates here.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from senpi_state.atomic import atomic_write, load_json
from senpi_state.mcporter import mcporter_call_safe
from senpi_state.validation import validate_dsl_state


def _extract_positions(section_data: dict) -> dict:
    """Extract non-zero positions from a Hyperliquid clearinghouse section."""
    positions: dict[str, dict] = {}
    for p in section_data.get("assetPositions", []):
        pos = p.get("position", {})
        coin = pos.get("coin")
        if not coin:
            continue
        szi = float(pos.get("szi", 0))
        if szi == 0:
            continue
        margin_used = float(pos.get("marginUsed", 0))
        pos_value = float(pos.get("positionValue", 0))
        positions[coin] = {
            "direction": "SHORT" if szi < 0 else "LONG",
            "size": abs(szi),
            "entryPx": pos.get("entryPx"),
            "unrealizedPnl": pos.get("unrealizedPnl"),
            "returnOnEquity": pos.get("returnOnEquity"),
            "leverage": round(pos_value / margin_used, 1) if margin_used > 0 else None,
            "marginUsed": margin_used,
            "positionValue": pos_value,
        }
    return positions


def fetch_wallet_positions(wallet: str) -> tuple[dict, dict, str | None]:
    """Get all positions (crypto + xyz) from a single clearinghouse call.

    Returns:
        (crypto_positions, xyz_positions, error_string_or_None)
    """
    data = mcporter_call_safe(
        "strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not data:
        return {}, {}, "clearinghouse fetch failed"
    crypto = _extract_positions(data.get("main", {}))
    xyz = _extract_positions(data.get("xyz", {}))
    return crypto, xyz, None


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return float("inf") if a != 0 else 0
    return abs(a - b) / abs(b) * 100


def read_dsl_states(dsl_glob_pattern: str) -> dict[str, dict]:
    """Read all DSL state files matching a glob pattern.

    Returns:
        Dict of asset_key → { active, pendingClose, file, direction, ... , _raw }
    """
    states: dict[str, dict] = {}
    for f in sorted(glob.glob(dsl_glob_pattern)):
        try:
            with open(f) as fh:
                state = json.load(fh)
            asset = os.path.basename(f).replace("dsl-", "").replace(".json", "")
            states[asset] = {
                "active": state.get("active", False),
                "pendingClose": state.get("pendingClose", False),
                "file": f,
                "direction": state.get("direction"),
                "lastCheck": state.get("lastCheck"),
                "size": state.get("size"),
                "entryPrice": state.get("entryPrice"),
                "leverage": state.get("leverage"),
                "highWaterPrice": state.get("highWaterPrice"),
                "_raw": state,
            }
        except (json.JSONDecodeError, IOError):
            continue
    return states


def check_instance(
    wallet: str,
    instance_key: str,
    dsl_glob_pattern: str,
    dsl_state_path_fn: Callable[[str], str],
    create_dsl_fn: Optional[Callable] = None,
    tiers: Optional[list] = None,
    stale_minutes: float = 10,
) -> tuple[list[dict], list[str], list[str]]:
    """Run health checks for a single skill instance / strategy.

    Args:
        wallet: Strategy wallet address.
        instance_key: Strategy key or instance ID.
        dsl_glob_pattern: Glob pattern for DSL state files
            (e.g. ``state/tiger-abc123/dsl-*.json``).
        dsl_state_path_fn: Callable(asset) → file path for a new DSL state file.
        create_dsl_fn: Optional callable(asset, direction, entry_price, size,
            leverage, instance_key) → dict.  If provided, enables auto-create
            for NO_DSL and auto-replace for DIRECTION_MISMATCH.
        tiers: Optional tier list passed to create_dsl_fn.
        stale_minutes: Alert if DSL hasn't been checked in this many minutes.

    Returns:
        (issues, on_chain_assets, active_dsl_assets)
    """
    issues: list[dict] = []
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not wallet:
        issues.append({
            "level": "CRITICAL", "type": "NO_WALLET",
            "instanceKey": instance_key, "action": "alert_only",
            "message": f"[{instance_key}] no wallet configured",
        })
        return issues, [], []

    crypto_pos, xyz_pos, fetch_err = fetch_wallet_positions(wallet)
    had_fetch_error = fetch_err is not None

    if had_fetch_error:
        issues.append({
            "level": "WARNING", "type": "FETCH_ERROR",
            "instanceKey": instance_key, "action": "alert_only",
            "message": f"[{instance_key}] {fetch_err}",
        })

    all_positions = {**crypto_pos, **xyz_pos}
    dsl_states = read_dsl_states(dsl_glob_pattern)

    # --- For each on-chain position, verify DSL coverage ---
    for coin, pos in all_positions.items():
        asset_key = coin
        if asset_key not in dsl_states:
            clean_key = coin.replace("xyz:", "")
            if clean_key in dsl_states:
                asset_key = clean_key
            else:
                _handle_no_dsl(issues, instance_key, coin, pos,
                               dsl_state_path_fn, create_dsl_fn, wallet, tiers)
                continue

        dsl = dsl_states[asset_key]

        if not dsl["active"] and not dsl["pendingClose"]:
            issues.append({
                "level": "CRITICAL", "type": "DSL_INACTIVE",
                "instanceKey": instance_key, "asset": coin,
                "action": "alert_only",
                "message": f"[{instance_key}] {coin} DSL active=false — unprotected position",
            })
        elif dsl["direction"] != pos["direction"]:
            _handle_direction_mismatch(issues, instance_key, coin, asset_key,
                                       pos, dsl, dsl_state_path_fn,
                                       create_dsl_fn, wallet, tiers)
        else:
            _reconcile_state(issues, instance_key, coin, pos, dsl, now_str)

        _check_staleness(issues, instance_key, coin, dsl, now, stale_minutes)

    # --- Orphan DSL: active DSL with no matching on-chain position ---
    for asset, dsl in dsl_states.items():
        if not dsl["active"]:
            continue
        clean_asset = asset.replace("xyz:", "")
        if (clean_asset in all_positions or asset in all_positions
                or f"xyz:{asset}" in all_positions):
            continue

        if had_fetch_error:
            issues.append({
                "level": "WARNING", "type": "ORPHAN_DSL",
                "instanceKey": instance_key, "asset": asset,
                "action": "skipped_fetch_error",
                "message": f"[{instance_key}] {asset} DSL appears orphaned "
                           f"but skipping auto-deactivate due to fetch error",
            })
        else:
            try:
                raw = dsl["_raw"]
                raw["active"] = False
                raw["closeReason"] = "externally_closed_detected_by_healthcheck"
                raw["deactivatedAt"] = now_str
                atomic_write(dsl["file"], raw)
                issues.append({
                    "level": "WARNING", "type": "ORPHAN_DSL",
                    "instanceKey": instance_key, "asset": asset,
                    "action": "auto_deactivated",
                    "message": f"[{instance_key}] {asset} DSL was active but "
                               f"no position found — auto-deactivated",
                })
            except Exception as e:
                issues.append({
                    "level": "WARNING", "type": "ORPHAN_DSL",
                    "instanceKey": instance_key, "asset": asset,
                    "action": "alert_only",
                    "message": f"[{instance_key}] {asset} orphaned DSL — "
                               f"auto-deactivate failed: {e}",
                })

    on_chain = list(all_positions.keys())
    active_dsl = [a for a, d in dsl_states.items() if d["active"]]
    return issues, on_chain, active_dsl


# ─── Internal helpers ────────────────────────────────────────────────

def _handle_no_dsl(issues, instance_key, coin, pos,
                   path_fn, create_fn, wallet, tiers):
    """Auto-create a DSL state file for an unprotected position."""
    if not create_fn:
        issues.append({
            "level": "CRITICAL", "type": "NO_DSL",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} {pos['direction']} has no DSL "
                       f"— no create_dsl_fn provided, cannot auto-create",
        })
        return

    entry_px = pos.get("entryPx")
    size = pos.get("size")
    leverage = pos.get("leverage")
    if not (entry_px and size and leverage):
        issues.append({
            "level": "CRITICAL", "type": "NO_DSL",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} {pos['direction']} has no DSL "
                       f"— incomplete clearinghouse data, cannot auto-create",
        })
        return

    try:
        clean_coin = coin.replace("xyz:", "")
        new_state = create_fn(
            asset=clean_coin, direction=pos["direction"],
            entry_price=float(entry_px), size=float(size),
            leverage=float(leverage), instance_key=instance_key,
        )
        new_state["wallet"] = wallet
        new_state["dex"] = "xyz" if coin.startswith("xyz:") else "hl"
        path = path_fn(clean_coin)
        atomic_write(path, new_state)
        issues.append({
            "level": "CRITICAL", "type": "NO_DSL",
            "instanceKey": instance_key, "asset": coin,
            "action": "auto_created",
            "message": f"[{instance_key}] {coin} {pos['direction']} had no DSL "
                       f"— auto-created at {path}",
        })
    except Exception as e:
        issues.append({
            "level": "CRITICAL", "type": "NO_DSL",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} {pos['direction']} has no DSL "
                       f"— auto-create failed: {e}",
        })


def _handle_direction_mismatch(issues, instance_key, coin, asset_key,
                                pos, dsl, path_fn, create_fn, wallet, tiers):
    """Replace DSL when direction doesn't match on-chain position."""
    if not create_fn:
        issues.append({
            "level": "CRITICAL", "type": "DIRECTION_MISMATCH",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} position is {pos['direction']} "
                       f"but DSL is {dsl['direction']} — no create_dsl_fn, "
                       f"cannot auto-replace",
        })
        return

    try:
        entry_px = pos.get("entryPx")
        size = pos.get("size")
        leverage = pos.get("leverage", dsl.get("leverage"))
        new_state = create_fn(
            asset=asset_key, direction=pos["direction"],
            entry_price=float(entry_px) if entry_px else float(dsl["entryPrice"]),
            size=float(size) if size else float(dsl["size"]),
            leverage=float(leverage) if leverage else 10,
            instance_key=instance_key,
        )
        new_state["wallet"] = wallet
        new_state["dex"] = "xyz" if coin.startswith("xyz:") else "hl"
        new_state["previousDirection"] = dsl["direction"]
        path = path_fn(asset_key)
        atomic_write(path, new_state)
        issues.append({
            "level": "CRITICAL", "type": "DIRECTION_MISMATCH",
            "instanceKey": instance_key, "asset": coin,
            "action": "auto_replaced",
            "message": f"[{instance_key}] {coin} was {dsl['direction']} but "
                       f"position is {pos['direction']} — DSL replaced",
        })
    except Exception as e:
        issues.append({
            "level": "CRITICAL", "type": "DIRECTION_MISMATCH",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} direction mismatch — "
                       f"auto-replace failed: {e}",
        })


def _reconcile_state(issues, instance_key, coin, pos, dsl, now_str):
    """Fix size/entry/leverage drift between DSL and on-chain."""
    updates: dict[str, Any] = {}
    on_chain_size = pos.get("size")
    on_chain_entry = pos.get("entryPx")
    on_chain_leverage = pos.get("leverage")

    if (dsl["size"] and on_chain_size
            and _pct_diff(float(on_chain_size), float(dsl["size"])) > 1):
        updates["size"] = float(on_chain_size)
    if (dsl["entryPrice"] and on_chain_entry
            and _pct_diff(float(on_chain_entry), float(dsl["entryPrice"])) > 0.1):
        updates["entryPrice"] = float(on_chain_entry)
    if (dsl["leverage"] and on_chain_leverage
            and abs(float(on_chain_leverage) - float(dsl["leverage"])) > 0.5):
        updates["leverage"] = float(on_chain_leverage)

    if not updates:
        return

    try:
        raw = dsl["_raw"]
        raw.update(updates)
        if "entryPrice" in updates and dsl.get("highWaterPrice"):
            hw = float(dsl["highWaterPrice"])
            new_entry = updates["entryPrice"]
            if ((dsl["direction"] == "LONG" and new_entry > hw)
                    or (dsl["direction"] == "SHORT" and new_entry < hw)):
                raw["highWaterPrice"] = new_entry
                updates["highWaterPrice"] = new_entry
        raw["lastReconciledAt"] = now_str
        atomic_write(dsl["file"], raw)
        issues.append({
            "level": "INFO", "type": "STATE_RECONCILED",
            "instanceKey": instance_key, "asset": coin,
            "action": "updated_state", "updates": updates,
            "message": f"[{instance_key}] {coin} DSL reconciled: "
                       f"{list(updates.keys())}",
        })
    except Exception as e:
        issues.append({
            "level": "WARNING", "type": "RECONCILE_FAILED",
            "instanceKey": instance_key, "asset": coin,
            "action": "alert_only",
            "message": f"[{instance_key}] {coin} reconciliation failed: {e}",
        })


def _check_staleness(issues, instance_key, coin, dsl, now, stale_minutes):
    """Alert if DSL hasn't been checked recently."""
    last_check = dsl.get("lastCheck")
    if not last_check:
        return
    try:
        last = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
        age_min = (now - last).total_seconds() / 60
        if age_min > stale_minutes:
            issues.append({
                "level": "WARNING", "type": "DSL_STALE",
                "instanceKey": instance_key, "asset": coin,
                "action": "alert_only",
                "message": f"[{instance_key}] {coin} DSL last checked "
                           f"{round(age_min)}min ago — cron may not be firing",
            })
    except (ValueError, TypeError):
        pass
