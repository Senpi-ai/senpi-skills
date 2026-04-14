#!/usr/bin/env python3
# Senpi WOLVERINE Scanner v2.2
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""WOLVERINE v2.2 — HYPE Alpha Hunter (Hardened).

v2.2 changes (fleet conviction leverage):
- Extreme velocity tiers on 15m/1h contribution (score up to +4 / +2)
- MAX_LEVERAGE constant 10x (HL cap)

v2.1 changes from fleet audit:
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- marginPercent replaced with marginAmount (dollar value)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: score 8-9 → 7x, score 10+ → 10x
- Margin increased to 50% of account
- 4H/1H alignment: was HARD GATE → now +2 score points
- No thesis exit (unchanged from v2.0)

Uses: leaderboard_get_markets (SM consensus on HYPE)
Runs every 3 minutes.
"""

import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wolverine_config as cfg

ASSET = "HYPE"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4


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

MAX_DAILY_LOSS_PCT = 10
COOLDOWN_MINUTES = 180
MARGIN_PCT = 0.50
MIN_SCORE = 8
MIN_SM_TRADERS = 15
MIN_SM_RANK = 30
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},  # HYPE max on HL is 10x
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def score_hype_signal(markets_data):
    """Score HYPE. All signals are score contributors — no hard gates."""
    hype_data = None
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = str(m.get("dex", "")).lower()
        if token == ASSET and dex != "xyz":
            hype_data = m
            break

    if not hype_data:
        return 0, None, []

    sm_direction = str(hype_data.get("direction", "")).lower()
    trader_count = int(hype_data.get("trader_count", 0))
    sm_rank = int(hype_data.get("rank", hype_data.get("position", 999)))
    contribution = safe_float(hype_data.get("pct_of_top_traders_gain", 0))
    contrib_change = safe_float(hype_data.get("contribution_pct_change_4h", 0))
    contrib_15m = safe_float(hype_data.get("contribution_pct_change_15m", 0))
    contrib_1h = safe_float(hype_data.get("contribution_pct_change_1h", 0))

    if not sm_direction or sm_direction not in ("long", "short"):
        return 0, None, []

    direction = sm_direction

    # Minimum traders — keep as gate (need data to score)
    if trader_count < MIN_SM_TRADERS:
        return 0, None, [f"LOW_TRADERS ({trader_count} < {MIN_SM_TRADERS})"]

    score = 0
    reasons = []

    # SM presence (base)
    score += 2
    reasons.append(f"SM_{direction.upper()} rank#{sm_rank} ({trader_count}t)")

    # SM rank bonus
    if sm_rank <= 5:
        score += 1
        reasons.append(f"TOP_5_SM")

    # Trader count depth
    if trader_count >= 40:
        score += 2
        reasons.append(f"DEEP_SM ({trader_count}t)")
    elif trader_count >= 25:
        score += 1
        reasons.append(f"SOLID_SM ({trader_count}t)")

    # Contribution strength
    if contribution >= 5.0:
        score += 2
        reasons.append(f"HIGH_CONTRIB {contribution:.1f}%")
    elif contribution >= 2.0:
        score += 1
        reasons.append(f"MODERATE_CONTRIB {contribution:.1f}%")

    # 15m velocity freshness — conviction-class penalty (not hard gate)
    if contrib_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({contrib_15m:.2f})")
    elif contrib_15m > 5.0:
        score += 4
        reasons.append(f"15M_EXTREME_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 2.0:
        score += 3
        reasons.append(f"15M_STRONG_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 0.5:
        score += 2
        reasons.append(f"15M_SPIKE +{contrib_15m:.2f}")
    elif contrib_15m > 0.1:
        score += 1
        reasons.append(f"15M_BUILDING +{contrib_15m:.2f}")

    if contrib_1h > 3.0:
        score += 2
        reasons.append(f"1H_STRONG_ACCEL +{contrib_1h:.2f}")
    elif contrib_1h > 1.0:
        score += 1
        reasons.append(f"1H_ACCEL +{contrib_1h:.2f}")

    if abs(contrib_change) >= 5.0:
        score += 1
        reasons.append(f"4H_MAJOR_SHIFT {contrib_change:+.1f}")

    # Acceleration pattern: 15m > 1h > 0 = SM inflow accelerating
    if contrib_15m > 0 and contrib_1h > 0 and contrib_15m > contrib_1h:
        score += 1
        reasons.append(f"ACCEL_PATTERN 15m({contrib_15m:.2f})>1h({contrib_1h:.2f})")

    # Price momentum — score contributor, not gate
    price_4h = safe_float(hype_data.get("token_price_change_pct_4h", 0))
    price_1h = safe_float(hype_data.get("token_price_change_pct_1h",
                          hype_data.get("price_change_1h", 0)))

    if direction == "long":
        if price_4h > 0 and price_1h > 0:
            score += 2
            reasons.append(f"4H_1H_ALIGNED (+{price_4h:.1f}%/+{price_1h:.1f}%)")
        elif price_4h > 0:
            score += 1
            reasons.append(f"4H_ALIGNED (+{price_4h:.1f}%)")
        elif price_4h < -1:
            score -= 1
            reasons.append(f"4H_OPPOSING ({price_4h:.1f}%)")
    else:
        if price_4h < 0 and price_1h < 0:
            score += 2
            reasons.append(f"4H_1H_ALIGNED ({price_4h:.1f}%/{price_1h:.1f}%)")
        elif price_4h < 0:
            score += 1
            reasons.append(f"4H_ALIGNED ({price_4h:.1f}%)")
        elif price_4h > 1:
            score -= 1
            reasons.append(f"4H_OPPOSING (+{price_4h:.1f}%)")

    # Volume confirmation
    volume = safe_float(hype_data.get("volume", hype_data.get("volume_24h", 0)))
    avg_volume = safe_float(hype_data.get("avg_volume_6h", hype_data.get("avgVolume", 0)))
    if avg_volume > 0 and volume > avg_volume * 1.2:
        score += 1
        reasons.append(f"VOLUME_UP ({volume / avg_volume:.1f}x)")

    return score, direction, reasons


def execute_entry(direction, margin, leverage):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        coin=ASSET,
        direction=direction.upper(),
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


def run():
    config = cfg.load_config()
    wallet, strategy_id = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # Check existing positions — do NOT re-evaluate
    account_value, positions = cfg.get_positions(wallet)
    hype_positions = [p for p in positions if p["coin"].upper() == ASSET]

    if len(hype_positions) >= MAX_POSITIONS:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HYPE position active. DSL manages exit.",
            "_v2_no_thesis_exit": True,
        })
        return

    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # Daily limits
    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Cooldown
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"HYPE on cooldown ({COOLDOWN_MINUTES} min)"})
        return

    # Fetch SM data
    raw = cfg.mcporter_call("leaderboard_get_markets")
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

    # Score HYPE
    score, direction, reasons = score_hype_signal(markets)

    if score < MIN_SCORE or not direction:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HYPE score {score} < {MIN_SCORE}. {', '.join(reasons[:3]) if reasons else 'Waiting.'}",
        })
        return

    # Conviction-scaled leverage and margin
    leverage = get_leverage_for_score(score)
    margin = round(account_value * MARGIN_PCT, 2)

    # Execute trade directly
    success, result = execute_entry(direction, margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        cfg.save_trade_counter(tc)

        cfg.output({
            "status": "ok",
            "action": "ENTRY",
            "signal": {"asset": ASSET, "direction": direction.upper(),
                       "score": score, "leverage": leverage, "reasons": reasons},
            "execution": {"asset": ASSET, "direction": direction.upper(),
                          "leverage": leverage, "margin": margin,
                          "orderType": "FEE_OPTIMIZED_LIMIT",
                          "ensureExecutionAsTaker": False},
            "result": result,
            "_wolverine_version": "2.2",
        })
    else:
        cfg.output({
            "status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": direction.upper(),
                       "score": score, "reasons": reasons},
            "error": result,
            "_wolverine_version": "2.2",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
