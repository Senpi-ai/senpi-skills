"""
tiger_config.py — Shared config loader and state management for TIGER.
All scripts import this. Reads tiger-state.json and tiger-config.json.
"""

import json
import os
import sys
import time
import tempfile
import subprocess
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

def atomic_write(path: str, data, indent=None):
    """Write JSON data atomically: write to temp file then os.replace()."""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── Instance-Scoped State ──────────────────────────────────

def instance_dir(config: dict) -> str:
    """Return a state directory scoped by strategy_id. Falls back to STATE_DIR."""
    sid = config.get("strategy_id")
    if sid:
        d = os.path.join(STATE_DIR, str(sid))
    else:
        d = STATE_DIR
    os.makedirs(d, exist_ok=True)
    return d


# ─── AliasDict ───────────────────────────────────────────────

_CAMEL_TO_SNAKE = {
    "perSlotBudget": "per_slot_budget",
    "maxLeverage": "max_leverage",
    "maxSlots": "max_slots",
    "minScore": "min_score",
    "dslTiers": "dsl_tiers",
    "scanGroups": "scan_groups",
    "strategyId": "strategy_id",
    "strategyWallet": "strategy_wallet",
    "telegramChatId": "telegram_chat_id",
    "deadlineDays": "deadline_days",
    "startTime": "start_time",
    "minLeverage": "min_leverage",
    "maxSingleLossPct": "max_single_loss_pct",
    "maxDailyLossPct": "max_daily_loss_pct",
    "maxDrawdownPct": "max_drawdown_pct",
    "minBbSqueezePercentile": "min_bb_squeeze_percentile",
    "minOiChangePct": "min_oi_change_pct",
    "rsiOverbought": "rsi_overbought",
    "rsiOversold": "rsi_oversold",
    "minFundingAnnualizedPct": "min_funding_annualized_pct",
    "btcCorrelationMovePct": "btc_correlation_move_pct",
    "minConfluenceScore": "min_confluence_score",
    "trailingLockPct": "trailing_lock_pct",
}

class AliasDict(dict):
    """Dict subclass that maps camelCase keys to snake_case on access."""

    def __getitem__(self, key):
        mapped = _CAMEL_TO_SNAKE.get(key, key)
        return super().__getitem__(mapped)

    def __contains__(self, key):
        mapped = _CAMEL_TO_SNAKE.get(key, key)
        return super().__contains__(mapped)

    def get(self, key, default=None):
        mapped = _CAMEL_TO_SNAKE.get(key, key)
        return super().get(mapped, default)


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


def load_config() -> "AliasDict":
    """Load tiger config, merging with defaults. Returns AliasDict supporting camelCase access."""
    config = AliasDict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        # Normalize camelCase keys from user config to snake_case
        for k, v in list(user_config.items()):
            snake = _CAMEL_TO_SNAKE.get(k)
            if snake and k != snake:
                user_config[snake] = v
                del user_config[k]
        config.update(user_config)
    return config


def save_config(config: dict):
    """Save config to disk atomically."""
    atomic_write(CONFIG_FILE, config, indent=2)


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


def _state_file(config: dict = None) -> str:
    """Return instance-scoped state file path."""
    if config:
        return os.path.join(instance_dir(config), "tiger-state.json")
    return STATE_FILE


def load_state(config: dict = None) -> dict:
    """Load tiger state, merging with defaults. Uses instance-scoped path if config provided."""
    state = dict(DEFAULT_STATE)
    state["active_positions"] = {}
    sf = _state_file(config)
    if os.path.exists(sf):
        with open(sf) as f:
            saved = json.load(f)
        state.update(saved)
    return state


def save_state(state: dict, config: dict = None):
    """Save state to disk atomically. Uses instance-scoped path if config provided."""
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(_state_file(config), state, indent=2)


# ─── OI History ──────────────────────────────────────────────

def _oi_history_file(config: dict = None) -> str:
    if config:
        return os.path.join(instance_dir(config), "oi-history.json")
    return OI_HISTORY_FILE


def load_oi_history(config: dict = None) -> dict:
    """Load OI history. Format: {asset: [{ts, oi, price}, ...]}"""
    f = _oi_history_file(config)
    if os.path.exists(f):
        with open(f) as fh:
            return json.load(fh)
    return {}


def save_oi_history(history: dict, config: dict = None):
    atomic_write(_oi_history_file(config), history)


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

def get_trade_log_path(config: dict = None) -> str:
    """Return instance-scoped trade log path."""
    if config:
        return os.path.join(instance_dir(config), "trade-log.json")
    return TRADE_LOG_FILE


def load_trade_log(config: dict = None) -> list:
    """Load the trade log. Returns list of trade dicts."""
    f = get_trade_log_path(config)
    if os.path.exists(f):
        with open(f) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    return []


def log_trade(trade: dict, config: dict = None):
    """Append a trade to the log atomically."""
    trades = load_trade_log(config)
    trade["timestamp"] = datetime.now(timezone.utc).isoformat()
    trades.append(trade)
    atomic_write(get_trade_log_path(config), trades, indent=2)


# ─── Prescreened Candidates ──────────────────────────────────

PRESCREENED_FILE = os.path.join(STATE_DIR, "prescreened.json")


def load_prescreened_candidates(instruments, config, group_key=None):
    """Load candidates from prescreened.json if fresh, else return None.
    Shared implementation used by all scanners."""
    scan_group = os.environ.get("SCAN_GROUP", "").lower()
    try:
        if not os.path.exists(PRESCREENED_FILE):
            return None
        with open(PRESCREENED_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > 7200:
            return None
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
            max_lev = inst.get("max_leverage", 0)
            result.append((name, ctx, max_lev))
        return result if result else None
    except Exception:
        return None


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
