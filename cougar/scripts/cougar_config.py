"""COUGAR v1.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

COUGAR — U.S. Equity Long/Short Hedge Fund. ONE producer script driven by the
COUGAR_LEG env var into one of two books over the tokenized U.S. equity universe
on Hyperliquid XYZ (trade.xyz: NVDA, TSLA, AAPL, AMZN, … + the index products),
each bound to its own wallet + runtime + DSL:

  COUGAR_LEG=long  -> Long book. Longs the relative-strength LEADERS of the
                     equity cross-section (highest 24h excess return vs the
                     equity-universe mean), trend-confirmed. Blow-off guard.
                     config/cougar-long-config.json -> cougar_long_signals
  COUGAR_LEG=short -> Short book. Shorts the relative-strength LAGGARDS (lowest
                     excess), trend-confirmed. Capitulation guard.
                     config/cougar-short-config.json -> cougar_short_signals

Run on two equally-funded wallets, the long-leaders / short-laggards pair is
~market-neutral and harvests EQUITY DISPERSION — the spread between the best and
worst tokenized stocks, which is at multi-decade extremes. The edge is
stock-selection, not market direction. NOT a copy-trader — each book ranks and
scores its own equity cross-section.

This module owns: leg resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls, and the per-leg
recent-signals race-dedup cache. The producer (cougar-producer.py) owns the
cross-sectional RS rank + dispersion scoring + emit. The runtime owns the LLM
gate, DSL exits, and all risk.guard_rails.

Plumbing: MCP calls + signal POSTs route through
senpi_runtime_helpers.SenpiClient (in-process, direct HTTPS). No
mcporter / openclaw subprocesses.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills

import functools
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Leg resolution ──────────────────────────────────────────
LEG = (os.environ.get("COUGAR_LEG") or "long").strip().lower()
if LEG not in ("long", "short"):
    raise RuntimeError(
        f"COUGAR_LEG must be 'long' or 'short' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "cougar-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"cougar-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / f"recent-signals-{LEG}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

_WALLET_ENV = "COUGAR_LONG_WALLET" if LEG == "long" else "COUGAR_SHORT_WALLET"
_STRATEGY_ENV = "COUGAR_LONG_STRATEGY_ID" if LEG == "long" else "COUGAR_SHORT_STRATEGY_ID"

RECENT_SIGNAL_TTL_SEC = 180


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
            "SENPI_AUTH_TOKEN is not set. Cougar's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("cougar_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


# ─── Atomic Write ────────────────────────────────────────────

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


# ─── Config ──────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_wallet_and_strategy():
    wallet = os.environ.get(_WALLET_ENV, "").strip()
    strategy_id = os.environ.get(_STRATEGY_ENV, "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or (config.get("wallet") or "").strip()
        strategy_id = strategy_id or (config.get("strategyId") or "").strip()
    return wallet, strategy_id


# ─── MCP Helper ──────────────────────────────────────────────

def mcp_call(tool, **params):
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[cougar-v1:{LEG}] mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts]).

    The 'main' and 'xyz' clearinghouse sections are TWO VIEWS of ONE
    cross-margined Senpi wallet, NOT two separate collateral silos. Both
    views report the SAME marginSummary.accountValue. So account_value is
    taken ONCE via max() across the two sections — never summed. Summing
    double-counts the balance and makes every position size 2x too large.
    """
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
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0) or 0),
                "margin": float(pos.get("marginUsed", 0) or 0),
                "entryPrice": float(pos.get("entryPx", 0) or 0),
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


# ─── recent-signals cache (held-asset dedup race-fix) ─────────

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
    print(f"[cougar-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
