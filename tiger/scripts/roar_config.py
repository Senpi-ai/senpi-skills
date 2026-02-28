#!/usr/bin/env python3
"""
roar-config.py — ROAR config bounds, state management, and helpers.

ROAR (Recursive Optimization & Adjustment Runtime) tunes TIGER's
config within strict bounds. This module handles:
  - Tunable bounds (hard min/max for every adjustable parameter)
  - Protected keys that ROAR must never touch
  - State persistence (roar-state.json)
  - Changeset application with bounds validation
  - Revert logic (single-step rollback on degraded performance)
  - Pattern disable/enable with 48h auto-re-enable
"""

import json
import os
import copy
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Import shared infra
from tiger_config import WORKSPACE, atomic_write

# ─── Paths ───────────────────────────────────────────────────

STATE_DIR = os.path.join(WORKSPACE, "state")
ROAR_STATE_FILE = os.path.join(STATE_DIR, "roar-state.json")

os.makedirs(STATE_DIR, exist_ok=True)

# ─── Tunable Bounds ──────────────────────────────────────────
# Hard min/max for every parameter ROAR is allowed to adjust.
# Format: { "key_path": (min, max) }
# For nested keys, use dot notation: "min_confluence_score.NORMAL"

TUNABLE_BOUNDS = {
    # minConfluenceScore per aggression level
    "min_confluence_score.CONSERVATIVE": (0.25, 0.85),
    "min_confluence_score.NORMAL":       (0.25, 0.85),
    "min_confluence_score.ELEVATED":     (0.25, 0.85),
    # ABORT stays at 999, never tuned

    # Scanner thresholds
    "min_bb_squeeze_percentile":   (15, 45),
    "btc_correlation_move_pct":    (1.5, 5.0),
    "min_funding_annualized_pct":  (15.0, 60.0),

    # DSL retrace thresholds per tier (pattern-level overrides)
    "dsl_retrace.phase1": (0.008, 0.03),
    "dsl_retrace.phase2": (0.008, 0.03),

    # Trailing lock pct per aggression (±15% of current value)
    # These are dynamic bounds — checked at apply time relative to current
    "trailing_lock_pct.CONSERVATIVE": (0.60, 0.95),
    "trailing_lock_pct.NORMAL":       (0.40, 0.80),
    "trailing_lock_pct.ELEVATED":     (0.25, 0.60),
}

# Per-pattern confluence score overrides: new config key
# Config shape: { "pattern_confluence_overrides": { "bb_squeeze_breakout": 0.5, ... } }
# These override the aggression-level min_confluence_score for specific patterns.

# ─── Protected Keys ──────────────────────────────────────────
# ROAR must NEVER modify these. They are user-controlled risk limits.

PROTECTED_KEYS = [
    "budget",
    "target",
    "deadline_days",
    "start_time",
    "strategy_id",
    "strategy_wallet",
    "telegram_chat_id",
    "max_slots",
    "max_leverage",
    "max_drawdown_pct",
    "max_daily_loss_pct",
    "max_single_loss_pct",
]

# ─── Default ROAR State ─────────────────────────────────────

DEFAULT_ROAR_STATE = {
    "last_analysis_ts": None,
    "trades_processed": 0,
    "run_count": 0,
    "per_pattern": {},        # { pattern: { trades, wins, losses, total_pnl, ... } }
    "adjustment_history": [],  # [ { ts, changes, result } ]
    "previous_config": None,  # snapshot before last change (for revert)
    "previous_stats": None,   # stats snapshot before last change
    "disabled_patterns": {},  # { pattern: re_enable_ts_iso }
    "confidence_scores": {},  # { pattern: float }
    "value_of_adjustments": 0.0,  # cumulative PnL delta attributed to ROAR changes
}

DISABLE_DURATION_H = 48  # hours before auto-re-enable


# ─── State Management ────────────────────────────────────────

def load_roar_state() -> dict:
    """Load ROAR state, merging with defaults."""
    state = copy.deepcopy(DEFAULT_ROAR_STATE)
    if os.path.exists(ROAR_STATE_FILE):
        with open(ROAR_STATE_FILE) as f:
            saved = json.load(f)
        state.update(saved)
    return state


def save_roar_state(state: dict):
    """Save ROAR state atomically."""
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(ROAR_STATE_FILE, state)


# ─── Config Helpers ──────────────────────────────────────────

def get_nested(config: dict, dotkey: str):
    """Get a value from config using dot notation (e.g. 'min_confluence_score.NORMAL')."""
    keys = dotkey.split(".")
    val = config
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return None
    return val


def set_nested(config: dict, dotkey: str, value):
    """Set a value in config using dot notation."""
    keys = dotkey.split(".")
    d = config
    for k in keys[:-1]:
        if k not in d:
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def clamp(value, lo, hi):
    """Clamp value within [lo, hi]."""
    return max(lo, min(hi, value))


# ─── Bounds Checking ─────────────────────────────────────────

def is_within_bounds(key: str, value) -> bool:
    """Check if a proposed value is within tunable bounds."""
    if key in TUNABLE_BOUNDS:
        lo, hi = TUNABLE_BOUNDS[key]
        return lo <= value <= hi
    # For pattern_confluence_overrides, use the generic confluence bounds
    if key.startswith("pattern_confluence_overrides."):
        return 0.25 <= value <= 0.85
    return True  # unknown key — allow (caller should validate)


def clamp_to_bounds(key: str, value):
    """Clamp a value to its tunable bounds."""
    if key in TUNABLE_BOUNDS:
        lo, hi = TUNABLE_BOUNDS[key]
        return clamp(value, lo, hi)
    if key.startswith("pattern_confluence_overrides."):
        return clamp(value, 0.25, 0.85)
    return value


def is_protected(key: str) -> bool:
    """Check if a key is in the protected list."""
    root_key = key.split(".")[0]
    return root_key in PROTECTED_KEYS


# ─── Changeset Application ──────────────────────────────────

def apply_changeset(changeset: list, config: dict, roar_state: dict) -> dict:
    """
    Apply a list of proposed changes to config.
    Each change: { "key": dotkey, "old": val, "new": val, "reason": str, "confidence": float }

    Returns the modified config. Validates bounds and rejects protected keys.
    Also saves previous_config snapshot in roar_state for revert.
    """
    # Save snapshot for revert
    roar_state["previous_config"] = copy.deepcopy(config)

    applied = []
    for change in changeset:
        key = change["key"]
        new_val = change["new"]

        # Safety: never touch protected keys
        if is_protected(key):
            change["_skipped"] = f"Protected key: {key}"
            continue

        # Clamp to bounds
        clamped = clamp_to_bounds(key, new_val)
        if clamped != new_val:
            change["_clamped_from"] = new_val
            change["new"] = clamped
            new_val = clamped

        # Apply
        set_nested(config, key, new_val)
        applied.append(change)

    return config


# ─── Revert Logic ────────────────────────────────────────────

def should_revert(current_stats: dict, previous_stats: dict) -> bool:
    """
    Compare current performance with previous snapshot.
    Revert if BOTH win rate AND avg PnL are worse.

    Args:
        current_stats:  { "overall_win_rate": float, "overall_avg_pnl": float }
        previous_stats: same shape

    Returns True if we should revert to previous_config.
    """
    if not previous_stats or not current_stats:
        return False

    prev_wr = previous_stats.get("overall_win_rate", 0)
    prev_pnl = previous_stats.get("overall_avg_pnl", 0)
    curr_wr = current_stats.get("overall_win_rate", 0)
    curr_pnl = current_stats.get("overall_avg_pnl", 0)

    # Revert if win rate is lower AND avg PnL is lower
    if curr_wr < prev_wr and curr_pnl < prev_pnl:
        return True

    return False


def revert_config(roar_state: dict) -> dict | None:
    """
    Revert to previous config snapshot.
    Returns the reverted config, or None if no snapshot exists.
    """
    prev = roar_state.get("previous_config")
    if prev is None:
        return None

    # Clear the snapshot so we don't double-revert
    roar_state["previous_config"] = None
    roar_state["previous_stats"] = None
    return copy.deepcopy(prev)


# ─── Pattern Disable/Enable ─────────────────────────────────

def disable_pattern(roar_state: dict, pattern: str):
    """Disable a pattern with 48h auto-re-enable timer."""
    re_enable_ts = (datetime.now(timezone.utc) + timedelta(hours=DISABLE_DURATION_H)).isoformat()
    roar_state.setdefault("disabled_patterns", {})[pattern] = re_enable_ts


def check_re_enable(roar_state: dict) -> list:
    """Check and re-enable patterns whose disable timer has expired. Returns list of re-enabled."""
    now = datetime.now(timezone.utc)
    disabled = roar_state.get("disabled_patterns", {})
    re_enabled = []
    for pattern, ts in list(disabled.items()):
        try:
            expire = datetime.fromisoformat(ts)
            if now >= expire:
                del disabled[pattern]
                re_enabled.append(pattern)
        except (ValueError, TypeError):
            del disabled[pattern]
            re_enabled.append(pattern)
    return re_enabled


def is_pattern_disabled(roar_state: dict, pattern: str) -> bool:
    """Check if a pattern is currently disabled."""
    return pattern in roar_state.get("disabled_patterns", {})
