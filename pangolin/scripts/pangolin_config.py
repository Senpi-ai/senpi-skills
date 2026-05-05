"""PANGOLIN v2 — Shared MCP helper + atomic state I/O + output helpers.

v2 producer responsibilities are narrower than v1:
  - Fetch market data via MCP (market_list_instruments,
    leaderboard_get_markets, market_get_funding_regime,
    market_get_funding_history, strategy_get_clearinghouse_state)
  - Push signals via direct HTTP POST to the runtime API on 127.0.0.1
    through `senpi_runtime_helpers.SenpiClient.push_signal` (no
    `openclaw senpi external-scanner ingest` subprocess; no CLI cold
    start). The runtime owns execution.

Runtime handles: position tracking, DSL exits, risk guardrails,
trade counting, asset cooldowns. All of that state lives in the
runtime's state dir, not here.

This module provides:
  - load_config()    — read config/pangolin-config.json
  - mcporter_call()  — Senpi MCP call helper, routes via wrapper
  - atomic_write()   — atomic temp+rename write for JSON state files
  - output() / log() / now_iso() — output helpers
  - _wrapper_client  — process-wide SenpiClient (lazy, see pangolin-producer)

Per-wallet state (asset cooldowns, daily counter, lock) lives under
SKILL_DIR/state/<wallet-hash>/ — see pangolin-producer.py for the
wallet hashing.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "pangolin-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "pangolin-config.json"


# ─── senpi_runtime_helpers ───
# `wrapped-skills` ships the helpers package alongside the skill, so the
# wrapper is guaranteed available. Import is hard-required — if it fails,
# the deployment is broken and we want to scream, not silently fall back.

_helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore

_wrapper_client = SenpiClient()
log_event("pangolin_wrapper_enabled", helpers_path=_helpers_path)


# ─── Config ──────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically via tmp file + os.replace."""
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── MCP Helper ──────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=25, **params):
    """Call a Senpi MCP tool via the wrapper. Direct HTTPS, no subprocess.

    `retries` is accepted for caller-API compatibility but unused — the
    wrapper has its own timeout. If the wrapper raises, we let it propagate
    (we are testing the wrapper; silencing errors defeats the point).
    """
    return _wrapper_client.mcp_call(tool, timeout=timeout, **params)


# ─── Output helpers ──────────────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[PANGOLIN-v2] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
