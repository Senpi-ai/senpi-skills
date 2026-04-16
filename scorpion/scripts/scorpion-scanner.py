#!/usr/bin/env python3
# Senpi SCORPION Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SCORPION v3.0 — Multi-Market Active Trader.

Arena winner #2/#3 playbook: trade BOTH crypto AND XYZ DEX commodities,
signal-driven LONG and SHORT, up to 3 concurrent positions, short holds.

This is the only Senpi predator that trades both markets simultaneously.
Crypto majors + mid-caps + XYZ commodities/indices in one universe.

Universe:
  Crypto: BTC, ETH, SOL, HYPE, ZEC, LIT, GRASS, FARTCOIN, TAO, ONDO,
          SUI, ARB, WLD, DOGE, AVAX
  XYZ:    CL (crude oil), BRENTOIL, GOLD, SPX

Direction: Signal-driven, BOTH long and short based on SM direction + 4H
           price trend alignment.

Design:
  - MAX 3 concurrent positions (30% margin each = 90% max exposure)
  - Leverage 5-10x, score-scaled
  - MIN_SCORE 6 (lower bar, broader universe, faster cadence)
  - 120-min per-asset cooldown, 6 entries/day
  - Short hold profile: hard_timeout=720min (12h), dead_weight_cut=30min
  - Self-executing: calls create_position directly via mcporter
  - _v2_no_thesis_exit: scanner enters, DSL exits, scanner NEVER exits

XYZ handling:
  - XYZ assets use "xyz:" prefix for create_position (e.g. coin="xyz:CL")
  - XYZ assets require leverageType="ISOLATED"
  - XYZ assets have different price thresholds (commodities move less)
  - leaderboard_get_markets returns XYZ assets with dex="xyz" — we INCLUDE them

Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorpion_config as cfg

# ── CONFIGURATION ──
CRYPTO_ASSETS = {
    "BTC", "ETH", "SOL", "HYPE", "ZEC", "LIT", "GRASS", "FARTCOIN",
    "TAO", "ONDO", "SUI", "ARB", "WLD", "DOGE", "AVAX",
}
XYZ_ASSETS = {"CL", "BRENTOIL", "GOLD", "SPX"}

MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6

# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap. Winners get more trades, losers fewer."""
    if starting_budget <= 0:
        return 6
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand
    elif pnl_pct >= 0:     return 8    # Breakeven+
    elif pnl_pct >= -5:    return 6    # Careful
    elif pnl_pct >= -15:   return 4    # Defensive
    elif pnl_pct >= -25:   return 2    # Preserve
    else:                  return 0    # HARD STOP — circuit breaker


COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.30
MIN_SCORE = 6

# 4H price alignment thresholds — XYZ assets move less than crypto
MIN_4H_ALIGNED_PCT_CRYPTO = 1.0
MIN_4H_ALIGNED_PCT_XYZ = 0.5

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
    {"min_score": 6,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(token):
    """Check if a token is an XYZ DEX asset."""
    return token in XYZ_ASSETS


def coin_for_position(token):
    """Return the coin string for create_position — XYZ assets need 'xyz:' prefix."""
    if is_xyz(token):
        return f"xyz:{token}"
    return token


def leverage_type_for(token):
    """XYZ assets require ISOLATED, crypto can use CROSS."""
    if is_xyz(token):
        return "ISOLATED"
    return "CROSS"


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling stale ones >10min."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue
        has_fresh = True
    return has_fresh


def evaluate_markets(held_coins):
    """Score all allowed assets for trend-following opportunities.

    Returns a list of candidates sorted by score (best first).
    Excludes assets we already hold positions in.
    """
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return []
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    candidates = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()

        # Determine if this is an allowed asset
        is_xyz_asset = (dex == "xyz" and token in XYZ_ASSETS)
        is_crypto_asset = (dex != "xyz" and token in CRYPTO_ASSETS)

        if not is_xyz_asset and not is_crypto_asset:
            continue

        # Skip assets we already hold
        coin_key = coin_for_position(token) if is_xyz_asset else token
        if coin_key in held_coins or token in held_coins:
            continue

        sm_direction = str(m.get("direction", "")).upper()
        if sm_direction not in ("LONG", "SHORT"):
            continue

        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        p4h = safe_float(m.get("token_price_change_pct_4h", 0))
        p1h = safe_float(m.get("token_price_change_pct_1h",
                         m.get("price_change_1h", 0)))
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
        cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
        cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

        if traders < 5:
            continue

        # 4H price alignment gate — SM direction must match price trend
        min_4h = MIN_4H_ALIGNED_PCT_XYZ if is_xyz_asset else MIN_4H_ALIGNED_PCT_CRYPTO
        price_aligned = (sm_direction == "LONG" and p4h >= min_4h) or \
                        (sm_direction == "SHORT" and p4h <= -min_4h)
        if not price_aligned:
            continue

        score = 0
        reasons = []
        asset_label = f"xyz:{token}" if is_xyz_asset else token

        # SM concentration (0-3)
        if pct >= 15:
            score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
        elif pct >= 10:
            score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
        elif pct >= 5:
            score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

        # 4H price alignment (0-3) — bigger move = stronger trend
        big_move = 3.0 if is_xyz_asset else 5.0
        med_move = 1.5 if is_xyz_asset else 3.0
        if abs(p4h) >= big_move:
            score += 3; reasons.append(f"STRONG_TREND {p4h:+.1f}%")
        elif abs(p4h) >= med_move:
            score += 2; reasons.append(f"TREND {p4h:+.1f}%")
        elif abs(p4h) >= min_4h:
            score += 1; reasons.append(f"ALIGNED {p4h:+.1f}%")

        # 15m SM velocity (0-2) — is SM actively building?
        if cc_15m > 1.0:
            score += 2; reasons.append(f"15M_SM_BUILDING {cc_15m:+.2f}")
        elif cc_15m > 0.3:
            score += 1; reasons.append(f"15M_SM_FRESH {cc_15m:+.2f}")
        elif cc_15m < -0.5:
            score -= 1; reasons.append(f"15M_SM_FADING {cc_15m:+.2f}")

        # 1H acceleration (0-1) — is the move accelerating?
        if sm_direction == "LONG":
            if p1h > 0.5:
                score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")
        else:
            if p1h < -0.5:
                score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")

        # Trader depth (0-1)
        if traders >= 50:
            score += 1; reasons.append(f"DEEP_SM ({traders}t)")

        # 4H contribution shift (0-1) — conviction building over hours
        if abs(cc_4h) >= 5.0:
            score += 1; reasons.append(f"4H_CONVICTION {cc_4h:+.1f}")

        if score >= MIN_SCORE:
            reasons.insert(0, f"TREND_FOLLOW {asset_label} {sm_direction}")
            candidates.append({
                "token": token,
                "is_xyz": is_xyz_asset,
                "score": score,
                "direction": sm_direction,
                "reasons": reasons,
                "p4h": p4h,
                "smPct": pct,
                "smTraders": traders,
            })

    if not candidates:
        return []

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def execute_entry(wallet, token, direction, margin, leverage, is_xyz):
    """Execute a position entry via mcporter."""
    coin = coin_for_position(token) if is_xyz else token
    lev_type = leverage_type_for(token) if is_xyz else "CROSS"

    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": coin,
            "direction": direction,
            "leverage": leverage,
            "leverageType": lev_type,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": True,
                "executionTimeoutSeconds": 30,
            },
        }],
    )
    if result and result.get("success"):
        return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "cannot read account"})
        return

    # Check for resting orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    # Check existing positions — DSL manages all exits
    held_coins = set()
    for p in positions:
        held_coins.add(p["coin"])
    pos_count = len(positions)

    if pos_count >= MAX_POSITIONS:
        pos_desc = ", ".join(f"{p['coin']} {p['direction']}" for p in positions)
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"MAX_POSITIONS ({MAX_POSITIONS}): {pos_desc}. DSL manages exits.",
            "_v2_no_thesis_exit": True,
        })
        return

    # Daily limits
    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    effective_cap = min(dynamic_cap, MAX_DAILY_ENTRIES)
    if tc.get("entries", 0) >= effective_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({effective_cap}) reached. PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{effective_cap}"})
        return

    # Evaluate all allowed assets
    candidates = evaluate_markets(held_coins)
    if not candidates:
        pos_desc = ", ".join(f"{p['coin']} {p['direction']}" for p in positions) if positions else "none"
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"SCANNING: no qualifying signal across crypto+XYZ universe. Holding: {pos_desc}"})
        return

    # Try candidates in order until one passes cooldown and executes
    for candidate in candidates:
        token = candidate["token"]
        direction = candidate["direction"]

        # Per-asset cooldown
        if cfg.is_asset_cooled_down(token, COOLDOWN_MINUTES):
            continue

        leverage = get_leverage_for_score(candidate["score"])
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(
            wallet, token, direction, margin, leverage, candidate["is_xyz"]
        )

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            cfg.save_trade_counter(tc)
            cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="entry")

            coin_label = coin_for_position(token) if candidate["is_xyz"] else token
            cfg.output({
                "status": "ok",
                "action": "ENTRY",
                "signal": {
                    "asset": coin_label,
                    "direction": direction,
                    "score": candidate["score"],
                    "leverage": leverage,
                    "p4h": candidate["p4h"],
                    "is_xyz": candidate["is_xyz"],
                    "mode": "MULTI_MARKET_TREND",
                    "reasons": candidate["reasons"],
                },
                "execution": {
                    "coin": coin_label,
                    "direction": direction,
                    "leverage": leverage,
                    "leverageType": leverage_type_for(token) if candidate["is_xyz"] else "CROSS",
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": True,
                },
                "result": result,
                "positions_held": pos_count + 1,
                "max_positions": MAX_POSITIONS,
                "_scorpion_version": "3.0",
            })
            return
        else:
            cfg.log(f"Entry failed for {token}: {result}")
            continue

    # All candidates either on cooldown or failed
    cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": "All qualifying candidates on cooldown or entry failed."})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
