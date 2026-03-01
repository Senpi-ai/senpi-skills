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
from pathlib import Path
from datetime import datetime, timezone

# ─── Paths ───────────────────────────────────────────────────

WORKSPACE = os.environ.get("TIGER_WORKSPACE",
             os.environ.get("OPENCLAW_WORKSPACE",
             os.path.join(os.environ.get("HOME", "/data/workspace"), "tiger")))
SCRIPTS_DIR = os.path.join(WORKSPACE, "scripts")
STATE_DIR = os.path.join(WORKSPACE, "state")
CONFIG_FILE = os.path.join(WORKSPACE, "tiger-config.json")

VERBOSE = os.environ.get("TIGER_VERBOSE") == "1"


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


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically — crash-safe via os.replace()."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2 if VERBOSE else None)
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


_cached_config = None


def load_config():
    """Load TIGER config with deep merge of defaults. Returns AliasDict for snake_case compat."""
    global _cached_config
    try:
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        _cached_config = _to_alias_dict(deep_merge(DEFAULT_CONFIG, user_config))
    except FileNotFoundError:
        _cached_config = _to_alias_dict(dict(DEFAULT_CONFIG))
    return _cached_config


def _get_config(config=None):
    """Get config — uses passed config, cached, or loads fresh."""
    if config is not None:
        return config
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    return load_config()


def save_config(config):
    atomic_write(CONFIG_FILE, config)


# ─── State ───────────────────────────────────────────────────

def _instance_dir(config):
    instance_key = config.get("strategyId", "default")
    d = os.path.join(WORKSPACE, "state", instance_key)
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


def load_state(config=None):
    """Load state with defaults. Re-reads from disk. Config is optional (uses cached)."""
    config = _get_config(config)
    state_file = os.path.join(_instance_dir(config), "tiger-state.json")
    state = deep_merge(DEFAULT_STATE, {})
    state["instanceKey"] = config.get("strategyId", "default")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                saved = json.load(f)
            state = deep_merge(state, saved)
        except (json.JSONDecodeError, IOError):
            pass
    return _to_alias_dict(state)


def save_state(state_or_config, state=None):
    """Save state atomically. Accepts save_state(state) or save_state(config, state)."""
    if state is None:
        # Called as save_state(state) — config is implicit
        state = state_or_config
        config = _get_config()
    else:
        # Called as save_state(config, state) — explicit config
        config = state_or_config
    state_file = os.path.join(_instance_dir(config), "tiger-state.json")
    # Race condition guard: preserve halt flag set by other crons
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                current = json.load(f)
            cur_safety = current.get("safety", {})
            st_safety = state.get("safety", {})
            if cur_safety.get("halted") and not st_safety.get("halted"):
                if "safety" not in state or not isinstance(state.get("safety"), dict):
                    state["safety"] = {}
                state["safety"]["halted"] = True
                state["safety"]["haltReason"] = cur_safety.get("haltReason")
            # Also preserve halted at top level for scripts using flat access
            if current.get("halted") and not state.get("halted"):
                state["halted"] = True
                state["haltReason"] = current.get("haltReason", current.get("halt_reason"))
        except (json.JSONDecodeError, IOError):
            pass
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(state_file, dict(state))


# ─── OI History ──────────────────────────────────────────────

def _oi_file(config=None):
    config = _get_config(config)
    return os.path.join(_instance_dir(config), "oi-history.json")


def load_oi_history(config=None):
    """Load OI history. Format: {asset: [{ts, oi, price}, ...]}"""
    path = _oi_file(config)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def append_oi_snapshot(asset, oi, price, config=None):
    """Append OI datapoint. Keep last 288 per asset (24h at 5min)."""
    config = _get_config(config)
    history = load_oi_history(config)
    if asset not in history:
        history[asset] = []
    history[asset].append({
        "ts": int(time.time()),
        "oi": oi,
        "price": price
    })
    history[asset] = history[asset][-288:]
    atomic_write(_oi_file(config), history)


# ─── Trade Log ───────────────────────────────────────────────

def log_trade(trade, config=None):
    """Append trade to log atomically."""
    config = _get_config(config)
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


def get_trade_log_path(config=None):
    """Return the trade log file path for the current instance."""
    config = _get_config(config)
    return os.path.join(_instance_dir(config), "trade-log.json")


def load_trade_log(config=None):
    """Load trade log. Returns list of trade dicts."""
    path = get_trade_log_path(config)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            pass
    return []


# ─── DSL State ───────────────────────────────────────────────

def load_dsl_state(asset, config=None):
    """Load DSL state for a position."""
    config = _get_config(config)
    path = os.path.join(_instance_dir(config), f"dsl-{asset}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_dsl_state(asset, dsl_state, config=None):
    """Save DSL state atomically."""
    config = _get_config(config)
    path = os.path.join(_instance_dir(config), f"dsl-{asset}.json")
    dsl_state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(path, dsl_state)


# ─── DSL State Creation ──────────────────────────────────────

# Per-pattern DSL tier presets from SKILL.md
_DSL_PRESETS = {
    "COMPRESSION_BREAKOUT": {
        "phase1_retrace": 0.015,
        "tiers": [
            {"triggerPct": 5, "lockPct": 20, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 10, "lockPct": 50, "retrace": 0.012, "maxBreaches": 2},
            {"triggerPct": 20, "lockPct": 70, "retrace": 0.01, "maxBreaches": 2},
            {"triggerPct": 35, "lockPct": 80, "retrace": 0.008, "maxBreaches": 1},
        ],
    },
    "MOMENTUM_BREAKOUT": {
        "phase1_retrace": 0.012,
        "tiers": [
            {"triggerPct": 5, "lockPct": 60, "breachesNeeded": 3},
            {"triggerPct": 10, "lockPct": 60, "breachesNeeded": 2},
            {"triggerPct": 15, "lockPct": 65, "breachesNeeded": 2},
            {"triggerPct": 20, "lockPct": 70, "breachesNeeded": 1},
        ],
    },
    "CORRELATION_LAG": {
        "phase1_retrace": 0.015,
        "tiers": [
            {"triggerPct": 5, "lockPct": 20, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 10, "lockPct": 50, "retrace": 0.012, "maxBreaches": 2},
            {"triggerPct": 20, "lockPct": 70, "retrace": 0.01, "maxBreaches": 2},
            {"triggerPct": 35, "lockPct": 80, "retrace": 0.008, "maxBreaches": 1},
        ],
    },
    "MEAN_REVERSION": {
        "phase1_retrace": 0.015,
        "tiers": [
            {"triggerPct": 5, "lockPct": 30, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 10, "lockPct": 55, "retrace": 0.012, "maxBreaches": 2},
            {"triggerPct": 20, "lockPct": 70, "retrace": 0.01, "maxBreaches": 2},
            {"triggerPct": 35, "lockPct": 80, "retrace": 0.008, "maxBreaches": 1},
        ],
    },
    "FUNDING_ARB": {
        "phase1_retrace": 0.020,
        "tiers": [
            {"triggerPct": 5, "lockPct": 20, "retrace": 0.020, "maxBreaches": 3},
            {"triggerPct": 10, "lockPct": 40, "retrace": 0.018, "maxBreaches": 2},
            {"triggerPct": 20, "lockPct": 60, "retrace": 0.015, "maxBreaches": 2},
            {"triggerPct": 35, "lockPct": 75, "retrace": 0.012, "maxBreaches": 1},
        ],
    },
}


def create_dsl_state(asset, direction, entry_price, size, margin,
                     leverage, pattern, config=None):
    """Create a correctly-structured DSL state dict for a new position.

    Uses per-pattern tier presets from SKILL.md.  Falls back to
    MOMENTUM_BREAKOUT defaults if pattern is unknown.
    """
    config = _get_config(config)
    preset = _DSL_PRESETS.get(pattern, _DSL_PRESETS["MOMENTUM_BREAKOUT"])
    retrace = preset["phase1_retrace"]

    if direction.upper() == "LONG":
        absolute_floor = round(entry_price * (1 - retrace), 6)
    else:
        absolute_floor = round(entry_price * (1 + retrace), 6)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "version": 1,
        "active": True,
        "instanceKey": config.get("strategyId", "default"),
        "asset": asset,
        "direction": direction.upper(),
        "entryPrice": entry_price,
        "size": size,
        "margin": margin,
        "leverage": leverage,
        "pattern": pattern,
        "wallet": config.get("strategyWallet", config.get("strategy_wallet", "")),
        "strategyWallet": config.get("strategyWallet", config.get("strategy_wallet", "")),
        "strategyId": config.get("strategyId", config.get("strategy_id", "")),
        "createdAt": now_iso,
        "updatedAt": now_iso,
        "phase": 1,
        "currentTierIndex": -1,
        "currentBreachCount": 0,
        "tierFloorPrice": 0,
        "highWaterPrice": entry_price,
        "highWaterRoe": 0,
        "highWaterTime": now_iso,
        "consecutiveBreaches": 0,
        "consecutiveFetchFailures": 0,
        "floorPrice": absolute_floor,
        "phase1": {
            "retraceThreshold": retrace,
            "consecutiveBreachesRequired": 3,
            "absoluteFloor": absolute_floor,
            "maxMinutes": 90,
        },
        "phase2": {
            "retraceThreshold": retrace * 0.8,
            "consecutiveBreachesRequired": 2,
            "stagnationTpRoe": 0.08,
            "stagnationTpStaleMinutes": 60,
        },
        "phase2TriggerTier": 0,
        "tiers": preset["tiers"],
        "lastCheck": None,
        "lastPrice": None,
        "pendingClose": False,
        "closedAt": None,
        "closeReason": None,
        "closePrice": None,
    }


# ─── MCP Helpers ─────────────────────────────────────────────

def mcporter_call(tool, **kwargs):
    """Call a Senpi MCP tool via mcporter with 3-attempt retry."""
    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd = [mcporter_bin, "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            cmd.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            cmd.append(f"{k}={'true' if v else 'false'}")
        else:
            cmd.append(f"{k}={v}")

    last_error = None
    for attempt in range(3):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("timeout")
            if proc.returncode != 0:
                raise RuntimeError(stderr.strip())
            data = json.loads(stdout)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "unknown"))
            return data
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)
    # Return error dict on total failure (don't crash)
    return {"error": str(last_error), "success": False}


def get_all_instruments():
    """Fetch all instruments with OI, funding, volume."""
    result = mcporter_call("market_list_instruments")
    data = result.get("data", result)
    return data.get("instruments", [])


def get_asset_candles(asset, intervals=None, include_funding=False):
    """Fetch candle data for an asset."""
    if intervals is None:
        intervals = ["1h", "4h"]
    return mcporter_call("market_get_asset_data",
                         asset=asset,
                         candle_intervals=intervals,
                         include_order_book=False,
                         include_funding=include_funding)


def get_prices(assets=None):
    """Fetch current prices."""
    kwargs = {}
    if assets:
        kwargs["assets"] = assets
    return mcporter_call("market_get_prices", **kwargs)


def get_sm_markets(limit=50):
    """Get smart money market concentration."""
    result = mcporter_call("leaderboard_get_markets", limit=limit)
    data = result.get("data", {})
    markets = data.get("markets", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    return markets if isinstance(markets, list) else []


def get_portfolio():
    """Get current portfolio."""
    return mcporter_call("account_get_portfolio")


def get_clearinghouse(wallet):
    """Get clearinghouse state for a strategy wallet."""
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def create_position(wallet, orders, reason=""):
    """Create a position."""
    return mcporter_call("create_position",
                         strategyWalletAddress=wallet,
                         orders=orders,
                         reason=reason)


def edit_position(wallet, coin, **kwargs):
    """Edit a position."""
    return mcporter_call("edit_position",
                         strategyWalletAddress=wallet,
                         coin=coin,
                         **kwargs)


def close_position(wallet, coin, reason=""):
    """Close a position."""
    return mcporter_call("close_position",
                         strategyWalletAddress=wallet,
                         coin=coin,
                         reason=reason)


# ─── Output ──────────────────────────────────────────────────

def output(data):
    """Print JSON output. Minimal by default, verbose opt-in via TIGER_VERBOSE=1."""
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
    start_dt = datetime.fromisoformat(start)
    elapsed = (now_utc() - start_dt).total_seconds() / 86400
    return max(0, config.get("deadlineDays", 7) - elapsed)


def day_number(config):
    """Current day number (1-indexed)."""
    start = config.get("startTime")
    if not start:
        return 1
    start_dt = datetime.fromisoformat(start)
    elapsed = (now_utc() - start_dt).total_seconds() / 86400
    return min(int(elapsed) + 1, config.get("deadlineDays", 7))


def shorten_address(addr):
    if not addr or len(addr) <= 10:
        return addr or ""
    return f"{addr[:6]}...{addr[-4:]}"


# ─── Prescreener Integration ────────────────────────────────

def load_prescreened_candidates(instruments, config=None, include_leverage=True):
    """Load candidates from prescreened.json if fresh (<10min).
    
    Returns list of (name, ctx, max_lev) tuples if include_leverage=True,
    or (name, ctx) tuples if False. Returns None if no fresh data.
    
    Respects SCAN_GROUP env var: 'a' = group_a, 'b' = group_b, unset = all.
    """
    import time as _time
    prescreened_file = os.path.join(STATE_DIR, "prescreened.json")
    scan_group = os.environ.get("SCAN_GROUP", "").lower()

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


# ─── Lifecycle & Health Check Adapters ───────────────────────
# Used by generic senpi-enter.py / senpi-close.py / senpi-healthcheck.py.
# After rebase onto main, lib/senpi_state/ provides the shared engine.

import glob as _glob


def _dsl_glob_pattern(config=None):
    config = _get_config(config)
    return os.path.join(_instance_dir(config), "dsl-*.json")


def _dsl_path(asset, config=None):
    config = _get_config(config)
    return os.path.join(_instance_dir(config), f"dsl-{asset}.json")


def get_lifecycle_adapter(**kwargs):
    """Return callbacks for generic senpi-enter/close scripts."""
    config = load_config()
    wallet = config.get("strategyWallet", config.get("strategy_wallet", ""))
    instance_key = config.get("strategyId", "default")
    max_slots = config.get("maxSlots", 3)
    inst_dir = _instance_dir(config)
    journal_path = os.path.join(inst_dir, "trade-journal.jsonl")

    def _load_state_cb():
        return load_state(config)

    def _save_state_cb(state):
        save_state(state)

    def _create_dsl(asset, direction, entry_price, size, margin, leverage, pattern):
        return create_dsl_state(asset, direction, entry_price, size, margin,
                                leverage, pattern, config)

    def _save_dsl(asset, dsl_state):
        save_dsl_state(asset, dsl_state, config)

    def _load_dsl(asset):
        return load_dsl_state(asset, config)

    def _log_trade_cb(trade):
        log_trade(trade, config)

    def _create_dsl_for_healthcheck(asset, direction, entry_price, size,
                                    leverage, instance_key=None):
        return create_dsl_state(asset, direction, entry_price, size, 0,
                                leverage, "HEALTHCHECK_AUTO_CREATE", config)

    return {
        "wallet": wallet,
        "skill": "tiger",
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
        # Healthcheck adapter keys
        "dsl_glob": _dsl_glob_pattern(config),
        "dsl_state_path": lambda asset: _dsl_path(asset, config),
        "create_dsl_for_healthcheck": _create_dsl_for_healthcheck,
        "tiers": None,
    }


def get_healthcheck_adapter(**kwargs):
    """Return adapter dict for senpi-healthcheck.py."""
    adapter = get_lifecycle_adapter(**kwargs)
    return {
        "wallet": adapter["wallet"],
        "skill": adapter["skill"],
        "instance_key": adapter["instance_key"],
        "dsl_glob": adapter["dsl_glob"],
        "dsl_state_path": adapter["dsl_state_path"],
        "create_dsl": adapter["create_dsl_for_healthcheck"],
        "tiers": adapter.get("tiers"),
    }
