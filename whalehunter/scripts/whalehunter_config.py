"""WHALEHUNTERHEDGE v1.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

WHALEHUNTERHEDGE — a long/short copy book that follows the SINGLE highest-conviction
trades of CONSISTENT + PATIENT Hyperliquid winners. It watches traders tagged
ELITE (consistency) AND PATIENT (activity) on Senpi Discover — traders who sit on
their hands for long stretches, so when they finally commit a big slice of their
own balance to a new position, that is their highest-conviction read. WhaleHunter
mirrors that strike and rides it on a wide DSL.

ONE producer script, two INDEPENDENT sleeves on SEPARATE wallets, selected by
WHALEHUNTER_LEG:

  WHALEHUNTER_LEG=long   the LONG sleeve. Mirrors whales' high-conviction LONG strikes.
  WHALEHUNTER_LEG=short  the SHORT sleeve. Mirrors whales' high-conviction SHORT strikes.

The two sleeves are on SEPARATE wallets so the book can hold CONFLICTING positions
on the same asset at once — if one elite whale is high-conviction LONG ETH while a
different elite whale is high-conviction SHORT ETH, the long sleeve holds ETH-long
and the short sleeve holds ETH-short simultaneously (no netting). The combined book
is a genuine long/short hedge driven by whale conviction; funding default 50/50.

This module owns: leg + wallet resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls (account_value via max(main,
xyz) — never sum), and the cached trader pool + per-leg position baseline. The
producer (whalehunter-producer.py) owns pool refresh, conviction-strike detection,
and emit. The runtime owns the LLM gate (pass-through), the wide DSL, and risk.
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
LEG = (os.environ.get("WHALEHUNTER_LEG") or "long").strip().lower()
if LEG not in ("long", "short"):
    raise RuntimeError(
        f"WHALEHUNTER_LEG must be 'long' or 'short' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "whalehunter-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"whalehunter-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
# The trader pool is shared across both sleeves (same winners); the position
# baseline is per-leg (each sleeve diffs only its own direction).
POOL_PATH = STATE_DIR / "pool.json"
LAST_SEEN_PATH = STATE_DIR / f"last-seen-{LEG}.json"
# v2.0 cohort engine: smart/crowd membership (shared, daily) + the per-coin
# net-positioning ledger (shared, one snapshot per UTC day) for the "adding daily" trend.
COHORT_PATH = STATE_DIR / "cohorts.json"
LEDGER_PATH = STATE_DIR / "cohort-ledger.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

_WALLET_ENV = "WHALEHUNTER_LONG_WALLET" if LEG == "long" else "WHALEHUNTER_SHORT_WALLET"
_STRATEGY_ENV = "WHALEHUNTER_LONG_STRATEGY_ID" if LEG == "long" else "WHALEHUNTER_SHORT_STRATEGY_ID"


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
            "SENPI_AUTH_TOKEN is not set. WhaleHunter's MCP calls and signal POST "
            "both require it (and a USER-scoped token for discovery_* tools). Set it "
            "on the runtime host before starting the producer daemon."
        )
    client = SenpiClient()
    log_event("whalehunter_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
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
            f"[whalehunter-v1:{LEG}] mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts]). 'main' and 'xyz' are two VIEWS of
    ONE cross-margined wallet reporting the SAME accountValue — take it ONCE via
    max(), never sum (summing doubles every size)."""
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
                "margin": float(pos.get("marginUsed", 0) or 0),
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


# ─── Pool + baseline state ───────────────────────────────────

def load_pool():
    if POOL_PATH.exists():
        try:
            with open(POOL_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_pool(data):
    try:
        atomic_write(POOL_PATH, data)
    except OSError:
        pass


def load_last_seen():
    if LAST_SEEN_PATH.exists():
        try:
            with open(LAST_SEEN_PATH) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_last_seen(data):
    try:
        atomic_write(LAST_SEEN_PATH, data)
    except OSError:
        pass


def _load_json(path):
    if path.exists():
        try:
            with open(path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_cohorts():
    return _load_json(COHORT_PATH)


def save_cohorts(data):
    try:
        atomic_write(COHORT_PATH, data)
    except OSError:
        pass


def load_ledger():
    return _load_json(LEDGER_PATH)


def save_ledger(data):
    try:
        atomic_write(LEDGER_PATH, data)
    except OSError:
        pass


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[whalehunter-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()
