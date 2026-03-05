"""
tiger_config.py — Shared config loader and state management for TIGER.
All scripts import this. Reads tiger-state.json and tiger-config.json.
"""

import json
import os
import sys
import time
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# ─── Paths ───────────────────────────────────────────────────

WORKSPACE = os.environ.get("TIGER_WORKSPACE", "/data/workspace/recipes/tiger")
SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
STATE_DIR = os.path.join(WORKSPACE, "state")
CONFIG_FILE = os.path.join(WORKSPACE, "tiger-config.json")
STATE_FILE = os.path.join(STATE_DIR, "tiger-state.json")
OI_HISTORY_FILE = os.path.join(STATE_DIR, "oi-history.json")
TRADE_LOG_FILE = os.path.join(STATE_DIR, "trade-log.json")
SCAN_HISTORY_DIR = os.path.join(STATE_DIR, "scan-history")

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(SCAN_HISTORY_DIR, exist_ok=True)


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically via tmp file + rename. Prevents corruption from concurrent cron writes."""
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


# ─── AliasDict ───────────────────────────────────────────────

def _to_snake(name):
    """Convert camelCase to snake_case."""
    result = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            result.append("_")
        result.append(c.lower())
    return "".join(result)


class AliasDict(dict):
    """Dict that accepts both snake_case and camelCase keys, stores as snake_case."""

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return super().__getitem__(_to_snake(key))

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return super().__contains__(key) or super().__contains__(_to_snake(key))


def _alias_wrap(d):
    """Recursively wrap dicts as AliasDict."""
    if isinstance(d, dict) and not isinstance(d, AliasDict):
        return AliasDict({k: _alias_wrap(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_alias_wrap(i) for i in d]
    return d


# ─── Instance Scoping ────────────────────────────────────────

def _instance_dir(config):
    """Return instance-scoped state directory. Falls back to STATE_DIR."""
    key = config.get("instanceKey") or config.get("instance_key", "")
    if key:
        d = os.path.join(STATE_DIR, key)
        os.makedirs(d, exist_ok=True)
        return d
    return STATE_DIR


# ─── Load Trade Log (instance-scoped) ────────────────────────

def get_trade_log_path(config=None):
    """Return path to trade-log.json, optionally instance-scoped."""
    if config:
        return os.path.join(_instance_dir(config), "trade-log.json")
    return TRADE_LOG_FILE


def load_trade_log(config=None):
    """Load trade log, optionally instance-scoped."""
    path = get_trade_log_path(config)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


# ─── Prescreened Candidates (shared utility) ─────────────────

def load_prescreened_candidates(instruments, config=None, include_leverage=True):
    """Load candidates from prescreened.json if fresh, else return None."""
    prescreened_file = os.path.join(STATE_DIR, "prescreened.json")
    scan_group = os.environ.get("SCAN_GROUP", "").lower()

    try:
        if not os.path.exists(prescreened_file):
            return None
        with open(prescreened_file) as f:
            data = json.load(f)
        # Check freshness (< 10 min)
        if time.time() - data.get("timestamp", 0) > 600:
            return None

        # Pick group
        if scan_group and f"group_{scan_group}" in data:
            names = set(data[f"group_{scan_group}"])
        elif scan_group:
            names = set(data.get(f"group_{scan_group}", []))
        else:
            names = set(c["name"] for c in data.get("candidates", []))

        if not names:
            return None

        inst_map = {i.get("name"): i for i in instruments}
        result = []
        for name in names:
            inst = inst_map.get(name)
            if not inst:
                continue
            ctx = inst.get("context", {})
            if include_leverage:
                max_lev = inst.get("max_leverage", 0)
                result.append((name, ctx, max_lev))
            else:
                result.append((name, ctx))
        return result if result else None
    except Exception:
        return None


# ─── Config ──────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "budget": 1000,
    "target": 2000,
    "deadline_days": 7,
    "start_time": None,  # ISO timestamp, set by setup
    "strategy_id": None,
    "strategy_wallet": None,
    "telegram_chat_id": None,
    "max_slots": 3,
    "max_leverage": 10,
    "min_leverage": 5,
    "max_single_loss_pct": 5.0,
    "max_daily_loss_pct": 12.0,
    "max_drawdown_pct": 20.0,
    "min_bb_squeeze_percentile": 20,  # BB width below this = squeeze
    "min_oi_change_pct": 5.0,  # OI rising this much = accumulation
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "min_funding_annualized_pct": 30,  # Extreme funding threshold
    "btc_correlation_move_pct": 3.0,  # BTC move to trigger lag scan
    "min_confluence_score": {
        "CONSERVATIVE": 3.0,
        "NORMAL": 2.0,
        "ELEVATED": 1.5,
        "ABORT": 999  # Never enter in ABORT
    },
    "trailing_lock_pct": {
        "CONSERVATIVE": 0.80,
        "NORMAL": 0.60,
        "ELEVATED": 0.40,
        "ABORT": 0.90
    }
}


def load_config() -> dict:
    """Load tiger config, merging with defaults. Returns AliasDict (supports snake_case + camelCase keys)."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        config.update(user_config)
    return _alias_wrap(config)


def save_config(config: dict):
    """Save config to disk atomically."""
    atomic_write(CONFIG_FILE, config)


# ─── State ───────────────────────────────────────────────────

DEFAULT_STATE = {
    "current_balance": 0,
    "peak_balance": 0,
    "day_start_balance": 0,
    "daily_pnl": 0,
    "total_pnl": 0,
    "trades_today": 0,
    "wins_today": 0,
    "total_trades": 0,
    "total_wins": 0,
    "aggression": "NORMAL",
    "daily_rate_needed": 0,
    "days_remaining": 7,
    "active_positions": {},  # coin -> {entry, direction, leverage, size, opened_at, pattern}
    "halted": False,
    "halt_reason": None,
    "last_goal_recalc": None,
    "last_btc_price": None,
    "last_btc_check": None,
    "day_number": 1,
    "created_at": None,
    "updated_at": None
}


def load_state() -> dict:
    """Load tiger state, merging with defaults."""
    state = dict(DEFAULT_STATE)
    state["active_positions"] = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = json.load(f)
        state.update(saved)
    return state


def save_state(state: dict):
    """Save state to disk atomically."""
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(STATE_FILE, state)


# ─── OI History ──────────────────────────────────────────────

def load_oi_history() -> dict:
    """Load OI history. Format: {asset: [{ts, oi, price}, ...]}"""
    if os.path.exists(OI_HISTORY_FILE):
        with open(OI_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_oi_history(history: dict):
    atomic_write(OI_HISTORY_FILE, history)


def append_oi_snapshot(asset: str, oi: float, price: float):
    """Append an OI datapoint. Keep last 2016 per asset (7 days at 5min intervals).
    Extended from 288 to support synthetic candle generation for xyz: assets."""
    history = load_oi_history()
    if asset not in history:
        history[asset] = []
    history[asset].append({
        "ts": int(time.time()),
        "oi": oi,
        "price": price
    })
    # Trim to 2016 entries (7 days at 5min)
    history[asset] = history[asset][-2016:]
    save_oi_history(history)


# ─── Trade Log ───────────────────────────────────────────────

def log_trade(trade: dict):
    """Append a trade to the log."""
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            trades = json.load(f)
    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
    trades.append(trade)
    atomic_write(TRADE_LOG_FILE, trades)


# ─── MCP Helpers ─────────────────────────────────────────────

def mcporter_call(tool: str, timeout_s: int = 15, **kwargs) -> dict:
    """Call a Senpi MCP tool via mcporter. Returns parsed JSON."""
    inner_cmd = ["mcporter", "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            inner_cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            inner_cmd.append(f"{k}={'true' if v else 'false'}")
        else:
            inner_cmd.append(f"{k}={v}")

    # Wrap with `timeout` command for reliable kill (subprocess.communicate timeout
    # can hang if mcporter's node child holds pipes open)
    cmd = ["timeout", "--signal=KILL", str(timeout_s)] + inner_cmd

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate()
        if proc.returncode == 137:  # killed by timeout
            return {"error": "timeout", "success": False}
        if proc.returncode != 0:
            return {"error": stderr.strip(), "success": False}
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": f"invalid json: {stdout[:200]}", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


def get_all_instruments() -> list:
    """Fetch all instruments with OI, funding, volume."""
    result = mcporter_call("market_list_instruments", timeout_s=30)
    if not result.get("success") and not result.get("data"):
        return []
    data = result.get("data", result)
    return data.get("instruments", [])


def _synthesize_candles_from_history(asset: str, intervals: list) -> dict:
    """Synthesize OHLCV candles from OI history price snapshots (5min samples).
    Returns format matching MCP market_get_asset_data response."""
    history = load_oi_history()
    entries = history.get(asset, [])
    if len(entries) < 12:  # Need at least 1h of data (12 × 5min)
        return {"success": False, "error": "insufficient_history"}

    candles = {}
    interval_seconds = {"1h": 3600, "4h": 14400, "15m": 900, "5m": 300}

    for interval in (intervals or ["1h", "4h"]):
        bucket_s = interval_seconds.get(interval, 3600)
        # Group snapshots into buckets
        buckets = {}
        for e in entries:
            bucket_key = (e["ts"] // bucket_s) * bucket_s
            if bucket_key not in buckets:
                buckets[bucket_key] = []
            buckets[bucket_key].append(e)

        # Convert buckets to OHLCV candles
        candle_list = []
        for ts in sorted(buckets.keys()):
            points = buckets[ts]
            prices = [p["price"] for p in points]
            oi_vals = [p["oi"] for p in points]
            candle_list.append({
                "t": ts * 1000,  # ms timestamp
                "o": prices[0],
                "h": max(prices),
                "l": min(prices),
                "c": prices[-1],
                "v": sum(oi_vals) / len(oi_vals),  # Use avg OI as volume proxy
            })

        candles[interval] = candle_list

    return {"success": True, "data": {"candles": candles, "synthetic": True}}


def get_asset_candles(asset: str, intervals: list = None, include_funding: bool = False) -> dict:
    """Fetch candle data for an asset."""
    if intervals is None:
        intervals = ["1h", "4h"]
    # xyz: assets don't have MCP candle data — go straight to synthesis
    if asset.startswith("xyz:"):
        return _synthesize_candles_from_history(asset, intervals)
    kwargs = {
        "asset": asset,
        "candle_intervals": intervals,
        "include_order_book": False,
        "include_funding": include_funding
    }
    result = mcporter_call("market_get_asset_data", timeout_s=10, **kwargs)
    if result.get("success") or result.get("data"):
        return result
    # Fallback: synthesize candles from OI history price snapshots
    return _synthesize_candles_from_history(asset, intervals)


def get_prices(assets: list = None) -> dict:
    """Fetch current prices."""
    kwargs = {}
    if assets:
        kwargs["assets"] = assets
    return mcporter_call("market_get_prices", **kwargs)


def get_sm_markets(limit: int = 50) -> list:
    """Get smart money market concentration."""
    result = mcporter_call("leaderboard_get_markets", limit=limit)
    data = result.get("data", {})
    markets = data.get("markets", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def get_portfolio() -> dict:
    """Get current portfolio."""
    return mcporter_call("account_get_portfolio")


def get_clearinghouse(wallet: str) -> dict:
    """Get clearinghouse state for a strategy wallet."""
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def create_position(wallet: str, orders: list, reason: str = "") -> dict:
    """Create a position."""
    return mcporter_call("create_position",
                         strategyWalletAddress=wallet,
                         orders=orders,
                         reason=reason)


def edit_position(wallet: str, coin: str, **kwargs) -> dict:
    """Edit a position."""
    return mcporter_call("edit_position",
                         strategyWalletAddress=wallet,
                         coin=coin,
                         **kwargs)


def close_position(wallet: str, coin: str, reason: str = "") -> dict:
    """Close a position."""
    return mcporter_call("close_position",
                         strategyWalletAddress=wallet,
                         coin=coin,
                         reason=reason)


# ─── Time Helpers ────────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def days_remaining(config: dict) -> int:
    """Calculate days remaining until deadline."""
    if not config.get("start_time"):
        return config.get("deadline_days", 7)
    start = datetime.fromisoformat(config["start_time"])
    elapsed = (now_utc() - start).total_seconds() / 86400
    remaining = config.get("deadline_days", 7) - elapsed
    return max(0, remaining)


def day_number(config: dict) -> int:
    """Current day number (1-indexed)."""
    if not config.get("start_time"):
        return 1
    start = datetime.fromisoformat(config["start_time"])
    elapsed = (now_utc() - start).total_seconds() / 86400
    return min(int(elapsed) + 1, config.get("deadline_days", 7))


# ─── Output ──────────────────────────────────────────────────

def output(data: dict):
    """Print JSON output for the agent to consume."""
    print(json.dumps(data, indent=2), flush=True)


def shorten_address(addr: str) -> str:
    if not addr:
        return ""
    if len(addr) <= 10:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"
