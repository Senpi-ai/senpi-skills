"""TURBINE v2 — Shared MCP helpers + config loader + session state.

v2 producer is lean compared to v1:
  - Pick next asset from rotation list with XYZ-weighted round-robin
  - Query funding regime + spread
  - Choose direction (funding-fade if crowded, else alternate)
  - Emit signals for empty slots, one per tick
  - Push signals via `openclaw senpi external-scanner ingest`

The v2 runtime owns everything else: position tracking, DSL-managed
exits, risk guardrails, per-asset cooldowns, daily loss halts.
"""
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
SKILL_DIR = Path(WORKSPACE) / "skills" / "turbine-tracker"
CONFIG_PATH = SKILL_DIR / "config" / "turbine-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Config loader ────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_wallet_and_strategy():
    """Wallet + strategy id are required to emit signals. Env > config."""
    w = os.environ.get("TURBINE_WALLET") or os.environ.get("STRATEGY_ADDRESS", "")
    s = os.environ.get("TURBINE_STRATEGY_ID") or os.environ.get("STRATEGY_ID", "")
    if not w or not s:
        c = load_config()
        w = w or c.get("wallet", "")
        s = s or c.get("strategyId", "")
    return w, s


# ─── Session state ────────────────────────────────────────────

def atomic_write(path, data):
    path = str(path)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_session_state():
    """Rotation state. Persists across cron ticks, resets at UTC day boundary."""
    today = now_date()
    p = STATE_DIR / "session-state.json"
    default = {
        "date": today,
        "rotation_index": 0,
        "last_direction_by_asset": {},   # asset → "LONG" | "SHORT" for flat-funding alternation
        "cycles_opened_today": 0,
        "signals_emitted_today": 0,
    }
    if p.exists():
        try:
            with open(p) as f:
                ss = json.load(f)
            if ss.get("date") != today:
                return dict(default)
            for k, v in default.items():
                if k not in ss:
                    ss[k] = v
            return ss
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_session_state(ss):
    ss["updated_at"] = now_iso()
    atomic_write(str(STATE_DIR / "session-state.json"), ss)


# ─── MCP helper ───────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via mcporter. Returns parsed JSON or None on failure."""
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


def get_open_positions(wallet):
    """Return list of currently open positions across main + xyz DEXes.
    Each entry: {coin, direction, size, margin, entryPrice, upnl}.
    """
    if not wallet:
        return []
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
    if not ch or not isinstance(ch, dict):
        return []
    data = ch.get("data", ch)
    positions = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = safe_float(pos.get("szi"))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "margin": safe_float(pos.get("marginUsed")),
                "entryPrice": safe_float(pos.get("entryPx")),
                "upnl": safe_float(pos.get("unrealizedPnl")),
                "dex": section,
            })
    return positions


# ─── Output / logging / time ──────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[TURBINE-v2] {msg}", file=sys.stderr)


def safe_float(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
