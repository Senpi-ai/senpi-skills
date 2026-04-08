"""JAGUAR Strategy v1.0 — Shared config, MCP helpers, state I/O.
Self-contained — does not depend on wolf_config."""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import subprocess
import sys
import tempfile
import time
import glob
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "jaguar-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "jaguar-config.json"
STATE_DIR = SKILL_DIR / "state"
HISTORY_FILE = os.path.join(WORKSPACE, "jaguar-emerging-history.json")
COOLDOWN_FILE = STATE_DIR / "asset-cooldowns.json"

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
            json.dump(data, f, indent=2)
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
    wallet = os.environ.get("JAGUAR_WALLET", "")
    strategy_id = os.environ.get("JAGUAR_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


# ─── State I/O ───────────────────────────────────────────────

def load_state(filename="state.json"):
    path = STATE_DIR / filename
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(data, filename="state.json"):
    atomic_write(str(STATE_DIR / filename), data)


def list_active_states():
    """Return list of active DSL state files (positions being managed)."""
    states = []
    for f in STATE_DIR.glob("*.json"):
        if f.name in ("trade-counter.json", "asset-cooldowns.json", "pyramid-tracker.json"):
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            if data.get("active", False):
                states.append({"file": f.name, "asset": data.get("asset", ""), "data": data})
        except (json.JSONDecodeError, IOError):
            pass
    return states


# ─── Trade Counter ───────────────────────────────────────────

def load_trade_counter():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = STATE_DIR / "trade-counter.json"
    default = {
        "date": today, "entries": 0, "realizedPnl": 0,
        "gate": "OPEN", "gateReason": None, "cooldownUntil": None,
        "lastResults": []
    }
    if path.exists():
        try:
            with open(path) as f:
                tc = json.load(f)
            if tc.get("date") != today:
                for k in ["entries", "realizedPnl"]:
                    tc[k] = 0
                tc["date"] = today
                tc["gate"] = "OPEN"
                tc["gateReason"] = None
                tc["cooldownUntil"] = None
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["updatedAt"] = now_iso()
    atomic_write(str(STATE_DIR / "trade-counter.json"), tc)


# ─── Asset Cooldowns ─────────────────────────────────────────

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
    """Check if an asset is in cooldown after a Phase 1 exit."""
    cooldowns = load_cooldowns()
    if token not in cooldowns:
        return False
    exit_ts = cooldowns[token].get("exitTimestamp", 0)
    elapsed_min = (now_ts() - exit_ts) / 60
    return elapsed_min < cooldown_minutes


def set_asset_cooldown(token, reason="phase1_exit"):
    """Set a cooldown on an asset after Phase 1 exit."""
    cooldowns = load_cooldowns()
    cooldowns[token] = {
        "exitTimestamp": now_ts(),
        "reason": reason,
        "setAt": now_iso(),
    }
    save_cooldowns(cooldowns)


# ─── Pyramid Tracker ─────────────────────────────────────────

def load_pyramid_tracker():
    path = STATE_DIR / "pyramid-tracker.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_pyramid_tracker(tracker):
    atomic_write(str(STATE_DIR / "pyramid-tracker.json"), tracker)


def has_pyramided(token):
    """Check if a position has already been pyramided (max 1 per position)."""
    tracker = load_pyramid_tracker()
    return token in tracker


def record_pyramid(token, margin, score):
    """Record that a pyramid was added to a position."""
    tracker = load_pyramid_tracker()
    tracker[token] = {
        "pyramidedAt": now_iso(),
        "margin": margin,
        "score": score,
    }
    save_pyramid_tracker(tracker)


def clear_pyramid(token):
    """Clear pyramid record when position is closed."""
    tracker = load_pyramid_tracker()
    if token in tracker:
        del tracker[token]
        save_pyramid_tracker(tracker)


# ─── Scanner History ─────────────────────────────────────────

def load_scan_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"scans": []}


def save_scan_history(history, max_scans=60):
    if len(history["scans"]) > max_scans:
        history["scans"] = history["scans"][-max_scans:]
    atomic_write(HISTORY_FILE, history)


# ─── MCP Helpers ─────────────────────────────────────────────

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
            entry_px = float(pos.get("entryPx", 0))
            mark_px = float(pos.get("markPx", pos.get("liquidationPx", 0)))
            margin_used = float(pos.get("marginUsed", 0))
            leverage = float(pos.get("leverage", {}).get("value", 10) if isinstance(pos.get("leverage"), dict) else pos.get("leverage", 10))
            roe = 0
            if entry_px > 0 and mark_px > 0:
                if szi > 0:
                    roe = ((mark_px - entry_px) / entry_px) * leverage * 100
                else:
                    roe = ((entry_px - mark_px) / entry_px) * leverage * 100
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "margin": margin_used,
                "entryPrice": entry_px,
                "markPrice": mark_px,
                "size": abs(szi),
                "leverage": leverage,
                "roe": round(roe, 2),
            })
    return account_value, positions


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
