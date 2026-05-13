"""CONDOR v4.0.0 — Shared config + MCP shim + helpers wrapper.

v4.0.0: senpi_runtime_helpers migration. mcporter_call now routes
through SenpiClient.mcp_call() (direct HTTPS) instead of mcporter
subprocess. _wrapper_client is exposed for the producer's signal
push (cfg._wrapper_client.push_signal(...)).
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "condor-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "condor-config.json"
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
            "SENPI_AUTH_TOKEN is not set. Condor's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("condor_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


# --- Atomic Write ---

def atomic_write(path, data):
    """Write JSON atomically via tmp file + os.replace."""
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


# --- Config ---

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy():
    """Resolve (wallet, strategyId) — env var first, config.json second.

    Env var resolution for wallet (first non-empty wins):
      1. CONDOR_WALLET — fleet-standard <SKILL>_WALLET name (v2.0.9 rule)
      2. cfg.load_config()["wallet"] — canonical source on disk
    """
    wallet = os.environ.get("CONDOR_WALLET", "").strip()
    strategy_id = os.environ.get("CONDOR_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "").strip()
        strategy_id = strategy_id or config.get("strategyId", "").strip()
    return wallet, strategy_id


# --- Trade counter (preserved for post-exit cooldown tracking only) ---

def load_trade_counter():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = STATE_DIR / "trade-counter.json"
    default = {"date": today, "entries": 0, "last_entry_ts": 0}
    if path.exists():
        try:
            with open(path) as f:
                tc = json.load(f)
            if tc.get("date") != today:
                tc["date"] = today
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["updatedAt"] = now_iso()
    atomic_write(str(STATE_DIR / "trade-counter.json"), tc)


# --- MCP Helper ---

def mcp_call(tool, **params):
    """Direct MCP call via SenpiClient (in-process HTTPS).

    Replaces v3.x mcporter subprocess. ~10-50× faster (~280ms vs
    2.5-5s cold start). Same call signature as v3's mcporter_call so
    legacy call sites (preserved from condor-scanner.py) keep working.
    """
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        log_event("condor_mcp_call_failed", tool=tool, error=str(e))
        return None


# Backward-compat alias — keeps any legacy `cfg.mcporter_call(...)` call
# sites working even though the underlying transport is in-process now.
mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts])."""
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


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[condor-v4] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
