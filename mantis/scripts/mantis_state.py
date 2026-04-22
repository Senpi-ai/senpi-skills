#!/usr/bin/env python3
# Senpi MANTIS State v5.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""MANTIS v5.0 — State management.

Persistent state lives at ${STATE_DIR}/. Survives session clears.
Three state surfaces:
  - asset-cooldowns.json   — per-asset cooldown timestamps (re-entry prevention)
  - entry-log.jsonl        — append-only log of every Mantis decision
  - position-metadata.json — per-position metadata for leader-reversal tracking
                             (leader_asset, leader_pct_at_entry, expected_lag, etc.)
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import mantis_config as cfg


# ─── Asset cooldowns ─────────────────────────────────────────────

def load_cooldowns() -> Dict[str, Any]:
    if cfg.COOLDOWN_FILE.exists():
        try:
            with open(cfg.COOLDOWN_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cooldowns(cooldowns: Dict[str, Any]):
    cfg.atomic_write(str(cfg.COOLDOWN_FILE), cooldowns)


def is_asset_in_cooldown(asset: str, cooldown_minutes: int = None) -> bool:
    if cooldown_minutes is None:
        cooldown_minutes = cfg.COOLDOWN_PER_ASSET_MINUTES
    cooldowns = load_cooldowns()
    if asset not in cooldowns:
        return False
    last_ts = cooldowns[asset].get("ts", 0)
    elapsed_min = (time.time() - last_ts) / 60
    return elapsed_min < cooldown_minutes


def mark_asset_cooldown(asset: str, reason: str = "entry"):
    cooldowns = load_cooldowns()
    cooldowns[asset] = {
        "ts": time.time(),
        "iso": cfg.now_iso(),
        "reason": reason,
    }
    save_cooldowns(cooldowns)


# ─── Entry log (append-only) ─────────────────────────────────────

def append_entry_log(event_type: str, **kwargs) -> Dict[str, Any]:
    """Append a structured JSONL line. Survives session clears."""
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


def count_entries_today() -> int:
    """Count STRIKE events from current UTC day."""
    cutoff = time.time() - 86400
    records = read_entry_log(limit=500)
    n = 0
    for r in records:
        if r.get("event") != "STRIKE":
            continue
        if r.get("ts", 0) < cutoff:
            continue
        n += 1
    return n


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
    Catches positions that closed via DSL or external action."""
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
            note="position no longer in clearinghouse — closed via DSL or external action",
        )
        del meta[a]
    save_position_metadata(meta)
    return closed
