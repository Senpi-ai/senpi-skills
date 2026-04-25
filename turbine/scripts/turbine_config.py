"""TURBINE v2 — Shared MCP helpers + config loader + session state.

v2 producer is lean compared to v1:
  - Pick next asset from rotation list with XYZ-weighted round-robin
  - Query funding regime + spread
  - Choose direction (funding-fade if crowded, else alternate)
  - Emit signals for empty slots, one per tick
  - Push signals via `openclaw senpi external-scanner ingest`

The v2 runtime owns everything else: position tracking, DSL-managed
exits, risk guardrails, per-asset cooldowns, daily loss halts.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
SKILL_DIR = Path(WORKSPACE) / "skills" / "turbine-tracker"
CONFIG_PATH = SKILL_DIR / "config" / "turbine-config.json"
STATE_DIR = SKILL_DIR / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Config loader ────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_wallet_and_strategy():
    """Wallet + strategy id are required to emit signals. Env > config."""
    w = os.environ.get("TURBINE_WALLET") or os.environ.get("STRATEGY_ADDRESS", "")
    s = os.environ.get("TURBINE_STRATEGY_ID") or os.environ.get("STRATEGY_ID", "")
    if not w or not s:
        c = load_config()
        w = w or c.get("wallet", "")
        s = s or c.get("strategyId", "")
    return w, s


# ─── Session state ────────────────────────────────────────────

def atomic_write(path, data):
    path = str(path)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_session_state():
    """Rotation state. Persists across cron ticks, resets at UTC day boundary."""
    today = now_date()
    p = STATE_DIR / "session-state.json"
    default = {
        "date": today,
        "rotation_index": 0,
        "last_direction_by_asset": {},   # asset → "LONG" | "SHORT" for flat-funding alternation
        "cycles_opened_today": 0,
        "signals_emitted_today": 0,
    }
    if p.exists():
        try:
            with open(p) as f:
                ss = json.load(f)
            if ss.get("date") != today:
                return dict(default)
            for k, v in default.items():
                if k not in ss:
                    ss[k] = v
            return ss
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_session_state(ss):
    ss["updated_at"] = now_iso()
    atomic_write(str(STATE_DIR / "session-state.json"), ss)


# ─── MCP helper ───────────────────────────────────────────────

def mcporter_call(tool, retries=2, timeout=30, **params):
    """Call a Senpi MCP tool via mcporter. Returns parsed JSON or None on failure."""
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


def get_open_positions(wallet):
    """Return list of currently open positions across main + xyz DEXes.
    Each entry: {coin, direction, size, margin, entryPrice, upnl}.
    """
    if not wallet:
        return []
    # v2.0.3: tight timeout. Clearinghouse reads should be sub-second in
    # the happy case; 8s×2 retries bounds hang propagation to ~16s max.
    ch = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet, timeout=8, retries=2)
    if not ch or not isinstance(ch, dict):
        return []
    data = ch.get("data", ch)
    positions = []
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = safe_float(pos.get("szi"))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "size": abs(szi),
                "margin": safe_float(pos.get("marginUsed")),
                "entryPrice": safe_float(pos.get("entryPx")),
                "upnl": safe_float(pos.get("unrealizedPnl")),
                "dex": section,
            })
    return positions


def get_resting_orders(wallet):
    """Return list of resting open orders across main + xyz DEXes.

    v2.0.4 (2026-04-25): added to fix the ghost-trade bug. With
    ensure_execution_as_taker: false on entry, ALO orders rest on
    the book for up to 120s before filling. During that window they
    don't appear in get_open_positions(). Producer would see the
    slot as empty and emit an opposing-direction signal for the
    SAME asset on the next tick (alternation logic). When market
    crossed both ALOs, one would fill (open) and the other fill
    (close) in the same second → ghost trades with 0-second hold
    time. Five such trades observed on 2026-04-24, all on xyz:GOLD
    and xyz:TSLA where ALO rest times are longest.

    Including resting orders in held_assets prevents the producer
    from re-emitting on the same asset during the ALO window.

    Returns: [{coin, direction, dex, size, limit_price}, ...]
    """
    if not wallet:
        return []

    orders = []
    for dex in ("", "xyz"):
        resp = mcporter_call(
            "strategy_get_open_orders",
            strategy_wallet=wallet,
            dex=dex,
            timeout=8,
            retries=2,
        )
        if not resp or not isinstance(resp, dict):
            continue

        # Response shape — orders may be at .data.orders, .orders, or top-level array
        payload = resp.get("data", resp)
        if isinstance(payload, list):
            order_list = payload
        elif isinstance(payload, dict):
            order_list = payload.get("orders", payload.get("openOrders", []))
        else:
            order_list = []

        for o in order_list:
            if not isinstance(o, dict):
                continue
            coin = o.get("coin") or o.get("asset") or ""
            if not coin:
                continue
            # HL convention: positive size = buy/long, negative = sell/short
            sz = safe_float(o.get("size", o.get("sz")))
            side = o.get("side")  # may be 'B' (buy) / 'A' (ask/sell) on HL
            if side == "B" or sz > 0:
                direction = "LONG"
            elif side == "A" or sz < 0:
                direction = "SHORT"
            else:
                direction = "UNKNOWN"
            orders.append({
                "coin": coin,
                "direction": direction,
                "dex": dex if dex else "main",
                "size": abs(sz),
                "limit_price": safe_float(o.get("limitPx", o.get("price"))),
            })

    return orders


def normalize_coin_key(coin):
    """Strip 'xyz:' prefix and uppercase. Used to compare across rotation
    list, open positions, and resting orders consistently.
    'xyz:GOLD' → 'GOLD'. 'BTC' → 'BTC'. None → ''.

    v2.0.4: previously held_assets used p['coin'].upper() which preserved
    the xyz prefix ('XYZ:GOLD'), but rotation comparisons stripped via
    split(':')[-1] ('GOLD'). The mismatch meant even genuinely-open XYZ
    positions weren't blocking re-emission on that asset. Now both sides
    normalize through this helper.
    """
    if not coin:
        return ""
    return str(coin).split(":")[-1].upper()


# ─── Output / logging / time ──────────────────────────────────

def output(data):
    print(json.dumps(data, default=str))
    sys.stdout.flush()


def log(msg):
    print(f"[TURBINE-v2] {msg}", file=sys.stderr)


def safe_float(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
