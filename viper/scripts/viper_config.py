# viper_config.py — Shared config, state, and MCP helpers for VIPER
# THE config loader. All scripts import this. No script reads config independently.

"""
VIPER Config Module
- atomic_write() for all state mutations
- deep_merge() for config with backward-compatible defaults
- call_mcp() with 3-attempt retry
- Percentage convention: all values are whole numbers (5 = 5%)
"""

import os
import json
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# ─── Paths ───────────────────────────────────────────────────

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
SKILL_DIR = os.path.join(WORKSPACE, "recipes", "viper")
CONFIG_FILE = os.path.join(SKILL_DIR, "viper-config.json")
STATE_DIR = os.path.join(SKILL_DIR, "state")
HISTORY_DIR = os.path.join(SKILL_DIR, "history")
MEMORY_DIR = os.path.join(SKILL_DIR, "memory")

VERBOSE = os.environ.get("VIPER_VERBOSE") == "1"


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically — crash-safe."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # atomic on POSIX


# ─── Deep Merge ──────────────────────────────────────────────

def deep_merge(base, override):
    """Recursively merge override into base. Preserves nested defaults."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ─── Config ──────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "version": 1,
    "budget": 5000,
    "strategyId": None,
    "strategyWallet": None,
    "telegramChatId": None,
    "maxSlots": 2,
    "maxRanges": 3,
    "maxDailyTrades": 6,

    # Range detection thresholds (all percentages are whole numbers)
    "adxThreshold": 20,
    "adxMinHours": 12,
    "bbWidthPercentile": 30,
    "minTouchesPerSide": 3,
    "minRangeWidthPct": 2,         # 2 = 2%
    "maxFundingPer8h": 3,          # 3 = 0.03% (stored as bps × 100)
    "minDailyVolume": 5000000,
    "minRangeScore": 60,           # 60 = 0.60 score (stored as int for convention)

    # Position sizing (percentages are whole numbers: 10 = 10%)
    "positionSizing": {
        "2-3": {"low": 10, "mid": 12, "high": 15},
        "3-5": {"low": 12, "mid": 15, "high": 18},
        ">5":  {"low": 15, "mid": 18, "high": 20}
    },
    "leverageByWidth": {
        "2-3": 7, "3-4": 6, "4-5": 5, ">5": 5
    },

    # Risk management (whole numbers)
    "maxSingleLossPct": 5,         # 5 = 5%
    "maxDailyLossPct": 6,          # 6 = 6%
    "maxDrawdownPct": 10,          # 10 = 10%
    "maxConsecutiveStops": 3,
    "maxBouncesPerRange": 8,

    # Break detection
    "breakVolumeMultiplier": 2,    # 2x average
    "breakAdxThreshold": 25,
    "breakOiSurgePct": 8,          # 8 = 8%
    "breakCooldownHours": 4,
    "breakFundingThresholdPer8h": 5,  # 5 = 0.05%

    # Exit
    "tpMode": "opposite_boundary",  # or "midrange", "full_boundary"
    "tpBufferPct": 5,              # 5 = 5% of range width
    "trailingLock50Pct": 60,       # Lock 60% at 50% of range captured
    "trailingLock80Pct": 75,       # Lock 75% at 80% of range captured
    "timeStopNarrowHours": 24,     # 24h for 2-3% range
    "timeStopWideHours": 36,       # 36h for wider ranges

    # Bounce aging
    "agingReducePct5_6": 25,       # Reduce 25% at bounce 5-6
    "agingReducePct7_8": 50,       # Reduce 50% at bounce 7-8
}


def load_config():
    """Load VIPER config with deep merge of defaults."""
    try:
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        return deep_merge(DEFAULT_CONFIG, user_config)
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)


def save_config(config):
    """Save config atomically."""
    atomic_write(CONFIG_FILE, config)


# ─── State ───────────────────────────────────────────────────

def _instance_dir(config):
    """Get instance-scoped state directory."""
    instance_key = config.get("strategyId", "default")
    d = os.path.join(STATE_DIR, instance_key)
    os.makedirs(d, exist_ok=True)
    return d


DEFAULT_STATE = {
    "version": 1,
    "active": True,
    "instanceKey": None,
    "createdAt": None,
    "updatedAt": None,
    "budget": 5000,
    "startingEquity": 5000,
    "ranges": {},
    "activePositions": {},
    "pendingOrders": {},
    "cooldown": {},
    "dailyStats": {
        "date": None,
        "bouncesTraded": 0,
        "bouncesWon": 0,
        "bouncesLost": 0,
        "breakExits": 0,
        "grossPnl": 0,
        "fees": 0,
        "netPnl": 0,
        "makerFillRate": 0,
        "avgHoldHours": 0
    },
    "safety": {
        "halted": False,
        "haltReason": None,
        "consecutiveStopsPerRange": {},
        "dailyLossPct": 0,
        "tradesToday": 0
    }
}


def load_state(config):
    """Load state with defaults. Re-reads from disk (race condition safe)."""
    state_file = os.path.join(_instance_dir(config), "viper-state.json")
    state = deep_merge(DEFAULT_STATE, {})
    state["instanceKey"] = config.get("strategyId", "default")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                saved = json.load(f)
            state = deep_merge(state, saved)
        except (json.JSONDecodeError, IOError):
            pass  # Corrupted file — use defaults
    return state


def save_state(config, state):
    """Save state atomically. Re-read before write to detect external changes."""
    state_file = os.path.join(_instance_dir(config), "viper-state.json")

    # Re-read to detect if another cron modified safety/halt
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                current = json.load(f)
            # Don't resurrect a halted state
            if current.get("safety", {}).get("halted") and not state.get("safety", {}).get("halted"):
                state["safety"]["halted"] = True
                state["safety"]["haltReason"] = current["safety"].get("haltReason")
        except (json.JSONDecodeError, IOError):
            pass

    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_file, state)


# ─── Range State ─────────────────────────────────────────────

def load_range(config, asset):
    """Load per-range state file."""
    path = os.path.join(_instance_dir(config), f"range-{asset}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "version": 1,
        "active": True,
        "instanceKey": config.get("strategyId", "default"),
        "asset": asset,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": None,
        "bounceHistory": []
    }


def save_range(config, asset, data):
    """Save per-range state atomically."""
    path = os.path.join(_instance_dir(config), f"range-{asset}.json")
    data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(path, data)


# ─── Trade Log ───────────────────────────────────────────────

def log_trade(config, trade):
    """Append trade to log atomically."""
    path = os.path.join(_instance_dir(config), "trade-log.json")
    trades = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                trades = json.load(f)
        except (json.JSONDecodeError, IOError):
            trades = []
    trade["version"] = 1
    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
    trades.append(trade)
    atomic_write(path, trades)


# ─── MCP Helpers ─────────────────────────────────────────────

def call_mcp(tool, **kwargs):
    """Call a Senpi MCP tool with 3-attempt retry."""
    cmd = ["mcporter", "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict, bool)):
            cmd.append(f"{k}={json.dumps(v)}")
        else:
            cmd.append(f"{k}={v}")

    last_error = None
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            data = json.loads(r.stdout)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "unknown"))
            return data
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)
    raise last_error


def get_all_instruments():
    """Fetch all instruments."""
    result = call_mcp("market_list_instruments")
    data = result.get("data", result)
    return data.get("instruments", [])


def get_asset_data(asset, intervals=None, include_book=False, include_funding=False):
    """Fetch candle data for an asset. Batch intervals in one call."""
    if intervals is None:
        intervals = ["1h", "4h"]
    return call_mcp("market_get_asset_data",
                     asset=asset,
                     candle_intervals=intervals,
                     include_order_book=include_book,
                     include_funding=include_funding)


def get_orderbook(asset):
    """Fetch L2 order book via market_get_asset_data with include_order_book=True."""
    return call_mcp("market_get_asset_data",
                     asset=asset,
                     candle_intervals=[],
                     include_order_book=True,
                     include_funding=False)


def get_clearinghouse(wallet):
    """Get clearinghouse state."""
    return call_mcp("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def open_position(wallet, orders, reason=""):
    """Open a position."""
    return call_mcp("create_position",
                     strategy_wallet=wallet,
                     orders=orders,
                     reason=reason)


def edit_position(wallet, coin, **kwargs):
    """Resize/modify a position."""
    return call_mcp("edit_position",
                     strategy_wallet=wallet,
                     coin=coin,
                     **kwargs)


def close_position(wallet, coin, reason=""):
    """Close a position."""
    return call_mcp("close_position",
                     strategy_wallet=wallet,
                     coin=coin,
                     reason=reason)


# ─── Output ──────────────────────────────────────────────────

def output(data):
    """Print JSON output for the agent. Minimal by default, verbose opt-in."""
    if not VERBOSE and "debug" in data:
        del data["debug"]
    # No indent in production — saves tokens
    print(json.dumps(data) if not VERBOSE else json.dumps(data, indent=2))


def output_heartbeat():
    """Immediate exit — nothing actionable."""
    print(json.dumps({"success": True, "heartbeat": "HEARTBEAT_OK"}))


def output_error(error_msg, actionable=False):
    """Structured error output."""
    print(json.dumps({
        "success": False,
        "error": error_msg,
        "actionable": actionable
    }))
    sys.exit(1)


# ─── Time Helpers ────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc)


def hours_since(iso_timestamp):
    """Hours elapsed since an ISO timestamp."""
    if not iso_timestamp:
        return 999
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        return (now_utc() - ts).total_seconds() / 3600
    except (ValueError, TypeError):
        return 999


# ─── Lifecycle Adapter ───────────────────────────────────────
# Used by generic senpi-enter.py / senpi-close.py scripts.
# After rebase onto main, lib/senpi_state/ provides the shared
# positions.py that calls these callbacks.

def get_lifecycle_adapter(**kwargs):
    """Return callbacks for generic senpi-enter/close scripts.

    Returns:
        Dict with wallet, skill, instance_key, max_slots, and all the
        load/save/create callbacks that positions.py expects.
    """
    config = load_config()
    wallet = config.get("strategyWallet", "")
    instance_key = config.get("strategyId", "default")
    max_slots = config.get("maxSlots", 2)
    inst_dir = _instance_dir(config)
    journal_path = os.path.join(inst_dir, "trade-journal.jsonl")

    def _load_state_cb():
        return load_state(config)

    def _save_state_cb(state):
        save_state(config, state)

    def _create_dsl(asset, direction, entry_price, size, margin, leverage, pattern):
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        retrace_pct = 5.0 / max(leverage, 1)
        if direction.upper() == "LONG":
            absolute_floor = round(entry_price * (1 - retrace_pct / 100), 6)
        else:
            absolute_floor = round(entry_price * (1 + retrace_pct / 100), 6)

        approximate = entry_price <= 0 or size <= 0

        return {
            "version": 1,
            "asset": asset,
            "direction": direction.upper(),
            "entryPrice": entry_price,
            "size": size,
            "margin": margin,
            "leverage": leverage,
            "pattern": pattern,
            "active": True,
            "highWaterPrice": entry_price,
            "phase": 1,
            "currentBreachCount": 0,
            "currentTierIndex": None,
            "tierFloorPrice": 0,
            "floorPrice": absolute_floor if not approximate else 0,
            "tiers": [
                {"triggerPct": 5, "lockPct": 50, "breaches": 3},
                {"triggerPct": 10, "lockPct": 65, "breaches": 2},
                {"triggerPct": 15, "lockPct": 75, "breaches": 2},
                {"triggerPct": 20, "lockPct": 85, "breaches": 1},
            ],
            "phase1": {
                "retraceThreshold": retrace_pct,
                "absoluteFloor": absolute_floor if not approximate else 0,
                "consecutiveBreachesRequired": 3,
            },
            "phase2TriggerTier": 0,
            "createdAt": now_iso,
            "lastCheck": now_iso,
            "createdBy": pattern,
            "approximate": approximate or None,
            "wallet": wallet,
            "strategyId": instance_key,
        }

    def _save_dsl(asset, dsl_state):
        path = os.path.join(inst_dir, f"dsl-{asset}.json")
        dsl_state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        atomic_write(path, dsl_state)

    def _load_dsl(asset):
        path = os.path.join(inst_dir, f"dsl-{asset}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _log_trade_cb(trade):
        log_trade(config, trade)

    return {
        "wallet": wallet,
        "skill": "viper",
        "instance_key": instance_key,
        "max_slots": max_slots,
        "load_state": _load_state_cb,
        "save_state": _save_state_cb,
        "create_dsl": _create_dsl,
        "save_dsl": _save_dsl,
        "load_dsl": _load_dsl,
        "log_trade": _log_trade_cb,
        "journal_path": journal_path,
        "output": output,
    }
