"""FALCON v1.0.0 — Shared config + MCP shim + helpers wrapper.

Conversion-Event Momentum (XYZ Pre-IPO → equity). Falcon watches Hyperliquid
XYZ instruments and detects the moment a Pre-IPO Perpetual (IPOP) CONVERTS to a
standard equity perp — the funding signature jumps ~100x (~6.25e-8 → ~6.25e-6)
and the trade.xyz Discovery Bounds price throttle lifts. That transition opens a
window of free price discovery; Falcon trades the post-conversion momentum.

Distinct from Lemur (which trades the pre-IPO basket while it is STILL an IPOP).
Falcon only fires AROUND the conversion — it requires a tracked IPOP→STANDARD
class flip (or a freshly-listed standard equity, if enabled) plus confirmed
momentum, then rides the discovery move with a WIDE "let winners run" DSL.

Architecture: helpers-native producer + senpi-trading-runtime LLM gate +
momentum DSL (wide ladder, long hard_timeout). Mirrors the Badger/Piranha
state-cache pattern (instrument-class cache + conversion-window cache) plus the
fleet-standard race-window dedup, atomic write, simplified producer_daemon.
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "falcon-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "falcon-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / "recent-signals.json"
CLASS_STATE_PATH = STATE_DIR / "instrument-class.json"
CONVERSIONS_PATH = STATE_DIR / "conversions.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Race-window dedup TTL. Falcon ticks every 600s; ALO fills typically settle
# within 30-60s. 240s covers the full fill window plus the next-tick
# clearinghouseState refresh, so the on-chain held-asset check picks up where
# this cache leaves off.
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
            "SENPI_AUTH_TOKEN is not set. Falcon's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("falcon_wrapper_enabled", sdk_path=_sdk_path)
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
    document on success, or None if the wrapper raised (transient failures
    recover on the next tick)."""
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        sys.stderr.write(
            f"[senpi_helpers] falcon_mcp_call_failed tool={tool} "
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
    # read-sanity guard (funding/$0 glitch 2026-06): a corrupt clearinghouse read can report
    # margin/notional IN USE while returning an EMPTY positions list; sizing or running the
    # held-asset dedup off that re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, float(_ms.get("totalMarginUsed", 0) or 0), abs(float(_ms.get("totalNtlPos", 0) or 0)))
    if _use > 1.0 and not positions:
        return 0.0, []
    return account_value, positions


# ─── Atomic write + recent-signals race-window dedup ─────────

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


# ─── Instrument-class cache (detect IPOP → STANDARD conversion) ──
#
# market_list_instruments exposes funding + max_leverage per xyz instrument.
# Falcon classifies each into IPOP vs STANDARD by the funding signature, then
# compares against the prior-tick classification to detect the conversion flip.
# Stores {name: {"class": "IPOP"|"STANDARD", "ts": epoch}}.

def read_class_state():
    if not CLASS_STATE_PATH.exists():
        return {}
    try:
        with open(CLASS_STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_class_state(state):
    try:
        atomic_write(CLASS_STATE_PATH, state)
    except OSError:
        pass


# ─── Conversion-window cache (stay eligible after the flip) ──────
#
# When Falcon detects an IPOP→STANDARD flip it stamps the instrument here so it
# stays eligible to trade post-conversion momentum for conversionWindowHours,
# not just on the single flip tick. Stores {name: detected_epoch}.

def read_conversions():
    if not CONVERSIONS_PATH.exists():
        return {}
    try:
        with open(CONVERSIONS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def record_conversion(name, now=None):
    if not name:
        return
    now = now if now is not None else time.time()
    conv = read_conversions()
    conv[name] = now
    try:
        atomic_write(CONVERSIONS_PATH, conv)
    except OSError:
        pass


def prune_conversions(window_hours):
    """Drop conversion stamps older than window_hours. Returns the live dict."""
    cutoff = time.time() - (float(window_hours) * 3600.0)
    conv = {k: v for k, v in read_conversions().items() if v >= cutoff}
    try:
        atomic_write(CONVERSIONS_PATH, conv)
    except OSError:
        pass
    return conv


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[falcon-v1] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
