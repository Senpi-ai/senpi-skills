#!/usr/bin/env python3
# Senpi MANTIS Config v6.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""MANTIS v6.0.0 — Slipstream — Configuration constants + helpers wrapper.

v6.0.0: senpi_runtime_helpers migration. mcporter_call replaced by
SenpiClient.mcp_call() (direct HTTPS). _wrapper_client is exposed
for the producer's signal push + direct close_position calls (the
leader-reversal veto, which is producer-authoritative).

Override numeric thresholds via config/mantis-config.json or env vars.
"""

import functools
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "mantis-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "mantis-config.json"
STATE_DIR = SKILL_DIR / "state"
ENTRY_LOG_FILE = STATE_DIR / "entry-log.jsonl"
POSITION_META_FILE = STATE_DIR / "position-metadata.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)


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
            "SENPI_AUTH_TOKEN is not set. Mantis's MCP calls and signal "
            "POST both require it. Set it on the runtime host before "
            "starting the producer daemon."
        )
    client = SenpiClient()
    log_event("mantis_wrapper_enabled", sdk_path=_sdk_path)
    return client


class _WrapperClientProxy:
    def __getattr__(self, name: str):
        return getattr(_get_wrapper_client(), name)


_wrapper_client = _WrapperClientProxy()


# ─── Leader universe ─────────────────────────────────────────────
# Only BTC has pre-computed lag data in the v1 cross-asset flow tool.
# Add ETH/SOL/HYPE as Sarvesh ships their pre-computed coverage.
LEADER_UNIVERSE = ["BTC"]


# ─── Entry filters ───────────────────────────────────────────────
MIN_FOLLOW_RATE = 0.85
MIN_CONFIDENCE = 0.75
MIN_GAP_PCT = 1.5
REQUIRE_SM_ROTATION = True
MAX_LAG_STDDEV_MINUTES = 90


# ─── Sizing tiers (conviction-scaled off the tool's confidence score) ───
SIZING_TIERS = [
    {"confidence_min": 0.92, "margin_pct": 75, "leverage": 8},
    {"confidence_min": 0.85, "margin_pct": 50, "leverage": 7},
    {"confidence_min": 0.75, "margin_pct": 25, "leverage": 5},
]
MAX_LEVERAGE = 8
MAX_POSITION_NOTIONAL_PCT = 75


# ─── Exit / DSL ──────────────────────────────────────────────────
HARD_TIMEOUT_LAG_MULTIPLIER = 1.5
HARD_TIMEOUT_FLOOR_MINUTES = 30
HARD_TIMEOUT_CEILING_MINUTES = 240
LEADER_REVERSAL_VETO_PCT = 1.0


# ─── Risk controls (informational mirror of runtime.yaml guard_rails) ───
# Authoritative values now live in runtime.yaml risk.guard_rails block.
# These constants stay for legacy doc reference + producer-side defense.
MAX_CONCURRENT_POSITIONS = 2
COOLDOWN_PER_ASSET_MINUTES = 240
MAX_DAILY_ENTRIES = 6


# ─── Atomic write ────────────────────────────────────────────────

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


# ─── Config overlay ──────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy():
    """Resolve (wallet, strategyId) — env var first, config.json second.

    Env var resolution for wallet:
      1. MANTIS_WALLET — fleet-standard <SKILL>_WALLET name (v2.0.9 rule)
      2. cfg.load_config()["wallet"] — canonical source on disk
    """
    wallet = os.environ.get("MANTIS_WALLET", "").strip()
    strategy_id = os.environ.get("MANTIS_STRATEGY_ID", "").strip()
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "").strip()
        strategy_id = strategy_id or config.get("strategyId", "").strip()
    return wallet, strategy_id


def _apply_config_overlay():
    """Load config/mantis-config.json and overlay onto module globals."""
    cfg_dict = load_config()
    for k, v in cfg_dict.items():
        if k in globals() and not k.startswith("_"):
            globals()[k] = v


# ─── MCP helpers ─────────────────────────────────────────────────

def mcp_call(tool, **params):
    """Call a Senpi MCP tool via in-process SenpiClient.mcp_call().

    Replaces v5.0 mcporter subprocess. ~10-50× faster (~280ms vs
    2.5-5s cold start). Same call signature so legacy call sites
    keep working.
    """
    try:
        return _wrapper_client.mcp_call(tool, **params)
    except Exception as e:  # noqa: BLE001
        log_event("mantis_mcp_call_failed", tool=tool, error=str(e))
        return None


# Backward-compat alias — keeps any legacy `cfg.mcporter_call(...)` call
# sites working even though the underlying transport is in-process now.
mcporter_call = mcp_call


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


def get_cross_asset_flows(leader_asset):
    """Wrap the cross-asset flow MCP tool. Returns the flow result or None."""
    return mcp_call("market_get_cross_asset_flows", leader_asset=leader_asset)


# ─── Output / log ────────────────────────────────────────────────

def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[MANTIS-v6] {msg}", file=sys.stderr)
    sys.stderr.flush()


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


_apply_config_overlay()
