#!/usr/bin/env python3
"""
wolf_config.py — Multi-strategy config loader for WOLF v6

Provides a single importable module every script uses to load strategy config,
resolve state file paths, and handle legacy migration.

Usage:
    from wolf_config import load_strategy, load_all_strategies, dsl_state_path
    cfg = load_strategy("wolf-abc123")   # Specific strategy
    cfg = load_strategy()                # Default strategy
    strategies = load_all_strategies()   # All enabled strategies
    path = dsl_state_path("wolf-abc123", "HYPE")
"""

import glob
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time

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
    """Load the strategy registry, with auto-migration from legacy format."""
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE) as f:
            return json.load(f)

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

    _fail("No config found. Run wolf-setup.py first.")


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


def load_dsl_state(strategy_key, asset):
    """Load DSL state for a strategy + asset.  Returns dict or None."""
    path = dsl_state_path(strategy_key, asset)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_dsl_state(strategy_key, asset, dsl_state):
    """Save DSL state atomically for a strategy + asset."""
    path = dsl_state_path(strategy_key, asset)
    from datetime import datetime, timezone
    dsl_state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(path, dsl_state)


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
            except (json.JSONDecodeError, IOError, KeyError):
                continue
    return positions


def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter. Returns the `data` portion of the response.

    Standardized invocation across all wolf-strategy scripts:
      mcporter call senpi.{tool} key=value ...

    Args:
        tool: Tool name (e.g. "market_get_prices", "close_position").
        retries: Number of attempts before giving up.
        timeout: Subprocess timeout in seconds.
        **kwargs: Tool arguments as key=value pairs.

    Returns:
        The `data` dict from the MCP response envelope.

    Raises:
        RuntimeError: If all retries fail or the tool returns success=false.
    """
    args = []
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            args.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            args.append(f"{k}={json.dumps(v)}")
        else:
            args.append(f"{k}={v}")

    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd_str = " ".join(
        [shlex.quote(mcporter_bin), "call", shlex.quote(f"senpi.{tool}")]
        + [shlex.quote(a) for a in args]
    )
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


def atomic_write(path, data):
    """Atomically write JSON data to a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


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


def get_lifecycle_adapter(strategy_key=None):
    """Return callbacks and config for generic senpi-enter/close scripts.

    Synthesizes a state dict from wolf's per-strategy DSL files so that
    the shared positions.py guard checks (halted, slots, duplicates) work
    identically across all skills.

    Args:
        strategy_key: Strategy key (e.g. "wolf-abc123"). Falls back to
                      WOLF_STRATEGY env var or defaultStrategy from registry.

    Returns:
        Dict with wallet, skill, instance_key, max_slots, and all the
        load/save/create callbacks that positions.py expects.
    """
    cfg = load_strategy(strategy_key)
    resolved_key = cfg["_key"]
    sdir = cfg["_state_dir"]
    os.makedirs(sdir, exist_ok=True)
    wolf_state_file = os.path.join(sdir, "wolf-state.json")

    def _load_state():
        """Build state dict from wolf-state.json + DSL files."""
        base = {"activePositions": {}, "halted": False, "availableSlots": 0,
                "safety": {"halted": False}}
        if os.path.exists(wolf_state_file):
            try:
                with open(wolf_state_file) as f:
                    saved = json.load(f)
                for k in ("halted", "safety"):
                    if k in saved:
                        base[k] = saved[k]
            except (json.JSONDecodeError, IOError):
                pass

        positions = {}
        for sf in glob.glob(dsl_state_glob(resolved_key)):
            try:
                with open(sf) as f:
                    s = json.load(f)
                if not isinstance(s, dict):
                    continue
                if s.get("active"):
                    positions[s["asset"]] = {
                        "direction": s.get("direction", ""),
                        "leverage": s.get("leverage", 0),
                        "margin": s.get("margin", 0),
                        "entryPrice": s.get("entryPrice", 0),
                        "size": s.get("size", 0),
                        "pattern": s.get("createdBy", ""),
                        "enteredAt": s.get("createdAt", ""),
                    }
            except (json.JSONDecodeError, IOError, KeyError, AttributeError):
                continue

        base["activePositions"] = positions
        base["availableSlots"] = max(0, cfg.get("slots", 2) - len(positions))
        return base

    def _save_state(state):
        atomic_write(wolf_state_file, state)

    def _create_dsl(asset, direction, entry_price, size, margin, leverage, pattern):
        dsl = dsl_state_template(asset, direction, entry_price, size, leverage,
                                 strategy_key=resolved_key,
                                 tiers=cfg.get("dsl", {}).get("tiers"),
                                 created_by=pattern)
        dsl["margin"] = margin
        return dsl

    def _save_dsl(asset, dsl):
        path = dsl_state_path(resolved_key, asset)
        from datetime import datetime, timezone
        dsl["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        atomic_write(path, dsl)

    def _load_dsl(asset):
        return load_dsl_state(resolved_key, asset)

    def _log_trade(trade):
        from datetime import datetime, timezone
        trades_file = os.path.join(sdir, "trade-log.json")
        trades = []
        if os.path.exists(trades_file):
            try:
                with open(trades_file) as f:
                    trades = json.load(f)
            except (json.JSONDecodeError, IOError):
                trades = []
        trade["version"] = 1
        trade["strategyKey"] = resolved_key
        trade["timestamp"] = datetime.now(timezone.utc).isoformat()
        trades.append(trade)
        atomic_write(trades_file, trades)

    journal_path = os.path.join(sdir, "trade-journal.jsonl")

    def _create_dsl_for_healthcheck(asset, direction, entry_price, size,
                                    leverage, instance_key=None):
        """Adapter for healthcheck auto-create (different signature)."""
        return dsl_state_template(
            asset=asset, direction=direction, entry_price=entry_price,
            size=size, leverage=leverage,
            strategy_key=instance_key or resolved_key,
            tiers=cfg.get("dsl", {}).get("tiers"),
            created_by="healthcheck_auto_create",
        )

    return {
        "wallet": cfg.get("wallet", ""),
        "skill": "wolf",
        "instance_key": resolved_key,
        "max_slots": cfg.get("slots", 2),
        "load_state": _load_state,
        "save_state": _save_state,
        "create_dsl": _create_dsl,
        "save_dsl": _save_dsl,
        "load_dsl": _load_dsl,
        "log_trade": _log_trade,
        "journal_path": journal_path,
        "output": lambda data: print(json.dumps(data)),
        # Healthcheck adapter keys
        "dsl_glob": dsl_state_glob(resolved_key),
        "dsl_state_path": lambda asset: dsl_state_path(resolved_key, asset),
        "create_dsl_for_healthcheck": _create_dsl_for_healthcheck,
        "tiers": cfg.get("dsl", {}).get("tiers"),
    }


def list_instances(enabled_only=True):
    """Return all strategy keys for multi-strategy health checks."""
    return list(load_all_strategies(enabled_only=enabled_only).keys())


def get_healthcheck_adapter(strategy_key=None):
    """Return an adapter dict shaped for senpi-healthcheck.py.

    Reuses get_lifecycle_adapter() and maps keys to the healthcheck interface.
    """
    adapter = get_lifecycle_adapter(strategy_key=strategy_key)
    return {
        "wallet": adapter["wallet"],
        "skill": adapter["skill"],
        "instance_key": adapter["instance_key"],
        "dsl_glob": adapter["dsl_glob"],
        "dsl_state_path": adapter["dsl_state_path"],
        "create_dsl": adapter["create_dsl_for_healthcheck"],
        "tiers": adapter.get("tiers"),
    }


def dsl_state_template(asset, direction, entry_price, size, leverage,
                       strategy_key=None, tiers=None, created_by="entry_flow"):
    """Create a minimal valid DSL state dict for a new position.

    Computes correct absoluteFloor/floorPrice from entry price and leverage
    so Phase 1 protection is active immediately (addresses PR #24 gap where
    floor was left at 0).

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

    retrace_pct = 5.0 / max(leverage, 1)
    if direction.upper() == "LONG":
        absolute_floor = round(entry_price * (1 - retrace_pct / 100), 6)
    else:
        absolute_floor = round(entry_price * (1 + retrace_pct / 100), 6)

    approximate = entry_price <= 0 or size <= 0

    state = {
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
        "floorPrice": absolute_floor if not approximate else 0,
        "tiers": tiers,
        "phase1": {
            "retraceThreshold": retrace_pct,
            "absoluteFloor": absolute_floor if not approximate else 0,
            "consecutiveBreachesRequired": 3,
        },
        "phase2TriggerTier": 0,
        "createdAt": now,
        "lastCheck": now,
        "strategyKey": strategy_key,
        "createdBy": created_by,
    }

    if approximate:
        state["approximate"] = True

    return state
