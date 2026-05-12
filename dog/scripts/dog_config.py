"""DOG v3.0.0 — Shared config + MCP shim + helpers wrapper.

v3.0.0: senpi_runtime_helpers migration. mcporter_call now routes
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
import tempfile
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "dog-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "dog-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


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
            "SENPI_AUTH_TOKEN is not set. Dog's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("dog_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


def atomic_write(path, data):
    path = str(path)
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f: return json.load(f)
    return {}


def get_wallet_and_strategy():
    """Resolve (wallet, strategyId) — env var first, config.json second.

    Env var resolution for wallet (first non-empty wins):
      1. DOG_WALLET — fleet-standard <SKILL>_WALLET name (v2.0.9 rule)
      2. cfg.load_config()["wallet"] — canonical source on disk
    """
    wallet = os.environ.get("DOG_WALLET", "").strip()
    strategy_id = os.environ.get("DOG_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "").strip()
        strategy_id = strategy_id or config.get("strategyId", "").strip()
    return wallet, strategy_id


def mcp_call(tool, **params):
    """Direct MCP call via SenpiClient (in-process HTTPS).

    Replaces v2.x mcporter subprocess. ~10-50× faster (~280ms vs
    2.5-5s cold start). Same call signature as v2's mcporter_call so
    legacy call sites (preserved from dog-scanner.py) keep working.
    """
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        log_event("dog_mcp_call_failed", tool=tool, error=str(e))
        return None


# Backward-compat alias — keeps any legacy `cfg.mcporter_call(...)` call
# sites working even though the underlying transport is in-process now.
mcporter_call = mcp_call


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[DOG] {msg}", file=sys.stderr)
    sys.stderr.flush()
