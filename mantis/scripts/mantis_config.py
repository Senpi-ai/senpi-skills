#!/usr/bin/env python3
# Senpi MANTIS Config v5.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""MANTIS v5.0 — Slipstream — Configuration constants + MCP helpers.

Cross-asset catchup hunter. Strikes correlated alts that haven't yet
responded to a leader's move, before the catchup completes.

Override via config/mantis-config.json or environment variables.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "mantis-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "mantis-config.json"
STATE_DIR = SKILL_DIR / "state"
COOLDOWN_FILE = STATE_DIR / "asset-cooldowns.json"
ENTRY_LOG_FILE = STATE_DIR / "entry-log.jsonl"
POSITION_META_FILE = STATE_DIR / "position-metadata.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)


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


# ─── Risk controls ───────────────────────────────────────────────
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
    wallet = os.environ.get("MANTIS_WALLET", "")
    strategy_id = os.environ.get("MANTIS_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


def _apply_config_overlay():
    """Load config/mantis-config.json and overlay onto module globals."""
    cfg = load_config()
    for k, v in cfg.items():
        if k in globals() and not k.startswith("_"):
            globals()[k] = v


# ─── MCP helpers ─────────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=25, **params):
    """Call a Senpi MCP tool via mcporter."""
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool, "--args", args]
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            raw = json.loads(r.stdout)
            if isinstance(raw, dict) and "content" in raw:
                content = raw["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        try:
                            return json.loads(first["text"])
                        except (json.JSONDecodeError, TypeError):
                            pass
            return raw
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None
        except (json.JSONDecodeError, Exception):
            return None
    return None


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


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
        account_value += float(ms.get("accountValue", 0))
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
    return account_value, positions


def get_cross_asset_flows(leader_asset):
    """Wrap the new MCP tool. Returns the flow result or None."""
    return mcporter_call("market_get_cross_asset_flows", leader_asset=leader_asset)


# ─── Output / log ────────────────────────────────────────────────

def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(f"[MANTIS-v5] {msg}", file=sys.stderr)
    sys.stderr.flush()


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


_apply_config_overlay()
