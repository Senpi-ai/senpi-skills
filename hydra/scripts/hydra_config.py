# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

"""
HYDRA v1.0 — Standalone Config Helper
======================================
Self-contained utility module. No wolf_config dependency.

Provides: atomic_write, load_config, get_wallet_and_strategy,
load_state, save_state, load_runtime, save_runtime,
load_trade_counter, save_trade_counter, cooldown management,
oi_history management, mcporter_call, get_positions, output.
"""

import json
import os
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = os.environ.get("WORKSPACE", "/data/workspace")
SKILL_NAME = "hydra"
SKILL_DIR = os.path.join(WORKSPACE, "skills", SKILL_NAME)
STATE_DIR = os.path.join(SKILL_DIR, "state")
OI_HISTORY_DIR = os.path.join(STATE_DIR, "oi-history")
CONFIG_PATH = os.path.join(SKILL_DIR, "config", "hydra-config.json")


def ensure_dirs():
    """Create state and OI history directories if they don't exist."""
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(OI_HISTORY_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Atomic Write
# ---------------------------------------------------------------------------

def atomic_write(path: str, data: dict) -> None:
    """Write JSON via tmp file + atomic replace. Prevents partial writes."""
    ensure_dirs()
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load hydra-config.json. Returns empty dict on failure."""
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[HYDRA] Config load error: {e}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Wallet / Strategy
# ---------------------------------------------------------------------------

def get_wallet_and_strategy() -> tuple:
    """Get wallet address and strategy ID from env vars or config."""
    wallet = os.environ.get("WALLET_ADDRESS", "")
    strategy = os.environ.get("STRATEGY_ID", "")
    if not wallet or not strategy:
        cfg = load_config()
        wallet = wallet or cfg.get("wallet", {}).get("address", "")
        strategy = strategy or cfg.get("wallet", {}).get("strategyId", "")
    return wallet, strategy


# ---------------------------------------------------------------------------
# DSL State I/O
# ---------------------------------------------------------------------------

def load_state(coin: str) -> dict | None:
    """Load DSL state for a specific coin."""
    path = os.path.join(STATE_DIR, f"dsl-{coin}.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(coin: str, state: dict) -> None:
    """Save DSL state for a specific coin via atomic write."""
    path = os.path.join(STATE_DIR, f"dsl-{coin}.json")
    atomic_write(path, state)


def clear_state(coin: str) -> None:
    """Remove DSL state file for a coin (after position close)."""
    path = os.path.join(STATE_DIR, f"dsl-{coin}.json")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def list_active_dsl_states() -> list:
    """List all active DSL state files. Returns list of (coin, state) tuples."""
    ensure_dirs()
    active = []
    for f in os.listdir(STATE_DIR):
        if f.startswith("dsl-") and f.endswith(".json"):
            coin = f[4:-5]  # strip "dsl-" prefix and ".json" suffix
            state = load_state(coin)
            if state and state.get("active"):
                active.append((coin, state))
    return active


# ---------------------------------------------------------------------------
# Runtime State (trade log, tier stats, gate)
# ---------------------------------------------------------------------------

def _runtime_path() -> str:
    return os.path.join(STATE_DIR, "runtime.json")


def load_runtime() -> dict:
    """Load runtime state. Returns default structure if not found."""
    path = _runtime_path()
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "entriesThisDay": 0,
            "entriesDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "gate": "OPEN",
            "gateExpiresAt": None,
            "consecutiveLosses": 0,
            "tradeLog": [],
            "tierStats": {
                "MEDIUM": {"trades": 0, "wins": 0, "losses": 0},
                "HIGH": {"trades": 0, "wins": 0, "losses": 0},
            },
        }


def save_runtime(runtime: dict) -> None:
    """Save runtime state."""
    atomic_write(_runtime_path(), runtime)


def record_trade(runtime: dict, asset: str, tier: str, score: int,
                 outcome: str, roe: float) -> dict:
    """Record a completed trade in runtime state."""
    runtime["tradeLog"].append({
        "asset": asset,
        "tier": tier,
        "score": score,
        "outcome": outcome,
        "roe": roe,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Update tier stats
    if tier not in runtime["tierStats"]:
        runtime["tierStats"][tier] = {"trades": 0, "wins": 0, "losses": 0}
    runtime["tierStats"][tier]["trades"] += 1
    if roe > 0:
        runtime["tierStats"][tier]["wins"] += 1
        runtime["consecutiveLosses"] = 0
    else:
        runtime["tierStats"][tier]["losses"] += 1
        runtime["consecutiveLosses"] += 1

    return runtime


def check_gate(runtime: dict, config: dict) -> tuple:
    """
    Check if trading gate is open.
    Returns (is_open: bool, reason: str).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Reset daily counter if new day
    if runtime.get("entriesDate") != today:
        runtime["entriesThisDay"] = 0
        runtime["entriesDate"] = today
        if runtime["gate"] == "CLOSED":
            runtime["gate"] = "OPEN"

    # Check daily limit
    gate_cfg = config.get("gate", {})
    max_entries = gate_cfg.get("maxEntriesPerDay", 6)
    if runtime["entriesThisDay"] >= max_entries:
        runtime["gate"] = "CLOSED"
        return False, f"Daily limit reached ({runtime['entriesThisDay']}/{max_entries})"

    # Check cooldown expiry
    if runtime["gate"] == "COOLDOWN":
        expires = runtime.get("gateExpiresAt")
        if expires and time.time() < expires:
            return False, f"Cooldown active until {datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()}"
        runtime["gate"] = "OPEN"
        runtime["gateExpiresAt"] = None

    # Check consecutive losses
    max_losses = gate_cfg.get("maxConsecutiveLosses", 3)
    if runtime["consecutiveLosses"] >= max_losses:
        cooldown_min = gate_cfg.get("cooldownMinutes", 30)
        runtime["gate"] = "COOLDOWN"
        runtime["gateExpiresAt"] = time.time() + (cooldown_min * 60)
        return False, f"{runtime['consecutiveLosses']} consecutive losses → cooldown {cooldown_min}min"

    return True, "OPEN"


def is_tier_enabled(tier: str, runtime: dict, config: dict) -> bool:
    """Check if a conviction tier is enabled (not disabled by learning or config)."""
    # Config override
    tier_cfg = config.get("convictionTiers", {}).get(tier, {})
    if tier_cfg.get("enabled") is False:
        return False

    # Self-learning disablement
    learning_cfg = config.get("learning", {})
    if not learning_cfg.get("enabled", True):
        return True

    stats = runtime.get("tierStats", {}).get(tier, {})
    trades = stats.get("trades", 0)
    min_trades = learning_cfg.get("minTradesForDisable", 8)
    min_wr = learning_cfg.get("minWinRateForEnable", 0.15)

    if trades >= min_trades:
        wins = stats.get("wins", 0)
        wr = wins / trades if trades > 0 else 0
        if wr < min_wr:
            return False

    return True


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------

def _cooldown_path() -> str:
    return os.path.join(STATE_DIR, "asset-cooldowns.json")


def load_cooldowns() -> dict:
    """Load per-asset cooldown timestamps."""
    try:
        with open(_cooldown_path(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cooldowns(cooldowns: dict) -> None:
    atomic_write(_cooldown_path(), cooldowns)


def set_cooldown(asset: str, minutes: int = 120) -> None:
    """Set cooldown for an asset after a Phase 1 exit."""
    cooldowns = load_cooldowns()
    cooldowns[asset] = {
        "until": time.time() + (minutes * 60),
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    save_cooldowns(cooldowns)


def is_on_cooldown(asset: str) -> bool:
    """Check if an asset is still on cooldown."""
    cooldowns = load_cooldowns()
    entry = cooldowns.get(asset)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


# ---------------------------------------------------------------------------
# OI History (for OIS source)
# ---------------------------------------------------------------------------

def load_oi_history(asset: str) -> list:
    """Load OI snapshot history for an asset."""
    path = os.path.join(OI_HISTORY_DIR, f"{asset}.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append_oi_snapshot(asset: str, oi_value: float) -> None:
    """Append an OI snapshot. 7-day rolling window."""
    history = load_oi_history(asset)
    now = time.time()
    history.append({"oi": oi_value, "ts": now})

    # Prune older than 7 days
    cutoff = now - (7 * 24 * 3600)
    history = [h for h in history if h["ts"] > cutoff]

    path = os.path.join(OI_HISTORY_DIR, f"{asset}.json")
    atomic_write(path, history)


def get_oi_at(history: list, hours_ago: float) -> float | None:
    """Get OI value closest to N hours ago from snapshot history."""
    if not history:
        return None
    target_ts = time.time() - (hours_ago * 3600)
    closest = min(history, key=lambda h: abs(h["ts"] - target_ts))
    # Only return if within 30 min of target
    if abs(closest["ts"] - target_ts) > 1800:
        return None
    return closest["oi"]


# ---------------------------------------------------------------------------
# MCP Helper
# ---------------------------------------------------------------------------

def mcporter_call(tool_name: str, params: dict = None, retries: int = 2) -> dict:
    """Call a Senpi MCP tool with retry logic."""
    params = params or {}
    last_err = None
    for attempt in range(retries + 1):
        try:
            result = _do_mcp_call(tool_name, params)
            if result is not None:
                return result
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 * (attempt + 1))
    print(f"[HYDRA] MCP call {tool_name} failed after {retries + 1} attempts: {last_err}",
          file=sys.stderr)
    return {}


def _do_mcp_call(tool_name: str, params: dict) -> dict:
    """Execute MCP tool call. Replaced at runtime by agent framework."""
    api_base = os.environ.get("SENPI_API_BASE", "")
    if api_base:
        try:
            import requests
            resp = requests.post(f"{api_base}/mcp/{tool_name}", json=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"REST fallback failed: {e}")
    return {}


# ---------------------------------------------------------------------------
# Position Helper
# ---------------------------------------------------------------------------

def get_positions(wallet: str = None) -> list:
    """Get current open positions from clearinghouse state."""
    if not wallet:
        wallet, _ = get_wallet_and_strategy()
    result = mcporter_call("strategy_get_clearinghouse_state", {"wallet": wallet})

    if isinstance(result, dict):
        perp_state = result.get("assetPositions", result.get("positions", []))
    elif isinstance(result, list):
        perp_state = result
    else:
        perp_state = []

    positions = []
    for item in perp_state:
        if isinstance(item, dict):
            pos = item.get("position", item)
            szi = float(pos.get("szi", pos.get("size", 0)))
            if szi != 0:
                positions.append(pos)
    return positions


def get_wallet_balance(wallet: str = None) -> float:
    """Get current wallet balance from clearinghouse state."""
    if not wallet:
        wallet, _ = get_wallet_and_strategy()
    result = mcporter_call("strategy_get_clearinghouse_state", {"wallet": wallet})
    if isinstance(result, dict):
        # Try various balance field names
        for key in ("accountValue", "equity", "balance", "totalAccountValue"):
            val = result.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        # Check nested
        margin_summary = result.get("marginSummary", result.get("crossMarginSummary", {}))
        if isinstance(margin_summary, dict):
            val = margin_summary.get("accountValue", margin_summary.get("equity"))
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return 0.0


def get_deployed_margin(positions: list) -> float:
    """Calculate total margin currently deployed."""
    total = 0.0
    for pos in positions:
        margin = 0.0
        for key in ("marginUsed", "margin", "positionValue"):
            val = pos.get(key)
            if val is not None:
                try:
                    margin = abs(float(val))
                    break
                except (TypeError, ValueError):
                    continue
        if margin == 0:
            szi = abs(float(pos.get("szi", pos.get("size", 0))))
            entry = float(pos.get("entryPx", pos.get("avgEntryPrice", 0)))
            leverage = float(pos.get("leverage", {}).get("value", 1) if isinstance(pos.get("leverage"), dict) else pos.get("leverage", 1))
            if entry > 0 and leverage > 0:
                margin = (szi * entry) / leverage
        total += margin
    return total


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def output(data: dict) -> None:
    """Print JSON to stdout for agent consumption."""
    print(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    """Print to stderr so stdout stays clean for JSON output."""
    print(f"[HYDRA] {msg}", file=sys.stderr)
