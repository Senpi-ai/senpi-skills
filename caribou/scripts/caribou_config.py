"""CARIBOU v1.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

CARIBOU — Cross-Asset Trend Fund (managed futures / CTA). Trend-follows a
maximally diversified universe spanning EVERY asset class on Hyperliquid —
crypto, xyz stocks, indices, metals, energy — long the uptrends and short the
downtrends, each position sized to EQUAL RISK (volatility parity), capped per
asset class so it can never collapse into a single-class book. The migration:
follow the trend across all terrain.

ONE producer script, two INDEPENDENT sleeves on SEPARATE wallets, selected by
CARIBOU_LEG:

  CARIBOU_LEG=long   the LONG sleeve. Scans the whole cross-asset universe and
                     opens LONGS on assets in a confirmed uptrend.
  CARIBOU_LEG=short  the SHORT sleeve. Scans the same universe and opens SHORTS
                     on assets in a confirmed downtrend.

The two sleeves are deliberately on separate wallets so the fund is NOT
restricted from holding the SAME asset in opposite directions at once — e.g. the
long sleeve trails out a stale ETH long while the short sleeve opens a fresh ETH
short on a trend flip. Each wallet nets per-asset on its own; across the two
wallets, opposite-direction exposure on one asset is allowed.

This module owns: leg + wallet resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls (account_value via max(main,
xyz) — never sum), and the per-leg recent-signals race-dedup cache. The producer
(caribou-producer.py) owns the cross-asset trend scoring + vol-parity sizing +
class caps + emit. The runtime owns the LLM gate (pass-through), DSL exits, risk.
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
# One script, two sleeves. CARIBOU_LEG selects the config file, the scanner
# name, the wallet env var, and the recent-signals cache.
LEG = (os.environ.get("CARIBOU_LEG") or "long").strip().lower()
if LEG not in ("long", "short"):
    raise RuntimeError(
        f"CARIBOU_LEG must be 'long' or 'short' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "caribou-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"caribou-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / f"recent-signals-{LEG}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Per-leg wallet / strategy env var names. The runtime YAMLs bind
# ${CARIBOU_LONG_WALLET} / ${CARIBOU_SHORT_WALLET}; the producer reads the same
# names so producer and runtime always agree on the wallet.
_WALLET_ENV = "CARIBOU_LONG_WALLET" if LEG == "long" else "CARIBOU_SHORT_WALLET"
_STRATEGY_ENV = "CARIBOU_LONG_STRATEGY_ID" if LEG == "long" else "CARIBOU_SHORT_STRATEGY_ID"

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
            "SENPI_AUTH_TOKEN is not set. Caribou's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("caribou_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
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
            f"[caribou-v1:{LEG}] mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts]). 'main' and 'xyz' are two VIEWS
    of ONE cross-margined wallet that both report the SAME marginSummary
    accountValue (the whole wallet's equity). Take account value ONCE via max()
    across the two views — NEVER sum, or every position size doubles. Positions
    ARE per-sub-DEX, so those are collected from both."""
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
    p = RECENT_SIGNALS_PATH
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def record_signal(coin):
    if not coin:
        return
    now = time.time()
    signals = {k: v for k, v in _read_recent_signals().items() if v >= now - RECENT_SIGNAL_TTL_SEC * 4}
    signals[coin.upper()] = now
    try:
        atomic_write(RECENT_SIGNALS_PATH, signals)
    except OSError:
        pass


def was_recently_signaled(coin, ttl_sec=RECENT_SIGNAL_TTL_SEC):
    if not coin:
        return False
    last = _read_recent_signals().get(coin.upper())
    return last is not None and (time.time() - last) < ttl_sec


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[caribou-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()
