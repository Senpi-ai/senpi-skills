#!/usr/bin/env python3
"""
wolf_config.py — Multi-strategy config loader for WOLF v6.1

Provides a single importable module every script uses to load strategy config,
resolve state file paths, and handle legacy migration.

Usage:
    from wolf_config import load_strategy, load_all_strategies, dsl_state_path
    cfg = load_strategy("wolf-abc123")   # Specific strategy
    cfg = load_strategy()                # Default strategy
    strategies = load_all_strategies()   # All enabled strategies
    path = dsl_state_path("wolf-abc123", "HYPE")
"""

import json, os, sys, glob, subprocess, time, tempfile, shlex, fcntl
from contextlib import contextmanager
from datetime import datetime, timezone

WORKSPACE = os.environ.get("WOLF_WORKSPACE",
    os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))
REGISTRY_FILE = os.path.join(WORKSPACE, "wolf-strategies.json")
LEGACY_CONFIG = os.path.join(WORKSPACE, "wolf-strategy.json")
LEGACY_STATE_PATTERN = os.path.join(WORKSPACE, "dsl-state-WOLF-*.json")


def _fail(msg):
    """Print error JSON and exit."""
    print(json.dumps({"success": False, "error": msg}))
    sys.exit(1)


def _load_registry():
    """Load the strategy registry, with auto-migration from legacy format.

    Retries once on file-not-found to handle transient filesystem glitches
    (e.g. NFS/overlay mount delays in container environments).
    """
    for attempt in range(2):
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                _fail(f"Registry file corrupt at {REGISTRY_FILE}: {e}")
        elif attempt == 0:
            # Retry once after 1s — handles transient filesystem unavailability
            time.sleep(1)
            continue
        else:
            break  # fall through to legacy check

    # Fallback: auto-migrate legacy single-strategy config
    if os.path.exists(LEGACY_CONFIG):
        with open(LEGACY_CONFIG) as f:
            legacy = json.load(f)
        sid = legacy.get("strategyId", "unknown")
        key = f"wolf-{sid[:8]}" if sid != "unknown" else "wolf-default"

        # Build strategy entry from legacy config
        strategy = {
            "name": "Default Strategy",
            "wallet": legacy.get("wallet", ""),
            "strategyId": legacy.get("strategyId", ""),
            "budget": legacy.get("budget", 0),
            "slots": legacy.get("slots", 2),
            "marginPerSlot": legacy.get("marginPerSlot", 0),
            "defaultLeverage": legacy.get("defaultLeverage", 10),
            "dailyLossLimit": legacy.get("dailyLossLimit", 0),
            "autoDeleverThreshold": legacy.get("autoDeleverThreshold", 0),
            "dsl": {
                "preset": "aggressive",
                "tiers": [
                    {"triggerPct": 5, "lockPct": 50, "breaches": 3},
                    {"triggerPct": 10, "lockPct": 65, "breaches": 2},
                    {"triggerPct": 15, "lockPct": 75, "breaches": 2},
                    {"triggerPct": 20, "lockPct": 85, "breaches": 1}
                ]
            },
            "enabled": True
        }

        registry = {
            "version": 1,
            "defaultStrategy": key,
            "strategies": {key: strategy},
            "global": {
                "telegramChatId": str(legacy.get("telegramChatId", "")),
                "workspace": WORKSPACE,
                "notifications": {
                    "provider": "telegram",
                    "alertDedupeMinutes": 15
                }
            }
        }

        # Auto-migrate legacy state files to new directory structure
        _migrate_legacy_state_files(key)

        return registry

    _fail(f"No config found at {REGISTRY_FILE} (WORKSPACE={WORKSPACE}). Run wolf-setup.py first.")


def _migrate_legacy_state_files(strategy_key):
    """Move old dsl-state-WOLF-*.json files into state/{strategy_key}/dsl-*.json."""
    legacy_files = glob.glob(LEGACY_STATE_PATTERN)
    if not legacy_files:
        return

    new_dir = os.path.join(WORKSPACE, "state", strategy_key)
    os.makedirs(new_dir, exist_ok=True)

    for old_path in legacy_files:
        basename = os.path.basename(old_path)
        # dsl-state-WOLF-HYPE.json → dsl-HYPE.json
        asset = basename.replace("dsl-state-WOLF-", "").replace(".json", "")
        new_path = os.path.join(new_dir, f"dsl-{asset}.json")

        if os.path.exists(new_path):
            continue  # don't overwrite already-migrated files

        try:
            with open(old_path) as f:
                state = json.load(f)
            # Add strategy context
            state["strategyKey"] = strategy_key
            if "version" not in state:
                state["version"] = 2
            atomic_write(new_path, state)
        except (json.JSONDecodeError, IOError):
            continue


def load_strategy(strategy_key=None):
    """Load a single strategy config.

    Args:
        strategy_key: Strategy key (e.g. "wolf-abc123"). If None, uses
                      WOLF_STRATEGY env var or defaultStrategy from registry.

    Returns:
        Strategy config dict with injected _key, _global, _workspace, _state_dir.
    """
    reg = _load_registry()
    if strategy_key is None:
        strategy_key = os.environ.get("WOLF_STRATEGY", reg.get("defaultStrategy"))
    if not strategy_key or strategy_key not in reg["strategies"]:
        _fail(f"Strategy '{strategy_key}' not found. "
              f"Available: {list(reg['strategies'].keys())}")
    cfg = reg["strategies"][strategy_key].copy()
    cfg["_key"] = strategy_key
    cfg["_global"] = reg.get("global", {})
    cfg["_workspace"] = reg.get("global", {}).get("workspace", WORKSPACE)
    cfg["_state_dir"] = os.path.join(cfg["_workspace"], "state", strategy_key)
    return cfg


def load_all_strategies(enabled_only=True):
    """Load all strategies from the registry.

    Args:
        enabled_only: If True (default), skip strategies with enabled=False.

    Returns:
        Dict of strategy_key → strategy config.
    """
    reg = _load_registry()
    result = {}
    for key, cfg in reg["strategies"].items():
        if enabled_only and not cfg.get("enabled", True):
            continue
        entry = cfg.copy()
        entry["_key"] = key
        entry["_global"] = reg.get("global", {})
        entry["_workspace"] = reg.get("global", {}).get("workspace", WORKSPACE)
        entry["_state_dir"] = os.path.join(entry["_workspace"], "state", key)
        result[key] = entry
    return result


def state_dir(strategy_key):
    """Get (and create) the state directory for a strategy."""
    d = os.path.join(WORKSPACE, "state", strategy_key)
    os.makedirs(d, exist_ok=True)
    return d


def dsl_state_path(strategy_key, asset):
    """Get the DSL state file path for a strategy + asset."""
    return os.path.join(state_dir(strategy_key), f"dsl-{asset}.json")


def dsl_state_glob(strategy_key):
    """Get the glob pattern for all DSL state files in a strategy."""
    return os.path.join(state_dir(strategy_key), "dsl-*.json")


def get_all_active_positions():
    """Get all active positions across ALL strategies.

    Returns:
        Dict of asset → list of {strategyKey, direction, stateFile}.
    """
    positions = {}
    for key, cfg in load_all_strategies().items():
        for sf in glob.glob(dsl_state_glob(key)):
            try:
                with open(sf) as f:
                    s = json.load(f)
                if s.get("active"):
                    asset = s["asset"]
                    if asset not in positions:
                        positions[asset] = []
                    positions[asset].append({
                        "strategyKey": key,
                        "direction": s["direction"],
                        "stateFile": sf
                    })
            except (json.JSONDecodeError, IOError, KeyError, AttributeError):
                continue
    return positions


def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter. Returns the `data` portion of the response.

    Standardized invocation across all wolf-strategy scripts:
      mcporter call senpi.{tool} --args '{...}'

    Args:
        tool: Tool name (e.g. "market_get_prices", "close_position").
        retries: Number of attempts before giving up.
        timeout: Subprocess timeout in seconds.
        **kwargs: Tool arguments passed as a single --args JSON blob.

    Returns:
        The `data` dict from the MCP response envelope.

    Raises:
        RuntimeError: If all retries fail or the tool returns success=false.
    """
    filtered_args = {k: v for k, v in kwargs.items() if v is not None}

    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd = [mcporter_bin, "call", f"senpi.{tool}"]
    if filtered_args:
        cmd.extend(["--args", json.dumps(filtered_args)])
    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    last_error = None

    for attempt in range(retries):
        fd, tmp = None, None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            subprocess.run(
                f"{cmd_str} > {tmp} 2>/dev/null",
                shell=True, timeout=timeout,
            )
            with open(tmp) as f:
                d = json.load(f)
            if d.get("success"):
                return d.get("data", {})
            last_error = d.get("error", d)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
            last_error = str(e)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        if attempt < retries - 1:
            time.sleep(3)

    raise RuntimeError(f"mcporter {tool} failed after {retries} attempts: {last_error}")


def mcporter_call_safe(tool, retries=3, timeout=30, **kwargs):
    """Like mcporter_call but returns None instead of raising on failure."""
    try:
        return mcporter_call(tool, retries=retries, timeout=timeout, **kwargs)
    except RuntimeError:
        return None


def send_notification(message):
    """Send a Telegram notification directly via mcporter.

    Reads the telegram chatId from the strategy registry's global config.
    Silently fails — notifications should never crash the calling script.
    """
    try:
        reg = _load_registry()
        global_cfg = reg.get("global", {})
        chat_id = global_cfg.get("telegramChatId", "")
        if not chat_id:
            return
        target = f"telegram:{chat_id}"
        mcporter_call_safe("send_telegram_notification", retries=2, timeout=10,
                           target=target, message=message)
    except Exception:
        pass  # never crash the caller


HEARTBEAT_FILE = os.path.join(WORKSPACE, "state", "cron-heartbeats.json")

def heartbeat(cron_name):
    """Record that a cron job just ran. Called at the start of each script."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(HEARTBEAT_FILE) as f:
            beats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        beats = {}
    beats[cron_name] = now
    atomic_write(HEARTBEAT_FILE, beats)


def atomic_write(path, data):
    """Atomically write JSON data to a file."""
    if isinstance(data, str):
        data = json.loads(data)  # recover from pre-serialized input
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


@contextmanager
def strategy_lock(strategy_key, timeout=60):
    """Acquire an exclusive file lock per strategy key.

    Serializes position opens so that concurrent calls (e.g. two FIRST_JUMPs
    in the same scanner run) cannot race past the slot check.

    Args:
        strategy_key: Strategy key (e.g. "wolf-abc123").
        timeout: Seconds to wait for lock before raising.

    Yields once the lock is held; releases on exit.
    """
    lock_dir = os.path.join(WORKSPACE, "state", "locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{strategy_key}.lock")
    fd = open(lock_path, "w")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    fd.close()
                    raise RuntimeError(f"Could not acquire lock for {strategy_key} within {timeout}s")
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            fd.close()


# --- Risk-based leverage ---

RISK_LEVERAGE_RANGES = {
    "conservative": (0.15, 0.25),   # 15%-25% of max leverage
    "moderate":     (0.25, 0.50),   # 25%-50% of max leverage
    "aggressive":   (0.50, 0.75),   # 50%-75% of max leverage
}

SIGNAL_CONVICTION = {
    "FIRST_JUMP": 0.9,
    "CONTRIB_EXPLOSION": 0.8,
    "IMMEDIATE_MOVER": 0.7,
    "NEW_ENTRY_DEEP": 0.7,
    "DEEP_CLIMBER": 0.5,
}

ROTATION_COOLDOWN_MINUTES = 45  # positions younger than this can't be rotated out


def calculate_leverage(max_leverage, trading_risk="moderate", conviction=0.5):
    """Calculate leverage as a fraction of max leverage, scaled by risk tier and conviction.

    Args:
        max_leverage: Asset's maximum allowed leverage.
        trading_risk: Risk tier — "conservative", "moderate", or "aggressive".
        conviction: 0.0 to 1.0, where within the risk range to land.

    Returns:
        Integer leverage, clamped to [1, max_leverage].
    """
    min_pct, max_pct = RISK_LEVERAGE_RANGES.get(trading_risk, RISK_LEVERAGE_RANGES["moderate"])
    range_min = max_leverage * min_pct
    range_max = max_leverage * max_pct
    leverage = range_min + (range_max - range_min) * conviction
    return min(max(1, round(leverage)), max_leverage)


# --- DSL state file validation ---

DSL_REQUIRED_KEYS = [
    "asset", "direction", "entryPrice", "size", "leverage",
    "highWaterPrice", "phase", "currentBreachCount",
    "currentTierIndex", "tierFloorPrice", "tiers", "phase1",
]

PHASE1_REQUIRED_KEYS = ["retraceThreshold", "consecutiveBreachesRequired"]


def validate_dsl_state(state, state_file=None):
    """Validate a DSL state dict has all required keys.

    Args:
        state: The parsed JSON state dict.
        state_file: Optional file path for error messages.

    Returns:
        (True, None) if valid, (False, error_message) if invalid.
    """
    if not isinstance(state, dict):
        return False, f"state is not a dict ({state_file or 'unknown'})"

    missing = [k for k in DSL_REQUIRED_KEYS if k not in state]
    if missing:
        return False, f"missing keys {missing} ({state_file or 'unknown'})"

    phase1 = state.get("phase1")
    if not isinstance(phase1, dict):
        return False, f"phase1 is not a dict ({state_file or 'unknown'})"

    missing_p1 = [k for k in PHASE1_REQUIRED_KEYS if k not in phase1]
    if missing_p1:
        return False, f"phase1 missing keys {missing_p1} ({state_file or 'unknown'})"

    if not isinstance(state.get("tiers"), list):
        return False, f"tiers is not a list ({state_file or 'unknown'})"

    return True, None


def dsl_state_template(asset, direction, entry_price, size, leverage,
                       strategy_key=None, tiers=None, created_by="entry_flow"):
    """Create a minimal valid DSL state dict for a new position.

    Used by health check to create missing DSL state files.

    Args:
        asset: Coin symbol (e.g. "HYPE").
        direction: "LONG" or "SHORT".
        entry_price: Position entry price.
        size: Position size.
        leverage: Position leverage.
        strategy_key: Optional strategy key to embed.
        tiers: Optional tier list. Uses aggressive defaults if None.

    Returns:
        A valid DSL state dict ready for atomic_write.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if tiers is None:
        tiers = [
            {"triggerPct": 5, "lockPct": 50, "breaches": 3},
            {"triggerPct": 10, "lockPct": 65, "breaches": 2},
            {"triggerPct": 15, "lockPct": 75, "breaches": 2},
            {"triggerPct": 20, "lockPct": 85, "breaches": 1},
        ]

    # Calculate absoluteFloor from entry price, leverage, and retrace threshold
    retrace_roe = 10  # matches phase1.retraceThreshold
    retrace_price = (retrace_roe / 100) / leverage
    if direction.upper() == "LONG":
        abs_floor = round(entry_price * (1 - retrace_price), 6)
    else:
        abs_floor = round(entry_price * (1 + retrace_price), 6)

    return {
        "version": 2,
        "asset": asset,
        "direction": direction.upper(),
        "entryPrice": entry_price,
        "size": size,
        "leverage": leverage,
        "active": True,
        "highWaterPrice": entry_price,
        "phase": 1,
        "currentBreachCount": 0,
        "currentTierIndex": None,
        "tierFloorPrice": 0,
        "floorPrice": abs_floor,
        "tiers": tiers,
        "phase1": {
            "retraceThreshold": 10,
            "absoluteFloor": abs_floor,
            "consecutiveBreachesRequired": 3,
        },
        "phase2TriggerTier": 0,
        "createdAt": now,
        "lastCheck": now,
        "strategyKey": strategy_key,
        "createdBy": created_by,
    }


# --- Guard Rail defaults & helpers ---

GUARD_RAIL_DEFAULTS = {
    "maxEntriesPerDay": 8,
    "bypassOnProfit": True,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60,
}


def trade_counter_path(strategy_key):
    """Return the path to the trade counter file for a strategy."""
    return os.path.join(state_dir(strategy_key), "trade-counter.json")


def load_trade_counter(strategy_key):
    """Load (or create) the daily trade counter for a strategy.

    Handles day rollover: if the stored date != today (UTC), resets daily
    counters but preserves lastResults (streak carries across days),
    active cooldowns, and processedOrderIds.

    Merges guard rail config from the strategy registry with defaults.
    Does NOT auto-save — callers save after modifications.
    """
    path = trade_counter_path(strategy_key)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    old = {}
    try:
        with open(path) as f:
            old = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Load guard rail config from strategy registry
    try:
        cfg = load_strategy(strategy_key)
        gr_cfg = cfg.get("guardRails", {})
    except (SystemExit, Exception):
        gr_cfg = {}

    merged_config = {k: gr_cfg.get(k, v) for k, v in GUARD_RAIL_DEFAULTS.items()}

    if old.get("date") == today:
        # Same day — update config overlay but keep counters
        old.update(merged_config)
        return old

    # Day rollover — reset daily counters, preserve streaks + active cooldown
    counter = {
        "date": today,
        "accountValueStart": None,
        "entries": 0,
        "closedTrades": 0,
        "realizedPnl": 0.0,
        "gate": "OPEN",
        "gateReason": None,
        "cooldownUntil": None,
        "lastResults": old.get("lastResults", []),
        "processedOrderIds": [],
        "updatedAt": None,
    }
    counter.update(merged_config)

    # Preserve active cooldown across day boundary
    cooldown_until = old.get("cooldownUntil")
    if cooldown_until:
        try:
            cd_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if cd_dt > datetime.now(timezone.utc):
                counter["gate"] = "COOLDOWN"
                counter["gateReason"] = "consecutive_losses_cooldown (carried from previous day)"
                counter["cooldownUntil"] = cooldown_until
        except (ValueError, TypeError):
            pass

    return counter


def save_trade_counter(strategy_key, counter):
    """Save the trade counter, stamping updatedAt."""
    counter["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write(trade_counter_path(strategy_key), counter)


def check_gate(strategy_key):
    """Lightweight gate check. Returns (gate_status, gate_reason).

    - CLOSED -> ("CLOSED", reason)  — sticky until midnight
    - COOLDOWN with future expiry -> ("COOLDOWN", reason)
    - COOLDOWN expired -> clears gate, saves, returns ("OPEN", None)
    - Default -> ("OPEN", None)
    """
    path = trade_counter_path(strategy_key)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        with open(path) as f:
            counter = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ("OPEN", None)

    # Day rollover — treat as OPEN (but preserve active cooldown)
    if counter.get("date") != today:
        cooldown_until = counter.get("cooldownUntil")
        if cooldown_until:
            try:
                cd_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                if cd_dt > datetime.now(timezone.utc):
                    return ("COOLDOWN", counter.get("gateReason", "consecutive_losses_cooldown"))
            except (ValueError, TypeError):
                pass
        return ("OPEN", None)

    gate = counter.get("gate", "OPEN")

    if gate == "CLOSED":
        return ("CLOSED", counter.get("gateReason"))

    if gate == "COOLDOWN":
        cooldown_until = counter.get("cooldownUntil")
        if cooldown_until:
            try:
                cd_dt = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                if cd_dt > datetime.now(timezone.utc):
                    return ("COOLDOWN", counter.get("gateReason"))
            except (ValueError, TypeError):
                pass
        # Cooldown expired — acquire lock, re-read, then clear
        with strategy_lock(strategy_key):
            counter = load_trade_counter(strategy_key)
            # Re-check: another process may have already cleared it
            if counter.get("gate") != "COOLDOWN":
                return (counter.get("gate", "OPEN"), counter.get("gateReason"))
            counter["gate"] = "OPEN"
            counter["gateReason"] = None
            counter["cooldownUntil"] = None
            results = counter.get("lastResults", [])
            results.append("R")
            counter["lastResults"] = results[-20:]
            save_trade_counter(strategy_key, counter)
        return ("OPEN", None)

    return ("OPEN", None)


def increment_entry_counter(strategy_key):
    """Load counter, increment entries, save. Returns updated counter."""
    counter = load_trade_counter(strategy_key)
    counter["entries"] = counter.get("entries", 0) + 1
    save_trade_counter(strategy_key, counter)
    return counter
