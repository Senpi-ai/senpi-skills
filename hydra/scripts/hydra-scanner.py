#!/usr/bin/env python3
# Senpi HYDRA Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""HYDRA v2.0 — Squeeze Detector.

Detects crowded trades about to unwind. The thesis: when funding is extreme
(longs paying shorts heavily, or vice versa) AND smart money is positioned
AGAINST the crowd AND price starts moving against the crowd — that's a squeeze.

v1.0 had a 20% win rate because it fired on funding anomalies alone (FDD + LCD
at minimum 55 conviction) on illiquid tokens (BLUR, STABLE, DOOD). v2.0 fixes
everything:

How it works:
1. SCAN: Find assets with extreme funding (crowd is piled one direction)
2. CONFIRM: SM is positioned AGAINST the funding crowd
3. VALIDATE: Price is starting to move against the crowd (the squeeze is beginning)
4. FILTER: Only trade assets with $20M+ daily volume (no illiquid garbage)

The scanner goes OPPOSITE to the crowd:
- Funding extreme positive (longs paying shorts) → crowd is LONG → Hydra goes SHORT
- Funding extreme negative (shorts paying longs) → crowd is SHORT → Hydra goes LONG

Two API calls per scan: market_get_prices (funding rates) + leaderboard_get_markets (SM positioning).
Runs every 5 minutes.

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hydra_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 5
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.18                   # 18% of account per trade
MIN_SCORE = 7
XYZ_BANNED = True

# Funding thresholds — what constitutes "extreme" crowding
FUNDING_EXTREME = 0.0005            # >0.05%/hr = heavy crowding
FUNDING_VERY_EXTREME = 0.001        # >0.1%/hr = extreme crowding
FUNDING_MINIMUM = 0.0002            # Below this, no signal

# Liquidity gate
MIN_DAILY_VOLUME = 20_000_000       # $20M minimum daily volume
BANNED_ASSETS = {
    "BLUR", "STABLE", "DOOD", "DEGEN",  # Illiquid garbage from v1.0
}

# SM confirmation
MIN_SM_PCT = 3.0                    # SM must have meaningful presence
MIN_SM_TRADERS = 10


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

def fetch_funding_rates():
    """Get funding rates for all assets."""
    data = cfg.mcporter_call("market_get_prices")
    if not data:
        return {}

    prices = data.get("data", data)
    if isinstance(prices, dict):
        prices = prices.get("prices", prices)

    funding_map = {}
    if isinstance(prices, dict):
        for asset, info in prices.items():
            if isinstance(info, dict):
                funding = safe_float(info.get("funding", info.get("fundingRate", 0)))
                volume = safe_float(info.get("dayNtlVlm", info.get("volume24h", 0)))
                mid = safe_float(info.get("mid", info.get("markPx", 0)))
                funding_map[asset.upper()] = {
                    "funding": funding,
                    "volume": volume,
                    "price": mid,
                }
    elif isinstance(prices, list):
        for p in prices:
            if isinstance(p, dict):
                asset = str(p.get("asset", p.get("coin", ""))).upper()
                funding = safe_float(p.get("funding", p.get("fundingRate", 0)))
                volume = safe_float(p.get("dayNtlVlm", p.get("volume24h", 0)))
                mid = safe_float(p.get("mid", p.get("markPx", 0)))
                if asset:
                    funding_map[asset] = {
                        "funding": funding,
                        "volume": volume,
                        "price": mid,
                    }

    return funding_map


def fetch_sm_data():
    """Get SM positioning from leaderboard."""
    raw = cfg.mcporter_call("leaderboard_get_markets")
    if not raw:
        return {}

    markets = []
    if isinstance(raw, dict):
        markets = raw.get("markets", raw.get("data", []))
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
        }

    return sm_map


# ═══════════════════════════════════════════════════════════════
# SQUEEZE DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_squeezes(funding_map, sm_map):
    """Find assets where funding crowd is about to get squeezed.

    Logic:
    1. Funding extreme → crowd is piled one direction
    2. SM positioned OPPOSITE to the crowd → smart money disagrees
    3. Price moving against the crowd → squeeze is starting
    4. Sufficient volume → can exit cleanly
    """

    candidates = []

    for asset, fdata in funding_map.items():
        funding = fdata["funding"]
        volume = fdata["volume"]

        # Skip low funding — no crowd signal
        if abs(funding) < FUNDING_MINIMUM:
            continue

        # Liquidity gate
        if volume < MIN_DAILY_VOLUME:
            continue

        # Ban list
        if asset in BANNED_ASSETS:
            continue

        # Determine crowd direction from funding
        # Positive funding = longs pay shorts = crowd is LONG
        # Negative funding = shorts pay longs = crowd is SHORT
        if funding > 0:
            crowd_direction = "LONG"
            squeeze_direction = "SHORT"  # We go against the crowd
        else:
            crowd_direction = "SHORT"
            squeeze_direction = "LONG"

        # Get SM data for this asset
        sm = sm_map.get(asset)
        if not sm:
            continue

        # SM must have meaningful presence
        if sm["pct"] < MIN_SM_PCT or sm["traders"] < MIN_SM_TRADERS:
            continue

        # SM must be positioned AGAINST the crowd (or at least not with them)
        sm_direction = sm["direction"]
        sm_against_crowd = sm_direction == squeeze_direction

        if not sm_against_crowd:
            continue  # SM agrees with crowd — no squeeze

        # Price starting to move against the crowd (squeeze beginning)
        p4h = sm["price_chg_4h"]
        p1h = sm["price_chg_1h"]
        price_against_crowd = False
        if squeeze_direction == "SHORT" and p4h < 0:
            price_against_crowd = True
        elif squeeze_direction == "LONG" and p4h > 0:
            price_against_crowd = True

        candidates.append({
            "asset": asset,
            "squeeze_direction": squeeze_direction,
            "crowd_direction": crowd_direction,
            "funding": funding,
            "volume": volume,
            "sm_direction": sm_direction,
            "sm_pct": sm["pct"],
            "sm_traders": sm["traders"],
            "price_chg_4h": p4h,
            "price_chg_1h": p1h,
            "price_against_crowd": price_against_crowd,
        })

    return candidates


# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

def score_squeeze(cand):
    """Score a squeeze candidate. Returns (score, reasons)."""
    score = 0
    reasons = []

    # ── 1. Funding extremity (0-3 points) ────────────────────
    abs_funding = abs(cand["funding"])
    if abs_funding >= FUNDING_VERY_EXTREME:
        score += 3
        reasons.append(f"EXTREME_FUNDING {cand['funding']*100:.4f}%/hr")
    elif abs_funding >= FUNDING_EXTREME:
        score += 2
        reasons.append(f"HIGH_FUNDING {cand['funding']*100:.4f}%/hr")
    elif abs_funding >= FUNDING_MINIMUM:
        score += 1
        reasons.append(f"ELEVATED_FUNDING {cand['funding']*100:.4f}%/hr")

    # ── 2. SM conviction against crowd (0-3 points) ──────────
    sm_pct = cand["sm_pct"]
    sm_traders = cand["sm_traders"]
    if sm_pct >= 10:
        score += 3
        reasons.append(f"SM_STRONG_AGAINST {sm_pct:.1f}% ({sm_traders}t) {cand['sm_direction']}")
    elif sm_pct >= 5:
        score += 2
        reasons.append(f"SM_AGAINST {sm_pct:.1f}% ({sm_traders}t) {cand['sm_direction']}")
    else:
        score += 1
        reasons.append(f"SM_POSITIONED {sm_pct:.1f}% ({sm_traders}t) {cand['sm_direction']}")

    # ── 3. Price moving against crowd (0-2 points) ───────────
    if cand["price_against_crowd"]:
        p4h = abs(cand["price_chg_4h"])
        if p4h >= 2.0:
            score += 2
            reasons.append(f"SQUEEZE_ACTIVE {cand['price_chg_4h']:+.1f}% 4H")
        elif p4h >= 0.5:
            score += 1
            reasons.append(f"SQUEEZE_STARTING {cand['price_chg_4h']:+.1f}% 4H")
    else:
        # Price not yet moving — early signal, lower conviction
        reasons.append(f"PRICE_FLAT {cand['price_chg_4h']:+.2f}% 4H (pre-squeeze)")

    # ── 4. 1H momentum confirming (0-1 point) ────────────────
    p1h = cand["price_chg_1h"]
    squeeze_dir = cand["squeeze_direction"]
    if squeeze_dir == "SHORT" and p1h < -0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:.2f}%")
    elif squeeze_dir == "LONG" and p1h > 0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS +{p1h:.2f}%")

    # ── 5. Volume (0-1 point) ────────────────────────────────
    vol = cand["volume"]
    if vol >= 100_000_000:
        score += 1
        reasons.append(f"HIGH_VOLUME ${vol/1e6:.0f}M")

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

    # ── Check existing positions (NO thesis exit) ─────────────
    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if len(positions) >= MAX_POSITIONS:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{len(positions)} positions active. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    # ── Trade counter ─────────────────────────────────────────
    tc = load_trade_counter()
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
        save_trade_counter(tc)
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Fetch data (2 API calls) ──────────────────────────────
    funding_map = fetch_funding_rates()
    sm_map = fetch_sm_data()

    if not funding_map or not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Failed to fetch funding or SM data"})
        return

    # ── Detect squeezes ───────────────────────────────────────
    candidates = detect_squeezes(funding_map, sm_map)

    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No squeeze signals. {len(funding_map)} assets scanned."})
        return

    # ── Score and filter ──────────────────────────────────────
    for cand in candidates:
        cand["score"], cand["reasons"] = score_squeeze(cand)

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
                "direction": cand["squeeze_direction"],
                "score": cand["score"],
                "mode": "SQUEEZE",
                "reasons": cand["reasons"],
                "crowdDirection": cand["crowd_direction"],
                "funding": cand["funding"],
                "smPct": cand["sm_pct"],
                "smTraders": cand["sm_traders"],
                "volume": cand["volume"],
                "priceChg4h": cand["price_chg_4h"],
            },
            "entry": {
                "asset": asset,
                "direction": cand["squeeze_direction"],
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
            "_hydra_version": "2.0",
        })
        return

    cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"{len(candidates)} squeeze candidates, none passed score {MIN_SCORE}. "
                        f"Top: {candidates[0]['asset']} score {candidates[0]['score']} "
                        f"({', '.join(candidates[0]['reasons'][:3])})"
                        if candidates else "No candidates"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
