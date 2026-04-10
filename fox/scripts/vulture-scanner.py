#!/usr/bin/env python3
# Senpi VULTURE Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""VULTURE v1.0 — Multi-Asset SM Exhaustion Fader.

Thesis: When SM consensus is overwhelmingly strong in one direction AND the
4H price move is already extended (>3%), the move is exhausted and about to
reverse. Trade the opposite direction.

This is a purpose-built contrarian strategy informed by fleet analysis
(April 10, 2026) which found that 5 agents using SM consensus scanners
had perfectly inverted signals — they were systematically buying tops and
shorting bottoms because multi-timeframe confirmation enters after the
move is exhausted.

Vulture takes that finding and builds a dedicated agent around it:
- Score SM consensus strength (the stronger, the more exhausted)
- Require 4H price confirmation >3% (the move has already happened)
- FLIP direction (fade the exhausted move)
- Conservative leverage (5x-7x, contrarian trades need room)
- Wide DSL (6-hour hard timeout, let reversals develop)

Assets: BTC, ETH, SOL, HYPE only (majors with deep liquidity).
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vulture_config as cfg

# ── CONFIGURATION ──
ALLOWED_ASSETS = {"BTC", "ETH", "SOL", "HYPE"}
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 2
COOLDOWN_MINUTES = 120
SAME_DIR_COOLDOWN_MINUTES = 60
MARGIN_PCT = 0.30
MIN_SCORE = 8
MIN_4H_MOVE_PCT = 3.0  # 4H price must have moved >3% for exhaustion

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 7},
    {"min_score": 8,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
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


def evaluate_markets():
    """Score all allowed assets for exhaustion fade opportunities.
    Returns the best candidate or None."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return None
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
        if token not in ALLOWED_ASSETS or dex == "xyz":
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

        if traders < 10:
            continue

        # EXHAUSTION GATE: 4H price must have moved >3% in SM direction.
        # This confirms the move has already happened — we're fading the tail.
        price_aligned = (sm_direction == "LONG" and p4h > 0) or \
                        (sm_direction == "SHORT" and p4h < 0)
        if not price_aligned or abs(p4h) < MIN_4H_MOVE_PCT:
            continue

        score = 0
        reasons = []

        # SM concentration (0-3) — stronger consensus = more exhausted
        if pct >= 15:
            score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
        elif pct >= 10:
            score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
        elif pct >= 5:
            score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

        # 4H price move — EXHAUSTION BONUS (inverted from normal scanners)
        # The bigger the move, the more exhausted = better fade
        if abs(p4h) >= 5.0:
            score += 3; reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}%")
        elif abs(p4h) >= 4.0:
            score += 2; reasons.append(f"EXHAUSTION {p4h:+.1f}%")
        elif abs(p4h) >= MIN_4H_MOVE_PCT:
            score += 1; reasons.append(f"EXTENDED {p4h:+.1f}%")

        # 1H momentum — if 1H is fading while 4H is extended, reversal is starting
        if sm_direction == "LONG":
            if p1h < -0.2:
                score += 2; reasons.append(f"1H_REVERSING {p1h:+.2f}%")
            elif p1h < 0.2:
                score += 1; reasons.append(f"1H_STALLING {p1h:+.2f}%")
        else:
            if p1h > 0.2:
                score += 2; reasons.append(f"1H_REVERSING {p1h:+.2f}%")
            elif p1h > -0.2:
                score += 1; reasons.append(f"1H_STALLING {p1h:+.2f}%")

        # 15m velocity — fading velocity confirms exhaustion
        if cc_15m < -0.5:
            score += 2; reasons.append(f"15M_SM_FADING {cc_15m:.2f}")
        elif cc_15m < -0.1:
            score += 1; reasons.append(f"15M_SM_COOLING {cc_15m:.2f}")
        elif cc_15m > 1.0:
            score -= 1; reasons.append(f"15M_SM_STILL_BUILDING {cc_15m:.2f}")

        # Trader depth (0-1)
        if traders >= 100:
            score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

        # Funding alignment — if we'd collect funding on our fade, bonus
        # (funding data from asset_data is expensive, use contribution as proxy)
        if abs(cc_4h) >= 5.0:
            score += 1; reasons.append(f"4H_MAJOR_SHIFT {cc_4h:+.1f}")

        if score >= MIN_SCORE:
            # CONTRARIAN FLIP
            fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"
            reasons.insert(0, f"CONTRARIAN_FADE {token} (SM is {sm_direction})")
            candidates.append({
                "token": token,
                "score": score,
                "sm_direction": sm_direction,
                "fade_direction": fade_direction,
                "reasons": reasons,
                "p4h": p4h,
                "smPct": pct,
                "smTraders": traders,
            })

    if not candidates:
        return None

    # Pick highest-scoring candidate
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[0]


def execute_entry(token, direction, margin, leverage):
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
    if len(positions) >= MAX_POSITIONS:
        pos = positions[0]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {pos['coin']} {pos['direction']}. DSL manages exit.",
            "_v2_no_thesis_exit": True,
        })
        return

    # Daily limits
    tc = cfg.load_trade_counter()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # Evaluate all allowed assets for exhaustion
    candidate = evaluate_markets()
    if not candidate:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "CIRCLING: no exhaustion fade opportunity on BTC/ETH/SOL/HYPE"})
        return

    token = candidate["token"]
    fade_direction = candidate["fade_direction"]

    # Per-asset cooldown
    if cfg.is_asset_cooled_down(token, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{token} on cooldown"})
        return

    # Same-direction re-entry cooldown after a win
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            if fade_direction == last_win_dir:
                remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 -
                                (time.time() - last_win_ts)) / 60)
                cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"SAME_DIR_COOLDOWN: won {last_win_dir} {remaining}min ago"})
                return

    leverage = get_leverage_for_score(candidate["score"])
    margin = round(account_value * MARGIN_PCT, 2)

    success, result = execute_entry(token, fade_direction, margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        cfg.save_trade_counter(tc)
        cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="entry")

        cfg.output({
            "status": "ok",
            "action": "FADE_ENTRY",
            "signal": {
                "asset": token,
                "sm_direction": candidate["sm_direction"],
                "fade_direction": fade_direction,
                "score": candidate["score"],
                "leverage": leverage,
                "p4h": candidate["p4h"],
                "mode": "EXHAUSTION_FADE",
                "reasons": candidate["reasons"],
            },
            "execution": {
                "asset": token,
                "direction": fade_direction,
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "result": result,
            "_vulture_version": "1.0",
        })
    else:
        cfg.output({
            "status": "ok",
            "action": "FADE_ENTRY_FAILED",
            "signal": {
                "asset": token,
                "sm_direction": candidate["sm_direction"],
                "fade_direction": fade_direction,
                "score": candidate["score"],
                "reasons": candidate["reasons"],
            },
            "error": result,
            "_vulture_version": "1.0",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
