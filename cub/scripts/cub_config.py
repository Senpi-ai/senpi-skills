"""CUB v1.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

CUB — Two-Speed-Market (K-Shaped) Cross-Asset Long/Short Hedge Fund. ONE
producer script driven by the CUB_LEG env var into one of two thematic books,
each bound to its own wallet + runtime + DSL:

  CUB_LEG=long  -> "Haves" book. LONGS the structural winners of a two-speed
                    world — the AI complex on Hyperliquid XYZ (trade.xyz: NVDA,
                    AMD, MRVL, TSM, ASML, ARM, AVGO, CRWV, PLTR, ORCL, …) plus
                    the crypto winners (HYPE large, SOL modest), trend-confirmed.
                    config/cub-long-config.json -> cub_long_signals
  CUB_LEG=short -> "Have-nots" book. SHORTS the laggards — the broad U.S. equity
                    market via the SP500 index product (the "economy suffers"
                    core) plus a curated, tightly-gated basket of laggard crypto
                    alts, trend-confirmed (capitulation guard).
                    config/cub-short-config.json -> cub_short_signals

The thesis is a K-SHAPED divergence: the AI complex and HYPE/SOL keep booming
while the broad U.S. economy and the rest of crypto struggle. LONG the haves,
SHORT the have-nots — the P&L is the DISPERSION between the two speeds. Net
exposure (the long/short funding balance) is an explicit operator decision (see
each config's `_net_exposure_note`), defaulting to a modest net-long tilt that
matches the directional AI/HYPE conviction. NOT a copy-trader — each book ranks
and scores its own curated, cross-asset universe.

This module owns: leg resolution, config load, the senpi_runtime_helpers client,
the MCP call shim, account/position pulls, and the per-leg recent-signals
race-dedup cache. The producer (cub-producer.py) owns the thematic universe
build + trend/dispersion scoring + per-group sizing + emit. The runtime owns the
LLM gate, DSL exits, and all risk.guard_rails.

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
LEG = (os.environ.get("CUB_LEG") or "long").strip().lower()
if LEG not in ("long", "short", "preipo"):
    raise RuntimeError(
        f"CUB_LEG must be 'long', 'short' or 'preipo' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "cub-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"cub-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / f"recent-signals-{LEG}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

_WALLET_ENV = {"long": "CUB_LONG_WALLET", "short": "CUB_SHORT_WALLET", "preipo": "CUB_PREIPO_WALLET"}[LEG]
_STRATEGY_ENV = {"long": "CUB_LONG_STRATEGY_ID", "short": "CUB_SHORT_STRATEGY_ID", "preipo": "CUB_PREIPO_STRATEGY_ID"}[LEG]

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
            "SENPI_AUTH_TOKEN is not set. Cub's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("cub_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
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
            f"[cub-v1:{LEG}] mcp_call_failed tool={tool} "
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
    Cub holds both xyz equities and main-DEX crypto on the same wallet, so
    both sections routinely carry positions; iterate both, sum the
    account_value via max(), and collect positions from each.
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
    print(f"[cub-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
