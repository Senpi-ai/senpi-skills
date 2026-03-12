#!/usr/bin/env python3
"""Shared library for HAWK Multi-Asset Momentum Sniper strategy.

Provides: atomic_write, mcporter_call (array syntax), state I/O, trade counter, MCP helpers.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
BASE_DIR = Path(WORKSPACE) / "recipes" / "hype-sniper"
CONFIG_PATH = BASE_DIR / "hype-config.json"
STATE_DIR = BASE_DIR / "state"
HISTORY_DIR = BASE_DIR / "history"

STATE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically via tmp file + rename. Prevents corruption from concurrent cron writes."""
    path = str(path)
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


# ─── Config ──────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ─── State I/O ───────────────────────────────────────────────

def load_state(filename="positions.json"):
    path = STATE_DIR / filename
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(data, filename="positions.json"):
    path = STATE_DIR / filename
    atomic_write(str(path), data)


def load_prices_history():
    return load_state("price_history.json")


def save_prices_history(data):
    save_state(data, "price_history.json")


# ─── Trade Counter ───────────────────────────────────────────

COUNTER_FILE = STATE_DIR / "trade-counter.json"

def load_trade_counter():
    """Load or initialize trade counter with day rollover."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    default = {
        "date": today,
        "accountValueStart": 0,
        "entries": 0,
        "closedTrades": 0,
        "realizedPnl": 0,
        "gate": "OPEN",
        "gateReason": None,
        "cooldownUntil": None,
        "lastResults": [],
    }
    if COUNTER_FILE.exists():
        try:
            with open(COUNTER_FILE) as f:
                counter = json.load(f)
        except (json.JSONDecodeError, IOError):
            counter = dict(default)
        if counter.get("date") != today:
            counter["date"] = today
            counter["entries"] = 0
            counter["closedTrades"] = 0
            counter["realizedPnl"] = 0
            counter["gate"] = "OPEN"
            counter["gateReason"] = None
            counter["cooldownUntil"] = None
        for k, v in default.items():
            if k not in counter:
                counter[k] = v
        return counter
    return dict(default)


def save_trade_counter(counter):
    counter["updatedAt"] = datetime.now(timezone.utc).isoformat()
    atomic_write(str(COUNTER_FILE), counter)


def increment_entry(counter):
    """Increment entry count. Call after successful position open."""
    counter["entries"] = counter.get("entries", 0) + 1
    save_trade_counter(counter)


def record_trade_result(counter, pnl):
    """Record a trade result. Call after position close."""
    result = "W" if pnl >= 0 else "L"
    counter["lastResults"].append(result)
    counter["lastResults"] = counter["lastResults"][-20:]
    counter["closedTrades"] = counter.get("closedTrades", 0) + 1
    counter["realizedPnl"] = counter.get("realizedPnl", 0) + pnl
    save_trade_counter(counter)


# ─── Peak Balance ────────────────────────────────────────────

PEAK_FILE = STATE_DIR / "peak-balance.json"

def load_peak():
    if PEAK_FILE.exists():
        try:
            with open(PEAK_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"peak": 0, "updatedAt": None}


def save_peak(peak_data):
    atomic_write(str(PEAK_FILE), peak_data)


# ─── MCP Helpers ─────────────────────────────────────────────

def mcporter_call(tool_name, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via mcporter. Uses array syntax (no shell=True)."""
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool_name, "--args", args]

    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                print(f"mcporter error: {result.stderr.strip()}", file=sys.stderr)
                return None
            text = result.stdout.strip()
            if not text:
                return None
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("{") or line.startswith("["):
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            pass
                return {"raw": text}
            # Unwrap mcporter content envelope
            if isinstance(raw, dict) and "content" in raw:
                content = raw["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and "text" in first:
                        try:
                            return json.loads(first["text"])
                        except (json.JSONDecodeError, TypeError):
                            pass
            return raw
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print(f"mcporter timeout after {retries} attempts", file=sys.stderr)
            return None
        except Exception as e:
            print(f"mcporter exception: {e}", file=sys.stderr)
            return None
    return None


def get_market_data(asset="HYPE", candle_intervals=None):
    """Fetch market data including price, funding, OI, and optional candles."""
    kwargs = {"asset": asset, "include_funding": True}
    if candle_intervals:
        kwargs["candle_intervals"] = candle_intervals
    return mcporter_call("market_get_asset_data", **kwargs)


def get_clearinghouse_state(wallet):
    """Get strategy clearinghouse state."""
    if not wallet:
        return None
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def create_position(wallet, orders, reason=""):
    """Create a new position."""
    return mcporter_call("create_position",
                         strategyWalletAddress=wallet,
                         orders=orders,
                         reason=reason)


def close_position(wallet, coin, order_type="MARKET"):
    """Close a position."""
    return mcporter_call("close_position",
                         strategyWalletAddress=wallet,
                         coin=coin,
                         orderType=order_type)


def edit_position(wallet, coin, **kwargs):
    """Edit position (leverage, SL, TP, margin)."""
    params = {"strategyWalletAddress": wallet, "coin": coin}
    params.update(kwargs)
    return mcporter_call("edit_position", **params)


def output_json(data):
    """Print JSON to stdout."""
    print(json.dumps(data))


def error(msg):
    """Print error to stderr."""
    print(msg, file=sys.stderr)


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()
