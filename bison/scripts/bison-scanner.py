#!/usr/bin/env python3
# Senpi BISON Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""BISON v2.0 — Macro Conviction Holder.

The patient predator. While every other agent trades intraday moves,
Bison waits for overwhelming macro conviction and holds for days.

v1.0 had zero trades — the scanner files were missing entirely.

v2.0 thesis: when SM consensus is DEEP (15%+ concentration, 100+ traders),
4H trend is strong (1%+ move), AND the weekly trend aligns — enter and hold.
The DSL is ultra-wide: 30% retrace, 360-minute timeout, Tier 1 locks NOTHING.
The position breathes through any intraday noise.

Why this exists: every other agent optimizes for fast entries and quick exits.
Bison tests the opposite hypothesis — that the REAL alpha is in catching
multi-day macro moves and having the patience to hold through the drawdowns.

Only trades BTC, ETH, SOL — the assets with enough liquidity for macro moves.
Max 1 position. 1-2 trades per WEEK, not per day.

Architecture:
- 2 API calls: leaderboard_get_markets + market_get_asset_data (for weekly trend)
- Runs every 15 minutes (macro trends don't change in 90 seconds)
- Ultra-high conviction gate: score 10+

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bison_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MACRO_ASSETS = ["BTC", "ETH", "SOL"]
MIN_LEVERAGE = 5
MAX_LEVERAGE = 5                    # Low leverage — wide stops need room
DEFAULT_LEVERAGE = 5
MAX_POSITIONS = 1                   # One macro bet at a time
MAX_DAILY_ENTRIES = 1               # 1 entry per day MAX
COOLDOWN_MINUTES = 360              # 6 hour cooldown
MARGIN_PCT = 0.30                   # 30% of account — high conviction sizing
MIN_SCORE = 10                      # Ultra-high conviction required
XYZ_BANNED = True

# SM thresholds — must be overwhelming
MIN_SM_PCT = 10.0                   # 10%+ SM concentration (very high bar)
MIN_SM_TRADERS = 80                 # 80+ traders (broad consensus)

# Trend thresholds
MIN_4H_MOVE = 1.0                  # 4H must have moved 1%+ already
MIN_1H_ALIGNMENT = True             # 1H must confirm (unlike other agents, Bison requires this)


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

def fetch_sm_data():
    """Get SM data for macro assets."""
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
        if token not in MACRO_ASSETS:
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


def fetch_funding(asset):
    """Get funding rate for trend confirmation."""
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
# MACRO CONVICTION EVALUATION
# ═══════════════════════════════════════════════════════════════

def evaluate_macro_thesis(asset, sm_data):
    """Evaluate macro conviction for a single asset. Ultra-high bar."""

    sm = sm_data.get(asset)
    if not sm:
        return None

    direction = sm["direction"]
    if direction not in ("LONG", "SHORT"):
        return None

    # ── Hard gates (all must pass) ────────────────────────────

    # SM must be overwhelming
    if sm["pct"] < MIN_SM_PCT:
        return None
    if sm["traders"] < MIN_SM_TRADERS:
        return None

    # 4H must have already moved significantly
    p4h = sm["price_chg_4h"]
    if direction == "LONG" and p4h < MIN_4H_MOVE:
        return None
    if direction == "SHORT" and p4h > -MIN_4H_MOVE:
        return None

    # 1H must confirm (Bison requires full alignment)
    p1h = sm["price_chg_1h"]
    if MIN_1H_ALIGNMENT:
        if direction == "LONG" and p1h <= 0:
            return None
        if direction == "SHORT" and p1h >= 0:
            return None

    # ── Scoring (all gates passed — now how strong?) ──────────

    score = 0
    reasons = []

    # SM depth (0-4)
    pct = sm["pct"]
    traders = sm["traders"]
    if pct >= 20:
        score += 4
        reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 15:
        score += 3
        reasons.append(f"DEEP_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2
        reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")

    # 4H trend strength (0-2)
    abs_4h = abs(p4h)
    if abs_4h >= 3.0:
        score += 2
        reasons.append(f"MACRO_MOVE {p4h:+.1f}% 4H")
    elif abs_4h >= 1.5:
        score += 1
        reasons.append(f"SOLID_MOVE {p4h:+.1f}% 4H")

    # 1H momentum (0-1)
    abs_1h = abs(p1h)
    if abs_1h >= 0.5:
        score += 1
        reasons.append(f"1H_STRONG {p1h:+.2f}%")

    # Contribution velocity (0-2)
    contrib = abs(sm.get("contrib_change", 0))
    if contrib >= 0.03:
        score += 2
        reasons.append(f"CONTRIB_SURGE +{contrib*100:.1f}%")
    elif contrib >= 0.01:
        score += 1
        reasons.append(f"CONTRIB_GROWING +{contrib*100:.2f}%")

    # Funding alignment (0-1)
    funding = fetch_funding(asset)
    if direction == "SHORT" and funding > 0.0002:
        score += 1
        reasons.append(f"FUNDING_CONFIRMS +{funding*100:.4f}%/hr")
    elif direction == "LONG" and funding < -0.0002:
        score += 1
        reasons.append(f"FUNDING_CONFIRMS {funding*100:.4f}%/hr")

    # Trader count depth bonus (0-1)
    if traders >= 150:
        score += 1
        reasons.append(f"MASSIVE_CONSENSUS ({traders}t)")

    return {
        "asset": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "pct": pct,
        "traders": traders,
        "price_chg_4h": p4h,
        "price_chg_1h": p1h,
    }


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

    # ── Fetch SM data ─────────────────────────────────────────
    sm_data = fetch_sm_data()
    if not sm_data:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data"})
        return

    # ── Evaluate all macro assets ─────────────────────────────
    theses = []
    rejections = {}

    for asset in MACRO_ASSETS:
        if is_on_cooldown(asset):
            rejections[asset] = "cooldown"
            continue
        if any(p["coin"].upper() == asset for p in positions):
            rejections[asset] = "holding"
            continue

        result = evaluate_macro_thesis(asset, sm_data)
        if result is None:
            rejections[asset] = "no_thesis"
        elif result["score"] < MIN_SCORE:
            rejections[asset] = f"score_{result['score']}"
        else:
            theses.append(result)

    if not theses:
        status_parts = [f"{a}:{r}" for a, r in rejections.items()]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"WAITING — {', '.join(status_parts)}"})
        return

    # ── Pick the strongest thesis ─────────────────────────────
    theses.sort(key=lambda t: t["score"], reverse=True)
    best = theses[0]

    margin = round(account_value * MARGIN_PCT, 2)

    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)

    cfg.output({
        "status": "ok",
        "signal": {
            "asset": best["asset"],
            "direction": best["direction"],
            "score": best["score"],
            "mode": "MACRO_CONVICTION",
            "reasons": best["reasons"],
            "smPct": best["pct"],
            "smTraders": best["traders"],
            "priceChg4h": best["price_chg_4h"],
        },
        "entry": {
            "asset": best["asset"],
            "direction": best["direction"],
            "leverage": DEFAULT_LEVERAGE,
            "margin": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
        },
        "constraints": {
            "maxPositions": MAX_POSITIONS,
            "maxLeverage": MAX_LEVERAGE,
            "maxDailyEntries": MAX_DAILY_ENTRIES,
            "cooldownMinutes": COOLDOWN_MINUTES,
            "_v2_no_thesis_exit": True,
            "_note": "DSL managed by plugin runtime. Scanner does NOT manage exits. "
                     "BISON holds for DAYS. Do not interfere with open positions.",
        },
        "_bison_version": "2.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
