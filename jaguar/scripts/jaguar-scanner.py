#!/usr/bin/env python3
# Senpi JAGUAR Scanner v3.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""JAGUAR v3.1 — Striker-Only (Fleet Hardened).

v3.1 changes from fleet audit (2026-04-09):
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: 14+→20x, 12+→15x, 10+→10x, 9+→7x
- Margin increased to 50% (was 20%)
- has_resting_orders() with reduceOnly filter prevents position stacking
- Hyperfeed multi-window contribution velocity (15m, 1h) scoring
- No thesis exit (unchanged)
- XYZ equities banned

v2.0 changes:
- Stalker REMOVED, Hunter REMOVED, Pyramiding REMOVED
- Leverage reduced from 10x to conviction-scaled
- Exit management handled by plugin runtime (runtime.yaml)

The Striker logic detects FIRST_JUMP signals: assets rocketing from rank 25+
into the top 10 with 15+ rank jump, volume 1.5x, score 9+.
These are violent SM explosions — rare but high-conviction.

2 API calls: leaderboard_get_markets (current) + scan history (previous).
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jaguar_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 3


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Default starting budget — override per-agent if different

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap based on drawdown from starting budget.

    Winners get more trades (ride the hot hand).
    Losers get fewer trades (preserve capital).
    Catastrophic drawdown triggers HARD STOP (circuit breaker).
    """
    if starting_budget <= 0:
        return 4  # Safe fallback
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand — up >5%
    elif pnl_pct >= 0:     return 8    # Small win / breakeven
    elif pnl_pct >= -5:    return 5    # Careful
    elif pnl_pct >= -15:   return 3    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve — only highest conviction
    else:                  return 0    # HARD STOP — circuit breaker

COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 9
XYZ_BANNED = True

# Fleet-standard conviction-scaled leverage.
# Score 14+ is genuinely rare for Striker signals — requires FIRST_JUMP + deep SM
# + 4H strong + high velocity + volume explosion. Max leverage only on extremes.
# Fleet analysis: >10x leverage destroys edge via fee amplification
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 9,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10

# Striker thresholds
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25
STRIKER_MIN_VOLUME_RATIO = 1.5
STRIKER_MIN_REASONS = 4


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders. Ignores DSL stop-losses."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list):
        for o in orders:
            if not o.get("reduceOnly", False):
                return True
    return False


def check_4h_alignment(direction, price_chg_4h):
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# STRIKER SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP signals with Hyperfeed velocity scoring."""

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m.get("token", ""), m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market.get("token", "")
        dex = market.get("dex", "")
        current_rank = market.get("rank", 999)
        direction = market.get("direction", "").upper()
        current_contrib = market.get("contribution", 0)
        traders = market.get("traders", 0)

        if current_rank <= 10:
            continue

        price_chg_4h = market.get("price_chg_4h", 0)
        if not check_4h_alignment(direction, price_chg_4h):
            continue

        if XYZ_BANNED and dex == "xyz":
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        if not prev_market:
            continue

        rank_jump = prev_market.get("rank", 999) - current_rank
        prev_rank = prev_market.get("rank", 999)

        is_first_jump = False
        is_immediate = False
        reasons = []

        if rank_jump >= 10 and prev_rank >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_rank}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_rank >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_rank}->#{current_rank}")

        if not is_first_jump and not is_immediate:
            continue

        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        # Contribution explosion
        if prev_market.get("contribution", 0) > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        # Contribution velocity from history
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m.get("contribution", 0))
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        # ── Scoring ──
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2

        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs(contrib_velocity):.1f}")

        if prev_rank >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        # 4H strength bonus
        if abs(price_chg_4h) > 3:
            score += 1
            reasons.append(f"STRONG_4H {price_chg_4h:+.1f}%")

        # Trader count (SM depth)
        if traders >= 30:
            score += 1
            reasons.append(f"DEEP_SM ({traders}t)")

        # Hyperfeed 15m/1h contribution velocity + freshness gate
        contrib_15m = market.get("contrib_15m", 0)
        contrib_1h = market.get("contrib_1h", 0)

        # Striker-class hard gate: SM must be actively building right now
        if contrib_15m <= 0:
            reasons.append(f"15M_STALE ({contrib_15m:.2f})")
            continue  # Signal not fresh, skip

        if contrib_15m > 2.0:
            score += 3
            reasons.append(f"15M_STRONG_SPIKE +{contrib_15m:.2f}")
        elif contrib_15m > 0.5:
            score += 2
            reasons.append(f"15M_SPIKE +{contrib_15m:.2f}")
        elif contrib_15m > 0.1:
            score += 1
            reasons.append(f"15M_BUILDING +{contrib_15m:.2f}")

        if contrib_1h > 1.0:
            score += 1
            reasons.append(f"1H_ACCEL +{contrib_1h:.2f}")

        # Acceleration pattern
        if contrib_15m > 0 and contrib_1h > 0 and contrib_15m > contrib_1h:
            score += 1
            reasons.append(f"ACCEL_PATTERN 15m({contrib_15m:.2f})>1h({contrib_1h:.2f})")

        if score < MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # Volume confirmation
        vol_ratio = safe_float(market.get("vol_ratio", market.get("volume_ratio", 0)))
        if vol_ratio < STRIKER_MIN_VOLUME_RATIO:
            volume = safe_float(market.get("volume", 0))
            avg_volume = safe_float(market.get("avg_volume", market.get("avgVolume", 0)))
            if avg_volume > 0:
                vol_ratio = volume / avg_volume
            if vol_ratio < STRIKER_MIN_VOLUME_RATIO:
                continue
        reasons.append(f"VOL {vol_ratio:.1f}x")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": traders,
            "priceChg4h": price_chg_4h,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ═══════════════════════════════════════════════════════════════
# SCAN HISTORY
# ═══════════════════════════════════════════════════════════════

def load_scan_history():
    p = os.path.join(cfg.STATE_DIR, "scan-history.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > 60:
        history["scans"] = scans[-60:]
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "scan-history.json"), history)


def build_scan_snapshot(markets_data):
    markets = []
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        markets.append({
            "token": str(m.get("token", m.get("asset", ""))).upper(),
            "dex": m.get("dex", ""),
            "rank": int(m.get("rank", m.get("position", 999))),
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return {"markets": markets, "timestamp": now_iso()}


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(token, direction, leverage, margin):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        coin=token,
        direction=direction,
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 30,
        },
    )
    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


# ═══════════════════════════════════════════════════════════════
# COOLDOWN & TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0, "last_entry_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_trade_counter(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def is_on_cooldown(coin):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(coin)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    our_positions = [p for p in positions if not p.get("coin", "").lower().startswith("xyz")]

    if len(our_positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in our_positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v3_no_thesis_exit": True})
        return

    # Check for resting entry orders (not DSL stops)
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    tc = load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Global cooldown
    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Cooldown ({remaining}min remaining)"})
        return

    # Fetch SM data
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw

    # Build scan snapshot and save history
    current_scan = build_scan_snapshot(markets)
    history = load_scan_history()
    history["scans"].append(current_scan)
    save_scan_history(history)

    if len(history["scans"]) < 2:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Building scan history (need 2+ scans)"})
        return

    # Detect Striker signals
    signals = detect_striker_signals(current_scan, history)

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Striker signals. Scanned {len(current_scan['markets'])} markets."})
        return

    # Filter and select best signal
    held_coins = {p["coin"].upper() for p in our_positions}

    for signal in signals:
        token = signal["token"]

        if is_on_cooldown(token):
            continue

        if token.upper() in held_coins:
            continue

        # Execute entry directly
        leverage = get_leverage_for_score(signal["score"])
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(token, signal["direction"], leverage, margin)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            tc["last_entry_ts"] = time.time()
            save_trade_counter(tc)

            cfg.output({
                "status": "ok",
                "action": "ENTRY",
                "signal": {
                    "asset": token,
                    "direction": signal["direction"],
                    "score": signal["score"],
                    "leverage": leverage,
                    "mode": "STRIKER",
                    "reasons": signal["reasons"],
                    "rankJump": signal["rankJump"],
                    "isFirstJump": signal["isFirstJump"],
                    "volRatio": signal["volRatio"],
                    "traders": signal["traders"],
                },
                "execution": {
                    "asset": token,
                    "direction": signal["direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "result": result,
                "_jaguar_version": "3.1",
            })
        else:
            cfg.output({
                "status": "ok",
                "action": "ENTRY_FAILED",
                "signal": {
                    "asset": token,
                    "direction": signal["direction"],
                    "score": signal["score"],
                    "reasons": signal["reasons"],
                },
                "error": result,
                "_jaguar_version": "3.1",
            })
        return

    if signals:
        best = signals[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best: {best['token']} {best['direction']} score {best['score']}<{MIN_SCORE} or filtered. {', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"{len(signals)} Striker signals found but all filtered (cooldown/duplicate)"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
