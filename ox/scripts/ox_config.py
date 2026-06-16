"""OX v1.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

OX — Risk-Parity / All-Weather Hedge Fund. ONE producer script driven by the
OX_LEG env var into one of two books, each bound to its own wallet + runtime +
DSL. Ox's distinctive mechanic is INVERSE-VOLATILITY position sizing: instead of
a flat margin_pct per position, it weights each sleeve by 1/realized_vol so that
no single asset class dominates portfolio risk — true risk parity. It is the
fund line's CORE holding: always invested, low leverage, low turnover.

  OX_LEG=core    -> All-Weather Core book. Holds a vol-balanced LONG basket
                   across asset-class sleeves (crypto majors + equity indices +
                   metals + energy + FX), inverse-vol weighted to a portfolio
                   budget. Always invested, rebalances slowly, wide DSL.
                   config/ox-core-config.json -> ox_core_signals
  OX_LEG=ballast -> Defensive Ballast book. Holds defensives (gold / dollar /
                   yen / silver), inverse-vol weighted, scaled UP when a light
                   risk-off lean confirms (equities soft + gold/dollar bid) —
                   the active downside cushion. LONG only.
                   config/ox-ballast-config.json -> ox_ballast_signals

The edge is RISK BALANCING + diversification, NOT a directional bet (Wolf), NOT
crisis convexity (Rhino), NOT per-asset trend (Elephant). Ox never goes fully to
cash and never shorts — it is the steady core you hold while betting with the
other funds.

This module owns: leg resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls, and the per-leg
recent-signals race-dedup cache. The producer (ox-producer.py) owns the
realized-vol estimator, inverse-vol weighting, scoring + emit. The runtime owns
the LLM gate, DSL exits, and all risk.guard_rails. NOT a copy-trader — each book
scores its own sleeve basket.

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
# One script, two books. OX_LEG selects the config file, the scanner
# name, the wallet env var, and the recent-signals cache.
LEG = (os.environ.get("OX_LEG") or "core").strip().lower()
if LEG not in ("core", "ballast"):
    raise RuntimeError(
        f"OX_LEG must be 'core' or 'ballast' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "ox-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"ox-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / f"recent-signals-{LEG}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Per-leg wallet / strategy env var names. The runtime YAMLs bind
# ${OX_CORE_WALLET} / ${OX_BALLAST_WALLET}; the producer reads the same
# names so producer and runtime always agree on the wallet.
_WALLET_ENV = "OX_CORE_WALLET" if LEG == "core" else "OX_BALLAST_WALLET"
_STRATEGY_ENV = "OX_CORE_STRATEGY_ID" if LEG == "core" else "OX_BALLAST_STRATEGY_ID"

# How long after a push_signal() we treat the asset as "in-flight" and
# refuse to re-emit. 180s = 3x the typical ALO open fill window. Covers
# the race between push_signal() returning OK and the resulting position
# appearing in the next-tick clearinghouse pull. On-chain held-asset
# check is the safety floor underneath this.
RECENT_SIGNAL_TTL_SEC = 180


# ─── senpi_runtime_helpers (lazy + auth-validated) ───
# senpi_runtime_helpers ships inside the senpi-trading-runtime skill.
# Global skills install under ~/.openclaw/skills/ on standard hosts
# (e.g. /data/.openclaw/skills/ on Railway). Some setups install user
# skills under ${OPENCLAW_WORKSPACE}/skills/. Probe both in order.
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
            "SENPI_AUTH_TOKEN is not set. Ox's MCP calls and signal POST "
            "both require it. Set it on the runtime host before starting the "
            "producer daemon."
        )
    client = SenpiClient()
    log_event("ox_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically via tmp file + os.replace."""
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
    """Resolve (wallet, strategyId) — leg env var first, config.json second."""
    wallet = os.environ.get(_WALLET_ENV, "").strip()
    strategy_id = os.environ.get(_STRATEGY_ENV, "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or (config.get("wallet") or "").strip()
        strategy_id = strategy_id or (config.get("strategyId") or "").strip()
    return wallet, strategy_id


# ─── MCP Helper ──────────────────────────────────────────────

def mcp_call(tool, **params):
    """Direct MCP call via SenpiClient (in-process HTTPS). Returns the
    unwrapped JSON document on success, or None if the wrapper raised."""
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(
            f"[ox-v1:{LEG}] mcp_call_failed tool={tool} "
            f"err={type(e).__name__}: {e}\n"
        )
        return None


# Backward-compat alias — keeps legacy `cfg.mcporter_call(...)` call
# sites working even though the transport is in-process now.
mcporter_call = mcp_call


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcp_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, [position_dicts]).

    The 'main' and 'xyz' clearinghouse sections are TWO VIEWS of ONE
    cross-margined Senpi wallet, NOT two separate collateral silos. Both
    views report the SAME marginSummary.accountValue (the whole wallet's
    equity). So account_value is taken ONCE via max() across the two
    sections — never summed. Summing double-counts the balance and makes
    every position size 2x too large (margin sizes downstream are derived
    from account_value). max() is exact whether the views mirror (equal →
    max is the shared value), one view is empty/0 (the populated view
    wins), or positions are open on both sub-DEXs (still one shared
    cross-margin balance). assetPositions ARE per-sub-DEX, so those are
    still enumerated across both sections.
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
#
# Each successful push_signal(coin) writes {coin: epoch_seconds} to
# recent-signals-<leg>.json. The producer checks this BEFORE scoring and
# skips coins seen within RECENT_SIGNAL_TTL_SEC. This covers the gap
# between push_signal returning OK and the resulting position appearing
# in the next-tick clearinghouse pull. The on-chain held-asset check
# (get_positions) is the safety floor underneath.

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
    """Drop entries older than 4x TTL to keep the file bounded."""
    cutoff = now - (RECENT_SIGNAL_TTL_SEC * 4)
    return {k: v for k, v in signals.items() if v >= cutoff}


def record_signal(coin):
    """Mark `coin` as recently signaled. Writes atomically."""
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
    """True if `coin` was pushed within `ttl_sec`."""
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
    print(f"[ox-v1:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
