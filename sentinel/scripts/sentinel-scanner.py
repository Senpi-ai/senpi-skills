#!/usr/bin/env python3
# Senpi SENTINEL Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SENTINEL v2.0 — Quality Trader Convergence Scanner.

Inverted pipeline: instead of starting with an asset and checking if
SM is there, start with QUALITY TRADERS and find where they converge.

v1.0 had zero trades. The pipeline was too complex — it tried to cross-
reference multiple data sources per asset. v2.0 simplifies:

1. FIND: discovery_get_top_traders (ELITE + RELIABLE, open positions only)
2. AGGREGATE: count how many quality traders hold each asset and in which direction
3. THRESHOLD: when 5+ quality traders converge on the same asset + direction → signal
4. CONFIRM: leaderboard_get_markets SM concentration must agree
5. ENTER: score based on convergence depth + SM alignment + price momentum

Why this works: ELITE/RELIABLE traders are historically profitable.
When multiple of them independently arrive at the same trade, it's
not coincidence — it's informed consensus.

Why v1.0 failed: too many API calls per scan (checked each asset
individually). v2.0 uses 2 bulk API calls and aggregates in memory.

Architecture:
- 2 API calls: discovery_get_top_traders + leaderboard_get_markets
- Aggregation in memory (no per-asset API calls)
- Runs every 5 minutes (quality convergence shifts slowly)

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sentinel_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 5
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.20
MIN_SCORE = 7
XYZ_BANNED = True

# Convergence thresholds
MIN_QUALITY_TRADERS = 5             # At least 5 ELITE/RELIABLE on same asset+direction
ELITE_WEIGHT = 2                    # ELITE counts as 2 traders for convergence
RELIABLE_WEIGHT = 1                 # RELIABLE counts as 1

# SM confirmation
MIN_SM_PCT = 3.0                    # SM must have meaningful concentration
MIN_SM_TRADERS = 15

# Discovery query
DISCOVERY_LIMIT = 100               # Top 100 quality traders
DISCOVERY_TIMEFRAME = "WEEKLY"      # Weekly performers (not daily noise)


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


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_quality_traders():
    """Get top ELITE + RELIABLE traders with open positions."""
    data = cfg.mcporter_call("discovery_get_top_traders",
                              time_frame=DISCOVERY_TIMEFRAME,
                              consistency=["ELITE", "RELIABLE"],
                              open_position_filter=True,
                              sort_by="PROFIT_AND_LOSS",
                              limit=DISCOVERY_LIMIT)
    if not data:
        return []

    traders = data.get("traders", data.get("data", []))
    if isinstance(traders, dict):
        traders = traders.get("traders", [])
    if not isinstance(traders, list):
        return []

    return traders


def fetch_sm_data():
    """Get SM positioning from leaderboard."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}

    markets = []
    if isinstance(raw, dict):
        raw_data = raw.get("data", raw)
        if isinstance(raw_data, dict):
            markets = raw_data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(raw_data, list):
            markets = raw_data
    elif isinstance(raw, list):
        markets = raw

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()

        if XYZ_BANNED and dex == "xyz":
            continue
        if not token:
            continue

        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
        }

    return sm_map


# ═══════════════════════════════════════════════════════════════
# CONVERGENCE DETECTION
# ═══════════════════════════════════════════════════════════════

def build_convergence_map(traders):
    """Aggregate quality trader positions by asset + direction.
    Returns: {ASSET: {LONG: weighted_count, SHORT: weighted_count, traders: [...]}}"""

    convergence = {}

    for trader in traders:
        if not isinstance(trader, dict):
            continue

        address = trader.get("address", trader.get("trader_address", ""))
        tcs = str(trader.get("consistency",
                  trader.get("tcs", trader.get("consistency_label", "")))).upper()

        # Determine weight
        if tcs == "ELITE":
            weight = ELITE_WEIGHT
        elif tcs == "RELIABLE":
            weight = RELIABLE_WEIGHT
        else:
            continue

        # Get open positions from trader data
        positions = trader.get("open_positions",
                    trader.get("positions",
                    trader.get("top_markets", [])))

        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, dict):
                    asset = str(pos.get("market", pos.get("asset",
                                pos.get("coin", "")))).upper()
                    direction = str(pos.get("direction", pos.get("side", ""))).upper()

                    if not asset or direction not in ("LONG", "SHORT"):
                        continue
                    if XYZ_BANNED and asset.lower().startswith("xyz"):
                        continue

                    if asset not in convergence:
                        convergence[asset] = {"LONG": 0, "SHORT": 0, "traders": []}

                    convergence[asset][direction] += weight
                    convergence[asset]["traders"].append({
                        "address": address[:10] + "...",
                        "tcs": tcs,
                        "direction": direction,
                    })

                elif isinstance(pos, str):
                    # top_markets is sometimes just a list of asset names
                    asset = pos.upper()
                    if XYZ_BANNED and asset.lower().startswith("xyz"):
                        continue
                    # Can't determine direction from just asset name — skip
                    continue

    return convergence


def find_convergence_signals(convergence_map, sm_map):
    """Find assets with strong quality trader convergence confirmed by SM."""

    candidates = []

    for asset, data in convergence_map.items():
        long_weight = data["LONG"]
        short_weight = data["SHORT"]

        # Determine dominant direction
        if long_weight >= MIN_QUALITY_TRADERS and long_weight > short_weight:
            direction = "LONG"
            convergence_strength = long_weight
        elif short_weight >= MIN_QUALITY_TRADERS and short_weight > long_weight:
            direction = "SHORT"
            convergence_strength = short_weight
        else:
            continue

        # SM must agree
        sm = sm_map.get(asset)
        if not sm:
            continue
        if sm["direction"] != direction:
            continue
        if sm["pct"] < MIN_SM_PCT or sm["traders"] < MIN_SM_TRADERS:
            continue

        candidates.append({
            "asset": asset,
            "direction": direction,
            "convergence_strength": convergence_strength,
            "long_weight": long_weight,
            "short_weight": short_weight,
            "sm_direction": sm["direction"],
            "sm_pct": sm["pct"],
            "sm_traders": sm["traders"],
            "price_chg_4h": sm["price_chg_4h"],
            "price_chg_1h": sm["price_chg_1h"],
            "contrib_change": sm.get("contrib_change", 0),
            "trader_details": data["traders"],
        })

    return candidates


# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

def score_candidate(cand):
    """Score a convergence candidate."""
    score = 0
    reasons = []

    # 1. Convergence depth (0-4 points)
    strength = cand["convergence_strength"]
    if strength >= 15:
        score += 4
        reasons.append(f"DEEP_CONVERGENCE {strength} weighted traders")
    elif strength >= 10:
        score += 3
        reasons.append(f"STRONG_CONVERGENCE {strength} weighted traders")
    elif strength >= 7:
        score += 2
        reasons.append(f"SOLID_CONVERGENCE {strength} weighted traders")
    elif strength >= 5:
        score += 1
        reasons.append(f"BASE_CONVERGENCE {strength} weighted traders")

    # 2. SM alignment strength (0-2 points)
    sm_pct = cand["sm_pct"]
    if sm_pct >= 10:
        score += 2
        reasons.append(f"SM_STRONG {sm_pct:.1f}% ({cand['sm_traders']}t)")
    elif sm_pct >= 5:
        score += 1
        reasons.append(f"SM_ALIGNED {sm_pct:.1f}% ({cand['sm_traders']}t)")

    # 3. Price momentum (0-2 points)
    p4h = cand["price_chg_4h"]
    direction = cand["direction"]
    if direction == "LONG" and p4h > 0.5:
        score += 1
        reasons.append(f"4H_CONFIRMS +{p4h:.1f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 1
        reasons.append(f"4H_CONFIRMS {p4h:.1f}%")

    p1h = cand["price_chg_1h"]
    if direction == "LONG" and p1h > 0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS +{p1h:.2f}%")
    elif direction == "SHORT" and p1h < -0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:.2f}%")

    # 4. Contribution velocity (0-1 point)
    contrib = abs(cand.get("contrib_change", 0))
    if contrib >= 0.01:
        score += 1
        reasons.append(f"CONTRIB_ACCEL +{contrib*100:.1f}%")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER & COOLDOWN
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": now_date(), "entries": 0}


def save_trade_counter(tc):
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def is_on_cooldown(asset):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(asset)
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

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    tc = load_trade_counter()
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
        save_trade_counter(tc)
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Fetch data (2 API calls) ──────────────────────────────
    traders = fetch_quality_traders()
    sm_map = fetch_sm_data()

    if not traders:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No quality traders with open positions"})
        return

    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data"})
        return

    # ── Build convergence map ─────────────────────────────────
    convergence_map = build_convergence_map(traders)

    # ── Find signals ──────────────────────────────────────────
    candidates = find_convergence_signals(convergence_map, sm_map)

    if not candidates:
        # Report top convergence for debugging
        top_assets = sorted(convergence_map.items(),
                           key=lambda x: max(x[1]["LONG"], x[1]["SHORT"]),
                           reverse=True)[:3]
        top_summary = [(a, max(d["LONG"], d["SHORT"])) for a, d in top_assets]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No convergence signals. "
                            f"{len(traders)} quality traders tracked. "
                            f"Top convergence: {top_summary}"})
        return

    # ── Score and filter ──────────────────────────────────────
    for cand in candidates:
        cand["score"], cand["reasons"] = score_candidate(cand)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    for cand in candidates:
        asset = cand["asset"]

        if cand["score"] < MIN_SCORE:
            continue
        if is_on_cooldown(asset):
            continue
        if any(p["coin"].upper() == asset.upper() for p in positions):
            continue

        # ── Entry ─────────────────────────────────────────────
        margin = round(account_value * MARGIN_PCT, 2)

        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

        cfg.output({
            "status": "ok",
            "signal": {
                "asset": asset,
                "direction": cand["direction"],
                "score": cand["score"],
                "mode": "CONVERGENCE",
                "reasons": cand["reasons"],
                "convergenceStrength": cand["convergence_strength"],
                "smPct": cand["sm_pct"],
                "smTraders": cand["sm_traders"],
                "priceChg4h": cand["price_chg_4h"],
                "qualityTraders": len(cand["trader_details"]),
            },
            "entry": {
                "asset": asset,
                "direction": cand["direction"],
                "leverage": DEFAULT_LEVERAGE,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "constraints": {
                "maxPositions": MAX_POSITIONS,
                "maxLeverage": MAX_LEVERAGE,
                "maxDailyEntries": MAX_DAILY_ENTRIES,
                "cooldownMinutes": COOLDOWN_MINUTES,
                "xyzBanned": XYZ_BANNED,
                "_v2_no_thesis_exit": True,
                "_note": "DSL managed by plugin runtime. Scanner does NOT manage exits.",
            },
            "_sentinel_version": "2.0",
        })
        return

    # Report best candidate that didn't pass
    if candidates:
        best = candidates[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best convergence: {best['asset']} {best['direction']} "
                            f"score {best['score']} < {MIN_SCORE}. "
                            f"{', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Convergence found but no SM alignment"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
