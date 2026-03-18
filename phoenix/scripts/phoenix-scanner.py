#!/usr/bin/env python3
# Senpi PHOENIX Scanner v1.0.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PHOENIX v1.0 — Contribution Velocity Scanner.

First principles: enter when smart money interest in an asset is ACCELERATING,
before the price move catches up.

Every scanner in the zoo watches rank or position data. None use the
`contribution_pct_change_4h` field from `leaderboard_get_markets` — Senpi's
server-side calculation of how fast an asset's share of top trader profits
is growing. This is SM interest velocity, pre-computed, and nobody reads it.

PHOENIX is the simplest possible scanner:
- One API call: `leaderboard_get_markets`
- One key field: `contribution_pct_change_4h`
- One thesis: if SM profit concentration in an asset is growing fast,
  the price move hasn't fully happened yet

No scan history. No local state. No momentum events. No multi-scan climbing.
Just: "which assets are seeing the fastest growth in SM profit share right now?"

The contribution_pct_change_4h field measures:
- How much an asset's pct_of_top_traders_gain changed over 4 hours
- High positive value = SM is piling into this asset's profits
- Negative value = SM is rotating out
- This is VELOCITY of SM interest, not position

Why this works: rank climbing (Orca) lags contribution velocity. An asset's
contribution can surge before its rank changes, because contribution measures
profit growth while rank measures relative position. The contribution spike
IS the leading indicator that rank will follow.

Expected: 5-10 signals per day. More active than RAPTOR, less than Orca.
Single API call per scan. Runs every 90 seconds.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phoenix_config as cfg

# ─── Constants ────────────────────────────────────────────────

MAX_LEVERAGE = 10
MIN_LEVERAGE = 5
MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6
XYZ_BANNED = True

# Contribution velocity thresholds
MIN_CONTRIB_CHANGE_4H = 5.0          # Minimum 4h contribution change (%)
HIGH_CONTRIB_CHANGE_4H = 15.0        # High acceleration threshold
EXTREME_CONTRIB_CHANGE_4H = 30.0     # Extreme — SM is flooding in

# Leaderboard position gates
MIN_RANK = 6                          # Not already at the top
MAX_RANK = 40                         # Not too deep (has some SM attention)
MIN_CONTRIBUTION_PCT = 1.0            # Meaningful share of SM gains
MIN_TRADER_COUNT = 30                 # Broad SM base, not a single whale
MIN_PRICE_CHG_ALIGNMENT = True        # 4H price must agree with SM direction

# DSL
PHOENIX_DSL_TIERS = [
    {"triggerPct": 5,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 65, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 80, "consecutiveBreachesRequired": 1},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

PHOENIX_STAGNATION_TP = {"enabled": True, "roeMin": 8, "hwStaleMin": 40}


# ─── Fetch and Score ──────────────────────────────────────────

def fetch_and_score():
    """Single API call. Score every asset by contribution velocity."""
    data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not data or not data.get("success"):
        return None, []

    markets_data = data.get("data", data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", markets_data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", [])

    signals = []

    for i, m in enumerate(markets_data):
        if not isinstance(m, dict):
            continue

        token = m.get("token", "")
        dex = m.get("dex", "")
        rank = i + 1
        direction = m.get("direction", "").upper()
        contribution = float(m.get("pct_of_top_traders_gain", 0))
        contrib_change = float(m.get("contribution_pct_change_4h", 0))
        price_chg_4h = float(m.get("token_price_change_pct_4h", 0) or 0)
        trader_count = int(m.get("trader_count", 0))
        max_lev = int(m.get("max_leverage", 0))

        # ─── Hard gates ───

        # XYZ ban
        if XYZ_BANNED and (dex.lower() == "xyz" or token.lower().startswith("xyz:")):
            continue

        # Leverage floor
        if max_lev < MIN_LEVERAGE:
            continue

        # Rank window
        if rank < MIN_RANK or rank > MAX_RANK:
            continue

        # Minimum contribution
        if contribution < MIN_CONTRIBUTION_PCT:
            continue

        # Minimum trader count
        if trader_count < MIN_TRADER_COUNT:
            continue

        # THE KEY GATE: contribution velocity must be positive and above threshold
        if contrib_change < MIN_CONTRIB_CHANGE_4H:
            continue

        # 4H price must align with SM direction
        if MIN_PRICE_CHG_ALIGNMENT:
            if direction == "LONG" and price_chg_4h < 0:
                continue
            if direction == "SHORT" and price_chg_4h > 0:
                continue

        # ─── Scoring ───

        score = 0
        reasons = []

        # Contribution velocity (the core signal)
        if contrib_change >= EXTREME_CONTRIB_CHANGE_4H:
            score += 5
            reasons.append(f"EXTREME_VELOCITY +{contrib_change:.1f}% 4h contrib change")
        elif contrib_change >= HIGH_CONTRIB_CHANGE_4H:
            score += 3
            reasons.append(f"HIGH_VELOCITY +{contrib_change:.1f}% 4h contrib change")
        else:
            score += 2
            reasons.append(f"CONTRIB_VELOCITY +{contrib_change:.1f}% 4h contrib change")

        # Contribution magnitude (large share of SM profits)
        if contribution >= 10:
            score += 2
            reasons.append(f"DOMINANT_SM {contribution:.1f}% of top trader gains")
        elif contribution >= 5:
            score += 1
            reasons.append(f"STRONG_SM {contribution:.1f}% of top trader gains")

        # Rank position (sweet spot: rising but not peaked)
        if 10 <= rank <= 20:
            score += 2
            reasons.append(f"SWEET_SPOT rank #{rank}")
        elif 6 <= rank < 10:
            score += 1
            reasons.append(f"APPROACHING_TOP rank #{rank}")
        elif 20 < rank <= 30:
            score += 1
            reasons.append(f"DEEP_RISER rank #{rank}")

        # Trader depth (more traders = more reliable signal)
        if trader_count >= 150:
            score += 2
            reasons.append(f"MASSIVE_SM {trader_count} traders")
        elif trader_count >= 80:
            score += 1
            reasons.append(f"DEEP_SM {trader_count} traders")

        # Price hasn't caught up yet (the alpha window)
        if abs(price_chg_4h) < 1.5:
            score += 2
            reasons.append(f"PRICE_LAG only {price_chg_4h:+.1f}% vs {contrib_change:+.1f}% contrib")
        elif abs(price_chg_4h) < 3:
            score += 1
            reasons.append(f"EARLY_MOVE {price_chg_4h:+.1f}% price, {contrib_change:+.1f}% contrib")

        # Velocity vs price divergence (the strongest signal)
        # When contribution is growing 5x+ faster than price, SM knows something the market doesn't
        if abs(price_chg_4h) > 0.1:
            velocity_ratio = contrib_change / abs(price_chg_4h)
            if velocity_ratio >= 10:
                score += 2
                reasons.append(f"EXTREME_DIVERGENCE {velocity_ratio:.0f}x contrib/price ratio")
            elif velocity_ratio >= 5:
                score += 1
                reasons.append(f"DIVERGENCE {velocity_ratio:.1f}x contrib/price ratio")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "score": score,
            "reasons": reasons,
            "leaderboard": {
                "rank": rank,
                "contribution": contribution,
                "contribution_change_4h": contrib_change,
                "price_chg_4h": price_chg_4h,
                "trader_count": trader_count,
                "max_leverage": max_lev,
            },
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return len(markets_data), signals


# ─── DSL State Builder ───────────────────────────────────────

def build_dsl_state_template(asset, direction, score):
    """Build DSL state. Phoenix trades are medium-high conviction."""
    if score >= 12:
        timeout, weak_peak, dead_weight, floor_roe = 60, 25, 20, -25
    elif score >= 9:
        timeout, weak_peak, dead_weight, floor_roe = 45, 20, 15, -22
    else:
        timeout, weak_peak, dead_weight, floor_roe = 30, 15, 10, -20

    return {
        "active": True,
        "asset": asset,
        "direction": direction,
        "score": score,
        "phase": 1,
        "highWaterPrice": None,
        "highWaterRoe": None,
        "currentTierIndex": -1,
        "consecutiveBreaches": 0,
        "lockMode": "pct_of_high_water",
        "phase2TriggerRoe": 5,
        "phase1": {
            "enabled": True,
            "retraceThreshold": 0.03,
            "consecutiveBreachesRequired": 3,
            "phase1MaxMinutes": timeout,
            "weakPeakCutMinutes": weak_peak,
            "deadWeightCutMin": dead_weight,
            "absoluteFloorRoe": floor_roe,
            "weakPeakCut": {"enabled": True, "intervalInMinutes": weak_peak, "minValue": 3.0},
        },
        "phase2": {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": PHOENIX_DSL_TIERS,
        "stagnationTp": PHOENIX_STAGNATION_TP,
        "execution": {"phase1SlOrderType": "MARKET", "phase2SlOrderType": "MARKET", "breachCloseOrderType": "MARKET"},
        "_phoenix_version": "1.0.1",
        "_note": "Generated by phoenix-scanner.py. Do not modify.",
    }


# ─── Per-Asset Cooldown ──────────────────────────────────────

COOLDOWN_FILE = os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"),
    "skills", "phoenix-strategy", "state", "asset-cooldowns.json"
)


def is_asset_cooled_down(asset, cooldown_minutes=90):
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE) as f:
                cooldowns = json.load(f)
            if asset in cooldowns:
                elapsed = (time.time() - cooldowns[asset].get("exitTimestamp", 0)) / 60
                return elapsed < cooldown_minutes
    except (json.JSONDecodeError, IOError):
        pass
    return False


# ─── Main ─────────────────────────────────────────────────────

def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"gate={tc['gate']}"})
        return

    max_entries = config.get("risk", {}).get("maxEntriesPerDay", MAX_DAILY_ENTRIES)
    if tc.get("entries", 0) >= max_entries:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if len(positions) >= MAX_POSITIONS:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"max positions ({len(positions)}/{MAX_POSITIONS})"})
        return

    active_coins = {p["coin"] for p in positions}
    cooldown_min = config.get("risk", {}).get("cooldownMinutes", 90)

    # Single API call — score everything
    markets_count, signals = fetch_and_score()
    if markets_count is None:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "failed to fetch markets"})
        return

    # Filter: already holding, cooled down
    signals = [s for s in signals if s["token"] not in active_coins]
    signals = [s for s in signals if not is_asset_cooled_down(s["token"], cooldown_min)]

    # Apply minimum score
    min_score = config.get("entry", {}).get("minScore", 7)
    signals = [s for s in signals if s["score"] >= min_score]

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{markets_count} markets scanned, no velocity confluence"})
        return

    best = signals[0]

    # Margin scaling
    if best["score"] >= 12:
        margin_pct = 0.30
    elif best["score"] >= 9:
        margin_pct = 0.25
    else:
        margin_pct = 0.20
    margin = round(account_value * margin_pct, 2)

    leverage = min(best["leaderboard"]["max_leverage"], MAX_LEVERAGE)

    cfg.output({
        "status": "ok",
        "signal": best,
        "entry": {
            "coin": best["token"],
            "direction": best["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
        },
        "dslState": build_dsl_state_template(best["token"], best["direction"], best["score"]),
        "constraints": {
            "minLeverage": MIN_LEVERAGE,
            "maxLeverage": MAX_LEVERAGE,
            "maxPositions": MAX_POSITIONS,
            "stagnationTp": PHOENIX_STAGNATION_TP,
            "_dslNote": "Use dslState as the DSL state file. Do NOT merge with dsl-profile.json.",
        },
        "allSignals": signals[:5],
        "marketsScanned": markets_count,
    })


if __name__ == "__main__":
    run()
