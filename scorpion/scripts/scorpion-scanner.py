#!/usr/bin/env python3
"""
SCORPION v2.0 — Altcoin Swarm Hunter

PURPOSE: Detect coordinated altcoin risk-off events (swarms) where 5+
non-major altcoins simultaneously attract SM SHORT concentration, then
trade the highest-conviction target within the swarm.

THESIS: When SM goes risk-off, altcoins dump in a correlated swarm.
Top SM traders make the most money on altcoin SHORTs (LIT, TAO, MON,
FARTCOIN, VVV, ZRO), not on BTC/ETH. No current agent detects the
*pattern* of simultaneous altcoin SM convergence. That pattern is the
highest-conviction signal in the Hyperfeed.

Evidence from April 3, 2026: Trader 0x039c was up 99.8% in 4 hours
with 33 positions, biggest winners all altcoin SHORTs: LIT +$11.6K,
SOL +$10K, FARTCOIN +$6.3K, TAO +$4.9K.

DESIGN PRINCIPLES:
1. Detect the SWARM first — count altcoins with SM SHORT/LONG >2%
2. Only enter when swarm count >= 5 (correlated risk event confirmed)
3. Pick the BEST target from the swarm (highest SM + price confirmation)
4. ONE position at a time, $350 margin, 5x leverage (altcoin max)
5. FEE_OPTIMIZED_LIMIT orders, wide DSL, let positions breathe
6. 3 entries per day, 90-min cooldown, 120-min per-asset cooldown

WHAT MAKES THIS DIFFERENT FROM COBRA:
- Cobra trades the #1 SM asset (usually a major like BTC/ETH/HYPE)
- Scorpion trades the #1 ALTCOIN within a confirmed swarm pattern
- Scorpion requires a meta-signal (swarm detection) before evaluating
  individual assets — this filters out noise and only trades during
  genuine coordinated risk events

SIGNALS (1 API call):
1. leaderboard_get_markets (limit=100) → Full market scan
   - Count altcoins with SM concentration >2% in same direction
   - If swarm count >= 5: score individual targets
   - Pick best target by combined SM + price + trader count

SCORING (max 8 points):
- Swarm Size: >=7 alts = +2, >=5 = +1 (meta-signal)
- SM Concentration: >10% = +2, >5% = +1, >2% = +0
- Price Confirmation: >1% in direction = +2, >0.5% = +1
- Trader Count: >=50 = +1
- Contribution Velocity: >3% = +1

MIN_SCORE: 5 (requires swarm + strong individual signal)
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
PER_ASSET_COOLDOWN_MINUTES = 120
MIN_SCORE = 5
MARGIN_AMOUNT = 350
MAX_POSITIONS = 1
LEVERAGE = 5  # Altcoins typically max at 3-5x

# Major assets — EXCLUDED from swarm detection (Cobra's territory)
MAJOR_ASSETS = {"BTC", "ETH", "SOL", "HYPE"}

# Minimum SM concentration for an altcoin to count as part of the swarm
MIN_SWARM_SM_PCT = 2.0

# Minimum number of altcoins in swarm to confirm a coordinated event
MIN_SWARM_COUNT = 5

# XYZ equities banned from trading (but counted in swarm for context)
XYZ_BANNED_TRADING = True

# State file
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "config", "scorpion-state.json")

# Leverage overrides for specific altcoins with higher max leverage
LEVERAGE_OVERRIDES = {
    "AVAX": 10, "DOGE": 10, "LINK": 10, "XRP": 10,
    "ADA": 10, "NEAR": 10, "DOT": 10, "UNI": 10,
    "AAVE": 10, "kPEPE": 10, "FARTCOIN": 10, "LTC": 10,
}


def load_state():
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
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_cooldown(state, cooldown_min):
    last_time = state.get("last_entry_time")
    if not last_time:
        return True, 0
    try:
        last_dt = datetime.fromisoformat(last_time)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds() / 60
        remaining = max(0, cooldown_min - elapsed)
        return elapsed >= cooldown_min, round(remaining, 1)
    except (ValueError, TypeError):
        return True, 0


def check_asset_cooldown(state, asset):
    cooldowns = state.get("asset_cooldowns", {})
    last_time = cooldowns.get(asset)
    if not last_time:
        return True, 0
    try:
        last_dt = datetime.fromisoformat(last_time)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds() / 60
        remaining = max(0, PER_ASSET_COOLDOWN_MINUTES - elapsed)
        return elapsed >= PER_ASSET_COOLDOWN_MINUTES, round(remaining, 1)
    except (ValueError, TypeError):
        return True, 0


def detect_swarm(markets):
    """
    Detect coordinated altcoin swarm events.

    Scans all non-major assets for SM concentration >2%.
    Groups by dominant direction (SHORT vs LONG).
    Returns the swarm info if count >= MIN_SWARM_COUNT.
    """
    short_swarm = []
    long_swarm = []

    for m in markets:
        token = m.get("token", "")
        dex = m.get("dex", "")
        direction = m.get("direction", "")
        sm_pct = m.get("pct_of_top_traders_gain", 0)

        # Skip majors — those are Cobra's territory
        if token in MAJOR_ASSETS:
            continue

        # Skip XYZ for swarm counting too — we want crypto altcoins
        if dex == "xyz":
            continue

        if sm_pct < MIN_SWARM_SM_PCT:
            continue

        entry = {
            "token": token,
            "direction": direction,
            "sm_pct": sm_pct,
            "contrib_change": m.get("contribution_pct_change_4h", 0) or 0,
            "price_change_4h": m.get("token_price_change_pct_4h", 0),
            "trader_count": m.get("trader_count", 0),
            "max_leverage": m.get("max_leverage", 3),
        }

        if direction == "short":
            short_swarm.append(entry)
        else:
            long_swarm.append(entry)

    # Pick the dominant swarm direction
    if len(short_swarm) >= len(long_swarm) and len(short_swarm) >= MIN_SWARM_COUNT:
        return "SHORT", short_swarm
    elif len(long_swarm) >= MIN_SWARM_COUNT:
        return "LONG", long_swarm
    else:
        return None, []


def score_target(target, swarm_size):
    """
    Score an individual altcoin target within a confirmed swarm.

    Args:
        target: dict with token info from swarm detection
        swarm_size: number of altcoins in the swarm

    Returns: (score, breakdown)
    """
    token = target["token"]
    direction = target["direction"]
    sm_pct = target["sm_pct"]
    contrib = target["contrib_change"]
    price_4h = target["price_change_4h"]
    trader_count = target["trader_count"]

    score = 0
    breakdown = {
        "token": token,
        "direction": direction.upper(),
        "sm_pct": round(sm_pct, 2),
        "contrib_change": round(contrib, 2),
        "price_change_4h": round(price_4h, 3),
        "trader_count": trader_count,
        "swarm_size": swarm_size,
    }

    # 1. Swarm Size meta-signal (max 2 pts)
    if swarm_size >= 7:
        score += 2
        breakdown["swarm_score"] = f"+2 (MASSIVE swarm: {swarm_size} altcoins)"
    elif swarm_size >= 5:
        score += 1
        breakdown["swarm_score"] = f"+1 (CONFIRMED swarm: {swarm_size} altcoins)"

    # 2. SM Concentration on this target (max 2 pts)
    if sm_pct >= 10:
        score += 2
        breakdown["sm_score"] = f"+2 (DOMINANT {sm_pct:.1f}%)"
    elif sm_pct >= 5:
        score += 1
        breakdown["sm_score"] = f"+1 (STRONG {sm_pct:.1f}%)"
    else:
        breakdown["sm_score"] = f"+0 (IN SWARM {sm_pct:.1f}%)"

    # 3. Price Confirmation in direction (max 2 pts)
    confirms = False
    if direction == "short" and price_4h < -1.0:
        score += 2
        confirms = True
        breakdown["price_score"] = f"+2 (STRONG CONFIRM {price_4h:+.2f}%)"
    elif direction == "short" and price_4h < -0.5:
        score += 1
        confirms = True
        breakdown["price_score"] = f"+1 (CONFIRMS {price_4h:+.2f}%)"
    elif direction == "long" and price_4h > 1.0:
        score += 2
        confirms = True
        breakdown["price_score"] = f"+2 (STRONG CONFIRM {price_4h:+.2f}%)"
    elif direction == "long" and price_4h > 0.5:
        score += 1
        confirms = True
        breakdown["price_score"] = f"+1 (CONFIRMS {price_4h:+.2f}%)"
    else:
        breakdown["price_score"] = f"+0 (NOT CONFIRMING {price_4h:+.2f}%)"

    # 4. Trader Count depth (1 pt)
    if trader_count >= 50:
        score += 1
        breakdown["depth_score"] = f"+1 (DEEP: {trader_count} traders)"
    else:
        breakdown["depth_score"] = f"+0 ({trader_count} traders, need 50)"

    # 5. Contribution Velocity (1 pt)
    abs_contrib = abs(contrib)
    if abs_contrib >= 3:
        score += 1
        breakdown["contrib_score"] = f"+1 (VELOCITY {contrib:+.1f}%)"
    else:
        breakdown["contrib_score"] = f"+0 (SLOW {contrib:+.1f}%)"

    breakdown["total_score"] = score
    breakdown["min_score"] = MIN_SCORE
    breakdown["passes"] = score >= MIN_SCORE

    return score, breakdown


def pick_best_target(swarm, state):
    """
    From a confirmed swarm, pick the best tradeable target.
    Scores all candidates and returns the highest-scoring one
    that isn't on per-asset cooldown.
    """
    swarm_size = len(swarm)
    candidates = []

    for target in swarm:
        token = target["token"]

        # Check per-asset cooldown
        clear, _ = check_asset_cooldown(state, token)
        if not clear:
            continue

        score, breakdown = score_target(target, swarm_size)
        if score >= MIN_SCORE:
            candidates.append({
                "target": target,
                "score": score,
                "breakdown": breakdown,
            })

    if not candidates:
        return None

    # Sort by score descending, then by SM concentration as tiebreaker
    candidates.sort(key=lambda c: (c["score"], c["target"]["sm_pct"]),
                    reverse=True)
    return candidates[0]


def generate_entry(token, direction, leverage, margin):
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
        "phase1MaxMinutes": 45,
        "deadWeightCutMin": 45,
        "phase1": {
            "maxLossPct": 20.0,
            "retraceThreshold": 10,
            "enabled": True
        },
        "phase2": {
            "enabled": True,
            "tiers": [
                {"triggerPct": 5, "lockHwPct": 20},
                {"triggerPct": 10, "lockHwPct": 40},
                {"triggerPct": 15, "lockHwPct": 55},
                {"triggerPct": 20, "lockHwPct": 70},
                {"triggerPct": 30, "lockHwPct": 82},
                {"triggerPct": 50, "lockHwPct": 90}
            ]
        },
        "hardTimeout": {
            "enabled": True,
            "intervalInMinutes": 240
        },
        "weakPeakCut": {
            "enabled": True,
            "intervalInMinutes": 90,
            "minValue": 3.0
        },
        "deadWeightCut": {
            "enabled": True,
            "intervalInMinutes": 45
        }
    }


def main():
    state = load_state()
    now = datetime.now(timezone.utc)

    output = {
        "scanner": "scorpion",
        "version": "2.0",
        "timestamp": now.isoformat(),
        "action": "NONE",
        "reason": "",
        "score": 0,
        "swarm": {},
        "breakdown": {},
        "entry": None,
        "dsl_state": None,
        "status": {
            "entries_today": state["entries_today"],
            "max_entries": MAX_ENTRIES_PER_DAY,
            "last_asset": state.get("last_asset"),
        }
    }

    # Gate 1: Daily cap
    if state["entries_today"] >= MAX_ENTRIES_PER_DAY:
        output["reason"] = f"Daily cap reached ({state['entries_today']}/{MAX_ENTRIES_PER_DAY})"
        print(json.dumps(output, indent=2))
        return

    # Gate 2: Global cooldown
    clear, remaining = check_cooldown(state, COOLDOWN_MINUTES)
    if not clear:
        output["reason"] = f"Global cooldown: {remaining} min remaining"
        print(json.dumps(output, indent=2))
        return

    # Parse input
    try:
        if not sys.stdin.isatty():
            input_data = json.load(sys.stdin)
        else:
            output["reason"] = "AWAITING_DATA"
            output["instructions"] = [
                "1. Call senpi:leaderboard_get_markets with limit=100",
                "2. Pipe the full JSON to this scanner via stdin",
                "3. Scanner detects altcoin swarm patterns and scores targets",
                "4. If action=ENTER, call senpi:create_position with the entry block"
            ]
            print(json.dumps(output, indent=2))
            return
    except json.JSONDecodeError:
        output["reason"] = "Invalid JSON input"
        print(json.dumps(output, indent=2))
        return

    # Extract market data
    data = input_data.get("data", input_data) if "data" in input_data else input_data
    markets_list = data.get("markets", {}).get("markets", [])
    if not markets_list:
        output["reason"] = "No market data in input"
        print(json.dumps(output, indent=2))
        return

    # ================================================================
    # SWARM DETECTION
    # ================================================================
    swarm_direction, swarm = detect_swarm(markets_list)

    if swarm_direction is None:
        # Count what we found for diagnostics
        short_count = sum(1 for m in markets_list
                         if m.get("token") not in MAJOR_ASSETS
                         and m.get("dex", "") != "xyz"
                         and m.get("direction") == "short"
                         and m.get("pct_of_top_traders_gain", 0) >= MIN_SWARM_SM_PCT)
        long_count = sum(1 for m in markets_list
                        if m.get("token") not in MAJOR_ASSETS
                        and m.get("dex", "") != "xyz"
                        and m.get("direction") == "long"
                        and m.get("pct_of_top_traders_gain", 0) >= MIN_SWARM_SM_PCT)

        output["reason"] = (
            f"No swarm detected. SHORT alts: {short_count}, "
            f"LONG alts: {long_count} (need {MIN_SWARM_COUNT}+)"
        )
        output["swarm"] = {
            "detected": False,
            "short_count": short_count,
            "long_count": long_count,
            "min_required": MIN_SWARM_COUNT,
        }
        print(json.dumps(output, indent=2))
        return

    # Swarm confirmed
    swarm_tokens = [s["token"] for s in swarm[:10]]
    output["swarm"] = {
        "detected": True,
        "direction": swarm_direction,
        "count": len(swarm),
        "top_tokens": swarm_tokens,
    }

    # ================================================================
    # TARGET SELECTION
    # ================================================================
    best = pick_best_target(swarm, state)

    if best is None:
        output["reason"] = (
            f"Swarm detected ({len(swarm)} {swarm_direction} alts) "
            f"but no target reached MIN_SCORE {MIN_SCORE} or all on cooldown"
        )
        # Show top 3 candidates for diagnostics
        swarm_size = len(swarm)
        diagnostics = []
        for t in swarm[:3]:
            s, b = score_target(t, swarm_size)
            diagnostics.append({
                "token": t["token"],
                "score": s,
                "sm_pct": round(t["sm_pct"], 2),
                "price_4h": round(t["price_change_4h"], 2),
            })
        output["swarm"]["top_candidates"] = diagnostics
        print(json.dumps(output, indent=2))
        return

    # ================================================================
    # ENTRY SIGNAL
    # ================================================================
    target = best["target"]
    token = target["token"]
    direction = target["direction"].upper()
    leverage = LEVERAGE_OVERRIDES.get(token, min(LEVERAGE, target["max_leverage"]))

    entry = generate_entry(token, direction, leverage, MARGIN_AMOUNT)
    dsl_state = generate_dsl_state(token, direction, leverage)

    output["action"] = "ENTER"
    output["score"] = best["score"]
    output["breakdown"] = best["breakdown"]
    output["reason"] = (
        f"SCORPION STRIKES — {swarm_direction} swarm ({len(swarm)} alts). "
        f"Best target: {token} {direction} at {leverage}x, $350 margin. "
        f"Score {best['score']}/{MIN_SCORE}. SM {target['sm_pct']:.1f}%."
    )
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
