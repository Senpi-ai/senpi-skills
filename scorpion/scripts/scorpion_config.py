"""SCORPION Strategy — Shared config, MCP helpers, state I/O."""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "scorpion-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "scorpion-config.json"
STATE_DIR = SKILL_DIR / "state"
COOLDOWN_FILE = STATE_DIR / "asset-cooldowns.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path, data):
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


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_wallet_and_strategy():
    wallet = os.environ.get("SCORPION_WALLET", "")
    strategy_id = os.environ.get("SCORPION_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


def now_date_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_trade_counter():
    """Load trade counter. Timestamps persist across midnight."""
    path = STATE_DIR / "trade-counter.json"
    default = {
        "date": now_date_str(), "entries": 0,
        "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0,
        "last_global_entry_ts": 0,
    }
    if path.exists():
        try:
            with open(path) as f:
                tc = json.load(f)
            if tc.get("date") != now_date_str():
                tc["date"] = now_date_str()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["date"] = now_date_str()
    atomic_write(str(STATE_DIR / "trade-counter.json"), tc)


def load_cooldowns():
    if COOLDOWN_FILE.exists():
        try:
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cooldowns(cooldowns):
    atomic_write(str(COOLDOWN_FILE), cooldowns)


def is_asset_cooled_down(token, cooldown_minutes=120):
    cooldowns = load_cooldowns()
    if token not in cooldowns:
        return False
    exit_ts = cooldowns[token].get("exitTimestamp", 0)
    elapsed_min = (now_ts() - exit_ts) / 60
    return elapsed_min < cooldown_minutes


def set_asset_cooldown(token, reason="entry"):
    cooldowns = load_cooldowns()
    cooldowns[token] = {
        "exitTimestamp": now_ts(),
        "reason": reason,
        "setAt": now_iso(),
    }
    save_cooldowns(cooldowns)


def mcporter_call(tool, retries=2, timeout=25, **params):
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


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def log(msg):
    print(msg, file=sys.stderr)


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
