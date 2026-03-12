#!/usr/bin/env python3
"""Multi-Asset Momentum Scanner v3 — Scans top liquid markets simultaneously.

Replaces single-asset scanner_v2. Same 6-filter logic but across BTC, ETH, SOL, HYPE
and any asset where top traders are concentrated.

Upgrades over v2:
- Multi-asset: scans 4+ markets per tick
- Smart money filter: uses leaderboard_get_markets to see where top traders are printing
- Best-signal selection: only enters the strongest signal across all assets
- Per-asset leverage limits respected
- Max 2 concurrent positions
- Trend-following bias: prefers direction aligned with top-trader consensus
"""

import sys
import os
import time
import json
import subprocess
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import hype_lib as lib

# ── Assets to scan ────────────────────────────────────────────────────────

DEFAULT_SCAN_ASSETS = [
    {"coin": "BTC",  "max_leverage": 20, "margin_pct": 20},
    {"coin": "ETH",  "max_leverage": 20, "margin_pct": 20},
    {"coin": "SOL",  "max_leverage": 20, "margin_pct": 20},
    {"coin": "HYPE", "max_leverage": 10, "margin_pct": 25},
]

# ── Config ────────────────────────────────────────────────────────────────

DEFAULTS = {
    "momentum_5m_threshold": 1.0,       # raised from 0.4 — only decisive moves
    "momentum_15m_agree": True,
    "volume_spike_ratio": 1.2,
    "chop_range_max_pct": 0.3,
    "cooldown_normal_min": 10,
    "cooldown_after_sl_min": 20,
    "funding_long_max": 0.01,
    "funding_short_min": -0.005,
    "strong_momentum_multiplier": 1.8,
    "same_direction_block_min": 15,
    "max_concurrent_positions": 2,
    "smart_money_weight": 1.5,          # boost score when aligned with top traders
}


def get_config(config):
    merged = dict(DEFAULTS)
    overrides = config.get("entry_v3", config.get("entry_v2", {}))
    for k in DEFAULTS:
        if k in overrides:
            merged[k] = overrides[k]
    return merged


# ── MCP helpers ───────────────────────────────────────────────────────────

def _unwrap_mcporter(stdout_str):
    try:
        raw = json.loads(stdout_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
    return raw


def mcp_call(tool, args, timeout=15):
    try:
        r = subprocess.run(
            ["mcporter", "call", "senpi", tool, "--args", json.dumps(args)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None, r.stderr or r.stdout
        raw = _unwrap_mcporter(r.stdout)
        if not raw:
            return None, "empty response"
        if raw.get("success") is False:
            return None, str(raw.get("error", ""))
        return raw.get("data", raw), None
    except Exception as e:
        return None, str(e)


# ── Market data ───────────────────────────────────────────────────────────

def get_market_snapshot(coin):
    """Fetch 5m + 15m + 1h candles, price, funding, OI for one asset."""
    data, err = mcp_call("market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["5m", "15m", "1h"],
        "include_funding": True
    })
    if err or not data:
        return None

    ctx = data.get("asset_context", {})
    candles = data.get("candles", {})

    price = None
    for key in ("markPx", "midPx", "oraclePx"):
        if key in ctx:
            try:
                price = float(ctx[key])
                break
            except (TypeError, ValueError):
                pass

    if price is None:
        return None

    funding = None
    if ctx.get("funding"):
        try:
            funding = float(ctx["funding"])
        except (TypeError, ValueError):
            pass

    oi = None
    if ctx.get("openInterest"):
        try:
            oi = float(ctx["openInterest"])
        except (TypeError, ValueError):
            pass

    return {
        "coin": coin,
        "price": price,
        "funding": funding,
        "oi": oi,
        "candles_5m": candles.get("5m", []),
        "candles_15m": candles.get("15m", []),
        "candles_1h": candles.get("1h", []),
    }


def get_smart_money_direction():
    """Get top trader consensus from leaderboard_get_markets."""
    data, err = mcp_call("leaderboard_get_markets", {"limit": 200})
    if err or not data:
        return {}

    markets = data.get("markets", {}).get("markets", data.get("markets", []))
    if not isinstance(markets, list):
        return {}

    consensus = {}
    for m in markets:
        token = m.get("token", "")
        dex = m.get("dex", "")
        if dex == "xyz":
            continue  # skip xyz for now
        direction = m.get("direction", "")
        pct = m.get("pct_of_top_traders_gain", 0)
        price_chg = m.get("token_price_change_pct_4h", 0)
        traders = m.get("trader_count", 0)
        consensus[token] = {
            "direction": direction.upper(),
            "pct_of_gains": pct,
            "price_4h": price_chg,
            "traders": traders,
        }
    return consensus


# ── Signal computation ────────────────────────────────────────────────────

def compute_candle_momentum(candles, n_bars):
    if not candles or len(candles) < n_bars + 2:
        return None
    completed = candles[:-1]
    if len(completed) < n_bars + 1:
        return None
    old_close = float(completed[-(n_bars + 1)]["c"])
    new_close = float(completed[-1]["c"])
    if old_close == 0:
        return None
    return ((new_close - old_close) / old_close) * 100


def compute_candle_range_pct(candles, n_bars):
    if not candles or len(candles) < n_bars:
        return None
    recent = candles[-n_bars:]
    highs = [float(c.get("h", 0)) for c in recent]
    lows = [float(c.get("l", 0)) for c in recent]
    hi, lo = max(highs), min(lows)
    mid = (hi + lo) / 2
    if mid == 0:
        return None
    return ((hi - lo) / mid) * 100


def compute_volume_ratio(candles):
    if not candles or len(candles) < 3:
        return None
    latest = candles[-1]
    latest_vol = float(latest.get("v", 0))
    prior = candles[:-1]
    if not prior:
        return None
    avg_vol = sum(float(c.get("v", 0)) for c in prior) / len(prior)
    if avg_vol == 0:
        return None
    return latest_vol / avg_vol


def get_hourly_trend(candles_1h):
    if len(candles_1h) < 4:
        return "UNKNOWN", 0, 0
    completed = candles_1h[:-1]
    red = 0
    green = 0
    for c in reversed(completed):
        o, cl = float(c["o"]), float(c["c"])
        if cl < o:
            if green == 0:
                red += 1
            else:
                break
        elif cl > o:
            if red == 0:
                green += 1
            else:
                break
        else:
            break
    if red >= 2:
        return "BEARISH", red, 0
    elif green >= 2:
        return "BULLISH", 0, green
    return "MIXED", red, green


def score_signal(coin, snapshot, cfg, smart_money, active_coins):
    """Score a potential trade signal. Returns (score, direction, reasons) or None."""
    c5 = snapshot["candles_5m"]
    c15 = snapshot["candles_15m"]
    c1h = snapshot["candles_1h"]
    price = snapshot["price"]
    funding = snapshot["funding"]

    if len(c5) < 8 or len(c15) < 4:
        return None

    mom_5m = compute_candle_momentum(c5, 1)
    mom_15m = compute_candle_momentum(c15, 1)
    range_30m = compute_candle_range_pct(c5, 6)
    vol_ratio = compute_volume_ratio(c5)
    hourly_trend, reds, greens = get_hourly_trend(c1h)

    if mom_5m is None:
        return None

    threshold = cfg["momentum_5m_threshold"]
    reasons = []

    # Basic momentum check
    if abs(mom_5m) < threshold:
        return None

    direction = "LONG" if mom_5m > 0 else "SHORT"
    reasons.append(f"5m {mom_5m:+.3f}%")

    # Chop filter
    if range_30m is not None and range_30m < cfg["chop_range_max_pct"]:
        return None

    # Volume filter — hard block if below threshold
    if vol_ratio is not None:
        if vol_ratio < cfg["volume_spike_ratio"]:
            return None  # no volume = no conviction
        elif vol_ratio > 2.0:
            reasons.append(f"vol spike {vol_ratio:.2f}x")
        else:
            reasons.append(f"vol {vol_ratio:.2f}x")

    # 15m agreement — hard requirement, no override
    if cfg.get("require_15m_agreement", True) and mom_15m is not None:
        agrees = (mom_5m > 0 and mom_15m > 0) or (mom_5m < 0 and mom_15m < 0)
        if not agrees:
            return None  # 15m disagrees = fakeout wick
        reasons.append(f"15m confirms {mom_15m:+.3f}%")

    # 1h trend filter — hard block on counter-trend AND mixed, no override
    if cfg.get("require_1h_trend_alignment", True):
        if hourly_trend == "BEARISH" and direction == "LONG":
            return None
        if hourly_trend == "BULLISH" and direction == "SHORT":
            return None
        if hourly_trend == "MIXED" or hourly_trend == "UNKNOWN":
            return None  # no clear trend = no entry
    if hourly_trend == "BEARISH" and direction == "SHORT":
        reasons.append("1h trend aligned ↓")
    elif hourly_trend == "BULLISH" and direction == "LONG":
        reasons.append("1h trend aligned ↑")

    # Funding filter
    if funding is not None:
        if direction == "LONG" and funding > cfg["funding_long_max"]:
            return None
        elif direction == "SHORT" and funding < cfg["funding_short_min"]:
            return None

    # Already in this coin?
    if coin in active_coins:
        return None

    # ── Compute score ─────────────────────────────────────────────
    # Base score = absolute momentum magnitude
    score = abs(mom_5m)

    # 15m momentum boost
    if mom_15m is not None and ((mom_5m > 0 and mom_15m > 0) or (mom_5m < 0 and mom_15m < 0)):
        score += abs(mom_15m) * 0.5

    # Smart money — hard block if against, boost if aligned
    sm = smart_money.get(coin, {})
    if sm:
        sm_dir = sm.get("direction", "")
        if cfg.get("smart_money_hard_block", True) and sm_dir and sm_dir != direction:
            return None  # never trade against the whales
        if sm_dir == direction:
            score *= cfg["smart_money_weight"]
            reasons.append(f"smart money aligned ({sm.get('pct_of_gains', 0):.1f}% of gains)")

    # Hourly trend alignment boost
    if (hourly_trend == "BEARISH" and direction == "SHORT") or \
       (hourly_trend == "BULLISH" and direction == "LONG"):
        score *= 1.3

    return {
        "coin": coin,
        "direction": direction,
        "score": round(score, 4),
        "reasons": reasons,
        "price": price,
        "mom_5m": mom_5m,
        "mom_15m": mom_15m,
        "vol_ratio": vol_ratio,
        "hourly_trend": hourly_trend,
        "range_30m": range_30m,
        "funding": funding,
    }


# ── Position management ───────────────────────────────────────────────────

def get_active_positions(wallet):
    data, err = mcp_call("strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if err or not data:
        return [], 0
    coins = []
    account_value = 0
    margin_used = 0
    for section in ("main", "xyz"):
        sd = data.get(section, {})
        ms = sd.get("marginSummary", {})
        account_value += float(ms.get("accountValue", 0))
        margin_used += float(ms.get("totalMarginUsed", 0))
        for p in sd.get("assetPositions", []):
            pos = p.get("position", p)
            szi = float(pos.get("szi", 0))
            if szi != 0:
                coins.append(pos.get("coin", ""))
    return coins, account_value


def create_dsl_state_file(config, coin, direction, entry_price, size, leverage):
    """Create DSL v5.2 state file for any asset."""
    strategy_id = config.get("strategy_id", "")
    wallet = config.get("strategy_wallet", "")

    if not strategy_id or not wallet:
        return False

    dsl_state_dir = os.environ.get("DSL_STATE_DIR", "/data/workspace/dsl")
    strategy_dir = os.path.join(dsl_state_dir, strategy_id)
    os.makedirs(strategy_dir, exist_ok=True)

    is_long = direction == "LONG"
    abs_floor = round(entry_price * (1 - 0.08 / leverage) if is_long else entry_price * (1 + 0.08 / leverage), 4)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state = {
        "active": True,
        "asset": coin,
        "direction": direction,
        "leverage": leverage,
        "entryPrice": entry_price,
        "size": size,
        "wallet": wallet,
        "strategyId": strategy_id,
        "phase": 1,
        "phase1": {
            "retraceThreshold": 0.04,
            "consecutiveBreachesRequired": 5,
            "absoluteFloor": abs_floor
        },
        "phase2TriggerTier": 1,
        "phase2": {
            "retraceThreshold": 0.02,
            "consecutiveBreachesRequired": 2
        },
        "tiers": [
            {"triggerPct": 8,   "lockPct": 3,  "retrace": 0.020},
            {"triggerPct": 15,  "lockPct": 10, "retrace": 0.018},
            {"triggerPct": 25,  "lockPct": 18, "retrace": 0.015},
            {"triggerPct": 40,  "lockPct": 30, "retrace": 0.012},
            {"triggerPct": 60,  "lockPct": 48, "retrace": 0.010},
            {"triggerPct": 80,  "lockPct": 65, "retrace": 0.008},
            {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
        ],
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "highWaterPrice": entry_price,
        "floorPrice": abs_floor,
        "currentBreachCount": 0,
        "createdAt": now_iso,
        "consecutiveFetchFailures": 0,
        "timeDecay": {
            "enabled": True,
            "rules": [
                {"afterMinutes": 5,  "minRoePct": 3.0, "tightenFloorRoePct": 6.0},
                {"afterMinutes": 20, "minRoePct": 3.0, "tightenFloorRoePct": 4.0},
                {"afterMinutes": 45, "minRoePct": 5.0, "tightenFloorRoePct": 0.0, "forceClose": True}
            ]
        },
        "partialTp": {
            "enabled": True,
            "triggerTier": 3,  # Tier 4 (40% ROE) — let winners run longer
            "closePct": 25,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "executed": False
        }
    }

    filename = (coin.replace(":", "--") if ":" in coin else coin) + ".json"
    state_path = os.path.join(strategy_dir, filename)
    lib.atomic_write(str(state_path), state)
    return True


def parse_fill(result):
    if not result or not isinstance(result, dict):
        return None, None
    data = result.get("data", result)
    results = data.get("results", data) if isinstance(data, dict) else {}
    main_order = results.get("mainOrder", {}) if isinstance(results, dict) else {}
    avg_price = None
    filled_size = None
    if isinstance(main_order, dict):
        if "avgPrice" in main_order:
            try: avg_price = float(main_order["avgPrice"])
            except: pass
        if "filledSize" in main_order:
            try: filled_size = float(main_order["filledSize"])
            except: pass
    return avg_price, filled_size


# ── Cooldown ──────────────────────────────────────────────────────────────

def check_cooldown(state, cfg, coin):
    """Per-asset cooldown check."""
    cooldowns = state.get("cooldowns", {})
    cd = cooldowns.get(coin, {})
    last_ts = cd.get("last_trade_ts", 0)
    was_loss = cd.get("was_loss", False)
    cooldown_min = cfg["cooldown_after_sl_min"] if was_loss else cfg["cooldown_normal_min"]
    remaining = cooldown_min * 60 - (time.time() - last_ts)
    if remaining > 0:
        return True, remaining
    return False, 0


def update_cooldown(state, coin, was_loss=False):
    cooldowns = state.get("cooldowns", {})
    cooldowns[coin] = {"last_trade_ts": time.time(), "was_loss": was_loss}
    state["cooldowns"] = cooldowns


# ── Main ──────────────────────────────────────────────────────────────────

def run():
    config = lib.load_config()
    wallet = config.get("strategy_wallet", "")
    cfg = get_config(config)

    if not wallet:
        lib.output_json({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # Gate check — risk guardian may have closed the gate
    counter = lib.load_trade_counter()
    gate = counter.get("gate", "OPEN")
    if gate != "OPEN":
        cooldown_until = counter.get("cooldownUntil")
        if gate == "COOLDOWN" and cooldown_until:
            try:
                from datetime import datetime, timezone
                cd = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= cd:
                    counter["gate"] = "OPEN"
                    counter["gateReason"] = None
                    counter["cooldownUntil"] = None
                    lib.save_trade_counter(counter)
                    gate = "OPEN"
            except (ValueError, TypeError):
                pass
        if gate != "OPEN":
            lib.output_json({"success": True, "heartbeat": "NO_REPLY",
                             "note": f"gate={gate}: {counter.get('gateReason', '')}"})
            return

    # Read scan assets from config (with fallback to defaults)
    SCAN_ASSETS = config.get("scan_assets", DEFAULT_SCAN_ASSETS)

    # Get current positions
    active_coins, account_value = get_active_positions(wallet)
    if len(active_coins) >= cfg["max_concurrent_positions"]:
        lib.output_json({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"max positions ({len(active_coins)}/{cfg['max_concurrent_positions']})",
            "active": active_coins
        })
        return

    # Check max entries per day
    risk_cfg = config.get("risk", {})
    max_entries = risk_cfg.get("max_entries_per_day", 10)
    if counter.get("entries", 0) >= max_entries:
        lib.output_json({"success": True, "heartbeat": "NO_REPLY",
                         "note": f"max entries ({counter['entries']}/{max_entries})"})
        return

    # Load scanner state
    state = lib.load_state("scanner_state.json")

    # Get smart money consensus
    smart_money = get_smart_money_direction()

    # Scan all assets
    signals = []
    diagnostics = []

    for asset_cfg in SCAN_ASSETS:
        coin = asset_cfg["coin"]

        # Per-asset cooldown
        in_cd, remaining = check_cooldown(state, cfg, coin)
        if in_cd:
            diagnostics.append({"coin": coin, "status": f"cooldown ({remaining:.0f}s)"})
            continue

        # Already in this coin?
        if coin in active_coins:
            diagnostics.append({"coin": coin, "status": "position open"})
            continue

        # Fetch market data
        snapshot = get_market_snapshot(coin)
        if not snapshot:
            diagnostics.append({"coin": coin, "status": "no data"})
            continue

        # Score the signal
        signal = score_signal(coin, snapshot, cfg, smart_money, active_coins)
        if signal:
            signal["leverage"] = asset_cfg["max_leverage"]
            signal["margin_pct"] = asset_cfg["margin_pct"]
            signals.append(signal)
            diagnostics.append({
                "coin": coin, "status": "SIGNAL",
                "direction": signal["direction"],
                "score": signal["score"],
                "mom_5m": signal["mom_5m"],
            })
        else:
            # Still report diagnostics
            mom = compute_candle_momentum(snapshot["candles_5m"], 1) if len(snapshot["candles_5m"]) >= 3 else None
            diagnostics.append({
                "coin": coin, "status": "no signal",
                "price": snapshot["price"],
                "mom_5m": round(mom, 4) if mom else None,
            })

    # No signals?
    if not signals:
        lib.output_json({
            "success": True, "heartbeat": "NO_REPLY",
            "note": "no signals across all assets",
            "scanned": len(SCAN_ASSETS),
            "diagnostics": diagnostics,
            "smart_money_top3": {k: v for k, v in sorted(smart_money.items(), key=lambda x: x[1].get("pct_of_gains", 0), reverse=True)[:3]},
        })
        return

    # Pick the best signal
    signals.sort(key=lambda s: s["score"], reverse=True)
    best = signals[0]

    # Find asset config
    asset_cfg = next(a for a in SCAN_ASSETS if a["coin"] == best["coin"])
    leverage = asset_cfg["max_leverage"]
    budget = account_value or 978  # fallback
    margin_amount = round(budget * asset_cfg["margin_pct"] / 100, 2)

    # Execute
    order = {
        "coin": best["coin"],
        "direction": best["direction"],
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "ensureExecutionAsTaker": True,
        "leverage": leverage,
        "leverageType": "ISOLATED",
        "marginAmount": margin_amount
    }

    result = lib.create_position(
        wallet=wallet,
        orders=[order],
        reason=f"Scanner v3 {best['direction']} {best['coin']}: score={best['score']}, {', '.join(best['reasons'])}"
    )

    entry_price, filled_size = parse_fill(result)

    # Create DSL state
    dsl_created = False
    if entry_price and filled_size:
        dsl_created = create_dsl_state_file(config, best["coin"], best["direction"], entry_price, filled_size, leverage)

    # Update cooldown
    update_cooldown(state, best["coin"])
    state["last_trade_ts"] = time.time()
    state["last_signal"] = best["direction"]
    state["last_coin"] = best["coin"]
    state["trades_today"] = state.get("trades_today", 0) + 1
    lib.save_state(state, "scanner_state.json")

    # Increment trade counter for risk guardian
    counter = lib.load_trade_counter()
    lib.increment_entry(counter)

    lib.output_json({
        "success": True,
        "signal": best["direction"],
        "coin": best["coin"],
        "score": best["score"],
        "entry_price": entry_price or best["price"],
        "filled_size": filled_size,
        "leverage": leverage,
        "margin": margin_amount,
        "reasons": best["reasons"],
        "dsl_created": dsl_created,
        "all_signals": [{"coin": s["coin"], "dir": s["direction"], "score": s["score"]} for s in signals],
        "diagnostics": diagnostics,
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        lib.error(f"Scanner v3 error: {e}")
        lib.output_json({"success": False, "error": str(e)})
