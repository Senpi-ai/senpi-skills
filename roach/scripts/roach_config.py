"""ROACH v2 — Shared MCP helpers + config loader + atomic state I/O.

v2 producer responsibilities are narrower than v1:
  - Fetch market concentration via MCP (leaderboard_get_markets, market_get_asset_data)
  - Push signals via `SenpiClient.push_signal()` direct HTTP POST (runtime owns execution)

Runtime handles: position tracking, DSL exits, risk guardrails, trade counting,
asset cooldowns. All of that state lives in the runtime's state dir, not here.

This module provides:
  - load_config()    — read config/roach-config.json
  - mcporter_call()  — Senpi MCP call helper
  - atomic_write()   — atomic temp+rename write for JSON state files
  - output() / log() / now_iso() — output helpers

Per-wallet state (scan history, asset cooldowns, last-emitted) lives under
SKILL_DIR/state/<wallet-hash>/ — see roach-producer.py for the wallet hashing.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import functools
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "roach-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "roach-config.json"


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
_sdk_path = str(Path(WORKSPACE) / "skills" / "senpi-trading-runtime")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Roach's MCP calls and signal "
            "POST both require it."
        )
    client = SenpiClient()
    log_event("roach_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


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
    """v3.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised."""
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] roach_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


# ─── Output helpers ──────────────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[ROACH-v2] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
