#!/usr/bin/env python3
# Senpi MANTIS Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""MANTIS Scanner — High-Conviction Whale Mirror.

Evolved from SCORPION's live trading lessons: too few whales required,
too short aging filter, too tight stops. MANTIS fixes all three and adds
volume confirmation, whale quality weighting, and regime awareness.

The praying mantis: perfectly still, watching everything, strikes only
when the kill is certain.

Key differences from SCORPION:
  1. 4+ whale consensus required (SCORPION: 2)
  2. 30-minute aging filter (SCORPION: 10 min)
  3. Whale quality weighting — recent P&L, win rate, avg hold time
  4. Volume confirmation — don't mirror into dead volume
  5. Regime filter — don't short in strong bull regimes or vice versa
  6. Wide Phase 1 DSL (5% retrace, SCORPION: 3%)
  7. Immediate DSL arming — no naked positions ever

Runs every 5 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mantis_config as cfg


# ─── Whale Discovery & Scoring ────────────────────────────────

def discover_whales(entry_cfg):
    """Get top traders and score them by quality."""
    data = cfg.mcporter_call("discovery_get_top_traders",
                              timeframe="30d", limit=entry_cfg.get("topNTraders", 30))
    if not data or not data.get("success"):
        return []

    traders = data.get("data", data)
    if isinstance(traders, dict):
        traders = traders.get("traders", [])

    min_wr = entry_cfg.get("minWhaleWinRate", 55)
    min_trades = entry_cfg.get("minWhaleTrades", 15)
    min_pnl = entry_cfg.get("minWhalePnl", 0)

    qualified = []
    for t in traders:
        wr = float(t.get("winRate", t.get("win_rate", 0)))
        trades = int(t.get("totalTrades", t.get("total_trades", 0)))
        pnl = float(t.get("pnl", t.get("totalPnl", 0)))
        address = t.get("address", t.get("trader", ""))

        if wr < min_wr or trades < min_trades or pnl < min_pnl:
            continue

        # Quality score: combines win rate, trade count, and P&L
        quality = 0
        if wr >= 70: quality += 3
        elif wr >= 60: quality += 2
        elif wr >= 55: quality += 1

        if pnl > 50000: quality += 3
        elif pnl > 10000: quality += 2
        elif pnl > 1000: quality += 1

        if trades > 100: quality += 1

        qualified.append({
            "address": address,
            "winRate": wr,
            "trades": trades,
            "pnl": pnl,
            "quality": quality,
        })

    qualified.sort(key=lambda x: x["quality"], reverse=True)
    return qualified[:entry_cfg.get("maxTrackedWhales", 15)]


def get_whale_positions(whales):
    """Get current positions for all tracked whales."""
    all_positions = []
    for whale in whales:
        data = cfg.mcporter_call("market_get_positions",
                                  trader=whale["address"])
        if not data or not data.get("success"):
            continue
        positions = data.get("data", data)
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        for pos in positions:
            all_positions.append({
                "whaleAddress": whale["address"],
                "whaleQuality": whale["quality"],
                "whaleWinRate": whale["winRate"],
                "coin": pos.get("coin", pos.get("asset", "")),
                "direction": "LONG" if float(pos.get("size", pos.get("szi", 0))) > 0 else "SHORT",
                "size": abs(float(pos.get("size", pos.get("szi", 0)))),
                "entryPx": float(pos.get("entryPx", pos.get("entry_price", 0))),
            })
    return all_positions


# ─── Volume Confirmation ──────────────────────────────────────

def check_volume(coin, entry_cfg):
    """Confirm the asset has active volume — don't mirror into dead markets."""
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

    min_ratio = entry_cfg.get("minVolRatio", 0.5)
    ratio = latest_vol / avg_vol if avg_vol > 0 else 0

    return ratio >= min_ratio, ratio


# ─── Regime Filter ────────────────────────────────────────────

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


# ─── Persistence Tracking ─────────────────────────────────────

def check_consensus_persistence(state, coin, direction, whale_addresses, min_hold_min):
    """Track how long a consensus has been held. Returns (persisted, minutes)."""
    key = f"{coin}:{direction}"
    tracking = state.get("consensusHistory", {})
    now_ts = cfg.now_ts()

    if key not in tracking:
        tracking[key] = {
            "firstSeen": cfg.now_iso(),
            "ts": now_ts,
            "whales": whale_addresses,
        }
        state["consensusHistory"] = tracking
        return False, 0

    entry = tracking[key]
    minutes = (now_ts - entry.get("ts", now_ts)) / 60

    # Update whale list
    entry["whales"] = whale_addresses
    state["consensusHistory"] = tracking

    return minutes >= min_hold_min, minutes


def clear_stale_consensus(state, active_keys):
    """Remove tracking for consensus that no longer exists."""
    tracking = state.get("consensusHistory", {})
    stale = [k for k in tracking if k not in active_keys]
    for k in stale:
        del tracking[k]
    state["consensusHistory"] = tracking


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
    state = cfg.load_state("mantis-state.json")

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
        cfg.save_state(state, "mantis-state.json")
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        cfg.save_state(state, "mantis-state.json")
        return

    # CHECK 1: Whale exit detection for held positions (the sting)
    if positions:
        whales = discover_whales(entry_cfg)
        whale_positions = get_whale_positions(whales)
        mirrored = state.get("mirrored", {})

        for pos in positions:
            coin = pos["coin"]
            if coin not in mirrored:
                continue

            mirror_info = mirrored[coin]
            original_whale = mirror_info.get("whaleAddress")

            # Check if the whale we mirrored still holds this position
            whale_still_holds = any(
                wp["whaleAddress"] == original_whale and wp["coin"] == coin
                for wp in whale_positions
            )

            if not whale_still_holds:
                cfg.save_state(state, "mantis-state.json")
                cfg.output({
                    "success": True,
                    "action": "whale_exit",
                    "exits": [{
                        "coin": coin,
                        "direction": pos["direction"],
                        "reason": f"whale_{original_whale[:8]}_exited",
                        "upnl": pos.get("upnl", 0),
                    }],
                    "note": "whale exited — the sting fires, close immediately",
                })
                return

    # CHECK 2: Discover and score whales
    whales = discover_whales(entry_cfg)
    if not whales:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no qualified whales"})
        cfg.save_state(state, "mantis-state.json")
        return

    whale_positions = get_whale_positions(whales)

    # CHECK 3: Build consensus (4+ whales on same asset/direction)
    votes = {}
    for wp in whale_positions:
        key = f"{wp['coin']}:{wp['direction']}"
        if key not in votes:
            votes[key] = {"coin": wp["coin"], "direction": wp["direction"],
                          "whales": [], "totalQuality": 0}
        votes[key]["whales"].append(wp["whaleAddress"])
        votes[key]["totalQuality"] += wp["whaleQuality"]

    min_whale_count = entry_cfg.get("minWhaleCount", 4)
    min_hold_min = entry_cfg.get("minHoldMinutes", 30)
    banned = entry_cfg.get("bannedPrefixes", ["xyz:"])

    active_keys = set()
    signals = []

    for key, vote in votes.items():
        coin = vote["coin"]
        direction = vote["direction"]
        active_keys.add(key)

        if coin in our_coins:
            continue
        if any(coin.startswith(p) for p in banned):
            continue
        if len(vote["whales"]) < min_whale_count:
            continue

        # CHECK 4: Persistence — consensus must hold 30+ minutes
        persisted, minutes = check_consensus_persistence(
            state, coin, direction, vote["whales"], min_hold_min
        )
        if not persisted:
            continue

        # CHECK 5: Volume confirmation — don't mirror into dead markets
        vol_ok, vol_ratio = check_volume(coin, entry_cfg)
        if not vol_ok:
            continue

        # CHECK 6: Regime filter — don't fight the macro
        regime = get_btc_regime()
        regime_penalty = 0
        if (direction == "LONG" and regime == "BEARISH") or \
           (direction == "SHORT" and regime == "BULLISH"):
            regime_penalty = -2  # Penalty, not a block — whales might know something

        # Score: whale count + quality + persistence + volume + regime
        score = len(vote["whales"]) * 2
        score += vote["totalQuality"]
        score += min(int(minutes / 30), 3)  # Up to +3 for long persistence
        score += 1 if vol_ratio > 1.5 else 0
        score += regime_penalty

        reasons = [
            f"{len(vote['whales'])}_whales_aligned",
            f"quality_{vote['totalQuality']}",
            f"held_{minutes:.0f}min",
            f"vol_{vol_ratio:.1f}x",
        ]
        if regime_penalty:
            reasons.append(f"regime_{regime}_penalty")
        if regime == direction.replace("LONG", "BULLISH").replace("SHORT", "BEARISH"):
            reasons.append("regime_confirms")

        signals.append({
            "coin": coin, "direction": direction, "score": score,
            "reasons": reasons, "whaleCount": len(vote["whales"]),
            "whaleAddresses": vote["whales"], "totalQuality": vote["totalQuality"],
            "persistMinutes": minutes, "volRatio": vol_ratio,
        })

    clear_stale_consensus(state, active_keys)
    cfg.save_state(state, "mantis-state.json")

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"tracking {len(whales)} whales, no 4+ consensus at 30min+"})
        return

    min_score = entry_cfg.get("minScore", 12)
    signals = [s for s in signals if s["score"] >= min_score]

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": "consensus found but score below minimum"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Conviction-scaled margin
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["whaleCount"] >= 6:
        margin_pct = base_margin_pct * 1.5
    elif best["whaleCount"] >= 5:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    leverage = config.get("leverage", {}).get("default", 8)

    # Record mirror for exit tracking
    mirrored = state.get("mirrored", {})
    mirrored[best["coin"]] = {
        "whaleAddress": best["whaleAddresses"][0],
        "whaleDirection": best["direction"],
        "enteredAt": cfg.now_iso(),
        "whaleCount": best["whaleCount"],
    }
    state["mirrored"] = mirrored
    cfg.save_state(state, "mantis-state.json")

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["coin"], "direction": best["direction"],
            "leverage": leverage, "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "armDsl": True,
        "_note": "MANDATORY: run dsl-cli.py add-dsl IMMEDIATELY after entry fills. No naked positions.",
    })


if __name__ == "__main__":
    run()
