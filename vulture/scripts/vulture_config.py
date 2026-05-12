"""VULTURE v4.0.0 — Shared config + MCP shim + helpers wrapper.

v4.0.0: senpi_runtime_helpers migration. mcporter_call now routes
through SenpiClient.mcp_call() (direct HTTPS) instead of mcporter
subprocess. _wrapper_client is exposed for the producer's signal
push (cfg._wrapper_client.push_signal(...)).
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "vulture-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "vulture-config.json"


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# senpi_runtime_helpers ships inside the senpi-trading-runtime skill.
# Global skills install under ~/.openclaw/skills/ on standard hosts
# (e.g. /data/.openclaw/skills/ on Railway). Some setups install user
# skills under ${OPENCLAW_WORKSPACE}/skills/. Probe both in order.
_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next(
    (p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()),
    _sdk_candidates[0],
)
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Vulture's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("vulture_wrapper_enabled", sdk_path=_sdk_path)
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
    """v4.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised."""
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] vulture_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[VULTURE-v3] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
