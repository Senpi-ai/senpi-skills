"""HYDRA v1.0 — Shared config + MCP shim + helpers wrapper (COIN- + LEG-AWARE).

HYDRA — Single-Coin Portfolio Fund. A complete book on ONE major: a directional
thesis bet + a complementary dip-buyer + a stress-gated short hedge, each on its
own wallet ("head"). ONE producer script, parameterized by TWO env vars:

  HYDRA_COIN  — the asset this deployment trades (e.g. ETH, SOL, HYPE; any
                main-DEX perp, or an xyz: name). Set per deployment.
  HYDRA_LEG   — which head this wallet runs:
     core   -> the thesis bet. Trend-momentum, conviction-tiered, LONG the
               uptrend / SHORT a confirmed downtrend.
     dip    -> the complement. Buys PULLBACKS within a confirmed uptrend only
               (stands down in downtrends — never knife-catches the hedge).
     hedge  -> the hedge. Stress-gated SHORT — fires only when a confirmed
               downtrend + a fast-drawdown signal agree; idle (tiny bleed) in
               uptrends. Cushions the long heads during the flip.

Deploy a fund for one coin = three wallets (core/dip/hedge), all with the same
HYDRA_COIN. Run it for ETH, SOL, and HYPE = nine wallets, one codebase. The heads
are gated to different regimes so they never take opposing positions at once;
the fund is NET-LONG the coin, pressed on dips and cushioned on breaks.

This module owns: coin + leg resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls, and the per-(coin,leg)
recent-signals race-dedup cache. The producer (hydra-producer.py) owns the
per-leg scoring + emit. The runtime owns the LLM gate, DSL exits, and risk.
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

# ─── Leg + coin resolution ───────────────────────────────────
# Hydra ships as named per-coin VARIANTS (Hydra-ETH / Hydra-SOL / Hydra-HYPE) over
# ONE shared engine. A variant = a coin (HYDRA_COIN) + its three head wallets
# (HYDRA_LEG ∈ core/dip/hedge), each loading its own pinned, per-coin-tuned config:
#   config/hydra-<coin>-<leg>-config.json   (e.g. hydra-eth-core-config.json)
# The coin is part of the config PATH, so each variant is self-contained — the
# picker just recommends "Hydra-ETH" like any named strategy; no parameterized-
# fund machinery needed in the picker/ops layer.
LEG = (os.environ.get("HYDRA_LEG") or "core").strip().lower()
if LEG not in ("core", "dip", "hedge"):
    raise RuntimeError(
        f"HYDRA_LEG must be 'core', 'dip' or 'hedge' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

_WALLET_ENV = "HYDRA_WALLET"
_STRATEGY_ENV = "HYDRA_STRATEGY_ID"
_COIN_ENV = "HYDRA_COIN"

# Coin drives the config path, so it resolves from the environment (default ETH) —
# it cannot depend on the config file it is used to locate.
_coin_raw = (os.environ.get(_COIN_ENV) or "ETH").strip()
COIN = _coin_raw if _coin_raw.lower().startswith("xyz:") else _coin_raw.upper()
COIN_SLUG = COIN.lower().replace("xyz:", "")

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "hydra-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"hydra-{COIN_SLUG}-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)

RECENT_SIGNAL_TTL_SEC = 180


def resolve_coin():
    """The asset this variant trades — set by HYDRA_COIN (env), default ETH.
    Upper-cased; an xyz: prefix is preserved (lower-case) if present."""
    return COIN


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
            "SENPI_AUTH_TOKEN is not set. Hydra's MCP calls and signal POST both "
            "require it. Set it on the runtime host before starting the producer daemon."
        )
    client = SenpiClient()
    log_event("hydra_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
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
            f"[hydra-v1:{LEG}] mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts]). 'main' and 'xyz' sections are
    two VIEWS of ONE cross-margined wallet — take account value via max(), never
    sum (summing double-counts and doubles every position size)."""
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

def _recent_path():
    return STATE_DIR / f"recent-signals-{COIN_SLUG}-{LEG}.json"


def _read_recent_signals():
    p = _recent_path()
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
        atomic_write(_recent_path(), signals)
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
    print(f"[hydra-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()
