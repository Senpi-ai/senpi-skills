#!/usr/bin/env python3
"""
dsl_engine.py — Canonical DSL engine v5 (unified).

Single importable module for all trailing-stop logic. Used by dsl-v4.py (single position),
dsl-combined / wolf-cron (multi-strategy), and any consumer.

Unified from:
  - dsl-dynamic-stop-loss/scripts/dsl-v4.py (base)
  - wolf-strategy/scripts/dsl-v4.py, dsl-combined.py (correct formula + stagnation, auto-cut, etc.)

Correct tier floor formula (locks N% of entry→highwater range):
  tier_floor = entry + (hw - entry) * tier["lockPct"] / 100   [long]
  tier_floor = entry - (entry - hw) * tier["lockPct"] / 100   [short]

Backward-compatible with flat v2/v3/v4 state files (triggerPct, retraceThreshold, etc.).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# Default now if not provided
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_flat(state: dict, *keys: str, default=None):
    """Read from flat state or state.config (v3)."""
    for key in keys:
        if key in state:
            return state[key]
        if "config" in state and isinstance(state["config"], dict) and key in state["config"]:
            return state["config"][key]
    return default


def _get_runtime(state: dict, key: str, default=None):
    """Read from flat state or state.runtime (v3)."""
    if key in state:
        return state[key]
    if "runtime" in state and isinstance(state["runtime"], dict):
        return state["runtime"].get(key, default)
    return default


def _set_runtime(state: dict, key: str, value):
    """Write to flat state or state.runtime (v3)."""
    if "runtime" in state and isinstance(state["runtime"], dict):
        state["runtime"][key] = value
    else:
        state[key] = value


@dataclass
class ProcessResult:
    """Result of process_position() for a single position."""
    status: str  # "active" | "pending_close" | "closed" | "inactive"
    asset: str
    direction: str
    price: float
    upnl: float
    upnl_pct: float
    phase: int
    hw: float
    floor: float
    trailing_floor: float
    tier_floor: float | None
    tier_name: str
    locked_profit: float
    retrace_pct: float
    breach_count: int
    breaches_needed: int
    breached: bool
    should_close: bool
    closed: bool
    close_result: str | None = None
    close_reason: str | None = None
    time: str = ""
    tier_changed: bool = False
    previous_tier: str | None = None
    elapsed_minutes: int = 0
    distance_to_next_tier_pct: float | None = None
    pending_close: bool = False
    consecutive_failures: int = 0
    stagnation_triggered: bool = False
    phase1_autocut: bool = False

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


def _phase1_retrace_as_price_fraction(state: dict, is_long: bool) -> float:
    """Phase1 retraceThreshold: may be stored as decimal (0.03) or ROE%% (5). Return price fraction."""
    p1 = state.get("phase1") or {}
    ret = p1.get("retraceThreshold", 0.03)
    leverage = state.get("leverage", 10)
    if ret > 1:
        ret = ret / 100  # ROE% e.g. 5 → 0.05
    # ROE retrace → price retrace: price_retrace = roe_retrace / leverage
    return ret / leverage


def _auto_fix_absolute_floor(state: dict, is_long: bool, now_iso: str) -> None:
    """Ensure phase1.absoluteFloor is valid (cap loss from entry). Mutates state."""
    p1 = state.get("phase1")
    if not p1:
        return
    entry = _get_flat(state, "entryPrice") or state.get("entryPrice")
    leverage = state.get("leverage", 10)
    retrace_roe = p1.get("retraceThreshold", 0.03)
    retrace_decimal = retrace_roe / 100 if retrace_roe > 1 else retrace_roe
    retrace_price = retrace_decimal / leverage
    if is_long:
        correct_floor = round(entry * (1 - retrace_price), 6)
    else:
        correct_floor = round(entry * (1 + retrace_price), 6)
    existing = p1.get("absoluteFloor", correct_floor)
    if is_long:
        final_floor = min(correct_floor, existing) if existing > 0 else correct_floor
    else:
        final_floor = max(correct_floor, existing) if existing > 0 else correct_floor
    p1["absoluteFloor"] = final_floor


def process_position(
    state: dict,
    current_price: float,
    now_iso: str | None = None,
    *,
    close_fn: Callable[[str, str, str], tuple[bool, str]] | None = None,
    strategy_wallet: str | None = None,
) -> ProcessResult:
    """
    Run one tick of DSL logic on a position. Mutates state in place.

    Args:
        state: Position state dict (flat or v3 with config/runtime).
        current_price: Current mid price.
        now_iso: ISO timestamp string; default now.
        close_fn: (wallet, coin, reason) -> (closed: bool, result: str). If None, no close is attempted.
        strategy_wallet: Override wallet (e.g. from strategy config). Else state["wallet"].

    Returns:
        ProcessResult with status, prices, breach info, etc.
    """
    now_iso = now_iso or _now_iso()
    direction = (_get_flat(state, "direction") or state.get("direction") or "LONG").upper()
    is_long = direction == "LONG"
    breach_decay_mode = state.get("breachDecay", "hard")
    close_retries = state.get("closeRetries", 2)
    close_retry_delay = state.get("closeRetryDelaySec", 3)
    max_fetch_failures = state.get("maxFetchFailures", 10)

    asset = _get_flat(state, "asset") or state.get("asset", "")
    entry = _get_flat(state, "entryPrice") or state["entryPrice"]
    size = state["size"]
    leverage = state["leverage"]
    hw = _get_runtime(state, "highWaterPrice") or entry
    phase = _get_runtime(state, "phase") or 1
    breach_count = _get_runtime(state, "currentBreachCount") or 0
    tier_idx = _get_runtime(state, "currentTierIndex")
    if tier_idx is None:
        tier_idx = -1
    tier_floor = _get_runtime(state, "tierFloorPrice")
    tiers = _get_flat(state, "tiers") or state.get("tiers") or []
    force_close = _get_runtime(state, "pendingClose") or False

    # Stagnation config
    stag_cfg = state.get("stagnation", {})
    stag_enabled = stag_cfg.get("enabled", True)
    stag_min_roe = stag_cfg.get("minROE", 8.0)
    stag_stale_hours = stag_cfg.get("staleHours", 1.0)
    stag_range_pct = stag_cfg.get("priceRangePct", 1.0)

    # Auto-fix absoluteFloor
    _auto_fix_absolute_floor(state, is_long, now_iso)
    p1 = state.get("phase1", {})
    abs_floor = p1.get("absoluteFloor")

    # uPnL
    if is_long:
        upnl = (current_price - entry) * size
    else:
        upnl = (entry - current_price) * size
    margin = entry * size / leverage
    upnl_pct = upnl / margin * 100 if margin else 0

    # Update high water
    hw_updated = False
    if is_long and current_price > hw:
        hw = current_price
        _set_runtime(state, "highWaterPrice", hw)
        hw_updated = True
    elif not is_long and current_price < hw:
        hw = current_price
        _set_runtime(state, "highWaterPrice", hw)
        hw_updated = True
    if hw_updated or "hwTimestamp" not in state:
        _set_runtime(state, "hwTimestamp", now_iso)

    # Tier upgrades — CORRECT formula: floor = entry + (hw - entry) * lockPct/100
    previous_tier_idx = tier_idx
    tier_changed = False
    for i, tier in enumerate(tiers):
        if i <= tier_idx:
            continue
        trigger_pct = tier.get("triggerPct", tier.get("roePct", 0))
        if upnl_pct >= trigger_pct:
            tier_idx = i
            tier_changed = True
            if is_long:
                tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
            else:
                tier_floor = round(entry - (entry - hw) * tier["lockPct"] / 100, 4)
            _set_runtime(state, "currentTierIndex", tier_idx)
            _set_runtime(state, "tierFloorPrice", tier_floor)
            phase2_trigger = state.get("phase2TriggerTier", 0)
            if phase == 1 and tier_idx >= phase2_trigger:
                phase = 2
                _set_runtime(state, "phase", 2)
                breach_count = 0
                _set_runtime(state, "currentBreachCount", 0)

    # Effective floor
    p2 = state.get("phase2", {})
    p1_retrace_frac = _phase1_retrace_as_price_fraction(state, is_long)
    breaches_needed_p1 = p1.get("consecutiveBreachesRequired", p1.get("breachesRequired", 3))
    breaches_needed_p2 = p2.get("consecutiveBreachesRequired", p2.get("breachesRequired", 2))

    if phase == 1:
        breaches_needed = breaches_needed_p1
        if is_long:
            trailing_floor = round(hw * (1 - p1_retrace_frac), 4)
            effective_floor = max(abs_floor, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + p1_retrace_frac), 4)
            effective_floor = min(abs_floor, trailing_floor)
    else:
        if tier_idx >= 0 and tiers:
            t = tiers[tier_idx]
            t_retrace = t.get("retrace", p2.get("retraceThreshold", 0.015))
            if t_retrace > 1:
                t_retrace_frac = (t_retrace / 100) / leverage  # ROE% -> price fraction
            else:
                t_retrace_frac = t_retrace  # already price fraction (e.g. 0.015)
            breaches_needed = t.get("breachesRequired", t.get("retraceClose", breaches_needed_p2))
        else:
            t_retrace_frac = p2.get("retraceThreshold", 0.015)
            if t_retrace_frac > 1:
                t_retrace_frac = (t_retrace_frac / 100) / leverage
            # else already price fraction
            breaches_needed = breaches_needed_p2
        if is_long:
            trailing_floor = round(hw * (1 - t_retrace_frac), 4)
            effective_floor = max(tier_floor or 0, trailing_floor)
        else:
            trailing_floor = round(hw * (1 + t_retrace_frac), 4)
            effective_floor = min(tier_floor or float("inf"), trailing_floor)

    _set_runtime(state, "floorPrice", round(effective_floor, 4))

    # Stagnation check
    stagnation_triggered = False
    stag_hours_stale = 0.0
    hw_ts = _get_runtime(state, "hwTimestamp")
    if stag_enabled and upnl_pct >= stag_min_roe and hw_ts:
        try:
            hw_time = datetime.fromisoformat(hw_ts.replace("Z", "+00:00"))
            stag_hours_stale = (datetime.now(timezone.utc) - hw_time).total_seconds() / 3600
            if stag_hours_stale >= stag_stale_hours:
                hw_price = _get_runtime(state, "highWaterPrice") or hw
                if hw_price > 0:
                    price_move_pct = abs(current_price - hw_price) / hw_price * 100
                    if price_move_pct <= stag_range_pct:
                        stagnation_triggered = True
        except (ValueError, TypeError):
            pass

    # Phase 1 auto-cut (90 min max, 45 min weak peak)
    phase1_autocut = False
    phase1_autocut_reason = None
    elapsed_minutes = 0
    created_at = state.get("createdAt")
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            elapsed_minutes = int((datetime.now(timezone.utc) - created).total_seconds() / 60)
        except (ValueError, TypeError):
            pass

    peak_roe = _get_runtime(state, "peakROE")
    if peak_roe is None:
        peak_roe = upnl_pct
    if upnl_pct > peak_roe:
        peak_roe = upnl_pct
        _set_runtime(state, "peakROE", peak_roe)

    if phase == 1 and elapsed_minutes > 0:
        if elapsed_minutes >= 90:
            phase1_autocut = True
            phase1_autocut_reason = f"Phase 1 timeout: {elapsed_minutes}min, ROE never hit Tier 1 (5%)"
        elif elapsed_minutes >= 45 and peak_roe < 3 and upnl_pct < peak_roe:
            phase1_autocut = True
            phase1_autocut_reason = f"Weak peak early cut: {elapsed_minutes}min, peak ROE {round(peak_roe, 1)}%, now declining"

    # Breach check
    if is_long:
        breached = current_price <= effective_floor
    else:
        breached = current_price >= effective_floor
    if breached:
        breach_count += 1
    else:
        if breach_decay_mode == "soft":
            breach_count = max(0, breach_count - 1)
        else:
            breach_count = 0
    _set_runtime(state, "currentBreachCount", breach_count)

    should_close = (
        breach_count >= breaches_needed
        or force_close
        or stagnation_triggered
        or phase1_autocut
    )

    # Close if needed
    closed = False
    close_result = None
    close_reason = None
    if should_close:
        wallet = strategy_wallet or state.get("wallet", "")
        is_xyz = state.get("dex") == "xyz" or (isinstance(asset, str) and asset.startswith("xyz:"))
        close_coin = asset if (isinstance(asset, str) and asset.startswith("xyz:")) else (f"xyz:{asset}" if is_xyz else asset)
        if stagnation_triggered:
            close_reason = f"Stagnation TP: ROE {round(upnl_pct, 1)}%, stale {round(stag_hours_stale, 1)}h"
        elif phase1_autocut:
            close_reason = phase1_autocut_reason or "Phase 1 auto-cut"
        else:
            close_reason = f"DSL breach: Phase {phase}, {breach_count}/{breaches_needed}, price {current_price}, floor {effective_floor}"
        if wallet and close_fn:
            closed, close_result = close_fn(wallet, close_coin, close_reason)
            if closed:
                _set_runtime(state, "active", False)
                _set_runtime(state, "pendingClose", False)
                state["closedAt"] = now_iso
                state["closeReason"] = close_reason
                if close_result and "position_already_closed" in str(close_result).lower():
                    state["closeReason"] = "position_already_closed"
            else:
                _set_runtime(state, "pendingClose", True)
        else:
            if not wallet:
                close_result = "error: no wallet in state file"
            _set_runtime(state, "pendingClose", True)

    # Persist last check
    state["lastCheck"] = now_iso
    state["lastPrice"] = current_price

    # Build result
    if is_long:
        retrace_from_hw = (1 - current_price / hw) * 100 if hw > 0 else 0
    else:
        retrace_from_hw = (current_price / hw - 1) * 100 if hw > 0 else 0

    tier_name = "None"
    if tier_idx >= 0 and tiers:
        t = tiers[tier_idx]
        tier_name = f"Tier {tier_idx+1} ({t.get('triggerPct', t.get('roePct'))}%→lock {t['lockPct']}%)"
    previous_tier_name = None
    if tier_changed:
        if previous_tier_idx >= 0 and tiers:
            t = tiers[previous_tier_idx]
            previous_tier_name = f"Tier {previous_tier_idx+1} ({t.get('triggerPct', t.get('roePct'))}%→lock {t['lockPct']}%)"
        else:
            previous_tier_name = "None (Phase 1)"

    locked_profit = 0
    if tier_floor:
        locked_profit = round(((tier_floor - entry) if is_long else (entry - tier_floor)) * size, 2)

    distance_to_next_tier = None
    if tier_idx + 1 < len(tiers):
        distance_to_next_tier = round(tiers[tier_idx + 1].get("triggerPct", tiers[tier_idx + 1].get("roePct", 0)) - upnl_pct, 2)

    status = "inactive"
    if not closed and not _get_runtime(state, "pendingClose"):
        status = "active"
    elif _get_runtime(state, "pendingClose"):
        status = "pending_close"
    elif closed:
        status = "closed"

    return ProcessResult(
        status=status,
        asset=asset,
        direction=direction,
        price=current_price,
        upnl=round(upnl, 2),
        upnl_pct=round(upnl_pct, 2),
        phase=phase,
        hw=hw,
        floor=effective_floor,
        trailing_floor=trailing_floor,
        tier_floor=tier_floor,
        tier_name=tier_name,
        locked_profit=locked_profit,
        retrace_pct=round(retrace_from_hw, 2),
        breach_count=breach_count,
        breaches_needed=breaches_needed,
        breached=breached,
        should_close=should_close,
        closed=closed,
        close_result=close_result,
        close_reason=close_reason,
        time=now_iso,
        tier_changed=tier_changed,
        previous_tier=previous_tier_name,
        elapsed_minutes=elapsed_minutes,
        distance_to_next_tier_pct=distance_to_next_tier,
        pending_close=_get_runtime(state, "pendingClose") or False,
        consecutive_failures=state.get("consecutiveFetchFailures", 0),
        stagnation_triggered=stagnation_triggered,
        phase1_autocut=phase1_autocut,
    )


# ─── Data dir (for path helpers) ─────────────────────────────────────────────
def _data_dir(base: str | None = None) -> str:
    if base:
        return base
    return os.environ.get(
        "DSL_STATE_DIR",
        os.environ.get("WOLF_WORKSPACE", os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")),
    )


def migrate_state(state: dict, strategy_key: str = "default") -> dict:
    """
    Convert flat v1/v2 state to v3 (meta + config + runtime).
    Does not mutate the input; returns a new dict.
    """
    if state.get("meta", {}).get("schemaVersion") == 3:
        return state
    now = _now_iso()
    config_keys = {
        "asset", "direction", "entryPrice", "size", "leverage", "wallet", "dex",
        "strategyKey", "phase1", "phase2", "phase2TriggerTier", "tiers", "stagnation",
        "breachDecay", "closeRetries", "closeRetryDelaySec", "maxFetchFailures",
    }
    runtime_keys = {
        "phase", "active", "pendingClose", "highWaterPrice", "hwTimestamp",
        "currentTierIndex", "tierFloorPrice", "floorPrice", "currentBreachCount",
        "peakROE", "consecutiveFetchFailures", "lastCheck", "lastPrice",
        "closedAt", "closeReason",
    }
    config = {k: state[k] for k in config_keys if k in state}
    config.setdefault("strategyKey", strategy_key)
    runtime = {k: state[k] for k in runtime_keys if k in state}
    runtime.setdefault("phase", 1)
    runtime.setdefault("active", True)
    runtime.setdefault("currentTierIndex", -1)
    runtime.setdefault("currentBreachCount", 0)
    return {
        "meta": {
            "schemaVersion": 3,
            "namespace": strategy_key,
            "owner": state.get("meta", {}).get("owner") or {"skill": "dsl", "ref": strategy_key},
            "createdAt": state.get("createdAt", now),
            "updatedAt": now,
        },
        "config": config,
        "runtime": runtime,
    }


# Re-export I/O from dsl_common when run from same package
def load_state(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


# ─── Path helpers and strategy descriptor I/O ─────────────────────────────────
def strategy_dir(strategy_key: str, base: str | None = None) -> str:
    return os.path.join(_data_dir(base), "state", "dsl", strategy_key)


def dsl_state_path(strategy_key: str, asset: str, base: str | None = None) -> str:
    return os.path.join(strategy_dir(strategy_key, base), f"dsl-{asset.replace(':', '-')}.json")


def dsl_state_glob(strategy_key: str, base: str | None = None) -> str:
    return os.path.join(strategy_dir(strategy_key, base), "dsl-*.json")


def load_strategy(strategy_key: str, base: str | None = None) -> dict | None:
    path = os.path.join(strategy_dir(strategy_key, base), "strategy.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_strategy(strategy_key: str, descriptor: dict, base: str | None = None) -> None:
    path = os.path.join(strategy_dir(strategy_key, base), "strategy.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(descriptor, f, indent=2)
    os.replace(tmp, path)


def create_strategy(
    strategy_key: str,
    owner_skill: str,
    owner_ref: str,
    config: dict,
    display_name: str | None = None,
    base: str | None = None,
) -> dict:
    now = _now_iso()
    descriptor = {
        "strategyKey": strategy_key,
        "displayName": display_name or strategy_key,
        "schemaVersion": 1,
        "owner": {"skill": owner_skill, "ref": owner_ref},
        "active": True,
        "createdAt": now,
        "config": dict(config),
        "runtime": {
            "activePositions": 0,
            "slotsAvailable": config.get("maxPositions", 3),
            "totalUnrealizedROE": 0.0,
            "lastRunAt": None,
            "lastRunStatus": None,
            "consecutiveErrors": 0,
        },
    }
    save_strategy(strategy_key, descriptor, base)
    return descriptor


def list_strategies(base: str | None = None) -> list[str]:
    state_dsl = os.path.join(_data_dir(base), "state", "dsl")
    if not os.path.isdir(state_dsl):
        return []
    return [
        d for d in os.listdir(state_dsl)
        if os.path.isfile(os.path.join(state_dsl, d, "strategy.json"))
    ]


def strategy_has_slot(strategy_key: str, base: str | None = None) -> bool:
    desc = load_strategy(strategy_key, base)
    if not desc:
        return True
    rt = desc.get("runtime", {})
    slots = rt.get("slotsAvailable", 0)
    return slots > 0


def strategy_slot_count(strategy_key: str, base: str | None = None) -> tuple[int, int]:
    desc = load_strategy(strategy_key, base)
    if not desc:
        return 0, 0
    cfg = desc.get("config", {})
    rt = desc.get("runtime", {})
    max_p = cfg.get("maxPositions", 3)
    active = rt.get("activePositions", 0)
    return active, max_p


# ─── Event log (append-only, per strategy) ───────────────────────────────────
def _events_dir(base: str | None = None) -> str:
    return os.path.join(_data_dir(base), "events", "dsl")


def emit_event(strategy_key: str, event: str, payload: dict, base: str | None = None) -> None:
    """Append a single event to events/dsl/{strategy_key}.jsonl."""
    log_path = os.path.join(_events_dir(base), f"{strategy_key}.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    record = {
        "v": 1,
        "event": event,
        "ts": _now_iso(),
        "source": "dsl",
        "namespace": strategy_key,
        "payload": payload,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")


class EventReader:
    """Read new events from events/dsl/{strategy_key}.jsonl from last checkpoint."""

    def __init__(self, strategy_key: str, base: str | None = None):
        self.strategy_key = strategy_key
        self.base = base
        self._log_path = os.path.join(_events_dir(base), f"{strategy_key}.jsonl")
        self._ckpt_path = os.path.join(_events_dir(base), f"{strategy_key}.checkpoint")
        self._next_offset = 0

    def read_new(self) -> list[dict]:
        """Read events since last checkpoint. Does not update checkpoint."""
        events = []
        if not os.path.isfile(self._log_path):
            return events
        if os.path.isfile(self._ckpt_path):
            try:
                with open(self._ckpt_path) as f:
                    self._next_offset = int(f.read().strip() or "0")
            except (ValueError, IOError):
                self._next_offset = 0
        with open(self._log_path) as f:
            f.seek(self._next_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self._next_offset = f.tell()
        return events

    def save_checkpoint(self) -> None:
        """Persist current read offset so next read_new() continues from here."""
        os.makedirs(os.path.dirname(self._ckpt_path), exist_ok=True)
        with open(self._ckpt_path, "w") as f:
            f.write(str(self._next_offset))
