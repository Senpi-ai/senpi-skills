#!/usr/bin/env python3
"""shark_config.py — Shared infrastructure for SHARK strategy scripts.

Pattern follows fox_config.py / wolf_config.py conventions:
- WORKSPACE from env or default
- atomic_write() for all state mutations
- mcporter_call() with retry + timeout (senpi.{tool} dot syntax)
- load_strategy() / load_all_strategies() from strategy registry
- state_dir() / dsl_state_path() helpers
- heartbeat() / output() for structured cron output
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = os.environ.get("SHARK_WORKSPACE", os.environ.get("WORKSPACE", "/data/workspace"))
STRATEGY_REGISTRY = os.path.join(WORKSPACE, "strategies", "shark-strategies.json")
DSL_BASE_DIR = os.path.join(WORKSPACE, "dsl")
DEFAULT_STATE_BASE = os.path.join(WORKSPACE, "state")

# OI history limits
MAX_OI_ASSETS = 60          # Track top N assets by OI value
MAX_SNAPSHOTS_PER_ASSET = 288  # ~24h at 5min intervals


def state_dir(strategy_key: str) -> str:
    """Return state directory for a strategy: {DEFAULT_STATE_BASE}/{strategy_key}/"""
    d = os.path.join(DEFAULT_STATE_BASE, strategy_key)
    os.makedirs(d, exist_ok=True)
    return d


def dsl_state_path(strategy_id: str) -> str:
    """Return DSL state directory for a strategy: {DSL_BASE_DIR}/{strategy_id}/"""
    d = os.path.join(DSL_BASE_DIR, strategy_id)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Atomic file writes
# ---------------------------------------------------------------------------

def atomic_write(path: str, data: dict | list, indent: int = 2) -> None:
    """Write JSON atomically via tmp + rename."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: str, default=None):
    """Load JSON file, return default if missing/corrupt."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

def load_all_strategies() -> list[dict]:
    """Load all SHARK strategies from registry."""
    data = load_json(STRATEGY_REGISTRY, [])
    if isinstance(data, dict):
        data = data.get("strategies", [])
    return [s for s in data if isinstance(s, dict) and s.get("enabled", True)]


def load_strategy(strategy_key: str) -> dict | None:
    """Load a specific strategy by key (strategyId)."""
    for s in load_all_strategies():
        if s.get("strategyId") == strategy_key:
            return s
    return None


def save_strategies(strategies: list[dict]) -> None:
    """Save strategies to registry."""
    atomic_write(STRATEGY_REGISTRY, strategies)


# ---------------------------------------------------------------------------
# mcporter MCP calls
# ---------------------------------------------------------------------------

MCPORTER_TIMEOUT = 25
MCPORTER_RETRIES = 2


def _unwrap_mcporter_response(stdout_str: str) -> dict | None:
    """Unwrap mcporter MCP response envelope."""
    try:
        raw = json.loads(stdout_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, list) and len(content) > 0:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
    return raw


def mcporter_call(tool: str, args: dict | None = None, timeout: int = MCPORTER_TIMEOUT,
                   retries: int = MCPORTER_RETRIES) -> tuple[dict | None, str | None]:
    """Call senpi.{tool} via mcporter with retry. Returns (data, error)."""
    cmd = ["mcporter", "call", "senpi", tool]
    if args:
        cmd += ["--args", json.dumps(args)]

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                last_err = (r.stderr or r.stdout or "non-zero exit").strip()
                if attempt < retries:
                    time.sleep(1 + attempt)
                continue
            raw = _unwrap_mcporter_response(r.stdout)
            if raw is None:
                last_err = "empty/invalid response"
                if attempt < retries:
                    time.sleep(1 + attempt)
                continue
            # Check for API error
            if raw.get("success") is False:
                err = raw.get("error", {})
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return None, msg
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            return data, None
        except subprocess.TimeoutExpired:
            last_err = f"timeout ({timeout}s)"
            if attempt < retries:
                time.sleep(1 + attempt)
        except (FileNotFoundError, OSError) as e:
            return None, str(e)

    return None, last_err


# ---------------------------------------------------------------------------
# MCP convenience wrappers
# ---------------------------------------------------------------------------

def fetch_instruments(dex: str = "") -> tuple[list[dict], str | None]:
    """Fetch all instruments from market_list_instruments. Returns (instruments[], error)."""
    data, err = mcporter_call("market_list_instruments", {"dex": dex})
    if err:
        return [], err
    instruments = data.get("instruments", []) if isinstance(data, dict) else []
    return instruments, None


def fetch_prices(assets: list[str] | None = None, dex: str = "") -> tuple[dict, str | None]:
    """Fetch prices. Returns ({asset: price_str}, error)."""
    args: dict = {}
    if assets:
        args["assets"] = assets
    if dex:
        args["dex"] = dex
    data, err = mcporter_call("market_get_prices", args)
    if err:
        return {}, err
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    return prices, None


def fetch_asset_data(asset: str, candle_intervals: list[str] | None = None,
                     include_order_book: bool = False, include_funding: bool = False,
                     dex: str = "") -> tuple[dict, str | None]:
    """Fetch detailed asset data. Returns (data_dict, error)."""
    args: dict = {"asset": asset}
    if candle_intervals is not None:
        args["candle_intervals"] = candle_intervals
    if include_order_book:
        args["include_order_book"] = True
    if include_funding:
        args["include_funding"] = True
    if dex:
        args["dex"] = dex
    data, err = mcporter_call("market_get_asset_data", args)
    return data or {}, err


def fetch_sm_markets(limit: int = 100) -> tuple[list[dict], str | None]:
    """Fetch smart money market concentration. Returns (markets[], error)."""
    data, err = mcporter_call("leaderboard_get_markets", {"limit": limit})
    if err:
        return [], err
    markets_data = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", [])
    return markets_data if isinstance(markets_data, list) else [], None


def fetch_clearinghouse(wallet: str) -> tuple[dict, str | None]:
    """Fetch clearinghouse state for strategy wallet. Returns (data, error)."""
    return mcporter_call("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})


def get_active_positions(wallet: str) -> tuple[dict[str, dict], str | None]:
    """Get active positions as {coin: position_dict}. Returns (positions, error)."""
    data, err = fetch_clearinghouse(wallet)
    if err:
        return {}, err
    positions = {}
    for section in ("main", "xyz"):
        if section not in data:
            continue
        for p in data.get(section, {}).get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin")
            szi = float(pos.get("szi", 0))
            if coin and szi != 0:
                positions[coin] = pos
    return positions, None


def check_sm_alignment(asset: str, direction: str) -> tuple[bool, float, str]:
    """Check if entry direction aligns with smart money consensus.

    Returns (aligned, sm_pct, sm_direction).
    aligned=True means SM is profiting in the same direction we want to enter,
    OR there's no SM data for this asset (neutral — allow entry with caution).
    """
    clean_asset = asset.replace("xyz:", "").upper()
    markets, err = fetch_sm_markets(50)
    if err or not markets:
        return True, 0.0, "UNKNOWN"  # Can't check — allow entry

    # Find this asset in SM data
    best_match = None
    for m in markets:
        token = (m.get("token") or "").upper()
        if token == clean_asset:
            if best_match is None or m.get("pct_of_top_traders_gain", 0) > best_match.get("pct_of_top_traders_gain", 0):
                best_match = m

    if not best_match:
        return True, 0.0, "NO_DATA"  # No SM data for this asset — neutral

    sm_dir = (best_match.get("direction") or "").upper()
    sm_pct = float(best_match.get("pct_of_top_traders_gain", 0))

    # SM direction must match our direction
    aligned = sm_dir == direction.upper()

    return aligned, sm_pct, sm_dir


def check_candle_confirmation(asset: str, direction: str) -> tuple[bool, str]:
    """Check 15-min candles for trend confirmation before entry.

    LONG: last 3 candles must have ascending lows (higher lows)
    SHORT: last 3 candles must have descending highs (lower highs)

    Returns (confirmed, reason).
    """
    dex = "xyz" if asset.startswith("xyz:") else ""
    data, err = fetch_asset_data(asset, candle_intervals=["15m"], dex=dex)
    if err:
        return True, f"candle fetch failed: {err}"  # Can't check — allow

    # Navigate to candle data
    candles_raw = data.get("candles", {})
    if isinstance(candles_raw, dict):
        candles_15m = candles_raw.get("15m", [])
    elif isinstance(candles_raw, list):
        candles_15m = candles_raw
    else:
        return True, "no candle data"

    if len(candles_15m) < 4:
        return True, f"only {len(candles_15m)} candles"

    # Last 4 candles (need 3 comparisons)
    recent = candles_15m[-4:]

    if direction.upper() == "LONG":
        # Higher lows: each candle's low > previous candle's low
        lows = []
        for c in recent:
            low = float(c.get("l", 0) or c.get("low", 0) or 0)
            if low <= 0:
                return True, "invalid candle data"
            lows.append(low)
        ascending = all(lows[i] < lows[i+1] for i in range(len(lows)-1))
        if not ascending:
            return False, f"15m lows not ascending: {[round(l, 6) for l in lows]}"
        return True, "15m higher lows confirmed"

    else:  # SHORT
        # Lower highs: each candle's high < previous candle's high
        highs = []
        for c in recent:
            high = float(c.get("h", 0) or c.get("high", 0) or 0)
            if high <= 0:
                return True, "invalid candle data"
            highs.append(high)
        descending = all(highs[i] > highs[i+1] for i in range(len(highs)-1))
        if not descending:
            return False, f"15m highs not descending: {[round(h, 6) for h in highs]}"
        return True, "15m lower highs confirmed"


def create_position(wallet: str, coin: str, direction: str, leverage: int,
                    margin: float, reason: str = "",
                    fee_optimized: bool = True,
                    tp_pct: float | None = None,
                    sl_pct: float | None = None) -> tuple[dict | None, str | None]:
    """Open a position. Uses ALO (fee-optimized) maker-only by default.

    fee_optimized=True  → FEE_OPTIMIZED_LIMIT, NO taker fallback (maker only, resting order)
    fee_optimized=False → MARKET (immediate fill, higher fees — use for emergency exits)

    tp_pct / sl_pct: take-profit and stop-loss as % of margin (ROE%).
    These are set natively on Hyperliquid — no DSL needed for initial risk management.
    """
    order: dict = {
        "coin": coin,
        "direction": direction.upper(),
        "leverage": leverage,
        "marginAmount": round(margin, 2),
    }
    if fee_optimized:
        order["orderType"] = "FEE_OPTIMIZED_LIMIT"
        # DO NOT set ensureExecutionAsTaker — we want pure maker, no taker fallback
        # This avoids the 3.5bps taker fee on HL exchange
    else:
        order["orderType"] = "MARKET"

    # Native TP/SL — Hyperliquid manages these, not our code
    if tp_pct is not None:
        order["takeProfitPercent"] = round(tp_pct, 2)
    if sl_pct is not None:
        order["stopLossPercent"] = round(sl_pct, 2)

    args = {
        "strategyWalletAddress": wallet,
        "orders": [order],
    }
    if reason:
        args["reason"] = reason
    # ALO resting order — give it time to fill as maker
    timeout = 90 if fee_optimized else 30
    return mcporter_call("create_position", args, timeout=timeout)


def verify_position_filled(wallet: str, coin: str, max_wait: int = 45) -> tuple[bool, str | None]:
    """Poll clearinghouse to verify a position was filled after ALO entry.
    Returns (filled, error). Checks every 5s up to max_wait seconds.
    """
    import time
    start = time.time()
    while time.time() - start < max_wait:
        positions, err = get_active_positions(wallet)
        if err:
            return False, err
        if coin in positions:
            return True, None
        time.sleep(5)
    return False, None


def cancel_open_orders(wallet: str, coin: str) -> tuple[dict | None, str | None]:
    """Cancel any resting orders for a coin. Returns (result, error)."""
    return mcporter_call("cancel_order", {
        "strategyWalletAddress": wallet,
        "coin": coin,
    }, timeout=15)


def close_position(wallet: str, coin: str, reason: str = "") -> tuple[dict | None, str | None]:
    """Close a position. Uses MARKET for immediate exit. Returns (result, error)."""
    args = {"strategyWalletAddress": wallet, "coin": coin}
    if reason:
        args["reason"] = reason
    return mcporter_call("close_position", args, timeout=30)


def fetch_closed_trade_pnl(wallet: str, coin: str) -> tuple[float | None, str | None]:
    """Fetch realized PnL including all fees for a recently closed position.
    Returns (net_pnl, error). net_pnl = realizedPnl - totalFees.
    """
    data, err = mcporter_call("discovery_get_trader_history", {
        "trader_address": wallet, "limit": 5, "latest": True
    })
    if err:
        return None, err
    closed = data.get("closed_positions", []) if isinstance(data, dict) else []
    for trade in closed:
        if trade.get("coin") == coin or trade.get("coinDisplayName") == coin:
            try:
                realized = float(trade.get("realizedPnl", 0))
                fees = float(trade.get("totalFees", 0))
                return round(realized - fees, 2), None  # fees are already positive
            except (TypeError, ValueError):
                pass
    return None, f"no closed trade found for {coin}"


def fetch_strategy(strategy_id: str) -> tuple[dict | None, str | None]:
    """Fetch strategy details from Senpi. Returns (strategy, error)."""
    data, err = mcporter_call("strategy_get", {"strategy_id": strategy_id})
    if err:
        return None, err
    strategy = data.get("strategy") if isinstance(data, dict) else None
    return strategy, None


# ---------------------------------------------------------------------------
# Leverage estimation from funding rate
# ---------------------------------------------------------------------------

def estimate_leverage_from_funding(funding_rate: float) -> float:
    """Estimate average leverage from hourly funding rate.

    Funding rate ≈ (leverage_factor - 1) * base_rate.
    Empirical mapping (from spec):
      0.0000125 (base/min) → ~5x
      0.0001 (0.01%/hr) → ~8-10x
      0.0005 (0.05%/hr) → ~15-20x
      0.001+ → 20x+ (capped at 25)

    Uses continuous log mapping between anchor points.
    """
    abs_rate = abs(funding_rate)
    if abs_rate <= 0.0000125:
        return 5.0
    if abs_rate <= 0.0001:
        # Linear interp 5 → 9
        t = (abs_rate - 0.0000125) / (0.0001 - 0.0000125)
        return 5.0 + t * 4.0
    if abs_rate <= 0.0005:
        # Linear interp 9 → 17.5
        t = (abs_rate - 0.0001) / (0.0005 - 0.0001)
        return 9.0 + t * 8.5
    if abs_rate <= 0.001:
        # Linear interp 17.5 → 25
        t = (abs_rate - 0.0005) / (0.001 - 0.0005)
        return 17.5 + t * 7.5
    return 25.0


# ---------------------------------------------------------------------------
# Correlation guard
# ---------------------------------------------------------------------------

BTC_CORRELATED = {
    "BTC", "ETH", "SOL", "AVAX", "DOGE", "SHIB", "PEPE", "WIF", "BONK",
    "ADA", "DOT", "LINK", "MATIC", "NEAR", "APT", "SUI", "SEI", "TIA",
    "INJ", "FET", "RNDR", "WLD", "ARB", "OP", "STRK", "JUP", "PYTH",
    "JTO", "MEME", "ORDI", "STX", "XRP", "LTC", "BCH", "ETC",
}


def is_btc_correlated(asset: str) -> bool:
    """Check if asset is BTC-correlated (most major crypto assets)."""
    clean = asset.replace("xyz:", "").upper()
    return clean in BTC_CORRELATED


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def heartbeat(script_name: str) -> None:
    """Print heartbeat JSON and exit 0."""
    print(json.dumps({"status": "heartbeat", "script": script_name,
                       "ts": datetime.now(timezone.utc).isoformat()}))
    sys.exit(0)


def output(data: dict) -> None:
    """Print one ndjson line."""
    data.setdefault("ts", datetime.now(timezone.utc).isoformat())
    print(json.dumps(data, default=str))


def output_error(script: str, msg: str, **extra) -> None:
    """Print error line."""
    output({"status": "error", "script": script, "error": msg, **extra})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> int:
    return int(time.time())
