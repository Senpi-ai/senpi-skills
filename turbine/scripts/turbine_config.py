"""TURBINE v3.2 — Shared config + state I/O + helpers wrapper.

v3.2 (2026-05-08) — RUNNERS redesign.

v3.1's "hunt mode = HYPE-only momentum specialist" was the wrong
abstraction: ~$2,400 idle 90% of the time, no volume contribution
from the second wallet. The correct framing (clarified by Jason):
"run the volume play, but allow winners to run longer."

v3.2 keeps the two-wallet split (runtime-phase-2 still enforces
one runtime per wallet) but rewires it: BOTH wallets run the same
volume rotation alpha. The wallet boundary just selects DSL
behavior:

  Wallet A (volume):  own runtime turbine-volume-tracker
                      $4,000 funded ($500 × 7 slots + $500 buffer)
                      DSL: hard_timeout 10min, no Phase 2.
                      Pure rotation.

  Wallet B (runners): own runtime turbine-runners-tracker
                      $1,900 funded ($950 × 2 slots)
                      DSL: hard_timeout 240min (4h cap),
                      Phase 2 ratchet enabled.
                      SAME entries as volume; lets winners ride.

Both wallets receive the same volume-rotation signals from the
producer (same scoring, same asset universe, same funding-fade
direction). Most positions on either wallet exit at small loss/win.
The runners wallet's value: ~5% of entries land on a real
directional move that the 10-min hard_timeout would force-cut on
volume; runners' Phase 2 ratchet lets those ride to +20-50% margin
ROE before retrace.

v3.1 → v3.2 deltas:
  - "hunt" → "runners" everywhere (semantic rename)
  - HYPE-only specialty → general volume rotation on runners wallet
  - hunt_min_score / huntCooldownMin / minHuntWalletBalance dropped
  - Runners get the same scoring path as volume

Helpers-based (matches Cheetah v7.0.0 / Pangolin v2.2 patterns):
  - SenpiClient via lazy proxy (auth-validated on first use)
  - mcporter_call() shim for backward-compat call sites
  - _wrapper_client exposed for direct push_signal access

Helpers-based (matches Cheetah v7.0.0 / Pangolin v2.2 patterns):
  - SenpiClient via lazy proxy (auth-validated on first use)
  - mcporter_call() shim for backward-compat call sites
  - _wrapper_client exposed for direct push_signal access

Per-wallet state lives under SKILL_DIR/state/<wallet-hash>/:
  - last-closed.json  — close timestamps (post-close cooldown)
  - prev-held.json    — previous-tick held set (close detection)
  - cycle-stats.json  — rolling fill-rate window (volume only)
  - hunt-history.json — HYPE 4H breakout score history (hunt only)
  - rotation-index.json — volume rotation pointer
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "turbine-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "turbine-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
_helpers_path = str(Path(WORKSPACE) / "skills" / "_helpers")
if _helpers_path not in sys.path:
    sys.path.insert(0, _helpers_path)
from senpi_runtime_helpers import SenpiClient, log_event  # type: ignore  # noqa: E402


@functools.lru_cache(maxsize=1)
def _get_wrapper_client() -> SenpiClient:
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError(
            "SENPI_AUTH_TOKEN is not set. Turbine's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("turbine_wrapper_enabled", helpers_path=_helpers_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


# ─── Atomic write ─────────────────────────────────────────

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


# ─── Config ───────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_volume_wallet_and_strategy():
    """Resolve VOLUME wallet + strategyId. Env (TURBINE_VOLUME_WALLET /
    TURBINE_VOLUME_STRATEGY_ID) preferred → config.volume.{wallet,strategyId}
    fallback. STRATEGY_ADDRESS is BANNED per v2.0.9 contamination rule."""
    wallet = os.environ.get("TURBINE_VOLUME_WALLET", "").strip()
    strategy_id = os.environ.get("TURBINE_VOLUME_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        c = load_config().get("volume", {}) or {}
        wallet = wallet or (c.get("wallet") or "").strip()
        strategy_id = strategy_id or (c.get("strategyId") or "").strip()
    return wallet, strategy_id


def get_runners_wallet_and_strategy():
    """Resolve RUNNERS wallet + strategyId. Env (TURBINE_RUNNERS_WALLET /
    TURBINE_RUNNERS_STRATEGY_ID) preferred → config.runners.{wallet,strategyId}
    fallback. Empty wallet means runners mode is disabled — producer
    only emits volume signals on the volume wallet (graceful degradation
    for operators who only want pure volume engine)."""
    wallet = os.environ.get("TURBINE_RUNNERS_WALLET", "").strip()
    strategy_id = os.environ.get("TURBINE_RUNNERS_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        c = load_config().get("runners", {}) or {}
        wallet = wallet or (c.get("wallet") or "").strip()
        strategy_id = strategy_id or (c.get("strategyId") or "").strip()
    return wallet, strategy_id


# Legacy alias for v3.1 callers — to be removed once all callers updated.
def get_hunt_wallet_and_strategy():
    """DEPRECATED: use get_runners_wallet_and_strategy(). Kept temporarily
    for any v3.1 lingering references."""
    return get_runners_wallet_and_strategy()


def get_wallet_and_strategy():
    """Backward-compat shim: returns the volume wallet. Existing call
    sites that ask for "the" wallet are operating on volume mode."""
    return get_volume_wallet_and_strategy()


# ─── MCP wrapper shim ─────────────────────────────────────

def mcporter_call(tool, timeout=20, retries=2, **params):
    """Call a Senpi MCP tool via the senpi_runtime_helpers wrapper.

    v3.0: routes through SenpiClient.mcp_call() — direct HTTPS, no
    mcporter subprocess. Returns the unwrapped JSON document on
    success, or None if the wrapper raised.
    """
    try:
        return _wrapper_client.mcp_call(tool, timeout=timeout, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[senpi_helpers] turbine_mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


# ─── Account state helpers ────────────────────────────────

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def normalize_coin_key(coin):
    """Map 'xyz:GOLD' and 'GOLD' to the same canonical key."""
    if not coin:
        return ""
    return coin.split(":")[-1].upper()


def get_open_positions(wallet):
    """List of dicts with coin, direction, szi, size, margin, entry/markPrice,
    leverage, upnl. Both main + xyz dexes."""
    if not wallet:
        return []
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch or not isinstance(ch, dict):
        return []
    data = ch.get("data", ch)
    out = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap)
            szi = safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            out.append({
                "coin": pos.get("coin", ""),
                "dex": section,
                "direction": "LONG" if szi > 0 else "SHORT",
                "szi": szi,
                "size": abs(szi),
                "margin": safe_float(pos.get("marginUsed", 0)),
                "entryPrice": safe_float(pos.get("entryPx", 0)),
                "markPrice": safe_float(pos.get("markPx", 0)),
                "leverage": safe_float(
                    pos.get("leverage", {}).get("value", 5)
                    if isinstance(pos.get("leverage"), dict)
                    else pos.get("leverage", 5)
                ),
                "upnl": safe_float(pos.get("unrealizedPnl", 0)),
            })
    return out


def get_account_value(wallet):
    if not wallet:
        return 0.0
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch or not isinstance(ch, dict):
        return 0.0
    data = ch.get("data", ch)
    total = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        total += safe_float(ms.get("accountValue", 0))
    return total


def get_resting_orders(wallet):
    """Open (resting / ALO) non-reduceOnly orders. Reduce-only orders
    are DSL exit legs — not slot occupiers."""
    if not wallet:
        return []
    data = mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return []
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return []
    out = []
    for o in orders:
        if not isinstance(o, dict):
            continue
        if o.get("reduceOnly"):
            continue
        coin = o.get("coin") or o.get("asset", "")
        if not coin:
            continue
        side = (o.get("side") or "").upper()
        out.append({
            "coin": coin,
            "direction": "LONG" if side in ("B", "BUY") else "SHORT",
            "limit_price": safe_float(o.get("limitPx") or o.get("price")),
            "oid": o.get("oid"),
        })
    return out


# ─── Output / log ─────────────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[TURBINE-v3] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
