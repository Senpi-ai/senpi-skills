"""DIRE Strategy — Shared config, MCP helpers, state I/O, Wolverine execution pattern.

DIRE is a BRENTOIL XYZ specialist. Ported from Kodiak v5.0 with XYZ-specific
adaptations:
  - coin must be xyz:BRENTOIL (prefix mandatory)
  - leverageType forced to ISOLATED (XYZ DEX requirement)
  - No funding_regime / funding_history calls (not applicable to XYZ)
  - Wolverine execution pattern: Python scanner invokes MCP via mcporter CLI
    subprocess directly, bypassing LLM parse loop
  - DSL attach is inline and mandatory; if attach fails, position is closed
    immediately to prevent unprotected exposure
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "dire-strategy"
CONFIG_PATH = SKILL_DIR / "config" / "dire-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants (hardcoded for safety, overridable via config) ──
ASSET = "xyz:BRENTOIL"
LEVERAGE_TYPE = "ISOLATED"  # XYZ DEX requirement
MAX_POSITIONS = 1


# ─── Atomic Write ────────────────────────────────────────────

def atomic_write(path, data):
    """Write JSON atomically via tmp file + os.replace."""
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

_CONFIG_CACHE = None


def load_config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            _CONFIG_CACHE = json.load(f)
    else:
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def get_wallet_and_strategy():
    wallet = os.environ.get("DIRE_WALLET", "")
    strategy_id = os.environ.get("DIRE_STRATEGY_ID", "")
    if not wallet or not strategy_id:
        config = load_config()
        wallet = wallet or config.get("wallet", "")
        strategy_id = strategy_id or config.get("strategyId", "")
    return wallet, strategy_id


# ─── State I/O ───────────────────────────────────────────────

def load_state(filename="state.json"):
    path = STATE_DIR / filename
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(data, filename="state.json"):
    atomic_write(str(STATE_DIR / filename), data)


# ─── Trade Counter + Drawdown Gate ───────────────────────────

def load_trade_counter():
    """Load daily trade counter with drawdown gate state."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = STATE_DIR / "trade-counter.json"
    default = {
        "date": today,
        "entries": 0,
        "realizedPnl": 0,
        "gate": "OPEN",
        "gateReason": None,
        "cooldownUntil": None,
        "lastResults": [],
        "last_entry_ts": 0,
        "last_win_direction": None,
        "last_win_ts": 0,
        "peak_7d": 0,             # rolling 7-day equity peak
        "peak_7d_ts": 0,          # timestamp of the peak
        "dd_manual_unlock": False,  # operator-set manual override
    }
    if path.exists():
        try:
            with open(path) as f:
                tc = json.load(f)
            if tc.get("date") != today:
                # Rollover daily counters; preserve peak_7d and dd state
                tc["date"] = today
                tc["entries"] = 0
                tc["realizedPnl"] = 0
                tc["gate"] = "OPEN"
                tc["gateReason"] = None
                tc["cooldownUntil"] = None
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["updatedAt"] = now_iso()
    atomic_write(str(STATE_DIR / "trade-counter.json"), tc)


def increment_entry(tc):
    tc["entries"] = tc.get("entries", 0) + 1
    tc["last_entry_ts"] = now_ts()
    save_trade_counter(tc)


def record_trade_result(tc, pnl):
    tc["lastResults"].append("W" if pnl >= 0 else "L")
    tc["lastResults"] = tc["lastResults"][-20:]
    tc["realizedPnl"] = tc.get("realizedPnl", 0) + pnl
    save_trade_counter(tc)


def update_peak_7d(tc, current_equity):
    """Update 7-day rolling peak. Decay the peak if it's older than 7 days."""
    now = now_ts()
    seven_days = 7 * 24 * 3600
    peak = float(tc.get("peak_7d", 0))
    peak_ts = float(tc.get("peak_7d_ts", 0))
    peak_age = now - peak_ts
    # Expire peak if it's older than 7 days — reset to current equity
    if peak_age > seven_days:
        tc["peak_7d"] = current_equity
        tc["peak_7d_ts"] = now
    elif current_equity > peak:
        tc["peak_7d"] = current_equity
        tc["peak_7d_ts"] = now
    save_trade_counter(tc)


def check_drawdown_gate(tc, current_equity, config):
    """Returns (gate_open: bool, reason: str).
    gate_open True means HUNTING is allowed.
    """
    peak = float(tc.get("peak_7d", 0))
    if peak <= 0:
        # First scan ever; initialize
        return True, "INIT"
    drawdown_pct = config.get("drawdownGatePct", 0.15)
    auto_resume_pct = config.get("drawdownAutoResumePct", 0.90)
    ratio = current_equity / peak

    if tc.get("dd_manual_unlock"):
        return True, "MANUAL_UNLOCK"

    # If currently gated, check for auto-resume
    if tc.get("gate") == "CLOSED_DRAWDOWN":
        if ratio >= auto_resume_pct:
            tc["gate"] = "OPEN"
            tc["gateReason"] = None
            save_trade_counter(tc)
            return True, "AUTO_RESUMED"
        return False, f"DRAWDOWN_GATE_ACTIVE_{(1 - ratio) * 100:.1f}%_from_peak"

    # Check if we need to trigger gate
    if ratio < (1 - drawdown_pct):
        tc["gate"] = "CLOSED_DRAWDOWN"
        tc["gateReason"] = f"DRAWDOWN_{(1 - ratio) * 100:.1f}%_from_peak_{peak:.2f}"
        save_trade_counter(tc)
        return False, tc["gateReason"]

    return True, "OK"


# ─── Daily Entry Cap ─────────────────────────────────────────

def check_daily_cap(tc, config):
    """Returns (allowed: bool, reason: str)."""
    cap = int(config.get("dailyEntryCap", 2))
    entries = int(tc.get("entries", 0))
    if entries >= cap:
        return False, f"DAILY_CAP_{entries}/{cap}"
    return True, f"{entries}/{cap}"


# ─── MCP Helpers (Wolverine execution pattern) ───────────────

def mcporter_call(tool, retries=2, timeout=25, **params):
    """Call a Senpi MCP tool via mcporter CLI subprocess.

    This is the core primitive for the Wolverine execution pattern —
    Python invokes MCP directly, no LLM parse loop. Returns parsed JSON
    response or None on failure.
    """
    args = json.dumps(params) if params else "{}"
    cmd = ["mcporter", "call", "senpi", tool, "--args", args]
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            raw = json.loads(r.stdout)
            # mcporter may wrap response in content[0].text
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
            return None
        except (json.JSONDecodeError, Exception):
            return None
    return None


def get_clearinghouse(wallet):
    if not wallet:
        return None
    return mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)


def get_positions(wallet):
    """Returns (account_value, positions_list).

    Account value is summed across main + xyz sub-DEX views (single wallet,
    two views per Hyperliquid HIP-3 structure). Positions list filters for
    BRENTOIL only since Dire is single-asset.
    """
    ch = get_clearinghouse(wallet)
    if not ch:
        return 0.0, []
    data = ch.get("data", ch)
    positions = []
    account_value = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        try:
            account_value += float(ms.get("accountValue", 0))
        except (TypeError, ValueError):
            pass
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            try:
                szi = float(pos.get("szi", 0))
            except (TypeError, ValueError):
                continue
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            # Dire only cares about BRENTOIL positions (with or without xyz: prefix in response)
            if coin not in ("BRENTOIL", "xyz:BRENTOIL"):
                continue
            positions.append({
                "coin": coin,
                "direction": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0) or 0),
                "margin": float(pos.get("marginUsed", 0) or 0),
                "entryPrice": float(pos.get("entryPx", 0) or 0),
                "size": abs(szi),
                "leverage": int((pos.get("leverage", {}) or {}).get("value", 0) or 0),
                "liquidationPx": float(pos.get("liquidationPx", 0) or 0),
                "roe": float(pos.get("returnOnEquity", 0) or 0),
            })
    return account_value, positions


def get_asset_data(asset=ASSET, candle_intervals=None, include_funding=False, include_order_book=False):
    """Fetch market data for BRENTOIL. XYZ DEX asset — no funding info expected."""
    if candle_intervals is None:
        candle_intervals = ["5m", "15m", "1h", "4h"]
    return mcporter_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=candle_intervals,
        include_funding=include_funding,
        include_order_book=include_order_book,
        dex="xyz",
    )


def create_position(wallet, direction, leverage, margin_amount, limit_price=None):
    """Wolverine execution — direct mcporter call for entry order.

    Returns parsed JSON response (or None on failure). Caller validates
    mainOrder.status and filled size before proceeding to DSL attach.

    XYZ-specific: coin=xyz:BRENTOIL, leverageType=ISOLATED.
    """
    order = {
        "coin": ASSET,
        "direction": direction,
        "leverage": int(leverage),
        "marginAmount": round(float(margin_amount), 2),
        "leverageType": LEVERAGE_TYPE,
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "feeOptimizedLimitOptions": {
            "ensureExecutionAsTaker": True,
            "executionTimeoutSeconds": 10,
        },
    }
    # Note: FEE_OPTIMIZED_LIMIT does not accept limitPrice or slippagePercent
    return mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[order],
        reason="dire:entry",
    )


def attach_ratchet_stop(wallet, strategy_id, direction, entry_price, size, leverage, tiers):
    """Attach DSL ratchet stop to a newly-opened BRENTOIL position.

    This MUST succeed for the position to be considered safe. If it fails,
    caller should immediately close the position to prevent unprotected
    exposure.
    """
    return mcporter_call(
        "ratchet_stop_add",
        strategyId=strategy_id,
        asset="BRENTOIL",  # ratchet_stop_add does not use xyz: prefix for asset field
        strategy_wallet_address=wallet,
        direction=direction,
        entryPrice=round(float(entry_price), 4),
        size=round(float(size), 6),
        leverage=int(leverage),
        ratchetStopConfig={"tiered": {"tiers": tiers}},
    )


def close_position_market(wallet, slippage_pct=2.0):
    """Emergency close of BRENTOIL position via market order.

    Used when DSL attach fails after successful entry — we cannot leave
    a position unprotected.
    """
    return mcporter_call(
        "close_position",
        strategyWalletAddress=wallet,
        coin=ASSET,
        orderType="MARKET",
        slippagePercent=slippage_pct,
        reason="dire:emergency_close_dsl_attach_failed",
    )


# ─── Output + Utilities ──────────────────────────────────────

def output(data):
    """Emit scanner result as single-line JSON to stdout."""
    print(json.dumps(data))
    sys.stdout.flush()


def no_reply(note=""):
    """Emit NO_REPLY heartbeat — used when position exists (scanner does not exit)
    or when scanner is in a non-actionable state."""
    output({
        "status": "ok",
        "heartbeat": "NO_REPLY",
        "note": note,
    })


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─── Validation Helpers ──────────────────────────────────────

def validate_entry_response(response):
    """Validate a create_position response.

    Returns (success: bool, filled_size: float, fill_price: float, order_id: str, reason: str).
    """
    if not response:
        return False, 0.0, 0.0, "", "no_response"
    # mcporter returns { "success": True, "data": {...} } shape
    if not response.get("success"):
        err = response.get("error") or response.get("data", {}).get("error") or "unknown"
        return False, 0.0, 0.0, "", f"outer_fail:{err}"
    data = response.get("data", {})
    # data.results is typically a list of per-order results
    results = data.get("results", []) or []
    if not results:
        # Some response shapes put result fields directly on data
        main_order = data.get("mainOrder") or {}
    else:
        main_order = results[0].get("mainOrder") or results[0] or {}

    status = main_order.get("status", "")
    try:
        filled_size = float(main_order.get("filledSize", main_order.get("size", 0)) or 0)
        fill_price = float(main_order.get("avgFillPrice", main_order.get("price", 0)) or 0)
    except (TypeError, ValueError):
        filled_size, fill_price = 0.0, 0.0
    order_id = str(main_order.get("orderId", ""))

    if status in ("filled", "FILLED", "resting", "RESTING"):
        if filled_size > 0:
            return True, filled_size, fill_price, order_id, status
        return False, 0.0, 0.0, order_id, f"no_fill:{status}"
    return False, 0.0, fill_price, order_id, f"bad_status:{status}"


def validate_dsl_response(response):
    """Validate a ratchet_stop_add response. Returns (success: bool, reason: str)."""
    if not response:
        return False, "no_response"
    if not response.get("success"):
        return False, response.get("error", "unknown_fail")
    return True, "attached"
