"""SPIDER v5.0 — Shared config + MCP shim + helpers wrapper (LEG-AWARE).

v5.0.0 (2026-06-02): two-persona rebuild. Spider is now ONE producer
script driven by the SPIDER_LEG env var into one of two style legs,
each bound to its own wallet + runtime + DSL:

  SPIDER_LEG=swing  -> Tech & AI multi-day momentum (LONG only)
                       config/spider-swing-config.json  -> spider_swing_signals
  SPIDER_LEG=scalp  -> Macro & majors fast mean-reversion (BOTH dirs)
                       config/spider-scalp-config.json  -> spider_scalp_signals

This module owns: leg resolution, config load, the senpi_runtime_helpers
client, the MCP call shim, account/position pulls, and the per-leg
recent-signals race-dedup cache. The producer (spider-producer.py) owns
scoring + emit. The runtime owns the LLM gate, DSL exits, and all
risk.guard_rails. NOT a copy-trader — each leg scores its own universe.

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
# One script, two legs. SPIDER_LEG selects the config file, the
# scanner name, the wallet env var, and the recent-signals cache.
LEG = (os.environ.get("SPIDER_LEG") or "swing").strip().lower()
if LEG not in ("swing", "scalp"):
    raise RuntimeError(
        f"SPIDER_LEG must be 'swing' or 'scalp' (got {LEG!r}). "
        "Set it on the runtime host before starting the producer daemon."
    )

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "spider-strategy"
CONFIG_PATH = SKILL_DIR / "config" / f"spider-{LEG}-config.json"
STATE_DIR = SKILL_DIR / "state"
RECENT_SIGNALS_PATH = STATE_DIR / f"recent-signals-{LEG}.json"
# First-seen ledger for the swing leg's dynamic XYZ-equity universe.
# Tracks when each XYZ instrument first appeared so freshly-listed names
# (e.g. a new Pre-IPO Perpetual) can be auto-caught. Per-leg for symmetry.
XYZ_FIRST_SEEN_PATH = STATE_DIR / f"xyz-first-seen-{LEG}.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# Per-leg wallet / strategy env var names. The runtime YAMLs bind
# ${SPIDER_SWING_WALLET} / ${SPIDER_SCALP_WALLET}; the producer reads
# the same names so producer and runtime always agree on the wallet.
_WALLET_ENV = "SPIDER_SWING_WALLET" if LEG == "swing" else "SPIDER_SCALP_WALLET"
_STRATEGY_ENV = "SPIDER_SWING_STRATEGY_ID" if LEG == "swing" else "SPIDER_SCALP_STRATEGY_ID"

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
            "SENPI_AUTH_TOKEN is not set. Spider's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("spider_wrapper_enabled", leg=LEG, sdk_path=_sdk_path)
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
            f"[spider-v5:{LEG}] mcp_call_failed tool={tool} "
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
    every position size 2x too large (margin_usd = account_value *
    margin_pct downstream). max() is exact whether the views mirror
    (equal → max is the shared value), one view is empty/0 (the populated
    view wins), or positions are open on both sub-DEXs (still one shared
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


# ─── first-seen cache (dynamic-universe new-listing detection) ─────
#
# The swing leg builds its XYZ-equity universe dynamically each tick.
# To auto-catch freshly listed names (e.g. a new Pre-IPO Perpetual like
# CBRS/Cerebras) the producer records when each XYZ instrument first
# appeared. A name younger than xyzFreshDays is auto-eligible even if it
# isn't in the curated include-set. On the very first run (no state file)
# every current name is treated as already-old so the auto-catch doesn't
# fire across the whole board at once — only names that appear AFTER
# deploy are "fresh".

def read_first_seen():
    """Return {name: epoch_seconds} of when each instrument was first seen."""
    if not XYZ_FIRST_SEEN_PATH.exists():
        return {}
    try:
        with open(XYZ_FIRST_SEEN_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def write_first_seen(data):
    """Persist the first-seen map atomically. Best-effort."""
    try:
        atomic_write(XYZ_FIRST_SEEN_PATH, data)
    except OSError:
        pass


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[spider-v5:{LEG}] {msg}", file=sys.stderr, flush=True)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
