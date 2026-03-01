"""
DSL state validation — shared across all Senpi trading skills.

Validates that DSL state dicts contain all required keys before they're
written to disk.  Catches corrupt/incomplete state early rather than
letting it silently break the trailing stop engine at runtime.
"""

from __future__ import annotations

DSL_REQUIRED_KEYS = [
    "asset", "direction", "entryPrice", "size", "leverage",
    "highWaterPrice", "phase", "currentBreachCount",
    "currentTierIndex", "tierFloorPrice", "tiers", "phase1",
]

PHASE1_REQUIRED_KEYS = ["retraceThreshold", "consecutiveBreachesRequired"]


def validate_dsl_state(state, context: str = "") -> tuple[bool, str | None]:
    """Validate a DSL state dict has all required keys.

    Args:
        state: The parsed JSON state dict.
        context: Optional context string for error messages (e.g. file path).

    Returns:
        (True, None) if valid, (False, error_message) if invalid.
    """
    ctx = f" ({context})" if context else ""

    if not isinstance(state, dict):
        return False, f"state is not a dict{ctx}"

    missing = [k for k in DSL_REQUIRED_KEYS if k not in state]
    if missing:
        return False, f"missing keys {missing}{ctx}"

    phase1 = state.get("phase1")
    if not isinstance(phase1, dict):
        return False, f"phase1 is not a dict{ctx}"

    missing_p1 = [k for k in PHASE1_REQUIRED_KEYS if k not in phase1]
    if missing_p1:
        return False, f"phase1 missing keys {missing_p1}{ctx}"

    if not isinstance(state.get("tiers"), list):
        return False, f"tiers is not a list{ctx}"

    return True, None
