"""
State Doctor — full-stack state reconciliation + margin safety.

Reconciles three layers against on-chain clearinghouse data:
  1. DSL files (delegates to healthcheck.check_instance)
  2. Skill state (activePositions, availableSlots)
  3. Margin utilization and liquidation proximity

Issue types:
  STALE_POSITION          — in activePositions but not on-chain → auto-remove
  PHANTOM_POSITION        — on-chain but not in activePositions → auto-add
  SLOT_MISMATCH           — availableSlots wrong → auto-fix
  POSITION_DIRECTION_MISMATCH — direction drift → auto-fix
  MARGIN_HIGH             — margin utilization above threshold → warn or downsize
  LIQ_CLOSE               — liquidation proximity below threshold → warn or downsize
  LIQ_INSIDE_DSL          — liq closer than DSL floor → warn or downsize

All thresholds are configurable via MarginConfig.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger("senpi_state.state_doctor")

from senpi_state.healthcheck import (
    check_instance,
    fetch_wallet_positions,
    read_dsl_states,
    _extract_positions,
)
from senpi_state.mcporter import mcporter_call_safe


@dataclass
class MarginConfig:
    """Configurable thresholds for margin safety checks."""
    warn_utilization_pct: float = 70.0
    critical_utilization_pct: float = 85.0
    target_utilization_pct: float = 60.0
    warn_liq_distance_pct: float = 30.0
    critical_liq_distance_pct: float = 15.0
    auto_downsize: bool = True
    downsize_reduce_pct: float = 25.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _liq_distance_pct(price: float, liq: float, direction: str) -> float | None:
    if not price or not liq:
        return None
    if direction == "LONG":
        return round((price - liq) / price * 100, 2)
    else:
        return round((liq - price) / price * 100, 2)


def _dsl_floor_for_asset(dsl_states: dict, asset: str) -> float | None:
    dsl = dsl_states.get(asset)
    if not dsl or not dsl.get("active"):
        return None
    raw = dsl.get("_raw", {})
    floor = raw.get("tierFloorPrice") or raw.get("highWaterPrice")
    if not floor:
        p1 = raw.get("phase1", {})
        floor = p1.get("absoluteFloor") or p1.get("floorPrice")
    return float(floor) if floor else None


# ─── State Reconciliation ────────────────────────────────────────


def _reconcile_positions(
    issues: list,
    instance_key: str,
    state: dict,
    all_positions: dict,
    had_fetch_error: bool,
    active_positions_key: str,
    max_slots: int,
    save_state: Callable,
) -> bool:
    """Reconcile activePositions against on-chain data. Returns True if state was mutated."""
    active = state.get(active_positions_key, {})
    if not isinstance(active, dict):
        active = {}
    mutated = False
    now = _now_iso()

    if not had_fetch_error:
        for coin in list(active.keys()):
            if coin not in all_positions:
                issues.append({
                    "level": "WARNING", "type": "STALE_POSITION",
                    "instanceKey": instance_key, "asset": coin,
                    "action": "auto_removed",
                    "message": f"[{instance_key}] {coin} in activePositions "
                               f"but no on-chain position — removed",
                })
                del active[coin]
                mutated = True

        for coin, pos in all_positions.items():
            clean = coin.replace("xyz:", "")
            if clean not in active and coin not in active:
                entry = {
                    "direction": pos["direction"],
                    "entryPrice": float(pos.get("entryPx", 0)),
                    "size": pos.get("size", 0),
                    "leverage": pos.get("leverage"),
                    "margin": pos.get("marginUsed", 0),
                    "source": "state_doctor",
                    "addedAt": now,
                }
                active[clean] = entry
                issues.append({
                    "level": "WARNING", "type": "PHANTOM_POSITION",
                    "instanceKey": instance_key, "asset": coin,
                    "action": "auto_added",
                    "message": f"[{instance_key}] {coin} {pos['direction']} "
                               f"on-chain but not in activePositions — added",
                })
                mutated = True

            key = clean if clean in active else (coin if coin in active else None)
            if key and key in all_positions or coin in all_positions:
                on_chain = all_positions.get(coin, all_positions.get(key, {}))
                if active[key].get("direction") != on_chain.get("direction"):
                    old_dir = active[key].get("direction")
                    active[key]["direction"] = on_chain["direction"]
                    issues.append({
                        "level": "WARNING", "type": "POSITION_DIRECTION_MISMATCH",
                        "instanceKey": instance_key, "asset": key,
                        "action": "auto_fixed",
                        "message": f"[{instance_key}] {key} direction was "
                                   f"{old_dir}, on-chain is {on_chain['direction']} — fixed",
                    })
                    mutated = True

    expected_available = max(0, max_slots - len(active))
    current_available = state.get("availableSlots")
    if current_available != expected_available:
        issues.append({
            "level": "INFO", "type": "SLOT_MISMATCH",
            "instanceKey": instance_key,
            "action": "auto_fixed",
            "message": f"[{instance_key}] availableSlots was {current_available}, "
                       f"should be {expected_available} — fixed",
        })
        mutated = True

    if mutated or current_available != expected_available:
        state[active_positions_key] = active
        state["availableSlots"] = expected_available
        save_state(state)

    return mutated


# ─── Margin Safety ───────────────────────────────────────────────


def _fetch_margin_summary(wallet: str) -> dict | None:
    """Fetch full clearinghouse data including margin summary."""
    data = mcporter_call_safe(
        "strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not data:
        return None
    main = data.get("main", {})
    summary = main.get("marginSummary", {})
    return {
        "account_value": float(summary.get("accountValue", 0)),
        "margin_used": float(summary.get("totalMarginUsed", 0)),
        "maint_margin": float(main.get("crossMaintenanceMarginUsed", 0)),
        "positions_raw": main.get("assetPositions", []),
        "xyz": data.get("xyz", {}),
    }


def _check_margin_safety(
    issues: list,
    instance_key: str,
    wallet: str,
    margin_data: dict,
    dsl_states: dict,
    cfg: MarginConfig,
) -> int:
    """Run margin utilization and liq proximity checks. Returns downsize count."""
    acct = margin_data["account_value"]
    used = margin_data["margin_used"]
    downsizes = 0

    if acct <= 0:
        return 0

    util_pct = round(used / acct * 100, 2)

    maint = margin_data["maint_margin"]
    liq_buffer = round((acct - maint) / acct * 100, 2) if acct > 0 else 0

    if util_pct >= cfg.critical_utilization_pct:
        level = "CRITICAL"
        if cfg.auto_downsize:
            downsized = _auto_downsize_for_margin(
                issues, instance_key, wallet,
                margin_data, acct, used, util_pct, cfg)
            if downsized:
                downsizes += 1
        else:
            issues.append({
                "level": level, "type": "MARGIN_HIGH",
                "instanceKey": instance_key,
                "action": "alert_only",
                "message": f"[{instance_key}] Margin utilization at {util_pct}% "
                           f"(critical threshold: {cfg.critical_utilization_pct}%)",
                "utilization_pct": util_pct,
            })
    elif util_pct >= cfg.warn_utilization_pct:
        issues.append({
            "level": "WARNING", "type": "MARGIN_HIGH",
            "instanceKey": instance_key,
            "action": "alert_only",
            "message": f"[{instance_key}] Margin utilization at {util_pct}% "
                       f"(warning threshold: {cfg.warn_utilization_pct}%)",
            "utilization_pct": util_pct,
        })

    for ap in margin_data["positions_raw"]:
        pos = ap.get("position", {})
        coin = pos.get("coin")
        szi = float(pos.get("szi", 0))
        if not coin or szi == 0:
            continue

        direction = "SHORT" if szi < 0 else "LONG"
        liq = float(pos["liquidationPx"]) if pos.get("liquidationPx") else None
        margin_used = float(pos.get("marginUsed", 0))
        pos_value = float(pos.get("positionValue", 0))
        price = pos_value / abs(szi) if szi != 0 else 0

        if not liq or not price:
            continue

        liq_dist = _liq_distance_pct(price, liq, direction)
        if liq_dist is None:
            continue

        dsl_floor = _dsl_floor_for_asset(dsl_states, coin.replace("xyz:", ""))
        if dsl_floor:
            dsl_dist = _liq_distance_pct(price, dsl_floor, direction)
            if dsl_dist is not None and liq_dist < dsl_dist:
                issues.append({
                    "level": "CRITICAL", "type": "LIQ_INSIDE_DSL",
                    "instanceKey": instance_key, "asset": coin,
                    "action": "auto_downsized" if cfg.auto_downsize else "alert_only",
                    "message": f"[{instance_key}] {coin} liq ({liq_dist}% away) "
                               f"closer than DSL floor ({dsl_dist}% away)",
                    "liq_distance_pct": liq_dist,
                    "dsl_distance_pct": dsl_dist,
                })
                if cfg.auto_downsize:
                    if _reduce_position(wallet, coin, cfg.downsize_reduce_pct, issues, instance_key):
                        downsizes += 1

        if liq_dist < cfg.critical_liq_distance_pct:
            issues.append({
                "level": "CRITICAL", "type": "LIQ_CLOSE",
                "instanceKey": instance_key, "asset": coin,
                "action": "auto_downsized" if cfg.auto_downsize else "alert_only",
                "message": f"[{instance_key}] {coin} {direction} liquidation "
                           f"only {liq_dist}% away (critical: {cfg.critical_liq_distance_pct}%)",
                "liq_distance_pct": liq_dist,
            })
            if cfg.auto_downsize:
                if _reduce_position(wallet, coin, cfg.downsize_reduce_pct, issues, instance_key):
                    downsizes += 1
        elif liq_dist < cfg.warn_liq_distance_pct:
            issues.append({
                "level": "WARNING", "type": "LIQ_CLOSE",
                "instanceKey": instance_key, "asset": coin,
                "action": "alert_only",
                "message": f"[{instance_key}] {coin} {direction} liquidation "
                           f"{liq_dist}% away (warning: {cfg.warn_liq_distance_pct}%)",
                "liq_distance_pct": liq_dist,
            })

    return downsizes


def _auto_downsize_for_margin(
    issues, instance_key, wallet, margin_data, acct, used, util_pct, cfg
):
    """Find largest-margin position and reduce it to bring utilization toward target."""
    positions = []
    for ap in margin_data["positions_raw"]:
        pos = ap.get("position", {})
        coin = pos.get("coin")
        szi = float(pos.get("szi", 0))
        margin = float(pos.get("marginUsed", 0))
        if coin and szi != 0 and margin > 0:
            positions.append((coin, margin, abs(szi)))

    if not positions:
        return False

    positions.sort(key=lambda x: x[1], reverse=True)
    coin, pos_margin, pos_size = positions[0]

    target_used = acct * cfg.target_utilization_pct / 100
    excess = used - target_used
    if excess <= 0:
        return False

    reduce_frac = min(excess / pos_margin, cfg.downsize_reduce_pct / 100)
    reduce_frac = max(reduce_frac, 0.05)
    reduce_frac = min(reduce_frac, 0.50)

    return _reduce_position(wallet, coin, reduce_frac * 100, issues, instance_key)


def _reduce_position(wallet, coin, reduce_pct, issues, instance_key):
    """Reduce a position by reduce_pct% via edit_position."""
    try:
        result = mcporter_call_safe(
            "edit_position",
            strategy_wallet=wallet,
            coin=coin,
            action="reduce",
            reduce_pct=reduce_pct,
            reason=f"state_doctor auto-downsize ({reduce_pct:.0f}%)",
        )
        success = bool(result and not isinstance(result, str))
        issues.append({
            "level": "WARNING", "type": "STATE_DOCTOR_DOWNSIZE",
            "instanceKey": instance_key, "asset": coin,
            "action": "executed" if success else "failed",
            "message": f"[{instance_key}] {coin} reduced by {reduce_pct:.0f}% "
                       f"— {'success' if success else 'FAILED'}",
            "reduce_pct": reduce_pct,
        })
        return success
    except Exception as e:
        issues.append({
            "level": "CRITICAL", "type": "STATE_DOCTOR_DOWNSIZE",
            "instanceKey": instance_key, "asset": coin,
            "action": "failed",
            "message": f"[{instance_key}] {coin} downsize failed: {e}",
        })
        return False


# ─── Main Entry Point ────────────────────────────────────────────


def reconcile_state(
    wallet: str,
    instance_key: str,
    load_state: Callable,
    save_state: Callable,
    max_slots: int,
    dsl_glob_pattern: str,
    dsl_state_path_fn: Callable[[str], str],
    create_dsl_fn: Optional[Callable] = None,
    tiers: Optional[list] = None,
    active_positions_key: str = "activePositions",
    stale_minutes: float = 10,
    margin_config: Optional[MarginConfig] = None,
) -> dict:
    """Full state reconciliation: DSL health + position state + margin safety.

    Args:
        wallet: Strategy wallet address.
        instance_key: Strategy/instance identifier.
        load_state: Callable returning skill state dict.
        save_state: Callable(state) to persist state atomically.
        max_slots: Maximum concurrent positions.
        dsl_glob_pattern: Glob for DSL state files.
        dsl_state_path_fn: Callable(asset) -> file path.
        create_dsl_fn: Optional callable for auto-creating DSL.
        tiers: Optional tier list for DSL creation.
        active_positions_key: Key in state dict (default "activePositions").
        stale_minutes: DSL staleness threshold.
        margin_config: Margin safety thresholds. None = skip margin checks.

    Returns:
        Structured dict with status, issues, and actions taken.
    """
    now = _now_iso()
    dsl_issues: list[dict] = []
    state_issues: list[dict] = []
    margin_issues: list[dict] = []
    downsizes = 0

    # 1. DSL healthcheck
    dsl_issues_raw, on_chain_assets, active_dsl = check_instance(
        wallet=wallet,
        instance_key=instance_key,
        dsl_glob_pattern=dsl_glob_pattern,
        dsl_state_path_fn=dsl_state_path_fn,
        create_dsl_fn=create_dsl_fn,
        tiers=tiers,
        stale_minutes=stale_minutes,
    )
    dsl_issues = dsl_issues_raw

    # 2. Position state reconciliation
    crypto_pos, xyz_pos, fetch_err = fetch_wallet_positions(wallet)
    had_fetch_error = fetch_err is not None
    all_positions = {**crypto_pos, **xyz_pos}

    if had_fetch_error:
        state_issues.append({
            "level": "WARNING", "type": "FETCH_ERROR",
            "instanceKey": instance_key, "action": "alert_only",
            "message": f"[{instance_key}] {fetch_err} — skipping state reconciliation",
        })

    try:
        state = load_state()
    except Exception as e:
        state_issues.append({
            "level": "CRITICAL", "type": "STATE_LOAD_ERROR",
            "instanceKey": instance_key, "action": "alert_only",
            "message": f"[{instance_key}] Failed to load state: {e}",
        })
        state = {active_positions_key: {}}

    state_positions = list(state.get(active_positions_key, {}).keys())

    _reconcile_positions(
        state_issues, instance_key, state, all_positions,
        had_fetch_error, active_positions_key, max_slots, save_state,
    )

    # 3. Margin safety
    margin_summary = None
    if margin_config and not had_fetch_error:
        margin_data = _fetch_margin_summary(wallet)
        if margin_data:
            dsl_states = read_dsl_states(dsl_glob_pattern)
            downsizes = _check_margin_safety(
                margin_issues, instance_key, wallet,
                margin_data, dsl_states, margin_config,
            )
            margin_summary = {
                "account_value": margin_data["account_value"],
                "margin_used": margin_data["margin_used"],
                "utilization_pct": round(
                    margin_data["margin_used"] / margin_data["account_value"] * 100, 2
                ) if margin_data["account_value"] > 0 else 0,
                "maint_margin": margin_data["maint_margin"],
                "liq_buffer_pct": round(
                    (margin_data["account_value"] - margin_data["maint_margin"])
                    / margin_data["account_value"] * 100, 2
                ) if margin_data["account_value"] > 0 else 0,
            }

    all_issues = dsl_issues + state_issues + margin_issues
    actions = sum(1 for i in all_issues
                  if i.get("action") not in ("alert_only", "skipped_fetch_error"))
    criticals = sum(1 for i in all_issues if i["level"] == "CRITICAL")

    if criticals > 0:
        status = "critical"
    elif actions > 0:
        status = "fixed"
    else:
        status = "ok"

    result = {
        "status": status,
        "time": now,
        "instance_key": instance_key,
        "on_chain": on_chain_assets,
        "state_positions": state_positions,
        "active_dsl": active_dsl,
        "dsl_issues": dsl_issues,
        "state_issues": state_issues,
        "margin_issues": margin_issues,
        "all_issues": all_issues,
        "actions_taken": actions,
        "downsizes_executed": downsizes,
        "issue_count": len(all_issues),
        "critical_count": criticals,
    }
    if margin_summary:
        result["margin"] = margin_summary

    return result


# ─── Discord Notification ────────────────────────────────────────


def notify_discord(result: dict, webhook_url: str, skill: str = "") -> bool:
    """Post state doctor results to Discord via webhook. Only posts when actions taken."""
    if not webhook_url:
        return False

    actions = result.get("actions_taken", 0)
    downsizes = result.get("downsizes_executed", 0)
    criticals = result.get("critical_count", 0)
    status = result.get("status", "ok")

    if status == "ok" and actions == 0:
        return False

    if status == "critical":
        color = 16711680  # red
        emoji = "\U0001f6a8"  # 🚨
        title_suffix = "CRITICAL"
    elif downsizes > 0:
        color = 16711680  # red
        emoji = "\u26a0\ufe0f"  # ⚠️
        title_suffix = "DOWNSIZED"
    elif actions > 0:
        color = 16776960  # yellow
        emoji = "\U0001f527"  # 🔧
        title_suffix = "FIXED"
    else:
        color = 65280  # green
        emoji = "\u2705"  # ✅
        title_suffix = "OK"

    label = skill.upper() if skill else result.get("instance_key", "")
    title = f"{emoji} State Doctor — {label} — {title_suffix}"

    lines = []
    for issue in result.get("all_issues", []):
        lvl = issue.get("level", "INFO")
        icon = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1", "INFO": "\u2139\ufe0f"}.get(lvl, "\u2022")
        lines.append(f"{icon} {issue.get('message', '')}")

    margin = result.get("margin")
    if margin:
        lines.append("")
        lines.append(
            f"\U0001f4b0 Margin: {margin.get('utilization_pct', 0)}% used "
            f"| Buffer: {margin.get('liq_buffer_pct', 0)}%"
        )

    description = "\n".join(lines)[:4000] if lines else f"Actions: {actions}, Downsizes: {downsizes}"

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": result.get("time", _now_iso()),
    }

    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "State-Doctor/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except (urllib.error.URLError, Exception) as e:
        log.error("Discord webhook failed: %s", e)
        return False
