#!/usr/bin/env python3
# Senpi KOMODO Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""KOMODO v2.0 — Momentum Event Consensus.

COMPLETE REWRITE from v1.0. The v1.0 scanner used discovery_get_top_traders
to find historically good whales, then read their CURRENT OPEN POSITIONS.
This surfaced legacy positions (months-old shorts from $90k+) as "fresh
consensus" — the entire data source was wrong.

v2.0 uses leaderboard_get_momentum_events as the primary data source.
These are REAL-TIME threshold-crossing events that fire when a trader's
delta PnL crosses significance levels ($2M+/$5.5M+/$10M+) within a
4-hour rolling window. This captures ACTIONS, not stale positions.

Five-gate entry model:
  1. MOMENTUM EVENTS — 2+ recent events on same asset/direction within 60min
  2. TRADER QUALITY — filter by TCS (Elite/Reliable) and concentration
  3. MARKET CONFIRMATION — leaderboard_get_markets shows elevated SM count
  4. VOLUME CONFIRMATION — current volume alive (≥50% of 6h avg)
  5. REGIME FILTER — penalty (not block) for counter-trend entries

Enters WITH smart money momentum, not against it.

Runs every 5 minutes.
"""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import komodo_config as cfg


# ─── Gate 1: Momentum Event Fetching ─────────────────────────

def fetch_momentum_events(entry_cfg):
    """Fetch recent momentum events from the leaderboard.
    These are real-time threshold crossings — NOT stale positions."""
    mom_cfg = entry_cfg.get("momentumEvents", {})
    min_tier = mom_cfg.get("minTier", 1)
    lookback_min = mom_cfg.get("maxLookbackMinutes", 60)

    # Time range: last N minutes
    now = datetime.now(timezone.utc)
    from_time = (now - timedelta(minutes=lookback_min)).isoformat()

    data = cfg.mcporter_call("leaderboard_get_momentum_events",
                              tier=min_tier, limit=200,
                              **{"from": from_time})
    if not data or not data.get("success"):
        return []

    events = data.get("data", data)
    if isinstance(events, dict):
        events = events.get("events", events.get("data", []))
    if not isinstance(events, list):
        return []

    return events


def filter_quality_events(events, entry_cfg):
    """Gate 2: Filter events by trader quality (TCS, TAS, concentration)."""
    mom_cfg = entry_cfg.get("momentumEvents", {})
    quality_cfg = mom_cfg.get("traderQuality", {})

    allowed_tcs = set(quality_cfg.get("allowedTCS", ["Elite", "Reliable"]))
    allowed_tas = set(quality_cfg.get("allowedTAS", ["Tactical", "Patient", "Active"]))
    blocked_tas = set(quality_cfg.get("blockedTAS", ["Degen"]))
    min_concentration = quality_cfg.get("minConcentration", 0.4)

    filtered = []
    for event in events:
        if not isinstance(event, dict):
            continue
        tags = event.get("trader_tags", {})
        if isinstance(tags, str):
            # Handle case where tags is a JSON string
            try:
                import json
                tags = json.loads(tags)
            except Exception:
                tags = {}

        tcs = tags.get("TCS", tags.get("tcs", ""))
        tas = tags.get("TAS", tags.get("tas", ""))

        # TCS filter: only Elite/Reliable
        if allowed_tcs and tcs and tcs not in allowed_tcs:
            continue

        # TAS filter: block Degen
        if tas in blocked_tas:
            continue

        # Concentration filter
        concentration = float(event.get("concentration", 0))
        if concentration < min_concentration:
            continue

        filtered.append(event)

    return filtered


def build_consensus(events, entry_cfg):
    """Group events by asset+direction to find consensus.
    Returns consensus groups with 2+ events on the same side."""
    mom_cfg = entry_cfg.get("momentumEvents", {})
    min_events = mom_cfg.get("minEventsPerAsset", 2)

    # Extract positions from events and group by asset+direction
    votes = {}  # key: "BTC:LONG" -> list of events

    for event in events:
        top_positions = event.get("top_positions", [])
        if isinstance(top_positions, str):
            try:
                import json
                top_positions = json.loads(top_positions)
            except Exception:
                top_positions = []

        trader_id = event.get("trader_id", event.get("address", ""))
        tier = int(event.get("tier", 1))
        concentration = float(event.get("concentration", 0))

        for pos in top_positions:
            market = pos.get("market", pos.get("coin", ""))
            direction = pos.get("direction", "").upper()
            delta_pnl = float(pos.get("delta_pnl", 0))

            if not market or not direction:
                continue

            # Normalize direction
            if direction in ("LONG", "BUY"):
                direction = "LONG"
            elif direction in ("SHORT", "SELL"):
                direction = "SHORT"
            else:
                continue

            key = f"{market}:{direction}"
            if key not in votes:
                votes[key] = {
                    "coin": market,
                    "direction": direction,
                    "events": [],
                    "traders": set(),
                    "totalTier": 0,
                    "totalConcentration": 0,
                }
            votes[key]["events"].append(event)
            votes[key]["traders"].add(trader_id)
            votes[key]["totalTier"] += tier
            votes[key]["totalConcentration"] += concentration

    # Filter to consensus groups (min_events unique traders)
    consensus = []
    for key, vote in votes.items():
        if len(vote["traders"]) >= min_events:
            consensus.append({
                "coin": vote["coin"],
                "direction": vote["direction"],
                "traderCount": len(vote["traders"]),
                "eventCount": len(vote["events"]),
                "avgTier": vote["totalTier"] / len(vote["events"]),
                "avgConcentration": vote["totalConcentration"] / len(vote["events"]),
                "traders": list(vote["traders"]),
            })

    return consensus


# ─── Gate 3: Market Confirmation ─────────────────────────────

def check_market_concentration(coin, entry_cfg):
    """Confirm SM concentration via leaderboard_get_markets."""
    mkt_cfg = entry_cfg.get("marketConfirmation", {})
    if not mkt_cfg.get("enabled", True):
        return True, 0, 0

    top_n = mkt_cfg.get("topNTraders", 200)
    min_count = mkt_cfg.get("minTraderCount", 5)

    data = cfg.mcporter_call("leaderboard_get_markets", limit=top_n)
    if not data or not data.get("success"):
        return False, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("market", m.get("coin", "")))
        if token == coin:
            trader_count = int(m.get("trader_count", 0))
            percentage = float(m.get("percentage", m.get("pct_of_top_traders_gain", 0)))
            return trader_count >= min_count, trader_count, percentage

    return False, 0, 0


# ─── Gate 4: Volume Confirmation ─────────────────────────────

def check_volume(coin, entry_cfg):
    """Confirm the asset has active volume — don't mirror into dead markets."""
    vol_cfg = entry_cfg.get("volumeConfirmation", {})
    if not vol_cfg.get("enabled", True):
        return True, 1.0

    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return False, 0

    candles = data.get("data", {}).get("candles", {}).get("1h", [])
    if len(candles) < 6:
        return False, 0

    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-6:]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    latest_vol = vols[-1] if vols else 0

    min_ratio = vol_cfg.get("minVolRatio", 0.5)
    ratio = latest_vol / avg_vol if avg_vol > 0 else 0

    return ratio >= min_ratio, ratio


# ─── Gate 5: Regime Filter ───────────────────────────────────

def get_btc_regime():
    """Simple BTC regime: bullish, bearish, or neutral."""
    data = cfg.mcporter_call("market_get_asset_data", asset="BTC",
                              candle_intervals=["4h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return "NEUTRAL"

    candles = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles) < 6:
        return "NEUTRAL"

    closes = [float(c.get("close", c.get("c", 0))) for c in candles[-6:]]
    change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0

    if change > 2:
        return "BULLISH"
    elif change < -2:
        return "BEARISH"
    return "NEUTRAL"


# ─── Scoring ─────────────────────────────────────────────────

def score_consensus(consensus, vol_ratio, mkt_trader_count, mkt_pct, regime, direction, entry_cfg):
    """Score a consensus signal through all gates."""
    score = 0
    reasons = []

    # Trader count (core signal strength)
    tc = consensus["traderCount"]
    score += tc * 2
    reasons.append(f"{tc}_momentum_traders")

    # Tier bonus (higher tiers = bigger moves)
    avg_tier = consensus["avgTier"]
    if avg_tier >= 2.5:
        score += 3
        reasons.append(f"avg_tier_{avg_tier:.1f}_extreme")
    elif avg_tier >= 1.5:
        score += 2
        reasons.append(f"avg_tier_{avg_tier:.1f}_strong")
    else:
        score += 1
        reasons.append(f"avg_tier_{avg_tier:.1f}_base")

    # Concentration bonus (high conviction traders)
    avg_conc = consensus["avgConcentration"]
    if avg_conc > 0.7:
        score += 2
        reasons.append(f"high_conviction_{avg_conc:.0%}")
    elif avg_conc > 0.5:
        score += 1
        reasons.append(f"moderate_conviction_{avg_conc:.0%}")

    # Market confirmation bonus
    if mkt_trader_count > 10:
        score += 2
        reasons.append(f"market_hot_{mkt_trader_count}_traders_{mkt_pct:.0%}")
    elif mkt_trader_count > 5:
        score += 1
        reasons.append(f"market_active_{mkt_trader_count}_traders")

    # Volume bonus
    if vol_ratio > 1.5:
        score += 1
        reasons.append(f"vol_strong_{vol_ratio:.1f}x")
    else:
        reasons.append(f"vol_{vol_ratio:.1f}x")

    # Regime filter (penalty, not block)
    regime_cfg = entry_cfg.get("regimeFilter", {})
    if regime_cfg.get("enabled", True):
        if (direction == "LONG" and regime == "BEARISH") or \
           (direction == "SHORT" and regime == "BULLISH"):
            penalty = regime_cfg.get("penalty", -3)
            score += penalty
            reasons.append(f"regime_{regime}_penalty_{penalty}")
        elif (direction == "LONG" and regime == "BULLISH") or \
             (direction == "SHORT" and regime == "BEARISH"):
            score += 1
            reasons.append(f"regime_confirms_{regime}")

    return score, reasons


# ─── Main ─────────────────────────────────────────────────────

def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    entry_cfg = config.get("entry", {})
    max_positions = config.get("maxPositions", 3)
    our_coins = {p["coin"] for p in positions}

    # Dynamic slots
    dynamic = entry_cfg.get("dynamicSlots", {})
    if dynamic.get("enabled", True):
        base_max = dynamic.get("baseMax", 3)
        day_pnl = tc.get("realizedPnl", 0)
        effective_max = base_max
        for t in dynamic.get("unlockThresholds", []):
            if day_pnl >= t.get("pnl", 999999):
                effective_max = t.get("maxEntries", effective_max)
        max_entries = min(effective_max, dynamic.get("absoluteMax", 6))
    else:
        max_entries = config.get("risk", {}).get("maxEntriesPerDay", 4)

    if tc.get("entries", 0) >= max_entries:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        return

    # ── GATE 1: Fetch momentum events ─────────────────────────
    events = fetch_momentum_events(entry_cfg)
    if not events:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": "no momentum events in lookback window"})
        return

    # ── GATE 2: Filter by trader quality ──────────────────────
    quality_events = filter_quality_events(events, entry_cfg)
    if not quality_events:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"{len(events)} events but none passed quality filter"})
        return

    # ── Build consensus (2+ quality traders on same asset/direction) ──
    consensus_groups = build_consensus(quality_events, entry_cfg)
    if not consensus_groups:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"{len(quality_events)} quality events, no 2+ trader consensus"})
        return

    # Get regime once (used for all candidates)
    regime = get_btc_regime()

    # ── Score each consensus group through remaining gates ────
    signals = []
    banned = entry_cfg.get("bannedPrefixes", ["xyz:"])

    for group in consensus_groups:
        coin = group["coin"]
        direction = group["direction"]

        if coin in our_coins:
            continue
        if any(coin.startswith(p) for p in banned):
            continue

        # GATE 3: Market confirmation
        mkt_ok, mkt_count, mkt_pct = check_market_concentration(coin, entry_cfg)
        if entry_cfg.get("marketConfirmation", {}).get("enabled", True) and not mkt_ok:
            continue

        # GATE 4: Volume confirmation
        vol_ok, vol_ratio = check_volume(coin, entry_cfg)
        if entry_cfg.get("volumeConfirmation", {}).get("enabled", True) and not vol_ok:
            continue

        # GATE 5 + Scoring
        score, reasons = score_consensus(
            group, vol_ratio, mkt_count, mkt_pct, regime, direction, entry_cfg
        )

        signals.append({
            "coin": coin,
            "direction": direction,
            "score": score,
            "reasons": reasons,
            "traderCount": group["traderCount"],
            "eventCount": group["eventCount"],
            "avgTier": group["avgTier"],
            "avgConcentration": group["avgConcentration"],
            "volRatio": vol_ratio,
            "marketTraderCount": mkt_count,
        })

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"{len(consensus_groups)} consensus groups, none passed all gates"})
        return

    min_score = entry_cfg.get("minScore", 10)
    signals = [s for s in signals if s["score"] >= min_score]

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": "consensus found but score below minimum"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Conviction-scaled margin
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["traderCount"] >= 5:
        margin_pct = base_margin_pct * 1.5
    elif best["traderCount"] >= 3:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    leverage = config.get("leverage", {}).get("default", 8)

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["coin"],
            "direction": best["direction"],
            "leverage": leverage,
            "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "armDsl": True,
        "_note": "MANDATORY: run dsl-cli.py add-dsl IMMEDIATELY after entry fills. No naked positions.",
        "eventsSeen": len(events),
        "qualityEvents": len(quality_events),
        "consensusGroups": len(consensus_groups),
        "candidates": len(signals),
    })


if __name__ == "__main__":
    run()
