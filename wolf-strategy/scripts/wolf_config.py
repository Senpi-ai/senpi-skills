#!/usr/bin/env python3
"""
wolf_config.py — Multi-strategy config loader for WOLF v6

Provides a single importable module every script uses to load strategy config,
resolve state file paths, and handle legacy migration.

When the dsl skill (>=5.0.0) is installed as a peer, uses dsl path helpers and
EventReader for consistency. Otherwise uses local path logic.
"""

import json, os, sys, glob

WORKSPACE = os.environ.get("WOLF_WORKSPACE",
    os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"))

# Optional: use dsl skill path helpers and EventReader when available
_dsl_engine = None
try:
    skills_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    dsl_scripts = os.path.join(skills_root, "dsl", "scripts")
    if os.path.isdir(dsl_scripts) and dsl_scripts not in sys.path:
        sys.path.insert(0, dsl_scripts)
    from dsl_skill import require_skill
    _dsl_engine = require_skill("dsl", min_version="5.0.0")
except ImportError:
    pass
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
            "xyzWallet": legacy.get("xyzWallet"),
            "xyzStrategyId": legacy.get("xyzStrategyId"),
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
    if _dsl_engine is not None:
        return _dsl_engine.dsl_state_path(strategy_key, asset, base=WORKSPACE)
    return os.path.join(state_dir(strategy_key), f"dsl-{asset}.json")


def dsl_state_glob(strategy_key):
    """Get the glob pattern for all DSL state files in a strategy."""
    if _dsl_engine is not None:
        return _dsl_engine.dsl_state_glob(strategy_key, base=WORKSPACE)
    return os.path.join(state_dir(strategy_key), "dsl-*.json")


def strategy_has_slot(strategy_key):
    """True if the strategy has at least one free slot for a new position."""
    if _dsl_engine is not None:
        return _dsl_engine.strategy_has_slot(strategy_key, base=WORKSPACE)
    # Fallback: count dsl-*.json vs slots from registry
    cfg = load_strategy(strategy_key)
    slots = cfg.get("slots", 2)
    existing = len(glob.glob(dsl_state_glob(strategy_key)))
    return existing < slots


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


def atomic_write(path, data):
    """Atomically write JSON data to a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
