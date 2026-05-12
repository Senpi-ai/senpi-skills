"""GRIZZLY v7.0.0 — Shared config + MCP shim + helpers wrapper.

v7.0.0: senpi_runtime_helpers migration. mcporter_call now routes
through SenpiClient.mcp_call() (direct HTTPS) instead of mcporter
subprocess. _wrapper_client is exposed for the producer's signal
push (cfg._wrapper_client.push_signal(...)). All other helpers
(atomic_write, load_config, get_wallet_and_strategy, get_positions,
trade-counter I/O, output/log) preserved verbatim.
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "grizzly-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "grizzly-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# Pattern ported verbatim from cheetah/polar/wolverine/kodiak_config.py:
# mount the helpers package at module load, defer SenpiClient
# construction until first attribute access.

_helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Grizzly's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("grizzly_wrapper_enabled", helpers_path=_helpers_path)
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
            json.dump(data, f, indent=2, default=str)
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
    wallet = os.environ.get("GRIZZLY_WALLET", "")
    strategy_id = os.environ.get("GRIZZLY_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


def load_trade_counter():
    today = now_date()
    p = STATE_DIR / "trade-counter.json"
    default = {
        "date": today, "entries": 0, "realizedPnl": 0,
        "gate": "OPEN", "gateReason": None, "cooldownUntil": None,
        "lastResults": [],
        "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0,
    }
    if p.exists():
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != today:
                tc["date"] = today
                tc["entries"] = 0
                tc["realizedPnl"] = 0
                tc["lastResults"] = []
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["date"] = now_date()
    atomic_write(str(STATE_DIR / "trade-counter.json"), tc)


# ─── State I/O (v5.0 — 3-mode lifecycle) ─────────────────────

def load_state(filename="grizzly-state.json"):
    path = STATE_DIR / filename
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_state(data, filename="grizzly-state.json"):
    atomic_write(str(STATE_DIR / filename), data)


def is_asset_cooled_down(asset, cooldown_minutes=180):
    p = STATE_DIR / "cooldowns.json"
    if not p.exists():
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(asset)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


def set_asset_cooldown(asset, cooldown_minutes=180):
    p = STATE_DIR / "cooldowns.json"
    cooldowns = {}
    if p.exists():
        try:
            with open(p) as f:
                cooldowns = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    cooldowns[asset] = {
        "until": time.time() + cooldown_minutes * 60,
        "set_at": now_iso(),
    }
    atomic_write(str(p), cooldowns)


def mcporter_call(tool, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via the senpi_runtime_helpers wrapper.

    v7.0.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised. `retries` parameter
    preserved for call-site compat but not implemented — daemon
    recovers transient failures on the next tick.
    """
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        sys.stderr.write(
            f"[senpi_helpers] grizzly_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


def get_positions(wallet=None):
    if not wallet:
        wallet, _ = get_wallet_and_strategy()
    if not wallet:
        return 0, []
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch or not isinstance(ch, dict):
        return 0, []
    data = ch.get("data", ch)
    positions = []
    account_value = 0
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
                "szi": szi,
                "size": abs(szi),
                "margin": float(pos.get("marginUsed", 0)),
                "entryPrice": float(pos.get("entryPx", 0)),
                "markPrice": float(pos.get("markPx", 0)),
                "leverage": float(
                    pos.get("leverage", {}).get("value", 5)
                    if isinstance(pos.get("leverage"), dict)
                    else pos.get("leverage", 5)
                ),
                "upnl": float(pos.get("unrealizedPnl", 0)),
            })
    return account_value, positions


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[GRIZZLY] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
