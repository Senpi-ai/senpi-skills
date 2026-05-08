"""JACKAL v2 — State persistence for the producer.

Tracks:
  - pool.json:    current active trader pool (refreshed daily)
  - last-seen.json: last-known open positions per pool member
                  (used to detect new entries via diff)

State lives under SKILL_DIR/state/. Writes are atomic (temp-file + rename)
so concurrent producer runs don't corrupt the file.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import os
import tempfile
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "jackal-tracker"
STATE_DIR = SKILL_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

POOL_FILE = STATE_DIR / "pool.json"
LAST_SEEN_FILE = STATE_DIR / "last-seen.json"


def _atomic_write(path, data):
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_load(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_pool():
    data = _safe_load(POOL_FILE)
    if not data:
        return {"refreshed_at": 0, "traders": []}
    return data


def save_pool(data):
    _atomic_write(POOL_FILE, data)


def load_last_seen():
    data = _safe_load(LAST_SEEN_FILE)
    if not data:
        return {}
    # Normalize: addr keys lowercase
    return {k.lower(): v for k, v in data.items() if isinstance(v, list)}


def save_last_seen(by_addr):
    normalized = {k.lower(): v for k, v in by_addr.items()}
    _atomic_write(LAST_SEEN_FILE, normalized)
