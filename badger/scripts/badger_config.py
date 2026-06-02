"""BADGER v1.0.0 — Shared config + MCP shim + helpers wrapper.

OI-Divergence Breakout Anticipator. Multi-asset whitelist
(BTC/ETH/SOL/HYPE). Open interest is the fuel of a breakout: a price
push confirmed by RISING open interest = new money committing = real
follow-through. A breakout on flat/declining OI is a fakeout. Badger
takes the OI-confirmed breakout, long or short, with Smart-Money
agreement, and hands it to a wide DSL ladder so a real breakout can run.

Architecture: helpers-native producer + senpi-trading-runtime LLM gate
+ wide DSL Phase 2 ladder. Mirrors the Wolverine v6.0.0 / Bison v3.0.1
pattern (race-window dedup, atomic write, simplified producer_daemon).
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "badger-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "badger-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / "recent-signals.json"
OI_STATE_PATH = STATE_DIR / "oi-state.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Race-window dedup TTL. Badger ticks every 300s; ALO fills typically
# settle within 30-60s. 240s covers the full fill window plus next-tick
# clearinghouseState refresh, so the on-chain held-asset check picks up
# where this cache leaves off.
RECENT_SIGNAL_TTL_SEC = 240


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
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
            "SENPI_AUTH_TOKEN is not set. Badger's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("badger_wrapper_enabled", sdk_path=_sdk_path)
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

def mcp_call(tool, **params):
    """Call a Senpi MCP tool via the helpers wrapper. Returns the JSON
    document on success, or None if the wrapper raised (transient
    failures recover on the next tick)."""
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        sys.stderr.write(
            f"[senpi_helpers] badger_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


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
        account_value = max(account_value, float(ms.get("accountValue", 0)))  # one wallet, two sub-DEX views -> count equity ONCE (summing double-counts the shared free balance -> 2x sizing)
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


# ─── Atomic write + recent-signals race-window dedup ─────────
#
# Mirrors bison v3.0.1 / wolverine v6.0.0 pattern. After
# push_signal(coin) returns OK, record_signal(coin) writes
# {coin: epoch_seconds} to state/recent-signals.json. main() calls
# was_recently_signaled(coin) BEFORE push_signal and skips emission
# during the 30-90s gap between push_signal returning OK and the
# resulting position appearing in clearinghouseState.

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


# ─── OI state cache (for self-computed OI velocity) ──────────
#
# market_get_asset_data exposes oi_velocity, but it can be null. When
# it is, Badger falls back to a self-computed OI delta: it persists the
# last-seen openInterest per asset and compares on the next tick. Stores
# {coin: {"oi": float, "ts": epoch}}.

def read_oi_state():
    if not OI_STATE_PATH.exists():
        return {}
    try:
        with open(OI_STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def record_oi(coin, oi):
    if not coin or oi is None:
        return
    state = read_oi_state()
    state[coin.upper()] = {"oi": float(oi), "ts": time.time()}
    try:
        atomic_write(OI_STATE_PATH, state)
    except OSError:
        pass


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[badger-v1] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
