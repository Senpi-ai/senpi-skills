#!/usr/bin/env python3
# Senpi CONDOR Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""CONDOR v2.0 — Multi-Asset Thesis Picker.

Evaluates BTC, ETH, SOL, HYPE simultaneously. Picks the single strongest
thesis. Conviction-scaled margin (25/35/45% of account).

v1.0 had zero trades because all 4 assets had 1H opposing 4H. The scanner
was correct to wait — no strong thesis existed. v2.0 keeps the same high
bar but with cleaner architecture:

- Single API call (leaderboard_get_markets) gives SM data for all 4 assets
- market_get_asset_data per asset for funding confirmation
- 4H trend required (hard gate)
- 1H confirmation for bonus points (not a hard gate like v1.0)
- SM consensus required (majority of traders aligned)
- Funding confirmation for bonus points
- Conviction-scaled margin: score 8-9 = 25%, score 10-11 = 35%, score 12+ = 45%

Max 1 position at a time (best thesis only).
Max 3 entries/day. 120 min cooldown per asset.

DSL exit managed by plugin runtime. Scanner does NOT manage exits.

Uses: leaderboard_get_markets + market_get_asset_data (per asset)
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import condor_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "HYPE"]
MIN_LEVERAGE = 5
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 3
COOLDOWN_MINUTES = 120
MIN_SCORE = 8

MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 20

MARGIN_TIERS = {
    "HIGH": 0.45,
    "MEDIUM": 0.35,
    "BASE": 0.25,
}

FUNDING_CONFIRMS = 0.0001
FUNDING_EXTREME = 0.0005


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

def fetch_all_sm_data():
    """Get SM data for all tracked assets from one API call."""
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

        if dex == "xyz" or token not in TRACKED_ASSETS:
            continue

        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
            "rank": int(m.get("rank", m.get("position", 999))),
        }

    return sm_map


def fetch_funding(asset):
    """Get funding rate for a single asset."""
    data = cfg.mcporter_call("market_get_asset_data",
                              asset=asset,
                              candle_intervals=[],
                              include_funding=True)
    if not data:
        return 0

    ad = data.get("data", data)
    if not isinstance(ad, dict):
        return 0

    ac = ad.get("asset_context", ad.get("assetContext", {}))
    if not isinstance(ac, dict):
        return 0

    return safe_float(ac.get("funding", ac.get("fundingRate", 0)))


# ═══════════════════════════════════════════════════════════════
# THESIS EVALUATION
# ═══════════════════════════════════════════════════════════════

def evaluate_thesis(asset, sm_data):
    """Evaluate a single asset's thesis. Returns dict or None."""

    sm = sm_data.get(asset)
    if not sm:
        return None

    direction = sm["direction"]
    if direction not in ("LONG", "SHORT"):
        return None

    score = 0
    reasons = []

    # 1. SM CONSENSUS (0-4, required)
    pct = sm["pct"]
    traders = sm["traders"]

    if pct < MIN_SM_PCT or traders < MIN_SM_TRADERS:
        return None

    if pct >= 15:
        score += 4
        reasons.append(f"SM_DOMINANT {pct:.1f}% ({traders}t)")
    elif pct >= 8:
        score += 3
        reasons.append(f"SM_STRONG {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 2
        reasons.append(f"SM_SOLID {pct:.1f}% ({traders}t)")
    else:
        score += 1
        reasons.append(f"SM_BASE {pct:.1f}% ({traders}t)")

    # 2. 4H TREND (0-2, required)
    p4h = sm["price_chg_4h"]

    if direction == "LONG" and p4h > 0.5:
        score += 2
        reasons.append(f"4H_STRONG +{p4h:.1f}%")
    elif direction == "LONG" and p4h > 0:
        score += 1
        reasons.append(f"4H_POSITIVE +{p4h:.2f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 2
        reasons.append(f"4H_STRONG {p4h:.1f}%")
    elif direction == "SHORT" and p4h < 0:
        score += 1
        reasons.append(f"4H_POSITIVE {p4h:.2f}%")
    else:
        return None  # 4H opposes SM — hard block

    # 3. 1H CONFIRMATION (0-1, bonus)
    p1h = sm["price_chg_1h"]

    if direction == "LONG" and p1h > 0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS +{p1h:.2f}%")
    elif direction == "SHORT" and p1h < -0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:.2f}%")

    # 4. CONTRIBUTION VELOCITY (0-2)
    contrib = sm.get("contrib_change", 0)
    if abs(contrib) >= 0.03:
        score += 2
        reasons.append(f"CONTRIB_SURGE +{abs(contrib)*100:.1f}%")
    elif abs(contrib) >= 0.01:
        score += 1
        reasons.append(f"CONTRIB_ACCEL +{abs(contrib)*100:.2f}%")

    # 5. FUNDING (0-2)
    funding = fetch_funding(asset)

    if direction == "SHORT" and funding > FUNDING_EXTREME:
        score += 2
        reasons.append(f"FUNDING_EXTREME +{funding*100:.4f}%/hr")
    elif direction == "SHORT" and funding > FUNDING_CONFIRMS:
        score += 1
        reasons.append(f"FUNDING_CONFIRMS +{funding*100:.4f}%/hr")
    elif direction == "LONG" and funding < -FUNDING_EXTREME:
        score += 2
        reasons.append(f"FUNDING_EXTREME {funding*100:.4f}%/hr")
    elif direction == "LONG" and funding < -FUNDING_CONFIRMS:
        score += 1
        reasons.append(f"FUNDING_CONFIRMS {funding*100:.4f}%/hr")

    # 6. RANK (0-1)
    rank = sm.get("rank", 999)
    if rank <= 10:
        score += 1
        reasons.append(f"TOP_10 #{rank}")

    return {
        "asset": asset, "direction": direction, "score": score,
        "reasons": reasons, "pct": pct, "traders": traders,
        "price_chg_4h": p4h, "price_chg_1h": p1h,
    }


def get_margin_pct(score):
    if score >= 12:
        return MARGIN_TIERS["HIGH"]
    elif score >= 10:
        return MARGIN_TIERS["MEDIUM"]
    return MARGIN_TIERS["BASE"]


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

    sm_data = fetch_all_sm_data()
    if not sm_data:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "No SM data"})
        return

    theses = []
    rejections = {}

    for asset in TRACKED_ASSETS:
        if is_on_cooldown(asset):
            rejections[asset] = "cooldown"
            continue
        if any(p["coin"].upper() == asset for p in positions):
            rejections[asset] = "holding"
            continue

        result = evaluate_thesis(asset, sm_data)
        if result is None:
            rejections[asset] = "no_thesis"
        elif result["score"] < MIN_SCORE:
            rejections[asset] = f"score_{result['score']}"
        else:
            theses.append(result)

    if not theses:
        status_parts = [f"{a}:{r}" for a, r in rejections.items()]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"HUNTING — {', '.join(status_parts)}"})
        return

    theses.sort(key=lambda t: t["score"], reverse=True)
    best = theses[0]

    margin_pct = get_margin_pct(best["score"])
    margin = round(account_value * margin_pct, 2)

    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)

    cfg.output({
        "status": "ok",
        "signal": {
            "asset": best["asset"], "direction": best["direction"],
            "score": best["score"], "mode": "CONDOR",
            "reasons": best["reasons"],
            "marginPct": margin_pct,
            "conviction": "HIGH" if best["score"] >= 12 else
                          "MEDIUM" if best["score"] >= 10 else "BASE",
            "smPct": best["pct"], "smTraders": best["traders"],
            "priceChg4h": best["price_chg_4h"],
            "allTheses": [{"asset": t["asset"], "score": t["score"],
                          "direction": t["direction"]} for t in theses],
            "rejected": rejections,
        },
        "entry": {
            "asset": best["asset"], "direction": best["direction"],
            "leverage": DEFAULT_LEVERAGE, "margin": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
        },
        "constraints": {
            "maxPositions": MAX_POSITIONS, "maxLeverage": MAX_LEVERAGE,
            "maxDailyEntries": MAX_DAILY_ENTRIES,
            "cooldownMinutes": COOLDOWN_MINUTES,
            "_v2_no_thesis_exit": True,
            "_note": "DSL managed by plugin runtime. Scanner does NOT manage exits.",
        },
        "_condor_version": "2.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
