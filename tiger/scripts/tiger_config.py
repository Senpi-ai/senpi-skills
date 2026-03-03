"""
tiger_config.py — Shared config, state, and MCP helpers for TIGER.
THE config loader. All scripts import this. No script reads config independently.

- atomic_write() for all state mutations
- deep_merge() for config with backward-compatible defaults
- mcporter_call() with 3-attempt retry
- Percentage convention: risk limits are whole numbers (5 = 5%),
  confluence scores are decimals (0.40), retrace thresholds are decimals (0.015)
"""

import json
import os
import sys
import time
import subprocess
import copy
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

# ─── Paths ───────────────────────────────────────────────────

def _resolve_workspace(env=None):
    env = env or os.environ
    if env.get("TIGER_WORKSPACE"):
        return env["TIGER_WORKSPACE"]
    if env.get("OPENCLAW_WORKSPACE"):
        return env["OPENCLAW_WORKSPACE"]
    # Fallback to the script parent (workspace/scripts -> workspace).
    return str(Path(__file__).resolve().parent.parent)


@dataclass(frozen=True)
class TigerRuntime:
    workspace: str
    scripts_dir: str
    state_dir: str
    config_file: str
    source: str


def build_runtime(env=None, workspace=None, require_workspace_env=True):
    env = env or os.environ
    source = "explicit"
    if workspace:
        ws = workspace
    else:
        tiger_ws = env.get("TIGER_WORKSPACE")
        openclaw_ws = env.get("OPENCLAW_WORKSPACE")
        if tiger_ws:
            ws = tiger_ws
            source = "env:TIGER_WORKSPACE"
        elif require_workspace_env:
            raise RuntimeError(
                "TIGER_WORKSPACE must be set. "
                "Set TIGER_WORKSPACE to the strategy workspace path."
            )
        elif openclaw_ws:
            # Backward-compatible fallback for non-strict contexts/tests.
            ws = openclaw_ws
            source = "env:OPENCLAW_WORKSPACE"
        else:
            # Non-strict fallback for imports/tests. Runtime entrypoints use strict mode.
            ws = _resolve_workspace(env)
            source = "fallback"
    return TigerRuntime(
        workspace=ws,
        scripts_dir=os.path.join(ws, "scripts"),
        state_dir=os.path.join(ws, "state"),
        config_file=os.path.join(ws, "tiger-config.json"),
        source=source,
    )


def _runtime_attr(runtime, attr):
    runtime = runtime or build_runtime(require_workspace_env=True)
    if isinstance(runtime, dict):
        return runtime[attr]
    return getattr(runtime, attr)


# ─── Config Key Normalization ────────────────────────────────

# Canonical config uses camelCase keys. We accept snake_case inputs for
# backward compatibility and normalize them at load-time.
CONFIG_KEY_ALIASES = {
    "deadline_days": "deadlineDays",
    "start_time": "startTime",
    "strategy_id": "strategyId",
    "strategy_wallet": "strategyWallet",
    "telegram_chat_id": "telegramChatId",
    "max_slots": "maxSlots",
    "max_leverage": "maxLeverage",
    "min_leverage": "minLeverage",
    "max_single_loss_pct": "maxSingleLossPct",
    "max_daily_loss_pct": "maxDailyLossPct",
    "max_drawdown_pct": "maxDrawdownPct",
    "min_bb_squeeze_percentile": "bbSqueezePercentile",
    "bb_squeeze_percentile": "bbSqueezePercentile",
    "min_oi_change_pct": "minOiChangePct",
    "rsi_overbought": "rsiOverbought",
    "rsi_oversold": "rsiOversold",
    "min_funding_annualized_pct": "minFundingAnnualizedPct",
    "btc_correlation_move_pct": "btcCorrelationMovePct",
    "oi_collapse_threshold_pct": "oiCollapseThresholdPct",
    "oi_reduce_threshold_pct": "oiReduceThresholdPct",
    "min_confluence_score": "minConfluenceScore",
    "trailing_lock_pct": "trailingLockPct",
    "pattern_confluence_overrides": "patternConfluenceOverrides",
    "dsl_retrace": "dslRetrace",
}


# ─── Snake-to-Camel Key Aliasing ─────────────────────────────

def _snake_to_camel(name):
    """Convert snake_case to camelCase. e.g. max_slots -> maxSlots"""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class AliasDict(dict):
    """Dict that transparently maps snake_case key lookups to camelCase.
    Allows scripts written with snake_case keys (e.g. config["max_slots"])
    to read from a camelCase backing store (e.g. config["maxSlots"]).
    Direct camelCase access also works. Writes go through as-is."""

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            camel = _snake_to_camel(key)
            if camel != key and camel in self:
                return super().__getitem__(camel)
            raise

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        camel = _snake_to_camel(key)
        return camel != key and super().__contains__(camel)

    def __setitem__(self, key, value):
        # If camelCase version exists, write to that; otherwise write as-is
        camel = _snake_to_camel(key)
        if camel != key and camel in self and key not in dict.keys(self):
            super().__setitem__(camel, value)
        else:
            super().__setitem__(key, value)


def _to_alias_dict(d):
    """Recursively wrap dicts as AliasDict."""
    if isinstance(d, dict) and not isinstance(d, AliasDict):
        return AliasDict({k: _to_alias_dict(v) for k, v in d.items()})
    return d


def _normalize_user_config(user_config):
    """Normalize user config keys into canonical camelCase keys."""
    if not isinstance(user_config, dict):
        return {}
    normalized = {}
    for key, value in user_config.items():
        canonical = CONFIG_KEY_ALIASES.get(key, key)
        if canonical == "dslRetrace" and isinstance(value, dict):
            dsl = {}
            for dk, dv in value.items():
                dsl_key = {"phase_1": "phase1", "phase_2": "phase2"}.get(dk, dk)
                dsl[dsl_key] = dv
            value = dsl
        if canonical in normalized:
            existing = normalized[canonical]
            if isinstance(existing, dict) and isinstance(value, dict):
                normalized[canonical] = deep_merge(existing, value)
            elif (existing is None or existing == "") and value not in (None, ""):
                normalized[canonical] = value
        else:
            normalized[canonical] = value
    return normalized


def _to_plain(value):
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def _normalize_dict_with_aliases(data, aliases):
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for key, value in data.items():
        canonical = aliases.get(key, key)
        if canonical in normalized and isinstance(normalized[canonical], dict) and isinstance(value, dict):
            normalized[canonical] = deep_merge(normalized[canonical], value)
            continue
        normalized[canonical] = value
    return normalized


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data, runtime=None):
    """Write JSON atomically — crash-safe via os.replace()."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ─── Deep Merge ──────────────────────────────────────────────

def deep_merge(base, override):
    """Recursively merge override into base. Preserves nested defaults."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ─── Config ──────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "version": 1,
    "budget": 1000,
    "target": 2000,
    "deadlineDays": 7,
    "startTime": None,
    "strategyId": None,
    "strategyWallet": None,
    "telegramChatId": None,
    "maxSlots": 3,
    "maxLeverage": 10,
    "minLeverage": 5,

    # Risk limits (whole numbers: 5 = 5%)
    "maxSingleLossPct": 5,
    "maxDailyLossPct": 12,
    "maxDrawdownPct": 20,

    # Scanner thresholds
    "bbSqueezePercentile": 35,
    "minOiChangePct": 5,
    "rsiOverbought": 75,
    "rsiOversold": 25,
    "minFundingAnnualizedPct": 30,
    "btcCorrelationMovePct": 2,
    "oiCollapseThresholdPct": 25,
    "oiReduceThresholdPct": 10,

    # Aggression-dependent (confluence scores are decimals 0-1)
    "minConfluenceScore": {
        "CONSERVATIVE": 0.7,
        "NORMAL": 0.40,
        "ELEVATED": 0.4,
        "ABORT": 999
    },
    # Trailing lock (decimals: 0.60 = 60%)
    "trailingLockPct": {
        "CONSERVATIVE": 0.80,
        "NORMAL": 0.60,
        "ELEVATED": 0.40,
        "ABORT": 0.90
    },
}


def load_config(config_file=None, runtime=None):
    """Load TIGER config with deep-merged defaults and key normalization."""
    cfg_file = config_file or _runtime_attr(runtime, "config_file")
    try:
        with open(cfg_file) as f:
            user_config = _normalize_user_config(json.load(f))
        merged = deep_merge(DEFAULT_CONFIG, user_config)
    except FileNotFoundError:
        merged = deep_merge(DEFAULT_CONFIG, {})
    return _to_alias_dict(merged)


def _get_config(config=None, runtime=None):
    """Get config from explicit arg or by loading from disk."""
    if config is not None:
        return config
    return load_config(runtime=runtime)


def save_config(config, runtime=None):
    canonical = _normalize_user_config(_to_plain(config))
    merged = deep_merge(DEFAULT_CONFIG, canonical)
    atomic_write(_runtime_attr(runtime, "config_file"), merged, runtime=runtime)


# ─── State ───────────────────────────────────────────────────

def _instance_dir(config, runtime=None, create=False):
    instance_key = config.get("strategyId") or "default"
    d = os.path.join(_runtime_attr(runtime, "workspace"), "state", instance_key)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


DEFAULT_STATE = {
    "version": 1,
    "active": True,
    "instanceKey": None,
    "createdAt": None,
    "updatedAt": None,

    "currentBalance": 0,
    "peakBalance": 0,
    "dayStartBalance": 0,
    "dailyPnl": 0,
    "totalPnl": 0,
    "tradesToday": 0,
    "winsToday": 0,
    "totalTrades": 0,
    "totalWins": 0,

    "aggression": "NORMAL",
    "dailyRateNeeded": 0,
    "daysRemaining": 7,
    "dayNumber": 1,

    "activePositions": {},
    "safety": {
        "halted": False,
        "haltReason": None,
        "dailyLossPct": 0,
        "tradesToday": 0
    },

    "lastGoalRecalc": None,
    "lastBtcPrice": None,
    "lastBtcCheck": None,
}


STATE_KEY_ALIASES = {
    "instance_key": "instanceKey",
    "current_balance": "currentBalance",
    "peak_balance": "peakBalance",
    "day_start_balance": "dayStartBalance",
    "daily_pnl": "dailyPnl",
    "total_pnl": "totalPnl",
    "trades_today": "tradesToday",
    "wins_today": "winsToday",
    "total_trades": "totalTrades",
    "total_wins": "totalWins",
    "daily_rate_needed": "dailyRateNeeded",
    "days_remaining": "daysRemaining",
    "day_number": "dayNumber",
    "active_positions": "activePositions",
    "last_goal_recalc": "lastGoalRecalc",
    "last_btc_price": "lastBtcPrice",
    "last_btc_check": "lastBtcCheck",
    "adaptive_leverage": "adaptiveLeverage",
    "kelly_fraction": "kellyFraction",
    "per_slot_budget": "perSlotBudget",
}

SAFETY_KEY_ALIASES = {
    "halt_reason": "haltReason",
    "daily_loss_pct": "dailyLossPct",
    "trades_today": "tradesToday",
}

POSITION_KEY_ALIASES = {
    "entry_price": "entryPrice",
    "size_usd": "sizeUsd",
    "confluence_score": "confluenceScore",
    "opened_at": "openedAt",
    "dsl_state_file": "dslStateFile",
    "high_water_roe": "highWaterRoe",
    "prev_high_water": "prevHighWater",
    "stagnant_checks": "stagnantChecks",
    "breakout_candle_index": "breakoutCandleIndex",
    "candles_since_breakout": "candlesSinceBreakout",
    "bb_reentry": "bbReentry",
}


def _normalize_active_positions(active_positions):
    if not isinstance(active_positions, dict):
        return {}
    normalized = {}
    for asset, position in active_positions.items():
        if isinstance(position, dict):
            normalized[asset] = _normalize_dict_with_aliases(position, POSITION_KEY_ALIASES)
        else:
            normalized[asset] = position
    return normalized


def _normalize_state_data(state):
    if not isinstance(state, dict):
        return {}
    normalized = _normalize_dict_with_aliases(state, STATE_KEY_ALIASES)

    legacy_halted = normalized.pop("halted", None)
    legacy_reason = normalized.pop("haltReason", None)
    if "halt_reason" in normalized:
        legacy_reason = legacy_reason or normalized.pop("halt_reason")

    safety = normalized.get("safety", {})
    safety = _normalize_dict_with_aliases(safety, SAFETY_KEY_ALIASES)
    if not isinstance(safety.get("halted"), bool):
        safety["halted"] = bool(legacy_halted) if legacy_halted is not None else False
    elif legacy_halted and not safety.get("halted"):
        safety["halted"] = bool(legacy_halted)
    if not safety.get("haltReason") and legacy_reason:
        safety["haltReason"] = legacy_reason
    normalized["safety"] = safety

    if "activePositions" in normalized:
        normalized["activePositions"] = _normalize_active_positions(normalized["activePositions"])
    return normalized


def load_state(config=None, runtime=None):
    """Load state with defaults. Config is optional and loaded from disk if omitted."""
    config = _get_config(config, runtime=runtime)
    state_file = os.path.join(_instance_dir(config, runtime=runtime, create=False), "tiger-state.json")
    state = deep_merge(DEFAULT_STATE, {})
    state["instanceKey"] = config.get("strategyId", "default")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                saved = _normalize_state_data(json.load(f))
            state = deep_merge(state, saved)
        except (json.JSONDecodeError, IOError):
            pass
    return _to_alias_dict(state)


def save_state(state_or_config, state=None, runtime=None):
    """Save state atomically. Accepts save_state(state) or save_state(config, state)."""
    if state is None:
        # Called as save_state(state) — config is implicit
        state = state_or_config
        config = _get_config(runtime=runtime)
    else:
        # Called as save_state(config, state) — explicit config
        config = state_or_config
    state_file = os.path.join(_instance_dir(config, runtime=runtime, create=True), "tiger-state.json")
    state = _normalize_state_data(_to_plain(state))
    # Race condition guard: preserve halt flag set by other crons
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                current = _normalize_state_data(json.load(f))
            cur_safety = current.get("safety", {})
            st_safety = state.get("safety", {})
            if cur_safety.get("halted") and not st_safety.get("halted"):
                if "safety" not in state or not isinstance(state.get("safety"), dict):
                    state["safety"] = {}
                state["safety"]["halted"] = True
                state["safety"]["haltReason"] = cur_safety.get("haltReason")
        except (json.JSONDecodeError, IOError):
            pass
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_file, _to_plain(state), runtime=runtime)


# ─── OI History ──────────────────────────────────────────────

def _oi_file(config=None, runtime=None):
    config = _get_config(config, runtime=runtime)
    return os.path.join(_instance_dir(config, runtime=runtime, create=False), "oi-history.json")


def load_oi_history(config=None, runtime=None):
    """Load OI history. Format: {asset: [{ts, oi, price}, ...]}"""
    path = _oi_file(config, runtime=runtime)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def append_oi_snapshot(asset, oi, price, config=None, runtime=None):
    """Append OI datapoint. Keep last 288 per asset (24h at 5min)."""
    config = _get_config(config, runtime=runtime)
    history = load_oi_history(config, runtime=runtime)
    if asset not in history:
        history[asset] = []
    history[asset].append({
        "ts": int(time.time()),
        "oi": oi,
        "price": price
    })
    history[asset] = history[asset][-288:]
    dir_path = _instance_dir(config, runtime=runtime, create=True)
    atomic_write(os.path.join(dir_path, "oi-history.json"), history, runtime=runtime)


# ─── Trade Log ───────────────────────────────────────────────

def log_trade(trade, config=None, runtime=None):
    """Append trade to log atomically."""
    config = _get_config(config, runtime=runtime)
    path = os.path.join(_instance_dir(config, runtime=runtime, create=True), "trade-log.json")
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
    atomic_write(path, trades, runtime=runtime)


def get_trade_log_path(config=None, runtime=None):
    """Return the trade log file path for the current instance."""
    config = _get_config(config, runtime=runtime)
    return os.path.join(_instance_dir(config, runtime=runtime, create=False), "trade-log.json")


def load_trade_log(config=None, runtime=None):
    """Load trade log. Returns list of trade dicts."""
    path = get_trade_log_path(config, runtime=runtime)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            pass
    return []


# ─── BTC Cache ────────────────────────────────────────────────

def _btc_cache_file(config=None, runtime=None):
    config = _get_config(config, runtime=runtime)
    return os.path.join(_instance_dir(config, runtime=runtime, create=True), "btc-cache.json")


def load_btc_cache(config=None, runtime=None):
    """Load BTC price cache (used by correlation scanner)."""
    path = _btc_cache_file(config, runtime=runtime)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_btc_cache(price, check_ts, scan_price=None, scan_ts=None, config=None, runtime=None):
    """Save BTC price cache atomically.

    Args:
        price: Current BTC price.
        check_ts: ISO timestamp of this check.
        scan_price: BTC price at last full alt scan (set after alt scan completes).
        scan_ts: ISO timestamp of last full alt scan.
    """
    path = _btc_cache_file(config, runtime=runtime)
    data = load_btc_cache(config=config, runtime=runtime)
    data["last_btc_price"] = price
    data["last_btc_check"] = check_ts
    if scan_price is not None:
        data["last_scan_price"] = scan_price
    if scan_ts is not None:
        data["last_scan_ts"] = scan_ts
    atomic_write(path, data, runtime=runtime)


# ─── DSL State ───────────────────────────────────────────────

def load_dsl_state(asset, config=None, runtime=None):
    """Load DSL state for a position."""
    config = _get_config(config, runtime=runtime)
    path = os.path.join(_instance_dir(config, runtime=runtime, create=False), f"dsl-{asset}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_dsl_state(asset, dsl_state, config=None, runtime=None):
    """Save DSL state atomically."""
    config = _get_config(config, runtime=runtime)
    path = os.path.join(_instance_dir(config, runtime=runtime, create=True), f"dsl-{asset}.json")
    dsl_state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(path, dsl_state, runtime=runtime)


# ─── MCP Helpers ─────────────────────────────────────────────

def mcporter_call(tool, runner=None, sleep_fn=None, timeout_seconds=30, **kwargs):
    """Call a Senpi MCP tool via mcporter with 3-attempt retry.
    Raises RuntimeError after 3 failed attempts.
    Uses temp file for stdout to prevent pipe buffer truncation."""
    import tempfile
    runner = runner or subprocess.Popen
    sleep_fn = sleep_fn or time.sleep
    cmd = ["mcporter", "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            cmd.append(f"{k}={'true' if v else 'false'}")
        else:
            cmd.append(f"{k}={v}")

    last_error = None
    for attempt in range(3):
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="mcporter_")
        try:
            with os.fdopen(fd, "w+") as tmp_out:
                proc = runner(cmd, stdout=tmp_out, stderr=subprocess.PIPE, text=True)
                try:
                    _, stderr = proc.communicate(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError("timeout")
                if proc.returncode != 0:
                    raise RuntimeError(stderr.strip())
            with open(tmp_path) as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "unknown"))
            # Strip {success, data} envelope — callers get inner data directly
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            last_error = e
            if attempt < 2:
                sleep_fn(3)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    raise RuntimeError(f"mcporter {tool} failed after 3 attempts: {last_error}")


def mcporter_call_safe(tool, **kwargs):
    """Like mcporter_call but returns error dict instead of raising.
    Use for non-critical calls where the caller handles errors gracefully."""
    try:
        return mcporter_call(tool, **kwargs)
    except Exception as e:
        return {"error": str(e)}


def get_all_instruments(call_fn=None):
    """Fetch all instruments with OI, funding, volume."""
    call = call_fn or mcporter_call_safe
    result = call("market_list_instruments")
    if not result or result.get("error"):
        return []
    return result.get("instruments", [])


def get_asset_candles(asset, intervals=None, include_funding=False, call_fn=None):
    """Fetch candle data for an asset."""
    if intervals is None:
        intervals = ["1h", "4h"]
    call = call_fn or mcporter_call_safe
    return call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=intervals,
        include_order_book=False,
        include_funding=include_funding,
    )


def get_prices(assets=None, call_fn=None):
    """Fetch current prices."""
    kwargs = {}
    if assets:
        kwargs["assets"] = assets
    call = call_fn or mcporter_call_safe
    return call("market_get_prices", **kwargs)


def get_sm_markets(limit=50, call_fn=None):
    """Get smart money market concentration."""
    call = call_fn or mcporter_call_safe
    result = call("leaderboard_get_markets", limit=limit)
    if not result or result.get("error"):
        return []
    # leaderboard_get_markets nests: {markets: {markets: [...]}}
    markets = result.get("markets", result)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def get_portfolio(call_fn=None):
    """Get current portfolio."""
    call = call_fn or mcporter_call_safe
    return call("account_get_portfolio")


def get_clearinghouse(wallet, call_fn=None):
    """Get clearinghouse state for a strategy wallet."""
    call = call_fn or mcporter_call_safe
    return call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def create_position(wallet, orders, reason="", call_fn=None):
    """Create a position."""
    call = call_fn or mcporter_call_safe
    return call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=orders,
        reason=reason,
    )


def edit_position(wallet, coin, call_fn=None, **kwargs):
    """Edit a position."""
    call = call_fn or mcporter_call_safe
    return call(
        "edit_position",
        strategyWalletAddress=wallet,
        coin=coin,
        **kwargs,
    )


def close_position(wallet, coin, reason="", call_fn=None):
    """Close a position."""
    call = call_fn or mcporter_call_safe
    return call(
        "close_position",
        strategyWalletAddress=wallet,
        coin=coin,
        reason=reason,
    )


# ─── Output ──────────────────────────────────────────────────

def output(data, runtime=None):
    """Print JSON output with full signal data."""
    print(json.dumps(data, indent=2))


def output_heartbeat(runtime=None):
    _ = runtime
    print(json.dumps({"success": True, "heartbeat": "HEARTBEAT_OK"}))


def output_error(error_msg, actionable=False, runtime=None):
    _ = runtime
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
    if not iso_timestamp:
        return 999999
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        return (now_utc() - ts).total_seconds() / 3600
    except (ValueError, TypeError):
        return 999999


def days_remaining(config):
    """Calculate days remaining until deadline."""
    start = config.get("startTime")
    if not start:
        return config.get("deadlineDays", 7)
    start_dt = _parse_iso_datetime(start)
    if start_dt is None:
        return config.get("deadlineDays", 7)
    elapsed = (now_utc() - start_dt).total_seconds() / 86400
    return max(0, config.get("deadlineDays", 7) - elapsed)


def day_number(config):
    """Current day number (1-indexed)."""
    start = config.get("startTime")
    if not start:
        return 1
    start_dt = _parse_iso_datetime(start)
    if start_dt is None:
        return 1
    elapsed = (now_utc() - start_dt).total_seconds() / 86400
    return min(int(elapsed) + 1, config.get("deadlineDays", 7))


def get_active_positions(state):
    positions = state.get("activePositions", {})
    if not isinstance(positions, dict):
        positions = {}
    state["activePositions"] = positions
    return positions


def set_active_positions(state, positions):
    state["activePositions"] = positions if isinstance(positions, dict) else {}


def get_safety(state):
    safety = state.get("safety", {})
    if not isinstance(safety, dict):
        safety = {}
    safety = _normalize_dict_with_aliases(safety, SAFETY_KEY_ALIASES)
    safety.setdefault("halted", False)
    safety.setdefault("haltReason", None)
    state["safety"] = safety
    return safety


def is_halted(state):
    return bool(get_safety(state).get("halted", False))


def halt_reason(state):
    return get_safety(state).get("haltReason")


def set_halt_state(state, halted, reason=None):
    safety = get_safety(state)
    safety["halted"] = bool(halted)
    safety["haltReason"] = reason if halted else None


def shorten_address(addr):
    if not addr or len(addr) <= 10:
        return addr or ""
    return f"{addr[:6]}...{addr[-4:]}"


def _parse_iso_datetime(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def get_disabled_patterns(config=None, runtime=None):
    """Load currently-disabled patterns from ROAR state."""
    config = _get_config(config, runtime=runtime)
    path = os.path.join(_instance_dir(config, runtime=runtime, create=False), "roar-state.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            data = json.load(f)
        disabled = data.get("disabled_patterns", {})
        now = datetime.now(timezone.utc)
        active = set()
        if isinstance(disabled, dict):
            for pattern, ts in disabled.items():
                expiry = _parse_iso_datetime(ts)
                if expiry is None or now < expiry:
                    active.add(pattern)
        return active
    except (json.JSONDecodeError, IOError):
        return set()


def get_pattern_min_confluence(config, state, pattern):
    """Resolve min confluence using per-pattern override, then aggression default."""
    overrides = config.get("patternConfluenceOverrides", {})
    if isinstance(overrides, dict):
        if pattern in overrides:
            return float(overrides[pattern])
        # Backward-compatible lookup for historical snake_case pattern keys.
        snake = pattern.lower()
        if snake in overrides:
            return float(overrides[snake])
    aggression = state.get("aggression", "NORMAL")
    return float(config.get("minConfluenceScore", {}).get(aggression, 999))


# ─── Prescreener Integration ────────────────────────────────

def _prescreened_file(config=None, runtime=None):
    config = _get_config(config, runtime=runtime)
    return os.path.join(_instance_dir(config, runtime=runtime, create=False), "prescreened.json")


def load_prescreened_candidates(instruments, config=None, include_leverage=True, runtime=None, env=None):
    """Load candidates from prescreened.json if fresh (<10min).

    Returns list of (name, ctx, max_lev) tuples if include_leverage=True,
    or (name, ctx) tuples if False. Returns None if no fresh data.

    Respects SCAN_GROUP env var: 'a' = group_a, 'b' = group_b, unset = all.
    """
    import time as _time
    config = _get_config(config, runtime=runtime)
    prescreened_file = _prescreened_file(config, runtime=runtime)
    env = env or os.environ
    scan_group = env.get("SCAN_GROUP", "").lower()

    try:
        if not os.path.exists(prescreened_file):
            return None
        with open(prescreened_file) as f:
            data = json.load(f)
        if _time.time() - data.get("timestamp", 0) > 600:
            return None

        if scan_group == "b":
            names = set(data.get("group_b", []))
        elif scan_group == "a":
            names = set(data.get("group_a", []))
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


def resolve_dependencies(runtime=None, overrides=None, require_workspace_env=True):
    """Return injectable TIGER dependencies for script entrypoints/tests."""
    if runtime is None:
        try:
            runtime = build_runtime(require_workspace_env=require_workspace_env)
        except RuntimeError as exc:
            output_error(str(exc), actionable=True)
    deps = {
        "runtime": runtime,
        "load_config": lambda: load_config(runtime=runtime),
        "load_state": lambda config=None: load_state(config=config, runtime=runtime),
        "save_state": lambda state_or_config, state=None: save_state(
            state_or_config, state=state, runtime=runtime
        ),
        "save_config": lambda config: save_config(config, runtime=runtime),
        "load_oi_history": lambda config=None: load_oi_history(config=config, runtime=runtime),
        "append_oi_snapshot": lambda asset, oi, price, config=None: append_oi_snapshot(
            asset, oi, price, config=config, runtime=runtime
        ),
        "load_trade_log": lambda config=None: load_trade_log(config=config, runtime=runtime),
        "days_remaining": days_remaining,
        "day_number": day_number,
        "now_utc": now_utc,
        "is_halted": is_halted,
        "halt_reason": halt_reason,
        "set_halt_state": set_halt_state,
        "get_active_positions": get_active_positions,
        "set_active_positions": set_active_positions,
        "output": lambda payload: output(payload, runtime=runtime),
        "get_all_instruments": get_all_instruments,
        "get_asset_candles": get_asset_candles,
        "get_prices": get_prices,
        "get_sm_markets": get_sm_markets,
        "get_clearinghouse": get_clearinghouse,
        "close_position": close_position,
        "edit_position": edit_position,
        "load_prescreened_candidates": lambda instruments, config=None, include_leverage=True: load_prescreened_candidates(
            instruments,
            config=config,
            include_leverage=include_leverage,
            runtime=runtime,
        ),
        "get_pattern_min_confluence": get_pattern_min_confluence,
        "get_disabled_patterns": lambda config=None: get_disabled_patterns(config=config, runtime=runtime),
        "prescreened_file": lambda config=None: _prescreened_file(config=config, runtime=runtime),
        "load_btc_cache": lambda config=None: load_btc_cache(config=config, runtime=runtime),
        "save_btc_cache": lambda price, check_ts, scan_price=None, scan_ts=None, config=None: save_btc_cache(
            price, check_ts, scan_price=scan_price, scan_ts=scan_ts, config=config, runtime=runtime
        ),
        "atomic_write": lambda path, data: atomic_write(path, data, runtime=runtime),
        "workspace": _runtime_attr(runtime, "workspace"),
        "state_dir": _runtime_attr(runtime, "state_dir"),
    }
    if overrides:
        deps.update(overrides)
    return deps
