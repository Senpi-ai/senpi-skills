#!/usr/bin/env python3
"""
dsl_common.py — Shared helpers for DSL (Dynamic Stop Loss) scripts.

Provides:
  - State I/O: get_state_path(), load_state(), save_state() (atomic)
  - MCP: fetch_mid_price(asset, dex=None) via Senpi market_get_prices
  - Close: close_position(wallet, coin, reason, ...) via Senpi close_position
  - Helpers: is_xyz_state(state)

Used by dsl-v4.py and any other single-position DSL scripts.
"""
import json
import os
import subprocess
import time


def get_state_path():
    """State file path from DSL_STATE_FILE env or default."""
    return os.environ.get("DSL_STATE_FILE", "/data/workspace/trailing-stop-state.json")


def load_state(path=None):
    """Load DSL state JSON. path defaults to get_state_path()."""
    path = path or get_state_path()
    with open(path) as f:
        return json.load(f)


def save_state(path, state):
    """Atomically write state JSON (indent=2). Creates parent dir if needed."""
    path = path or get_state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def fetch_mid_price(asset, dex=None):
    """
    Fetch mid price via Senpi MCP market_get_prices.
    Returns float or None. Supports XYZ via dex='xyz'.
    """
    try:
        args = {}
        if dex is not None and dex != "":
            args["dex"] = dex
        r = subprocess.run(
            ["mcporter", "call", "senpi", "market_get_prices", "--args", json.dumps(args)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(r.stdout)
        inner = data.get("data", data.get("result", data))
        prices = inner.get("prices") if isinstance(inner, dict) else data.get("prices")
        if not isinstance(prices, dict):
            return None
        # Try asset as-is, then xyz: prefix or strip for XYZ
        key_alt = f"xyz:{asset}" if not (isinstance(asset, str) and asset.startswith("xyz:")) else asset.replace("xyz:", "", 1)
        price_str = prices.get(asset) or prices.get(key_alt)
        return float(price_str) if price_str is not None else None
    except Exception:
        return None


def fetch_all_prices(dex=None):
    """Fetch all mid prices via Senpi MCP market_get_prices. Returns dict asset->float (or str->float)."""
    try:
        args = {}
        if dex is not None and dex != "":
            args["dex"] = dex
        r = subprocess.run(
            ["mcporter", "call", "senpi", "market_get_prices", "--args", json.dumps(args)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(r.stdout)
        inner = data.get("data", data.get("result", data))
        prices = inner.get("prices") if isinstance(inner, dict) else data.get("prices")
        if not isinstance(prices, dict):
            return {}
        return {k: float(v) if v is not None else None for k, v in prices.items() if v is not None}
    except Exception:
        return {}


def is_xyz_state(state):
    """True if this state is for an XYZ DEX position (dex=xyz or asset starts with xyz:)."""
    asset = state.get("asset", "")
    if state.get("dex") == "xyz":
        return True
    return isinstance(asset, str) and asset.startswith("xyz:")


def close_position(wallet, coin, reason, retries=2, retry_delay_sec=3, timeout=30):
    """
    Close position via Senpi MCP close_position. Retries on failure.
    Returns (closed: bool, result: str).
    """
    for attempt in range(retries):
        try:
            r = subprocess.run(
                [
                    "mcporter", "call", "senpi", "close_position", "--args",
                    json.dumps({
                        "strategyWalletAddress": wallet,
                        "coin": coin,
                        "reason": reason,
                    }),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result_text = r.stdout.strip()
            no_position = "CLOSE_NO_POSITION" in result_text
            if r.returncode == 0 and ("error" not in result_text.lower() or no_position):
                return True, result_text if not no_position else "position_already_closed"
            last_result = f"api_error_attempt_{attempt + 1}: {result_text}"
        except Exception as e:
            last_result = f"error_attempt_{attempt + 1}: {str(e)}"
        if attempt < retries - 1:
            time.sleep(retry_delay_sec)
    return False, last_result
