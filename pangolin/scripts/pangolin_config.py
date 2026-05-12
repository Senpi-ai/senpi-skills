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

import functools
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


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# `wrapped-skills` ships the helpers package alongside the skill so the
# wrapper is always available. Import is sys.path-mounted at module load,
# but the SenpiClient construction is deferred to first use via
# `_get_wrapper_client()`. Two reasons for lazy:
#   1. Importing pangolin_config (e.g. from a test, REPL, or a sibling
#      module that wants `now_iso()`) shouldn't instantiate a network
#      client or write to stderr.
#   2. SENPI_AUTH_TOKEN is validated explicitly on first use — a missing
#      token raises loudly here instead of silently producing a 401 on
#      the first MCP call.

_helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    """Lazy SenpiClient accessor with explicit auth validation."""
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Pangolin's MCP calls and signal "
            "POST both require it. Set it on the runtime host (e.g. as a "
            "Railway service variable) before starting the producer."
        )
    client = SenpiClient()
    log_event("pangolin_wrapper_enabled", helpers_path=_helpers_path)
    return client


class _WrapperClientProxy:
    """Module-level attribute that defers SenpiClient construction until first
    attribute access. Preserves the legacy `cfg._wrapper_client.mcp_call(...)`
    call shape used by the producer + scanner without forcing eager
    instantiation at import time.

    All three of `__getattr__`, `__setattr__`, `__delattr__` are forwarders —
    the proxy holds NO state of its own. `__getattr__` alone is not enough:
    `__setattr__` is always called on assignment (it's not a fallback like
    `__getattr__`), so without delegation a stray `cfg._wrapper_client.X = Y`
    would land on the proxy's `__dict__` and a subsequent `cfg._wrapper_client.X`
    would find it via normal lookup, bypassing `__getattr__` entirely — i.e. a
    disconnected attribute that is invisible to the real `SenpiClient`. The
    canonical example is `cache.py`'s `setattr(client, "_cache", new_cache)`
    fallback path. SenpiClient now eager-inits `_cache` so that exact path is
    unreachable today, but the same hazard exists for any future caller that
    writes to the proxy. Delegate both ways to close it permanently.
    """

    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(_get_wrapper_client(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(_get_wrapper_client(), name)


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

def mcporter_call(tool, timeout=25, **params):
    """Call a Senpi MCP tool via the wrapper. Direct HTTPS, no subprocess.

    Returns the unwrapped JSON document on success, or `None` if the wrapper
    raised a transport / protocol error. Per-call retries are intentionally
    not implemented here — a transient failure is recovered by the daemon's
    next tick (5 minutes); the producer's pre-existing `if not r:` early
    returns continue to handle the no-data case gracefully.
    """
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001 — transport/protocol surface
        sys.stderr.write(
            f"[senpi_helpers] mcporter_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
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
