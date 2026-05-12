#!/usr/bin/env python3
# Senpi MANTIS State v6.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""MANTIS v6.0.0 — State management.

v6.0.0 migration: dropped asset-cooldowns.json + daily-entry counting
(both now handled by runtime.yaml risk.guard_rails). What remains is
producer-authoritative metadata that the runtime CANNOT model:

  - position-metadata.json — per-position {leader_asset, leader_pct_at_entry,
    expected_lag_minutes, ...}. REQUIRED for the leader-reversal veto pass.
    The runtime has no way to express "close this position if a separate
    asset's 4h move reverses by 1% from its value at entry time."
  - entry-log.jsonl — append-only log of every Mantis decision
    (STRIKE, NO_ENTRY, LEADER_REVERSAL_EXIT, POSITION_CLOSED_DETECTED).
    Pure observability — never read for decision-gating in v6.
"""

import json
import time
from typing import Any, Dict, List, Optional

import mantis_config as cfg


# ─── Entry log (append-only) — observability only ───────────────

def append_entry_log(event_type: str, **kwargs) -> Dict[str, Any]:
    """Append a structured JSONL line. Survives session clears.

    v6.0.0: this log is OBSERVABILITY ONLY. Daily-entry counting is
    handled by runtime.yaml risk.guard_rails.max_entries_per_day.
    """
    record = {
        "ts": time.time(),
        "iso": cfg.now_iso(),
        "event": event_type,
    }
    record.update(kwargs)
    try:
        with open(cfg.ENTRY_LOG_FILE, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (IOError, OSError):
        pass
    return record


def read_entry_log(limit: int = 200) -> List[Dict[str, Any]]:
    if not cfg.ENTRY_LOG_FILE.exists():
        return []
    try:
        with open(cfg.ENTRY_LOG_FILE) as f:
            lines = f.readlines()
    except (IOError, OSError):
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ─── Position metadata (per-trade tracking for leader-reversal veto) ───

def load_position_metadata() -> Dict[str, Dict[str, Any]]:
    if cfg.POSITION_META_FILE.exists():
        try:
            with open(cfg.POSITION_META_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_position_metadata(meta: Dict[str, Dict[str, Any]]):
    cfg.atomic_write(str(cfg.POSITION_META_FILE), meta)


def set_position_metadata(asset: str, metadata: Dict[str, Any]):
    """Store leader-reversal-veto data for an open position."""
    meta = load_position_metadata()
    meta[asset.upper()] = {
        "ts": time.time(),
        "iso": cfg.now_iso(),
        **metadata,
    }
    save_position_metadata(meta)


def get_position_metadata(asset: str) -> Optional[Dict[str, Any]]:
    meta = load_position_metadata()
    return meta.get(asset.upper())


def clear_position_metadata(asset: str):
    meta = load_position_metadata()
    if asset.upper() in meta:
        del meta[asset.upper()]
        save_position_metadata(meta)


def reconcile_position_metadata(open_position_assets: List[str]):
    """Remove metadata for assets no longer in the open position list.
    Catches positions that closed via DSL, veto, or external action."""
    meta = load_position_metadata()
    open_set = {a.upper() for a in open_position_assets}
    closed = [a for a in list(meta.keys()) if a not in open_set]
    if not closed:
        return []
    for a in closed:
        append_entry_log(
            "POSITION_CLOSED_DETECTED",
            asset=a,
            metadata=meta[a],
            note="position no longer in clearinghouse — closed via DSL, veto, or external action",
        )
        del meta[a]
    save_position_metadata(meta)
    return closed
