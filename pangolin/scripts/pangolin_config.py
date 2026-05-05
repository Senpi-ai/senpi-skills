"""PANGOLIN v2 — Shared MCP helper + atomic state I/O + output helpers.

v2 producer responsibilities are narrower than v1:
  - Fetch market data via MCP (market_list_instruments,
    leaderboard_get_markets, market_get_funding_regime,
    market_get_funding_history, strategy_get_clearinghouse_state)
  - Push signals via `openclaw senpi external-scanner ingest`
    (runtime owns execution)

Runtime handles: position tracking, DSL exits, risk guardrails,
trade counting, asset cooldowns. All of that state lives in the
runtime's state dir, not here.

This module provides:
  - load_config()    — read config/pangolin-config.json
  - mcporter_call()  — Senpi MCP call helper
  - atomic_write()   — atomic temp+rename write for JSON state files
  - output() / log() / now_iso() — output helpers

Per-wallet state (asset cooldowns, daily counter, lock) lives under
SKILL_DIR/state/<wallet-hash>/ — see pangolin-producer.py for the
wallet hashing.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "pangolin-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "pangolin-config.json"


# ─── senpi_runtime_helpers (opt-in via SENPI_USE_WRAPPER=1) ───
# When enabled:
#   - mcporter_call() routes via direct HTTPS to MCP (no mcporter subprocess)
#   - the producer's push_signal() routes via HTTP POST to the runtime's
#     /signals endpoint (no `openclaw senpi external-scanner ingest`
#     subprocess)
# Falls back automatically on any init error so the change is safe to ship
# behind the env flag.

_USE_WRAPPER = os.environ.get("SENPI_USE_WRAPPER", "").strip().lower() in ("1", "true", "yes")
_wrapper_client = None

if _USE_WRAPPER:
    try:
        _helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
        if _helpers_path not in sys.path:
            sys.path.insert(0, _helpers_path)
        from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore
        _wrapper_client = SenpiClient()
        log_event("pangolin_wrapper_enabled", helpers_path=_helpers_path)
    except Exception as _wrap_err:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] pangolin_wrapper_init_failed: {_wrap_err}\n"
        )
        _wrapper_client = None


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
    """Call a Senpi MCP tool.

    Routes through `senpi_runtime_helpers` when SENPI_USE_WRAPPER=1 (direct
    HTTPS, no mcporter subprocess, no 6-process spawn tree). Falls back to
    the legacy mcporter subprocess otherwise — same return shape either way
    (mcporter envelope `content[0].text` JSON unwrapped). Returns parsed JSON
    or None on failure.
    """
    if _wrapper_client is not None:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
        sys.stderr.write(
            f"[senpi_helpers] mcp_call_exhausted tool={tool} retries={retries} "
            f"err={type(last_err).__name__}: {last_err}\n"
        )
        return None

    # Legacy mcporter subprocess path — unchanged.
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool, "--args", args]
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            raw = json.loads(r.stdout)
            if isinstance(raw, dict) and "content" in raw:
                content = raw["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        try:
                            return json.loads(first["text"])
                        except (json.JSONDecodeError, TypeError):
                            pass
            return raw
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None
        except (json.JSONDecodeError, Exception):
            return None
    return None


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
