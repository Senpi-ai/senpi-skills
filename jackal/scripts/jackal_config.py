"""JACKAL v3 — Shared MCP helpers + config loader.

v3 producer responsibilities are narrower than v1:
  - Fetch trader universe and per-trader state via MCP (direct HTTPS via
    senpi_runtime_helpers.SenpiClient — no mcporter subprocess)
  - Push signals via SenpiClient.push_signal() (runtime owns execution)

Runtime handles: position tracking, DSL exits, risk guardrails, trade counting,
asset cooldowns. All of that state lives in the runtime's state dir, not here.

This module provides just the MCP call helper + config loader. State I/O for
the producer's own needs (trader pool cache, last-seen positions) lives in
jackal_state.py.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import functools
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "jackal-tracker"
CONFIG_PATH = SKILL_DIR / "config" / "jackal-config.json"


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
_sdk_path = str(Path(WORKSPACE) / "skills" / "senpi-trading-runtime")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Jackal's MCP calls and signal "
            "POST both require it."
        )
    client = SenpiClient()
    log_event("jackal_wrapper_enabled", sdk_path=_sdk_path)
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


# ─── MCP Helper ──────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=30, **params):
    """v3.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised."""
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] jackal_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


# ─── Output helpers ──────────────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[JACKAL-v2] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
