#!/usr/bin/env python3
"""
COBRA v1.1 — Arena Sprint Predator

PURPOSE: Win Arena weekly competitions by catching the #1 SM rotation
with maximum conviction. Unlike patient lifecycle hunters that wait for
perfect 8/10 scores, Cobra enters when SM dominance is extreme and
allocates maximum margin to a single concentrated bet.

THESIS: The Arena rewards ROE% over 7 days. The winning strategy is NOT
many small trades — it's 2-4 massive conviction trades that ride the
dominant SM trend. openclawJaime won Week 2 with +62% ROE on just 12 trades.

DESIGN PRINCIPLES:
1. ONE position at a time — full concentration
2. Only trade the #1 SM asset when dominance is extreme (>10%)
3. Higher margin allocation ($400 of $1K) for concentrated ROE
4. Lower entry threshold (score 5) but ONLY on the dominant asset
5. Fast exit cycling — take profits quickly, re-enter on next signal
6. 3 entries per day max, 90-minute cooldown, 120-min per-asset cooldown

SIGNALS (2 API calls):
1. leaderboard_get_markets → Find #1 SM asset, direction, concentration
2. market_get_asset_data → 4H candle + 1H candle for trend confirmation

SCORING (max 8 points):
- SM Dominance: >15% = +3, >10% = +2, >5% = +1
- Contribution Surge: >5% = +2, >2% = +1
- 4H Trend Confirms: >0.5% in direction = +1
- 1H Trend Confirms: >0.2% in direction = +1
- Deep Consensus: >200 traders = +1

MIN_SCORE: 5 (intentionally lower than other agents)
Only evaluates the #1 SM asset — never scatters across multiple assets.

LEVERAGE: 10x on majors (BTC/ETH/SOL/HYPE), 7x on mid-caps, 5x on others
MARGIN: $400 per trade (40% of budget for concentrated ROE)

DSL: Breathing room — 180min hard timeout, 45min dead weight, wide
Phase 2 locks. Let winners run. Condor-like efficiency over Polar-like volume.
"""

import json
import sys
import os
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================
MAX_ENTRIES_PER_DAY = 3
COOLDOWN_MINUTES = 90
PER_ASSET_COOLDOWN_MINUTES = 90
MIN_SCORE = 5
MARGIN_AMOUNT = 400  # $400 of $1K budget — concentrated
MAX_POSITIONS = 1

# Leverage tiers by asset class
MAJOR_ASSETS = {"BTC", "ETH", "SOL", "HYPE"}
MIDCAP_ASSETS = {"AVAX", "DOGE", "LINK", "UNI", "AAVE", "XRP", "ADA", "DOT", "NEAR"}
LEVERAGE_MAJOR = 10
LEVERAGE_MIDCAP = 7
LEVERAGE_OTHER = 5

# Minimum SM dominance to even consider the asset
MIN_SM_DOMINANCE = 10.0

# State file for tracking daily entries and cooldowns
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "cobra-state.json")

# XYZ equities are banned
XYZ_BANNED = True


def load_state():
    """Load persistent state (daily counter, cooldowns, last trade)."""
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if state.get("date") != today:
            state["date"] = today
            state["entries_today"] = 0
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "entries_today": 0,
            "last_entry_time": None,
            "last_asset": None,
            "last_direction": None,
            "asset_cooldowns": {}
        }


def save_state(state):
    """Persist state to disk."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_cooldown(state):
    """Check if global cooldown period has elapsed since last entry."""
    last_time = state.get("last_entry_time")
    if not last_time:
        return True, 0
    try:
        last_dt = datetime.fromisoformat(last_time)
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - last_dt).total_seconds() / 60
        remaining = max(0, COOLDOWN_MINUTES - elapsed_minutes)
        return elapsed_minutes >= COOLDOWN_MINUTES, round(remaining, 1)
    except (ValueError, TypeError):
        return True, 0


def check_asset_cooldown(state, asset):
    """Check per-asset cooldown to avoid re-entering same asset too quickly."""
    cooldowns = state.get("asset_cooldowns", {})
    last_time = cooldowns.get(asset)
    if not last_time:
        return True, 0
    try:
        last_dt = datetime.fromisoformat(last_time)
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - last_dt).total_seconds() / 60
        remaining = max(0, PER_ASSET_COOLDOWN_MINUTES - elapsed_minutes)
        return elapsed_minutes >= PER_ASSET_COOLDOWN_MINUTES, round(remaining, 1)
    except (ValueError, TypeError):
        return True, 0


def get_leverage(token):
    """Get leverage tier for the asset."""
    if token in MAJOR_ASSETS:
        return LEVERAGE_MAJOR
    elif token in MIDCAP_ASSETS:
        return LEVERAGE_MIDCAP
    else:
        return LEVERAGE_OTHER


def find_dominant_asset(markets):
    """
    Find the #1 non-XYZ asset by SM concentration.
    Returns the market entry or None.
    """
    for m in markets:
        if XYZ_BANNED and m.get("dex", "") == "xyz":
            continue
        return m
    return None


def score_asset(asset_data, candle_4h_pct=None, candle_1h_pct=None):
    """
    Score the dominant asset for entry.

    Args:
        asset_data: Market entry from leaderboard_get_markets
        candle_4h_pct: 4H candle % change from market_get_asset_data (optional, falls back to markets data)
        candle_1h_pct: 1H candle % change from market_get_asset_data (optional)

    Returns (score, breakdown)
    """
    token = asset_data.get("token", "")
    direction = asset_data.get("direction", "")
    sm_pct = asset_data.get("pct_of_top_traders_gain", 0)
    contrib_change = asset_data.get("contribution_pct_change_4h", 0) or 0
    price_change_4h = candle_4h_pct if candle_4h_pct is not None else asset_data.get("token_price_change_pct_4h", 0)
    price_change_1h = candle_1h_pct  # None if not provided
    trader_count = asset_data.get("trader_count", 0)

    score = 0
    breakdown = {
        "token": token,
        "direction": direction.upper(),
        "sm_pct": round(sm_pct, 2),
        "contrib_change": round(contrib_change, 2),
        "price_change_4h": round(price_change_4h, 3),
        "price_change_1h": round(price_change_1h, 3) if price_change_1h is not None else "N/A",
        "trader_count": trader_count,
    }

    # 1. SM Dominance (max 3 pts)
    if sm_pct >= 15:
        score += 3
        breakdown["sm_score"] = f"+3 (DOMINANT {sm_pct:.1f}%)"
    elif sm_pct >= 10:
        score += 2
        breakdown["sm_score"] = f"+2 (STRONG {sm_pct:.1f}%)"
    elif sm_pct >= 5:
        score += 1
        breakdown["sm_score"] = f"+1 (ACTIVE {sm_pct:.1f}%)"
    else:
        breakdown["sm_score"] = f"+0 (WEAK {sm_pct:.1f}%)"

    # 2. Contribution Surge (max 2 pts)
    abs_contrib = abs(contrib_change)
    if abs_contrib >= 5:
        score += 2
        breakdown["contrib_score"] = f"+2 (SURGE {contrib_change:+.1f}%)"
    elif abs_contrib >= 2:
        score += 1
        breakdown["contrib_score"] = f"+1 (RISING {contrib_change:+.1f}%)"
    else:
        breakdown["contrib_score"] = f"+0 (FLAT {contrib_change:+.1f}%)"

    # 3. 4H Price Trend Confirms Direction (1 pt)
    confirms_4h = False
    if direction == "long" and price_change_4h > 0.5:
        confirms_4h = True
    elif direction == "short" and price_change_4h < -0.5:
        confirms_4h = True

    if confirms_4h:
        score += 1
        breakdown["4h_score"] = f"+1 (CONFIRMS {price_change_4h:+.2f}%)"
    else:
        breakdown["4h_score"] = f"+0 (NO CONFIRM {price_change_4h:+.2f}%, need {'>' if direction == 'long' else '<'}{'0.5' if direction == 'long' else '-0.5'}%)"

    # 4. 1H Price Trend Confirms Direction (1 pt)
    if price_change_1h is not None:
        confirms_1h = False
        if direction == "long" and price_change_1h > 0.2:
            confirms_1h = True
        elif direction == "short" and price_change_1h < -0.2:
            confirms_1h = True

        if confirms_1h:
            score += 1
            breakdown["1h_score"] = f"+1 (CONFIRMS {price_change_1h:+.2f}%)"
        else:
            breakdown["1h_score"] = f"+0 (NO CONFIRM {price_change_1h:+.2f}%)"
    else:
        # No 1H data available — don't penalize, don't reward
        breakdown["1h_score"] = "+0 (NO 1H DATA — call market_get_asset_data)"

    # 5. Deep Consensus (1 pt)
    if trader_count >= 200:
        score += 1
        breakdown["consensus_score"] = f"+1 (DEEP: {trader_count} traders)"
    elif trader_count >= 100:
        breakdown["consensus_score"] = f"+0 ({trader_count} traders, need 200)"
    else:
        breakdown["consensus_score"] = f"+0 ({trader_count} traders, need 200)"

    breakdown["total_score"] = score
    breakdown["min_score"] = MIN_SCORE
    breakdown["passes"] = score >= MIN_SCORE

    return score, breakdown


def generate_entry(token, direction, leverage, margin):
    """Generate entry signal with DSL state."""
    return {
        "coin": token,
        "direction": direction.upper(),
        "leverage": leverage,
        "leverageType": "CROSS",
        "marginAmount": margin,
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "ensureExecutionAsTaker": True,
        "executionTimeoutSeconds": 30,
    }


def generate_dsl_state(token, direction, leverage):
    """Generate complete DSL state for the position."""
    return {
        "coin": token,
        "direction": direction.upper(),
        "leverage": leverage,
        "leverageType": "CROSS",
        "absoluteFloorRoe": None,
        "highWaterRoe": None,
        "highWaterPrice": None,
        "currentTier": 0,
        "consecutiveBreaches": 0,
        "consecutiveBreachesRequired": 3,
        "phase1MaxMinutes": 30,
        "deadWeightCutMin": 15,
        "phase1": {
            "maxLossPct": 15.0,
            "retraceThreshold": 8,
            "enabled": True
        },
        "phase2": {
            "enabled": True,
            "tiers": [
                {"triggerPct": 5, "lockHwPct": 25},
                {"triggerPct": 10, "lockHwPct": 45},
                {"triggerPct": 15, "lockHwPct": 60},
                {"triggerPct": 20, "lockHwPct": 75},
                {"triggerPct": 30, "lockHwPct": 85},
                {"triggerPct": 50, "lockHwPct": 92}
            ]
        },
        "hardTimeout": {
            "enabled": True,
            "intervalInMinutes": 180
        },
        "weakPeakCut": {
            "enabled": True,
            "intervalInMinutes": 60,
            "minValue": 3.0
        },
        "deadWeightCut": {
            "enabled": True,
            "intervalInMinutes": 15
        }
    }


def main():
    """
    Main scanner entry point.

    USAGE: The agent calls this scanner with market data as JSON on stdin.
    The agent is responsible for:
      1. Calling senpi:leaderboard_get_markets (limit=20)
      2. Optionally calling senpi:market_get_asset_data for the top asset
      3. Piping the combined result as JSON to this script
      4. Reading the output and executing the entry if action == "ENTER"

    INPUT FORMAT (stdin JSON):
    {
      "markets": { ... },          // Required: from leaderboard_get_markets
      "asset_data": {              // Optional: from market_get_asset_data
        "candle_4h_pct": -1.05,
        "candle_1h_pct": -0.35
      }
    }

    OUTPUT FORMAT (stdout JSON):
    {
      "scanner": "cobra",
      "action": "ENTER" | "NONE",
      "reason": "...",
      "score": 5,
      "breakdown": { ... },
      "entry": { ... } | null,
      "dsl_state": { ... } | null
    }
    """
    state = load_state()
    now = datetime.now(timezone.utc)

    output = {
        "scanner": "cobra",
        "version": "1.0",
        "timestamp": now.isoformat(),
        "action": "NONE",
        "reason": "",
        "score": 0,
        "breakdown": {},
        "entry": None,
        "dsl_state": None,
        "status": {
            "entries_today": state["entries_today"],
            "max_entries": MAX_ENTRIES_PER_DAY,
            "last_asset": state.get("last_asset"),
            "last_direction": state.get("last_direction"),
        }
    }

    # Gate 1: Daily entry limit
    if state["entries_today"] >= MAX_ENTRIES_PER_DAY:
        output["reason"] = f"Daily cap reached ({state['entries_today']}/{MAX_ENTRIES_PER_DAY}). Resets at UTC midnight."
        print(json.dumps(output, indent=2))
        return

    # Gate 2: Global cooldown
    cooldown_clear, cooldown_remaining = check_cooldown(state)
    if not cooldown_clear:
        output["reason"] = f"Global cooldown active. {cooldown_remaining} minutes remaining."
        print(json.dumps(output, indent=2))
        return

    # Parse input
    try:
        if not sys.stdin.isatty():
            input_data = json.load(sys.stdin)
        else:
            output["reason"] = "AWAITING_DATA"
            output["instructions"] = [
                "1. Call senpi:leaderboard_get_markets with limit=20",
                "2. Optionally call senpi:market_get_asset_data for the #1 asset to get 1H candle",
                "3. Pipe combined JSON to this scanner: echo '{...}' | python3 cobra-scanner.py",
                "4. If action=ENTER, call senpi:create_position with the entry block"
            ]
            print(json.dumps(output, indent=2))
            return
    except json.JSONDecodeError:
        output["reason"] = "Invalid JSON input"
        print(json.dumps(output, indent=2))
        return

    # Extract market data
    markets_data = input_data if "markets" in input_data.get("data", input_data) else input_data
    if "data" in markets_data:
        markets_data = markets_data["data"]

    markets_list = markets_data.get("markets", {}).get("markets", [])
    if not markets_list:
        output["reason"] = "No market data in input"
        print(json.dumps(output, indent=2))
        return

    # Find dominant non-XYZ asset
    dominant = find_dominant_asset(markets_list)
    if not dominant:
        output["reason"] = "All top assets are XYZ equities (banned)"
        print(json.dumps(output, indent=2))
        return

    token = dominant["token"]
    sm_pct = dominant.get("pct_of_top_traders_gain", 0)

    # Gate 3: Minimum SM dominance
    if sm_pct < MIN_SM_DOMINANCE:
        output["reason"] = f"Top asset {token} only {sm_pct:.1f}% SM (need >{MIN_SM_DOMINANCE}%). No dominant rotation."
        output["breakdown"] = {
            "token": token,
            "direction": dominant.get("direction", "").upper(),
            "sm_pct": round(sm_pct, 2),
            "trader_count": dominant.get("trader_count", 0)
        }
        print(json.dumps(output, indent=2))
        return

    # Gate 4: Per-asset cooldown
    asset_clear, asset_remaining = check_asset_cooldown(state, token)
    if not asset_clear:
        output["reason"] = f"Per-asset cooldown for {token}. {asset_remaining} minutes remaining."
        print(json.dumps(output, indent=2))
        return

    # Extract optional 1H candle data
    asset_extra = input_data.get("asset_data", {})
    candle_4h = asset_extra.get("candle_4h_pct")
    candle_1h = asset_extra.get("candle_1h_pct")

    # Score
    score, breakdown = score_asset(dominant, candle_4h_pct=candle_4h, candle_1h_pct=candle_1h)
    output["score"] = score
    output["breakdown"] = breakdown

    if score < MIN_SCORE:
        output["reason"] = f"Score {score}/{MIN_SCORE} on {token} {dominant['direction'].upper()} — below threshold."
        missing = MIN_SCORE - score
        output["breakdown"]["missing_points"] = missing
        output["breakdown"]["hint"] = f"Need {missing} more point(s). Check 4H/1H trend and contribution velocity."
        print(json.dumps(output, indent=2))
        return

    # ================================================================
    # ENTRY SIGNAL — Score passed, all gates clear
    # ================================================================
    direction = dominant["direction"].upper()
    leverage = get_leverage(token)

    entry = generate_entry(token, direction, leverage, MARGIN_AMOUNT)
    dsl_state = generate_dsl_state(token, direction, leverage)

    output["action"] = "ENTER"
    output["reason"] = f"COBRA STRIKES — {token} {direction} at {leverage}x, $400 margin. Score {score}/{MIN_SCORE}. SM {sm_pct:.1f}% with {dominant.get('trader_count', 0)} traders."
    output["entry"] = entry
    output["dsl_state"] = dsl_state

    # Update state
    state["entries_today"] += 1
    state["last_entry_time"] = now.isoformat()
    state["last_asset"] = token
    state["last_direction"] = direction
    if "asset_cooldowns" not in state:
        state["asset_cooldowns"] = {}
    state["asset_cooldowns"][token] = now.isoformat()
    save_state(state)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
