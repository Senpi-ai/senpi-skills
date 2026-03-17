#!/usr/bin/env python3
# Senpi RAPTOR Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""RAPTOR v1.0 — Momentum Event Confluence Scanner.

First principles: enter a position at the moment smart money starts making
real money on it, before the price move is fully priced in.

RAPTOR combines two Hyperfeed signals that no other scanner uses together:

1. TIER 2 MOMENTUM EVENTS ($5.5M+ delta PnL threshold)
   - 155/day vs 5,123 Tier 1 events — rare enough to be signal, not noise
   - Filtered by trader quality: TCS ELITE/RELIABLE + TRP SNIPER/AGGRESSIVE
   - Filtered by concentration: >0.7 (trader's gains are concentrated, not diversified)
   - Further filtered: ~30-40 qualify per day from quality traders

2. SM LEADERBOARD CONFIRMATION
   - The asset from the momentum event must also be climbing the SM leaderboard
   - contribution_pct_change_4h must be positive (SM interest is building)
   - Asset must not already be in Top 5 (move is over)

When both fire on the same asset: a proven, high-conviction trader just crossed
$5.5M in profits on a position, AND smart money concentration is building in that
asset across the broader trader base. That's two independent confirmations from
different data sources.

Expected: 3-5 actionable signals per day. Patient, high-conviction, data-rich.

Uses: leaderboard_get_momentum_events + leaderboard_get_markets (2 API calls per scan)
Runs every 90 seconds.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raptor_config as cfg

# ─── Hardcoded Constants ─────────────────────────────────────

MAX_LEVERAGE = 10
MIN_LEVERAGE = 5
MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6
XYZ_BANNED = True

# Momentum event filters
MOMENTUM_TIER = 2                    # $5.5M+ delta PnL (155/day vs 5,123 Tier 1)
MOMENTUM_LOOKBACK_MINUTES = 10       # Only recent events (avoid stale)
MIN_CONCENTRATION = 0.5              # Trader's gains must be concentrated (0-1)

# Trader quality filters (TCS/TAS/TRP from momentum events)
QUALITY_TCS = {"Elite", "Reliable"}  # Consistent performers only
QUALITY_TRP = {"Sniper", "Aggressive", "Balanced"}  # Active risk-takers
# TAS not filtered — all activity levels welcome if TCS/TRP pass

# Leaderboard confirmation
MIN_CONTRIBUTION_PCT = 1.0           # Asset must have meaningful SM concentration
MAX_RANK_FOR_ENTRY = 30              # Don't enter assets already at the top
MIN_CONTRIBUTION_CHANGE_4H = 0       # Must be positive (SM building, not leaving)
MIN_TRADER_COUNT = 20                # Enough SM traders for the signal to be meaningful

# DSL
RAPTOR_DSL_TIERS = [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

RAPTOR_STAGNATION_TP = {"enabled": True, "roeMin": 10, "hwStaleMin": 45}


# ─── Fetch Momentum Events ───────────────────────────────────

def fetch_momentum_events():
    """Fetch recent Tier 2 momentum events from Hyperfeed."""
    now = datetime.now(timezone.utc)
    from_time = (now - timedelta(minutes=MOMENTUM_LOOKBACK_MINUTES)).isoformat()
    to_time = now.isoformat()

    data = cfg.mcporter_call("leaderboard_get_momentum_events",
                              tier=MOMENTUM_TIER,
                              limit=50,
                              **{"from": from_time, "to": to_time})
    if not data or not data.get("success"):
        return []

    events_data = data.get("data", data)
    if isinstance(events_data, dict):
        events_data = events_data.get("events", events_data)
    if isinstance(events_data, dict):
        events = events_data.get("events", [])
    elif isinstance(events_data, list):
        events = events_data
    else:
        return []

    return events


def filter_quality_events(events):
    """Filter momentum events by trader quality and concentration."""
    qualified = []

    for event in events:
        if not isinstance(event, dict):
            continue

        # Concentration filter
        concentration = float(event.get("concentration", 0))
        if concentration < MIN_CONCENTRATION:
            continue

        # Trader quality filter
        tags = event.get("trader_tags", {})
        if not isinstance(tags, dict):
            continue

        tcs = tags.get("tcs", "").strip()
        trp = tags.get("trp", "").strip()

        # Normalize case (API returns mixed case)
        tcs_match = any(tcs.lower() == q.lower() for q in QUALITY_TCS)
        trp_match = any(trp.lower() == q.lower() for q in QUALITY_TRP)

        if not tcs_match or not trp_match:
            continue

        # Extract assets from top_positions
        top_positions = event.get("top_positions", [])
        if not top_positions:
            continue

        qualified.append({
            "trader_id": event.get("trader_id", ""),
            "tier": event.get("tier", 0),
            "delta_pnl": float(event.get("delta_pnl", 0)),
            "concentration": concentration,
            "tcs": tcs,
            "tas": tags.get("tas", ""),
            "trp": trp,
            "detected_at": event.get("detected_at", ""),
            "positions": top_positions,
        })

    return qualified


# ─── Fetch SM Leaderboard ────────────────────────────────────

def fetch_leaderboard():
    """Fetch current SM market concentration from Hyperfeed."""
    data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not data or not data.get("success"):
        return {}

    markets_data = data.get("data", data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", markets_data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", [])

    # Build lookup by token
    leaderboard = {}
    for i, m in enumerate(markets_data):
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        dex = m.get("dex", "")

        # XYZ ban
        if XYZ_BANNED and (dex.lower() == "xyz" or token.lower().startswith("xyz:")):
            continue

        key = f"{dex}:{token}" if dex else token
        leaderboard[token] = {
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": m.get("direction", ""),
            "contribution": float(m.get("pct_of_top_traders_gain", 0)),
            "contribution_change_4h": float(m.get("contribution_pct_change_4h", 0)),
            "price_chg_4h": float(m.get("token_price_change_pct_4h", 0) or 0),
            "trader_count": int(m.get("trader_count", 0)),
            "max_leverage": int(m.get("max_leverage", 0)),
        }

    return leaderboard


# ─── Confluence Detection ─────────────────────────────────────

def find_confluence_signals(quality_events, leaderboard):
    """Cross-reference quality momentum events with SM leaderboard."""
    signals = []
    seen_assets = set()

    for event in quality_events:
        for pos in event["positions"]:
            asset = pos.get("market", "")
            if not asset or asset in seen_assets:
                continue

            # Check if asset is on the leaderboard
            lb = leaderboard.get(asset)
            if not lb:
                continue

            # Leaderboard confirmation gates
            if lb["rank"] <= 5:
                continue  # Already at the top — move is over
            if lb["rank"] > MAX_RANK_FOR_ENTRY:
                continue  # Too deep — not enough SM attention yet
            if lb["contribution"] < MIN_CONTRIBUTION_PCT:
                continue  # Too small a share of SM gains
            if lb["contribution_change_4h"] < MIN_CONTRIBUTION_CHANGE_4H:
                continue  # SM leaving, not building
            if lb["trader_count"] < MIN_TRADER_COUNT:
                continue  # Not enough traders for reliable signal
            if lb["max_leverage"] < MIN_LEVERAGE:
                continue  # Can't trade with meaningful leverage

            # Direction confirmation: momentum event position direction must match leaderboard
            event_direction = pos.get("direction", "").upper()
            lb_direction = lb["direction"].upper()
            if event_direction and lb_direction and event_direction != lb_direction:
                continue  # Trader is long but SM consensus is short (or vice versa)

            direction = event_direction or lb_direction

            # Score
            score = 0
            reasons = []

            # Tier 2 momentum event from quality trader (base 4 pts)
            score += 4
            reasons.append(f"TIER2_MOMENTUM ${event['delta_pnl']:,.0f} from {event['tcs']}/{event['trp']} trader")

            # High concentration
            if event["concentration"] > 0.7:
                score += 1
                reasons.append(f"HIGH_CONCENTRATION {event['concentration']:.1%}")

            # SM leaderboard rank
            if lb["rank"] <= 10:
                score += 2
                reasons.append(f"SM_TOP10 rank #{lb['rank']}")
            elif lb["rank"] <= 20:
                score += 1
                reasons.append(f"SM_TOP20 rank #{lb['rank']}")

            # Contribution building
            if lb["contribution_change_4h"] > 5:
                score += 2
                reasons.append(f"SM_BUILDING_FAST +{lb['contribution_change_4h']:.1f}% 4h change")
            elif lb["contribution_change_4h"] > 0:
                score += 1
                reasons.append(f"SM_BUILDING +{lb['contribution_change_4h']:.1f}% 4h change")

            # Trader count depth
            if lb["trader_count"] >= 100:
                score += 1
                reasons.append(f"DEEP_SM {lb['trader_count']} traders")

            # Price hasn't fully moved yet (4h change still small)
            if abs(lb["price_chg_4h"]) < 3:
                score += 1
                reasons.append(f"EARLY_ENTRY price only {lb['price_chg_4h']:+.1f}% in 4h")

            # Position-level data
            pos_leverage = pos.get("leverage", 0)
            pos_delta = float(pos.get("delta_pnl", 0))
            if pos_leverage and pos_leverage >= 10:
                score += 1
                reasons.append(f"HIGH_LEVERAGE {pos_leverage}x on {asset}")

            seen_assets.add(asset)
            signals.append({
                "token": asset,
                "dex": lb.get("dex", ""),
                "direction": direction,
                "score": score,
                "reasons": reasons,
                "momentum": {
                    "trader_id": event["trader_id"],
                    "delta_pnl": event["delta_pnl"],
                    "concentration": event["concentration"],
                    "tcs": event["tcs"],
                    "tas": event["tas"],
                    "trp": event["trp"],
                    "position_leverage": pos_leverage,
                    "position_delta_pnl": pos_delta,
                },
                "leaderboard": {
                    "rank": lb["rank"],
                    "contribution": lb["contribution"],
                    "contribution_change_4h": lb["contribution_change_4h"],
                    "price_chg_4h": lb["price_chg_4h"],
                    "trader_count": lb["trader_count"],
                    "max_leverage": lb["max_leverage"],
                },
            })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ─── DSL State Builder ───────────────────────────────────────

def build_dsl_state_template(asset, direction, score):
    """Build DSL state file. Agent writes directly — no merging."""
    # Raptor entries are high conviction (dual-source confirmed)
    # Use generous timing — these are quality entries
    if score >= 10:
        timeout, weak_peak, dead_weight, floor_roe = 60, 30, 20, -30
    elif score >= 8:
        timeout, weak_peak, dead_weight, floor_roe = 45, 20, 15, -25
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
        "phase2TriggerRoe": 7,
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
        "tiers": RAPTOR_DSL_TIERS,
        "stagnationTp": RAPTOR_STAGNATION_TP,
        "execution": {"phase1SlOrderType": "MARKET", "phase2SlOrderType": "MARKET", "breachCloseOrderType": "MARKET"},
        "_raptor_version": "1.0",
        "_note": "Generated by raptor-scanner.py. Do not modify.",
    }


# ─── Per-Asset Cooldown ──────────────────────────────────────

COOLDOWN_FILE = os.path.join(
    os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace"),
    "skills", "raptor-strategy", "state", "asset-cooldowns.json"
)


def is_asset_cooled_down(asset, cooldown_minutes=120):
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
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": f"max positions ({len(positions)}/{MAX_POSITIONS})"})
        return

    active_coins = {p["coin"] for p in positions}
    cooldown_min = config.get("risk", {}).get("cooldownMinutes", 120)

    # Step 1: Fetch Tier 2 momentum events
    raw_events = fetch_momentum_events()
    if not raw_events:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"no Tier {MOMENTUM_TIER} momentum events in last {MOMENTUM_LOOKBACK_MINUTES}m"})
        return

    # Step 2: Filter by trader quality
    quality_events = filter_quality_events(raw_events)
    if not quality_events:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{len(raw_events)} Tier {MOMENTUM_TIER} events, none from quality traders"})
        return

    # Step 3: Fetch SM leaderboard
    leaderboard = fetch_leaderboard()
    if not leaderboard:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "failed to fetch leaderboard"})
        return

    # Step 4: Find confluence
    signals = find_confluence_signals(quality_events, leaderboard)

    # Filter: already holding, cooled down
    signals = [s for s in signals if s["token"] not in active_coins]
    signals = [s for s in signals if not is_asset_cooled_down(s["token"], cooldown_min)]

    # Apply minimum score
    min_score = config.get("entry", {}).get("minScore", 7)
    signals = [s for s in signals if s["score"] >= min_score]

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{len(quality_events)} quality events, {len(leaderboard)} markets, no confluence"})
        return

    best = signals[0]

    # Margin scaling by score
    if best["score"] >= 10:
        margin_pct = 0.35
    elif best["score"] >= 8:
        margin_pct = 0.30
    else:
        margin_pct = 0.25
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
            "stagnationTp": RAPTOR_STAGNATION_TP,
            "_dslNote": "Use dslState as the DSL state file. Do NOT merge with dsl-profile.json.",
        },
        "allSignals": signals[:5],
        "eventsScanned": len(raw_events),
        "qualityEvents": len(quality_events),
        "marketsScanned": len(leaderboard),
    })


if __name__ == "__main__":
    run()
