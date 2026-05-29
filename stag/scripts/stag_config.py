"""STAG v1.0.0 — Shared config + MCP shim + helpers wrapper.

Parabolic-Run Hunter. Stag is the entry-side pair for the parabolic_runner
DSL preset. Its job is to enter LONG only when a real parabolic-runner setup
is in place — established structural trend (price > 200-bar 4h SMA + fresh
7d high), 7d move >= 25% (this defines "parabolic"), volume surging vs the
7d baseline, acceleration (recent half at least as fast as full window), AND
Smart Money aligned LONG >= 60%. All five gates required.

The point isn't to fire often. The point is: when Stag does fire, the
parabolic_runner DSL holds the position through the 5-8% intraday gyrations
that would chop out a normal trend trail, and only releases at the structural
breakdown or the late lock tiers.

Architecture: helpers-native producer + senpi-trading-runtime LLM gate +
parabolic_runner DSL preset. Stateless evaluation + race-window dedup +
24h per-asset cooldown (don't re-enter after a bad take). Producer NEVER
closes — DSL owns exits.
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "stag-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "stag-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / "recent-signals.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Race-window dedup TTL. Stag ticks every 600s; ALO fills typically settle
# within 30-60s. 240s covers the full fill window plus the next-tick
# clearinghouseState refresh.
RECENT_SIGNAL_TTL_SEC = 240


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
            "SENPI_AUTH_TOKEN is not set. Stag's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("stag_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def mcp_call(tool, **params):
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] stag_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


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


def _read_recent_signals():
    if not RECENT_SIGNALS_PATH.exists():
        return {}
    try:
        with open(RECENT_SIGNALS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _prune_recent_signals(signals, now):
    cutoff = now - (RECENT_SIGNAL_TTL_SEC * 4)
    return {k: v for k, v in signals.items() if v >= cutoff}


def record_signal(coin):
    if not coin:
        return
    now = time.time()
    signals = _prune_recent_signals(_read_recent_signals(), now)
    signals[coin.upper()] = now
    try:
        atomic_write(RECENT_SIGNALS_PATH, signals)
    except OSError:
        pass


def was_recently_signaled(coin, ttl_sec=RECENT_SIGNAL_TTL_SEC):
    if not coin:
        return False
    signals = _read_recent_signals()
    last = signals.get(coin.upper())
    if last is None:
        return False
    return (time.time() - last) < ttl_sec


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[stag-v1] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
