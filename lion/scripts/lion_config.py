# lion_config.py — Shared config, state, and MCP helpers for LION
# THE config loader. All scripts import this. No script reads config independently.

"""
LION Config Module
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
SKILL_DIR = os.path.join(WORKSPACE, "recipes", "lion")
CONFIG_FILE = os.path.join(SKILL_DIR, "lion-config.json")
STATE_DIR = os.path.join(SKILL_DIR, "state")
HISTORY_DIR = os.path.join(SKILL_DIR, "history")
MEMORY_DIR = os.path.join(SKILL_DIR, "memory")
OI_HISTORY_FILE = os.path.join(HISTORY_DIR, "oi-history.json")

VERBOSE = os.environ.get("LION_VERBOSE") == "1"


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically — crash-safe."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


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
    "maxDailyTrades": 4,

    # Cascade detection (all percentages are whole numbers)
    "cascadeOiCliffPct": 8,               # 8 = 8% OI drop in 15min
    "cascadePriceVelocityPct": 2,          # 2 = 2% price move in 15min
    "cascadeFundingSpikePer8h": 5,         # 5 = 0.05% per 8h
    "cascadeVolumeMultiplier": 3,          # 3 = 3x 4h average
    "cascadeStabilizationCandles": 1,      # Wait for N 5-min candles against cascade

    # Book imbalance detection
    "bookImbalanceRatio": 3,               # 3 = 3:1 bid/ask ratio
    "bookMinDailyVolume": 20000000,        # $20M
    "bookPersistenceChecks": 2,            # 2 consecutive 30s checks
    "bookProximityPct": 50,                # 50 = 0.5% (bps)

    # Squeeze detection
    "squeezeFundingPer8h": 8,              # 8 = 0.08% per 8h
    "squeezeMinPeriods": 2,                # 2 consecutive funding periods
    "squeezeCompressionPct": 1,            # 1 = <1% move in 24h

    # Leverage per pattern
    "leverageCascade": 6,
    "leverageBookImbalance": 5,
    "leverageSqueeze": 8,

    # Position sizing (whole numbers = % of balance)
    "sizingStrongCascade": 20,
    "sizingNormalCascade": 12,
    "sizingBookImbalance": 8,
    "sizingStrongSqueeze": 15,
    "sizingModerateSqueeze": 10,

    # Exit rules
    "snapbackTargetPct": 50,               # 50 = recapture 50% of cascade
    "cascadeTimeStopMin": 120,             # 2 hours
    "bookTimeStopMin": 30,
    "squeezeTimeStopMin": 720,             # 12 hours
    "cascadeTrailingLockPct": 70,          # Lock 70% at 50% of target
    "bookTrailingLockPct": 80,
    "squeezeTrailingLock2Pct": 50,         # Lock 50% at 2% move
    "squeezeTrailingLock3Pct": 70,         # Lock 70% at 3% move
    "cascadeSecondWaveOiPct": 5,           # 5 = 5% more OI drop = exit
    "squeezeFundingNormalizePer8h": 3,     # 3 = 0.03% — thesis dead

    # Risk management (whole numbers)
    "maxSingleLossPct": 5,
    "maxDailyLossPct": 8,
    "maxDrawdownPct": 15,
    "thresholdEscalationPct": 25,
    "marginWarningPct": 50,
    "marginCriticalPct": 30,

    # OI monitoring
    "minDailyVolume": 5000000,
    "oiHistoryMaxEntries": 240,            # 4 hours at 60s
}


def load_config():
    """Load LION config with deep merge of defaults."""
    try:
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        return deep_merge(DEFAULT_CONFIG, user_config)
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)


def save_config(config):
    atomic_write(CONFIG_FILE, config)


# ─── State ───────────────────────────────────────────────────

def _instance_dir(config):
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
    "activePositions": {},
    "watchlist": {
        "squeeze": {},
        "preCascade": {}
    },
    "dailyStats": {
        "date": None,
        "trades": 0,
        "wins": 0,
        "cascadesDetected": 0,
        "cascadesTraded": 0,
        "squeezesDetected": 0,
        "squeezesTraded": 0,
        "imbalancesDetected": 0,
        "imbalancesTraded": 0,
        "grossPnl": 0,
        "fees": 0,
        "netPnl": 0
    },
    "safety": {
        "halted": False,
        "haltReason": None,
        "tradesToday": 0,
        "thresholdMultiplier": 100    # 100 = normal, 125 = +25%
    }
}


def load_state(config):
    """Load state with defaults. Re-reads from disk."""
    state_file = os.path.join(_instance_dir(config), "lion-state.json")
    state = deep_merge(DEFAULT_STATE, {})
    state["instanceKey"] = config.get("strategyId", "default")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                saved = json.load(f)
            state = deep_merge(state, saved)
        except (json.JSONDecodeError, IOError):
            pass
    return state


def save_state(config, state):
    """Save state atomically. Re-read before write for race condition guard."""
    state_file = os.path.join(_instance_dir(config), "lion-state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                current = json.load(f)
            if current.get("safety", {}).get("halted") and not state.get("safety", {}).get("halted"):
                state["safety"]["halted"] = True
                state["safety"]["haltReason"] = current["safety"].get("haltReason")
        except (json.JSONDecodeError, IOError):
            pass
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_file, state)


# ─── OI History (shared signal) ──────────────────────────────

def load_oi_history():
    """Load OI history. Shared across instances."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    if os.path.exists(OI_HISTORY_FILE):
        try:
            with open(OI_HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def append_oi_snapshot(asset, oi, price, volume_15m=0, funding=0, max_entries=240):
    """Append OI datapoint. Keep last max_entries per asset."""
    history = load_oi_history()
    if asset not in history:
        history[asset] = []
    history[asset].append({
        "ts": int(time.time()),
        "oi": oi,
        "price": price,
        "volume15m": volume_15m,
        "funding": funding
    })
    history[asset] = history[asset][-max_entries:]
    atomic_write(OI_HISTORY_FILE, history)


def get_oi_change_pct(history_entries, periods):
    """Compute OI change % over N periods. Returns whole number."""
    if len(history_entries) < periods + 1:
        return None
    old = history_entries[-(periods + 1)]["oi"]
    new = history_entries[-1]["oi"]
    if old == 0:
        return None
    return round(((new - old) / old) * 100)


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
    result = call_mcp("market_list_instruments")
    data = result.get("data", result)
    return data.get("instruments", [])


def get_asset_data(asset, intervals=None, include_book=False, include_funding=False):
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


def get_sm_markets(limit=50):
    result = call_mcp("leaderboard_get_markets", limit=limit)
    data = result.get("data", {})
    markets = data.get("markets", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def get_clearinghouse(wallet):
    return call_mcp("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def open_position(wallet, orders, reason=""):
    return call_mcp("create_position",
                     strategy_wallet=wallet,
                     orders=orders,
                     reason=reason)


def edit_position(wallet, coin, **kwargs):
    return call_mcp("edit_position",
                     strategy_wallet=wallet,
                     coin=coin,
                     **kwargs)


def close_position(wallet, coin, reason=""):
    return call_mcp("close_position",
                     strategy_wallet=wallet,
                     coin=coin,
                     reason=reason)


# ─── Output ──────────────────────────────────────────────────

def output(data):
    """Print JSON output. Minimal by default, verbose opt-in via LION_VERBOSE=1."""
    if not VERBOSE and "debug" in data:
        del data["debug"]
    print(json.dumps(data) if not VERBOSE else json.dumps(data, indent=2))


def output_heartbeat():
    print(json.dumps({"success": True, "heartbeat": "HEARTBEAT_OK"}))


def output_error(error_msg, actionable=False):
    print(json.dumps({
        "success": False,
        "error": error_msg,
        "actionable": actionable
    }))
    sys.exit(1)


# ─── Time Helpers ────────────────────────────────────────────

def now_utc():
    return datetime.now(timezone.utc)


def minutes_since(iso_timestamp):
    if not iso_timestamp:
        return 999999
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        return (now_utc() - ts).total_seconds() / 60
    except (ValueError, TypeError):
        return 999999


def hours_since(iso_timestamp):
    return minutes_since(iso_timestamp) / 60
