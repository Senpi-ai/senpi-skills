"""PYTHON v2.0.0 — Shared config + MCP shim + helpers wrapper.

v2.0.0: senpi_runtime_helpers migration. mcporter_call now routes
through SenpiClient.mcp_call().
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import functools
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "python-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "python-config.json"
STATE_DIR = SKILL_DIR / "state"
COOLDOWN_FILE = STATE_DIR / "asset-cooldowns.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)


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
            "SENPI_AUTH_TOKEN is not set. Python's MCP calls and signal "
            "POST both require it."
        )
    client = SenpiClient()
    log_event("python_wrapper_enabled", sdk_path=_sdk_path)
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
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy():
    wallet = os.environ.get("PYTHON_WALLET", "").strip()
    strategy_id = os.environ.get("PYTHON_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "").strip()
        strategy_id = strategy_id or config.get("strategyId", "").strip()
    return wallet, strategy_id


def mcp_call(tool, **params):
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        log_event("python_mcp_call_failed", tool=tool, error=str(e))
        return None


mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    ch = get_clearinghouse(wallet)
    if not ch:
        return 0, []
    data = ch.get("data", ch)
    positions, account_value = [], 0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value += float(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "margin": float(pos.get("marginUsed", 0)),
                "entryPrice": float(pos.get("entryPx", 0)),
                "size": abs(szi),
            })
    return account_value, positions


def is_asset_cooled_down(asset, cooldown_minutes=720):
    if not COOLDOWN_FILE.exists():
        return False
    try:
        with open(COOLDOWN_FILE) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    if asset not in cooldowns:
        return False
    last_ts = cooldowns[asset].get("ts", 0)
    elapsed_min = (time.time() - last_ts) / 60
    return elapsed_min < cooldown_minutes


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[python-v2] {msg}", file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()
