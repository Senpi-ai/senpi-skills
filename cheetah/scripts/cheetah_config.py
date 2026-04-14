"""CHEETAH APEX Strategy — Shared config, MCP helpers, state I/O.

v5.0 APEX rewrite. Previous versions of this file were a mantis
copy-paste with broken MANTIS_WALLET env var references.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "cheetah-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "cheetah-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


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
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy():
    wallet = os.environ.get("CHEETAH_WALLET", "")
    strategy_id = os.environ.get("CHEETAH_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


# ─── State I/O ───────────────────────────────────────────────

def load_state(filename="state.json"):
    path = STATE_DIR / filename
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(data, filename="state.json"):
    atomic_write(str(STATE_DIR / filename), data)


# ─── MCP Helpers ─────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via mcporter. Array syntax, no shell=True."""
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


def get_positions(wallet=None):
    """Returns (account_value, positions_list)."""
    if not wallet:
        wallet, _ = get_wallet_and_strategy()
    if not wallet:
        return 0, []
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch or not isinstance(ch, dict):
        return 0, []
    data = ch.get("data", ch)
    positions = []
    account_value = 0
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
                "szi": szi,
                "size": abs(szi),
                "margin": float(pos.get("marginUsed", 0)),
                "entryPrice": float(pos.get("entryPx", 0)),
                "markPrice": float(pos.get("markPx", 0)),
                "leverage": float(
                    pos.get("leverage", {}).get("value", 5)
                    if isinstance(pos.get("leverage"), dict)
                    else pos.get("leverage", 5)
                ),
                "upnl": float(pos.get("unrealizedPnl", 0)),
            })
    return account_value, positions


def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[CHEETAH-APEX] {msg}", file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
