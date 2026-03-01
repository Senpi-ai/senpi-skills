"""
senpi_state — Shared state management for Senpi trading skills.

Provides deterministic, atomic state operations so that LLM agents never
need to write JSON state files directly.  Every state mutation is journaled
as an append-only JSONL event for auditability.

Modules:
    atomic      — crash-safe JSON read/write primitives
    mcporter    — unified mcporter CLI wrapper with retry
    journal     — append-only trade event journal
    positions   — full position lifecycle (enter / close) with journaling
    validation  — DSL state schema validation
"""

from senpi_state.atomic import atomic_write, load_json, deep_merge
from senpi_state.mcporter import mcporter_call, mcporter_call_safe
from senpi_state.journal import TradeJournal, TradeEvent
from senpi_state.validation import validate_dsl_state

__all__ = [
    "atomic_write", "load_json", "deep_merge",
    "mcporter_call", "mcporter_call_safe",
    "TradeJournal", "TradeEvent",
    "validate_dsl_state",
]
