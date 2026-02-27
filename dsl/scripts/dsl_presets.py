#!/usr/bin/env python3
"""
dsl_presets.py — Preset definitions and generate_state_file() for DSL position state.

Presets: conservative, moderate, aggressive, tight, wolf.
Unified field names: triggerPct, lockPct, breachesRequired / consecutiveBreachesRequired,
retraceThreshold, minROE, staleHours.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Preset definitions: phase1, phase2, tiers, optional stagnation
PRESETS = {
    "conservative": {
        "phase1": {
            "retraceThreshold": 0.05,
            "consecutiveBreachesRequired": 4,
            "absoluteFloor": None,  # set from entry in generate_state_file
        },
        "phase2": {"retraceThreshold": 0.025, "consecutiveBreachesRequired": 3},
        "tiers": [
            {"triggerPct": 15, "lockPct": 8, "breachesRequired": 3},
            {"triggerPct": 30, "lockPct": 20, "retrace": 0.02, "breachesRequired": 2},
            {"triggerPct": 50, "lockPct": 35, "retrace": 0.015, "breachesRequired": 2},
            {"triggerPct": 75, "lockPct": 50, "retrace": 0.012, "breachesRequired": 1},
            {"triggerPct": 100, "lockPct": 70, "retrace": 0.01, "breachesRequired": 1},
        ],
        "breachDecay": "soft",
    },
    "moderate": {
        "phase1": {
            "retraceThreshold": 0.03,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": None,
        },
        "phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": [
            {"triggerPct": 10, "lockPct": 5, "breachesRequired": 3},
            {"triggerPct": 20, "lockPct": 14, "breachesRequired": 2},
            {"triggerPct": 30, "lockPct": 22, "retrace": 0.012, "breachesRequired": 2},
            {"triggerPct": 50, "lockPct": 40, "retrace": 0.01, "breachesRequired": 1},
            {"triggerPct": 75, "lockPct": 60, "retrace": 0.008, "breachesRequired": 1},
            {"triggerPct": 100, "lockPct": 80, "retrace": 0.006, "breachesRequired": 1},
        ],
        "breachDecay": "hard",
    },
    "aggressive": {
        "phase1": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 2,
            "absoluteFloor": None,
        },
        "phase2": {"retraceThreshold": 0.008, "consecutiveBreachesRequired": 1},
        "tiers": [
            {"triggerPct": 5, "lockPct": 2, "breachesRequired": 2},
            {"triggerPct": 10, "lockPct": 6, "retrace": 0.006, "breachesRequired": 2},
            {"triggerPct": 20, "lockPct": 14, "retrace": 0.005, "breachesRequired": 1},
            {"triggerPct": 35, "lockPct": 25, "retrace": 0.004, "breachesRequired": 1},
            {"triggerPct": 50, "lockPct": 40, "retrace": 0.003, "breachesRequired": 1},
        ],
        "breachDecay": "hard",
    },
    "tight": {
        "phase1": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 2,
            "absoluteFloor": None,
        },
        "phase2": {"retraceThreshold": 0.01, "consecutiveBreachesRequired": 1},
        "tiers": [
            {"triggerPct": 5, "lockPct": 40, "breachesRequired": 3},
            {"triggerPct": 10, "lockPct": 60, "breachesRequired": 2},
            {"triggerPct": 20, "lockPct": 75, "breachesRequired": 1},
        ],
        "stagnation": {"enabled": True, "minROE": 5.0, "staleHours": 1.0, "priceRangePct": 1.0},
        "breachDecay": "hard",
    },
    "wolf": {
        "phase1": {
            "retraceThreshold": 0.03,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": None,
        },
        "phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": [
            {"triggerPct": 5, "lockPct": 50, "breachesRequired": 3},
            {"triggerPct": 10, "lockPct": 65, "breachesRequired": 2},
            {"triggerPct": 15, "lockPct": 75, "breachesRequired": 2},
            {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1},
        ],
        "stagnation": {"enabled": True, "minROE": 8.0, "staleHours": 1.0, "priceRangePct": 1.0},
        "breachDecay": "hard",
    },
}


def _compute_absolute_floor(entry: float, leverage: int, retrace_threshold: float, is_long: bool) -> float:
    """Compute phase1 absolute floor from entry and retrace (ROE-based)."""
    retrace_decimal = retrace_threshold / 100 if retrace_threshold > 1 else retrace_threshold
    price_frac = retrace_decimal / leverage
    if is_long:
        return round(entry * (1 - price_frac), 6)
    return round(entry * (1 + price_frac), 6)


def generate_state_file(
    preset: str,
    asset: str,
    entry: float,
    size: float,
    leverage: int,
    direction: str = "long",
    wallet: str = "",
    strategy_key: str | None = None,
    owner_skill: str | None = None,
    owner_ref: str | None = None,
    dex: str | None = None,
) -> dict:
    """
    Build a new position state dict (flat format) for the given preset and args.

    Args:
        preset: One of conservative, moderate, aggressive, tight, wolf.
        asset: Ticker (e.g. HYPE, ETH).
        entry: Entry price.
        size: Position size in units.
        leverage: Leverage.
        direction: "long" or "short".
        wallet: Strategy wallet address.
        strategy_key: Optional strategy key (e.g. wolf-abc123).
        owner_skill: Optional owner skill name.
        owner_ref: Optional owner reference.
        dex: Optional dex (e.g. xyz).

    Returns:
        State dict ready to pass to dsl_engine.process_position (and to save via dsl_common.save_state).
    """
    p = PRESETS.get(preset, PRESETS["moderate"]).copy()
    phase1 = dict(p["phase1"])
    phase2 = dict(p["phase2"])
    tiers = [dict(t) for t in p["tiers"]]
    is_long = (direction or "long").lower() == "long"
    if phase1.get("absoluteFloor") is None:
        phase1["absoluteFloor"] = _compute_absolute_floor(
            entry, leverage, phase1.get("retraceThreshold", 0.03), is_long
        )
    state = {
        "active": True,
        "asset": asset,
        "direction": (direction or "long").upper(),
        "leverage": leverage,
        "entryPrice": entry,
        "size": size,
        "wallet": wallet or "",
        "phase": 1,
        "phase1": phase1,
        "phase2TriggerTier": 0,
        "phase2": phase2,
        "tiers": tiers,
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "highWaterPrice": entry,
        "floorPrice": phase1["absoluteFloor"],
        "currentBreachCount": 0,
        "consecutiveFetchFailures": 0,
        "pendingClose": False,
        "breachDecay": p.get("breachDecay", "hard"),
        "closeRetries": 2,
        "closeRetryDelaySec": 3,
        "maxFetchFailures": 10,
        "createdAt": _now_iso(),
    }
    if strategy_key:
        state["strategyKey"] = strategy_key
    if p.get("stagnation"):
        state["stagnation"] = p["stagnation"]
    if dex:
        state["dex"] = dex
    return state
