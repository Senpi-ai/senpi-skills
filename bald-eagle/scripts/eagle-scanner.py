#!/usr/bin/env python3
# Senpi BALD EAGLE Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BALD EAGLE v3.0 — XYZ Alpha Hunter (Hardened).

v3.0 redesign from fleet audit data:
- v2.0 bled -$75 across 54 trades. 14.3% win rate on DSL-managed trades.
  Root causes: AMM slippage on entry+exit, 8% retrace at 7x = 1.1% price
  move (oil noise), taker fallback destroying edge, too many illiquid assets.

Key changes:
- FOCUSED ASSET LIST: CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100 only.
  These have the deepest SM signal (CL: 181 traders, BRENTOIL: 166).
  Everything else is sub-50 traders on XYZ side — not enough signal.
- CONVICTION-SCALED LEVERAGE: score 8-9 → 5x, score 10-11 → 7x, score 12+ → 10x.
  High conviction = high leverage. When SM is screaming, press it.
- WIDER DSL: retrace 12%, absolute floor -25% ROE, dead weight 120min,
  weak peak 240min, hard timeout 480min. XYZ assets are macro-driven
  and need hours to play out.
- MAKER-ONLY execution: ensureExecutionAsTaker=false, no taker fallback.
  AMM slippage on XYZ assets destroyed v2.0. A missed fill is cheaper
  than -0.5% slippage on entry.
- HIGHER MIN_SCORE: 9 (was 7). When you're paying AMM spread, the
  macro thesis must be even stronger to overcome execution costs.
- Scanner calls create_position internally (Wolverine pattern).
- Thesis exit REMOVED. Scanner enters. RatchetStop exits.
- All hard gates converted to score contributors.

Runs every 5 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eagle_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

# Only trade XYZ assets with deep SM signal and real liquidity.
# CL=181 traders, BRENTOIL=166, SP500=35, GOLD=26, SILVER=42, XYZ100=50
ALLOWED_ASSETS = {"CL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100"}

# Conviction-scaled leverage
LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 10},
    {"min_score": 10, "leverage": 7},
    {"min_score": 8,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 10
MIN_LEVERAGE = 3

MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50           # 50% of account per trade
MIN_SCORE = 9               # Higher bar for XYZ — AMM spread tax
MAX_SPREAD_PCT = 0.001      # 0.1% max spread

MAX_DAILY_LOSS_PCT = 10

# SM thresholds — XYZ has fewer SM traders than crypto
MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 5

# XYZ-specific DSL — wider than crypto, macro assets need room
EAGLE_DSL_TIERS = [
    {"triggerPct": 5,   "lockHwPct": 0,  "consecutiveBreachesRequired": 3, "_note": "confirms working, no lock"},
    {"triggerPct": 10,  "lockHwPct": 20, "consecutiveBreachesRequired": 3, "_note": "light lock, macro needs room"},
    {"triggerPct": 20,  "lockHwPct": 35, "consecutiveBreachesRequired": 2, "_note": "starting to protect"},
    {"triggerPct": 30,  "lockHwPct": 50, "consecutiveBreachesRequired": 2, "_note": "meaningful lock"},
    {"triggerPct": 50,  "lockHwPct": 70, "consecutiveBreachesRequired": 1, "_note": "tightening"},
    {"triggerPct": 75,  "lockHwPct": 85, "consecutiveBreachesRequired": 1, "_note": "infinite trail"},
]

# Conviction-scaled Phase 1 timing — XYZ needs hours, not minutes
EAGLE_CONVICTION_TIERS = [
    {"minScore": 8,  "absoluteFloorRoe": -20, "hardTimeoutMin": 360, "weakPeakCutMin": 180, "deadWeightCutMin": 90},
    {"minScore": 10, "absoluteFloorRoe": -25, "hardTimeoutMin": 480, "weakPeakCutMin": 240, "deadWeightCutMin": 120},
    {"minScore": 12, "absoluteFloorRoe": -30, "hardTimeoutMin": 600, "weakPeakCutMin": 300, "deadWeightCutMin": 180},
]

EAGLE_STAGNATION_TP = {"enabled": True, "roeMin": 15, "hwStaleMin": 240}


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


def get_leverage_for_score(score):
    """Conviction-scaled leverage. Higher score = higher leverage."""
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


# ═══════════════════════════════════════════════════════════════
# SPREAD GATE
# ═══════════════════════════════════════════════════════════════

def check_spread(asset):
    """Check live order book spread. Returns (spread_pct, bid, ask) or (None, 0, 0)."""
    data = cfg.mcporter_call("market_get_asset_data",
                              asset=f"xyz:{asset}",
                              candle_intervals=[],
                              include_funding=False,
                              include_order_book=True)
    if not data:
        return None, 0, 0

    ad = data.get("data", data)
    if not isinstance(ad, dict):
        return None, 0, 0

    ob = ad.get("order_book", ad.get("orderBook", {}))
    if not isinstance(ob, dict):
        return None, 0, 0

    bids = ob.get("bids", ob.get("bid", []))
    asks = ob.get("asks", ob.get("ask", []))

    if not bids or not asks:
        return None, 0, 0

    best_bid = safe_float(bids[0][0] if isinstance(bids[0], list) else bids[0].get("price", 0))
    best_ask = safe_float(asks[0][0] if isinstance(asks[0], list) else asks[0].get("price", 0))

    if best_bid <= 0 or best_ask <= 0:
        return None, 0, 0

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid

    return spread_pct, best_bid, best_ask


# ═══════════════════════════════════════════════════════════════
# CANDLE DATA FOR SCORING
# ═══════════════════════════════════════════════════════════════

def fetch_candle_data(asset):
    """Fetch 1h and 4h candles for technical scoring."""
    data = cfg.mcporter_call("market_get_asset_data",
                              asset=f"xyz:{asset}",
                              candle_intervals=["1h", "4h"],
                              include_funding=True,
                              include_order_book=False)
    if not data or not data.get("success", not data.get("error")):
        return None
    return data.get("data", data)


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# SM SCANNING (XYZ only, focused assets)
# ═══════════════════════════════════════════════════════════════

def scan_xyz_sm():
    """Fetch SM data for focused XYZ assets only."""
    raw = cfg.mcporter_call("leaderboard_get_markets")
    if not raw:
        return []

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

    # Aggregate per-token (keep highest conviction direction)
    token_best = {}
    for m in markets:
        if not isinstance(m, dict):
            continue

        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()

        if dex != "xyz":
            continue
        if token not in ALLOWED_ASSETS:
            continue

        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        direction = str(m.get("direction", "")).upper()
        price_chg_4h = safe_float(m.get("token_price_change_pct_4h", 0))
        price_chg_1h = safe_float(m.get("token_price_change_pct_1h",
                                   m.get("price_change_1h", 0)))
        contrib_chg_1h = safe_float(m.get("contribution_pct_change_1h", 0))
        contrib_chg_4h = safe_float(m.get("contribution_pct_change_4h", 0))

        if direction not in ("LONG", "SHORT"):
            continue

        entry = {
            "token": token,
            "direction": direction,
            "pct": pct,
            "traders": traders,
            "price_chg_4h": price_chg_4h,
            "price_chg_1h": price_chg_1h,
            "contrib_chg_1h": contrib_chg_1h,
            "contrib_chg_4h": contrib_chg_4h,
        }

        # Keep highest conviction entry per token
        if token not in token_best or pct > token_best[token]["pct"]:
            token_best[token] = entry

    candidates = sorted(token_best.values(), key=lambda x: x["pct"], reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════════
# CONVICTION SCORING (all contributors, no hard gates)
# ═══════════════════════════════════════════════════════════════

def score_candidate(cand, candle_data):
    """Score an XYZ SM candidate. Everything contributes, nothing gates."""
    score = 0
    reasons = []
    direction = cand["direction"]

    # ── SM concentration (0-4 pts) ──
    pct = cand["pct"]
    if pct >= 20:
        score += 4
        reasons.append(f"DOMINANT_SM {pct:.1f}%")
    elif pct >= 10:
        score += 3
        reasons.append(f"HIGH_SM {pct:.1f}%")
    elif pct >= 5:
        score += 2
        reasons.append(f"SOLID_SM {pct:.1f}%")
    elif pct >= 3:
        score += 1
        reasons.append(f"BASE_SM {pct:.1f}%")

    # ── Trader depth (0-2 pts) ──
    traders = cand["traders"]
    if traders >= 100:
        score += 2
        reasons.append(f"DEEP_SM ({traders}t)")
    elif traders >= 30:
        score += 1
        reasons.append(f"SM_ACTIVE ({traders}t)")

    # ── SM contribution velocity (0-2 pts) ──
    # Rising contribution = SM is piling in, not just holding
    c1h = cand["contrib_chg_1h"]
    c4h = cand["contrib_chg_4h"]
    if c1h > 5:
        score += 2
        reasons.append(f"CONTRIB_SURGE_1H +{c1h:.1f}%")
    elif c1h > 2:
        score += 1
        reasons.append(f"CONTRIB_RISING_1H +{c1h:.1f}%")

    if c4h > 3 and c1h > 0:
        score += 1
        reasons.append(f"CONTRIB_SUSTAINED_4H +{c4h:.1f}%")

    # ── 4H price alignment (±2 pts) ──
    p4h = cand["price_chg_4h"]
    if direction == "LONG" and p4h > 0.5:
        score += 2
        reasons.append(f"4H_ALIGNED +{p4h:.1f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 2
        reasons.append(f"4H_ALIGNED {p4h:.1f}%")
    elif direction == "LONG" and p4h > 0:
        score += 1
        reasons.append(f"4H_POSITIVE +{p4h:.2f}%")
    elif direction == "SHORT" and p4h < 0:
        score += 1
        reasons.append(f"4H_POSITIVE {p4h:.2f}%")
    elif direction == "LONG" and p4h < -1:
        score -= 1
        reasons.append(f"4H_OPPOSING {p4h:.1f}%")
    elif direction == "SHORT" and p4h > 1:
        score -= 1
        reasons.append(f"4H_OPPOSING +{p4h:.1f}%")

    # ── Technical scoring from candles (if available) ──
    if candle_data:
        candles_1h = candle_data.get("candles", {}).get("1h", [])
        candles_4h = candle_data.get("candles", {}).get("4h", [])

        # 4H trend structure (0-2 pts)
        if len(candles_4h) >= 6:
            trend_4h, strength = trend_structure(candles_4h)
            if (direction == "LONG" and trend_4h == "BULLISH") or \
               (direction == "SHORT" and trend_4h == "BEARISH"):
                score += 2
                reasons.append(f"4H_TREND_{trend_4h} {strength:.0%}")
            elif trend_4h != "NEUTRAL" and \
                 ((direction == "LONG" and trend_4h == "BEARISH") or
                  (direction == "SHORT" and trend_4h == "BULLISH")):
                score -= 1
                reasons.append(f"4H_TREND_OPPOSING")

        # 1H momentum (0-1 pts)
        if len(candles_1h) >= 4:
            mom_1h = price_momentum(candles_1h, 2)
            if (direction == "LONG" and mom_1h > 0.3) or \
               (direction == "SHORT" and mom_1h < -0.3):
                score += 1
                reasons.append(f"1H_MOMENTUM {mom_1h:+.2f}%")

        # Volume trend (0-1 pts)
        if len(candles_1h) >= 8:
            vol = volume_trend(candles_1h)
            if vol > 15:
                score += 1
                reasons.append(f"VOL_RISING +{vol:.0f}%")

        # Funding alignment (0-1 pts)
        asset_ctx = candle_data.get("asset_context", candle_data)
        funding = safe_float(asset_ctx.get("funding", 0))
        if (direction == "LONG" and funding < -0.005) or \
           (direction == "SHORT" and funding > 0.005):
            score += 1
            reasons.append(f"FUNDING_ALIGNED {funding:+.4f}")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# DSL STATE BUILDER
# ═══════════════════════════════════════════════════════════════

def build_dsl_state(signal, leverage):
    """Build DSL state with XYZ-appropriate wide timings."""
    score = signal.get("score", 8)

    tier = EAGLE_CONVICTION_TIERS[0]
    for ct in EAGLE_CONVICTION_TIERS:
        if score >= ct["minScore"]:
            tier = ct

    return {
        "active": True,
        "asset": f"xyz:{signal['token']}",
        "direction": signal["direction"],
        "score": score,
        "leverage": leverage,
        "phase": 1,
        "highWaterPrice": None,
        "highWaterRoe": None,
        "currentTierIndex": -1,
        "consecutiveBreaches": 0,
        "lockMode": "pct_of_high_water",
        "phase2TriggerRoe": 5,
        "retraceThreshold": 0.12,
        "phase1": {
            "enabled": True,
            "retraceThreshold": 0.12,
            "consecutiveBreachesRequired": 3,
            "phase1MaxMinutes": tier["hardTimeoutMin"],
            "weakPeakCutMinutes": tier["weakPeakCutMin"],
            "deadWeightCutMin": tier["deadWeightCutMin"],
            "absoluteFloorRoe": tier["absoluteFloorRoe"],
            "weakPeakCut": {
                "enabled": True,
                "intervalInMinutes": tier["weakPeakCutMin"],
                "minValue": 3.0,
            },
        },
        "phase2": {
            "enabled": True,
            "retraceThreshold": 0.08,
            "consecutiveBreachesRequired": 2,
        },
        "tiers": EAGLE_DSL_TIERS,
        "stagnationTp": EAGLE_STAGNATION_TP,
        "execution": {
            "phase1SlOrderType": "MARKET",
            "phase2SlOrderType": "MARKET",
            "breachCloseOrderType": "MARKET",
        },
        "_eagle_version": "3.0",
        "_note": "Generated by eagle-scanner v3.0. XYZ-tuned wide DSL. Do not merge with dsl-profile.json.",
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTE TRADE (Wolverine pattern)
# ═══════════════════════════════════════════════════════════════

def execute_entry(signal, margin, leverage):
    """Call create_position directly from the scanner."""
    asset = f"xyz:{signal['token']}"
    direction = signal["direction"]

    result = cfg.mcporter_call(
        "create_position",
        coin=signal["token"],
        direction=direction,
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 45,
        },
        dex="xyz",
    )

    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # v3.0: NO thesis exit. Scanner enters. RatchetStop exits.
    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. RatchetStop manages exit."})
        return

    # Trade counter
    tc = cfg.load_trade_counter()
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
        cfg.save_trade_counter(tc)
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # Scan XYZ SM data (focused assets only)
    candidates = scan_xyz_sm()

    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No SM signals on {', '.join(sorted(ALLOWED_ASSETS))}"})
        return

    # Score, filter, and enter
    held_coins = {p["coin"].upper().replace("XYZ:", "") for p in positions}

    for cand in candidates:
        token = cand["token"]

        if token in held_coins:
            continue

        # Cooldown check
        if cfg.is_on_cooldown(token):
            continue

        # Fetch candle data for technical scoring
        candle_data = fetch_candle_data(token)

        # Score it (all contributors, no hard gates)
        score, reasons = score_candidate(cand, candle_data)
        if score < MIN_SCORE:
            continue

        # Spread gate (this one stays as a hard gate — execution quality)
        spread_pct, bid, ask = check_spread(token)
        if spread_pct is None:
            reasons.append("SPREAD_UNREADABLE")
            continue
        if spread_pct > MAX_SPREAD_PCT:
            reasons.append(f"SPREAD_WIDE {spread_pct*100:.3f}%")
            continue
        reasons.append(f"SPREAD_OK {spread_pct*100:.3f}%")

        # Conviction-scaled leverage
        leverage = get_leverage_for_score(score)

        # Margin
        margin = round(account_value * MARGIN_PCT, 2)

        # Build DSL state
        dsl_state = build_dsl_state(
            {"token": token, "direction": cand["direction"], "score": score},
            leverage,
        )

        # Execute trade directly
        success, result = execute_entry(
            {"token": token, "direction": cand["direction"]},
            margin, leverage,
        )

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            cfg.save_trade_counter(tc)

            cfg.output({
                "status": "ok",
                "action": "ENTRY",
                "signal": {
                    "asset": f"xyz:{token}",
                    "direction": cand["direction"],
                    "score": score,
                    "leverage": leverage,
                    "mode": "XYZ_SM",
                    "reasons": reasons,
                    "smPct": cand["pct"],
                    "smTraders": cand["traders"],
                    "spread": round(spread_pct * 100, 4),
                    "contribChg1h": cand["contrib_chg_1h"],
                    "contribChg4h": cand["contrib_chg_4h"],
                },
                "execution": {
                    "asset": f"xyz:{token}",
                    "direction": cand["direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "dslState": dsl_state,
                "result": result,
                "constraints": {
                    "allowedAssets": sorted(ALLOWED_ASSETS),
                    "maxPositions": MAX_POSITIONS,
                    "maxLeverage": MAX_LEVERAGE,
                    "leverageTiers": LEVERAGE_TIERS,
                    "maxDailyEntries": MAX_DAILY_ENTRIES,
                    "cooldownMinutes": COOLDOWN_MINUTES,
                    "maxSpreadPct": MAX_SPREAD_PCT,
                    "minScore": MIN_SCORE,
                    "_dslNote": "Use dslState directly. XYZ-tuned wide timings. Do NOT merge with dsl-profile.json.",
                },
                "_eagle_version": "3.0",
            })
            return
        else:
            cfg.output({
                "status": "ok",
                "action": "ENTRY_FAILED",
                "signal": {"asset": f"xyz:{token}", "direction": cand["direction"],
                           "score": score, "reasons": reasons},
                "error": result,
                "_eagle_version": "3.0",
            })
            return

    # No candidates passed
    best_seen = candidates[0] if candidates else None
    note = f"{len(candidates)} XYZ candidates"
    if best_seen:
        note += f", best: {best_seen['token']} {best_seen['direction']} {best_seen['pct']:.1f}% SM"
    note += f" — none passed score {MIN_SCORE} + spread gate"

    cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
