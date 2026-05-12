"""WOLVERINE v5.0.0 — Shared config + MCP shim + helpers wrapper.

v5.0.0: senpi_runtime_helpers migration. mcporter_call now routes
through SenpiClient.mcp_call() (direct HTTPS) instead of mcporter
subprocess. _wrapper_client is exposed for the producer's signal
push (cfg._wrapper_client.push_signal(...)). All other helpers
preserved verbatim.
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "wolverine-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "wolverine-config.json"


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# Pattern ported verbatim from cheetah/polar/kodiak_config.py: import
# the SDK at module load, defer SenpiClient construction
# until first attribute access. SENPI_AUTH_TOKEN validated on first use.

_sdk_path = str(Path(WORKSPACE) / "skills" / "senpi-trading-runtime")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Wolverine's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("wolverine_wrapper_enabled", sdk_path=_sdk_path)
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
    """Call a Senpi MCP tool via the senpi_runtime_helpers wrapper.

    v5.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised. `retries` parameter
    preserved for call-site compat but not implemented — daemon
    recovers transient failures on the next tick.
    """
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        sys.stderr.write(
            f"[senpi_helpers] wolverine_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[WOLVERINE-v4] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
