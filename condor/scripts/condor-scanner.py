#!/usr/bin/env python3
# Senpi CONDOR Scanner v1.0.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CONDOR v1.0 — Multi-Asset Alpha Hunter with Position Lifecycle.

Grizzly's three-mode lifecycle (HUNTING → RIDING → STALKING → RELOAD)
across BTC, ETH, SOL, and HYPE simultaneously. Evaluates all four assets
every scan and commits to the single strongest thesis.

The insight: single-asset hunters (Polar, Wolverine, Grizzly) are idle when
their asset isn't moving. Condor is always in the best trade available.

When HUNTING: scores all 4 assets, enters the highest conviction thesis (10+).
When RIDING: monitors the active position's thesis. If it breaks, exits and
  goes back to HUNTING across all 4 (not STALKING — thesis died).
When STALKING (after DSL exit): watches the SAME asset for reload. If reload
  conditions pass, re-enters. If thesis dies during stalk, resets to HUNTING.

Each asset uses the others as correlation confirmation:
  BTC → ETH as correlation
  ETH → BTC as correlation
  SOL → BTC as correlation
  HYPE → BTC as bonus-only (HYPE moves independently)

Runs every 3 minutes. One position at a time. Maximum conviction.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import condor_config as cfg

# Assets Condor watches
ASSETS = ["BTC", "ETH", "SOL", "HYPE"]

# Correlation pairs — which asset confirms which
CORRELATION_MAP = {
    "BTC": "ETH",
    "ETH": "BTC",
    "SOL": "BTC",
    "HYPE": "BTC",  # bonus-only for HYPE
}

# HYPE's BTC correlation is bonus-only, never a block
BONUS_ONLY_CORRELATION = {"HYPE"}

# ─── Hardcoded Constants ─────────────────────────────────────

MAX_LEVERAGE = 10
MIN_LEVERAGE = 7

CONDOR_DSL_TIERS = [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

CONDOR_CONVICTION_TIERS = [
    {"minScore": 8,  "absoluteFloorRoe": -25, "hardTimeoutMin": 45, "weakPeakCutMin": 20, "deadWeightCutMin": 15},
    {"minScore": 10, "absoluteFloorRoe": -30, "hardTimeoutMin": 60, "weakPeakCutMin": 30, "deadWeightCutMin": 20},
    {"minScore": 12, "absoluteFloorRoe": -35, "hardTimeoutMin": 90, "weakPeakCutMin": 45, "deadWeightCutMin": 30},
]

CONDOR_STAGNATION_TP = {"enabled": True, "roeMin": 10, "hwStaleMin": 45}


# ─── Technical Helpers ────────────────────────────────────────

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


def volume_ratio(candles, lookback=5):
    if len(candles) < lookback * 2:
        return 1.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles]
    recent = sum(vols[-lookback:]) / lookback
    earlier = sum(vols[-lookback * 2:-lookback]) / lookback
    if earlier == 0:
        return 1.0
    return recent / earlier


# ─── Data Fetching ───────────────────────────────────────────

def get_asset_data(asset):
    data = cfg.mcporter_call("market_get_asset_data", asset=asset,
                              candle_intervals=["5m", "15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    return data.get("data", data)


def get_correlation_data(asset):
    corr_asset = CORRELATION_MAP.get(asset, "BTC")
    data = cfg.mcporter_call("market_get_asset_data", asset=corr_asset,
                              candle_intervals=["15m", "1h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return None, None
    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    mom_15m = price_momentum(candles_15m, 1) if len(candles_15m) >= 2 else 0
    mom_1h = price_momentum(candles_1h, 1) if len(candles_1h) >= 2 else 0
    return mom_15m, mom_1h


def get_sm_direction(asset):
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0, 0

    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    asset_long_pct = 0
    asset_short_pct = 0
    asset_traders = 0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != asset:
            continue
        found = True
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)))
        if direction == "long":
            asset_long_pct = pct
            asset_traders += traders
        elif direction == "short":
            asset_short_pct = pct
            asset_traders += traders

    if not found:
        return None, 0, 0

    total = asset_long_pct + asset_short_pct
    if total == 0:
        return "NEUTRAL", 50, asset_traders
    long_ratio = (asset_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return "LONG", long_ratio, asset_traders
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio, asset_traders
    return "NEUTRAL", 50, asset_traders


# ─── Generic Thesis Builder ──────────────────────────────────

def build_thesis(asset, entry_cfg):
    """Build conviction thesis for any asset. Returns thesis dict or None."""
    asset_data = get_asset_data(asset)
    if not asset_data:
        return None

    candles_5m = asset_data.get("candles", {}).get("5m", [])
    candles_15m = asset_data.get("candles", {}).get("15m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    asset_ctx = asset_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", asset_data.get("funding", 0)))
    oi = float(asset_ctx.get("openInterest", asset_data.get("openInterest", 0)))

    if len(candles_5m) < 12 or len(candles_15m) < 8 or len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = float(candles_5m[-1].get("close", candles_5m[-1].get("c", 0)))

    # REQUIRED: 4h trend
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None

    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # REQUIRED: 1h agrees
    trend_1h, trend_strength_1h = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # REQUIRED: 15m momentum
    mom_15m = price_momentum(candles_15m, 1)
    min_mom = entry_cfg.get("minMom15mPct", 0.1)
    if direction == "LONG" and mom_15m < min_mom:
        return None
    if direction == "SHORT" and mom_15m > -min_mom:
        return None

    score = 0
    reasons = []

    # 4h trend (3 pts)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{trend_strength_4h:.0%}")

    # 1h confirms (2 pts)
    mom_1h = price_momentum(candles_1h, 2)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # 15m momentum (1 pt)
    score += 1
    reasons.append(f"15m_{mom_15m:+.2f}%")

    # 4TF alignment bonus
    trend_15m, _ = trend_structure(candles_15m, 4)
    trend_5m, _ = trend_structure(candles_5m, 6)
    if trend_15m == trend_4h and trend_5m == trend_4h:
        score += 1
        reasons.append("4TF_aligned")

    # SM direction
    sm_dir, sm_pct, sm_count = get_sm_direction(asset)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}traders")
        if sm_pct >= 70:
            score += 1
            reasons.append("sm_strongly_tilted")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return None  # SM opposes — hard block

    # Funding alignment
    funding_ann = abs(funding) * 8760 * 100
    if direction == "LONG" and funding < 0:
        score += 1
        reasons.append(f"funding_pays_longs_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 1
        reasons.append(f"funding_pays_shorts_{funding:+.4f}")

    # Volume
    vol = volume_ratio(candles_1h)
    if vol > 1.15:
        score += 1
        reasons.append(f"vol_rising_{(vol-1)*100:+.0f}%")

    # Correlation
    corr_15m, corr_1h = get_correlation_data(asset)
    if corr_15m is not None and corr_1h is not None:
        corr_agrees = (direction == "LONG" and corr_15m > 0 and corr_1h > 0) or \
                     (direction == "SHORT" and corr_15m < 0 and corr_1h < 0)
        if corr_agrees:
            bonus = 2 if asset in BONUS_ONLY_CORRELATION else 1
            score += bonus
            corr_asset = CORRELATION_MAP[asset]
            reasons.append(f"{corr_asset.lower()}_confirms_{corr_1h:+.2f}%")

    return {
        "asset": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "funding": funding,
        "oi": oi,
    }


# ─── Thesis Re-Evaluation ────────────────────────────────────

def evaluate_position(asset, direction, entry_cfg):
    """Re-evaluate thesis for held position. Returns (still_valid, invalidation_reasons)."""
    asset_data = get_asset_data(asset)
    if not asset_data:
        return True, ["data_unavailable_hold"]

    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    asset_ctx = asset_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", asset_data.get("funding", 0)))

    if len(candles_4h) < 6:
        return True, ["insufficient_data_hold"]

    invalidations = []

    # 4h trend flipped?
    trend_4h, _ = trend_structure(candles_4h)
    if direction == "LONG" and trend_4h == "BEARISH":
        invalidations.append("4h_trend_flipped_bearish")
    elif direction == "SHORT" and trend_4h == "BULLISH":
        invalidations.append("4h_trend_flipped_bullish")

    # SM flipped against?
    sm_dir, sm_pct, _ = get_sm_direction(asset)
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        invalidations.append(f"sm_flipped_{sm_dir}_{sm_pct:.0f}%")

    # Funding extreme?
    threshold = entry_cfg.get("fundingExtremeThreshold", 0.012)
    if direction == "LONG" and funding > threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")
    elif direction == "SHORT" and funding < -threshold:
        invalidations.append(f"funding_extreme_{funding:+.4f}")

    # Volume died?
    if len(candles_1h) >= 12:
        recent_vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]]
        avg_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-12:-3]) / 9
        if avg_vol > 0 and all(v < avg_vol * 0.3 for v in recent_vols):
            invalidations.append("volume_dried_up_3h")

    # Correlation divergence — NOT for HYPE (moves independently)
    if asset not in BONUS_ONLY_CORRELATION:
        corr_15m, corr_1h = get_correlation_data(asset)
        if corr_1h is not None:
            if direction == "LONG" and corr_1h < -1.0:
                invalidations.append(f"correlation_diverging_{corr_1h:+.1f}%")
            elif direction == "SHORT" and corr_1h > 1.0:
                invalidations.append(f"correlation_diverging_{corr_1h:+.1f}%")

    return (len(invalidations) == 0), invalidations


# ─── Stalk/Reload Evaluation ─────────────────────────────────

def evaluate_reload(exit_state, entry_cfg):
    """Check reload conditions on the same asset after DSL exit."""
    direction = exit_state.get("exitDirection", "LONG")
    asset = exit_state.get("exitAsset", "BTC")
    exit_ts = exit_state.get("exitTimestamp", 0)
    exit_vol = exit_state.get("exitEntryVolRatio", 1.0)

    stalk_cfg = entry_cfg.get("stalking", {})
    max_stalk_hours = stalk_cfg.get("maxStalkHours", 4)
    hours_stalking = (cfg.now_ts() - exit_ts) / 3600

    if hours_stalking > max_stalk_hours:
        return False, [f"stalk_timeout_{hours_stalking:.1f}h"]

    asset_data = get_asset_data(asset)
    if not asset_data:
        return False, ["data_unavailable"]

    candles_5m = asset_data.get("candles", {}).get("5m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    asset_ctx = asset_data.get("asset_context", {})
    funding = float(asset_ctx.get("funding", asset_data.get("funding", 0)))
    funding_ann = abs(funding) * 8760 * 100

    kill_reasons = []
    reload_checks = []

    # KILL: 4h trend reversed
    trend_4h, _ = trend_structure(candles_4h)
    expected = "BULLISH" if direction == "LONG" else "BEARISH"
    if trend_4h != expected and trend_4h != "NEUTRAL":
        kill_reasons.append(f"4h_trend_reversed_{trend_4h}")

    # KILL: SM flipped
    sm_dir, sm_pct, _ = get_sm_direction(asset)
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        kill_reasons.append(f"sm_flipped_{sm_dir}")

    if kill_reasons:
        return False, kill_reasons

    # RELOAD CHECK 1: Wait at least 30 min
    if hours_stalking < 0.5:
        reload_checks.append("waiting_for_cooldown")

    # RELOAD CHECK 2: Fresh 5m impulse
    if len(candles_5m) >= 3:
        mom = price_momentum(candles_5m, 1)
        if direction == "LONG" and mom > 0.15:
            reload_checks.append(f"fresh_impulse_{mom:+.2f}%")
        elif direction == "SHORT" and mom < -0.15:
            reload_checks.append(f"fresh_impulse_{mom:+.2f}%")
        else:
            reload_checks.append("no_impulse")

    # RELOAD CHECK 3: Volume alive
    vol = volume_ratio(candles_5m)
    min_vol_pct = stalk_cfg.get("minReloadVolPct", 50)
    if vol >= exit_vol * min_vol_pct / 100:
        reload_checks.append(f"vol_ok_{vol:.1f}x")
    else:
        reload_checks.append(f"vol_weak_{vol:.1f}x")

    # RELOAD CHECK 4: SM aligned
    if sm_dir == direction:
        reload_checks.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir == "NEUTRAL":
        reload_checks.append("sm_neutral_ok")
    else:
        reload_checks.append(f"sm_not_aligned_{sm_dir}")

    # RELOAD CHECK 5: 4h intact
    if trend_4h == expected:
        reload_checks.append("4h_intact")
    else:
        reload_checks.append(f"4h_{trend_4h}")

    fails = [r for r in reload_checks if any(bad in r for bad in
              ["no_impulse", "vol_weak", "sm_not_aligned", "waiting_for"])]

    return (not fails), reload_checks


# ─── DSL State Builder ───────────────────────────────────────

def build_dsl_state_template(asset, direction, score):
    tier = CONDOR_CONVICTION_TIERS[0]
    for ct in CONDOR_CONVICTION_TIERS:
        if score >= ct["minScore"]:
            tier = ct

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
            "phase1MaxMinutes": tier["hardTimeoutMin"],
            "weakPeakCutMinutes": tier["weakPeakCutMin"],
            "deadWeightCutMin": tier["deadWeightCutMin"],
            "absoluteFloorRoe": tier["absoluteFloorRoe"],
            "weakPeakCut": {"enabled": True, "intervalInMinutes": tier["weakPeakCutMin"], "minValue": 3.0},
        },
        "phase2": {"enabled": True, "retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
        "tiers": CONDOR_DSL_TIERS,
        "stagnationTp": CONDOR_STAGNATION_TP,
        "execution": {"phase1SlOrderType": "MARKET", "phase2SlOrderType": "MARKET", "breachCloseOrderType": "MARKET"},
        "_condor_version": "1.0.1",
    }


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
    state = cfg.load_state("condor-state.json")
    current_mode = state.get("currentMode", "HUNTING")

    # Find any position in our asset list
    active_position = None
    for p in positions:
        if p["coin"] in ASSETS:
            active_position = p
            break

    # ── MODE 2: RIDING ────────────────────────────────────────
    if active_position and current_mode in ("RIDING", "HUNTING"):
        asset = active_position["coin"]
        if current_mode != "RIDING":
            state["currentMode"] = "RIDING"
            state["activeAsset"] = asset
            cfg.save_state(state, "condor-state.json")

        still_valid, reasons = evaluate_position(asset, active_position["direction"], entry_cfg)
        if not still_valid:
            cfg.output({
                "success": True,
                "action": "thesis_exit",
                "exits": [{
                    "coin": asset,
                    "direction": active_position["direction"],
                    "reasons": reasons,
                    "upnl": active_position.get("upnl", 0),
                }],
                "note": f"{asset} thesis invalidated — conviction broken",
            })
            # Thesis died → HUNTING across all assets (not STALKING)
            state["currentMode"] = "HUNTING"
            state.pop("exitState", None)
            state.pop("activeAsset", None)
            cfg.save_state(state, "condor-state.json")
            return

        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {asset} {active_position['direction']} thesis intact"})
        return

    # ── Detect DSL exit: was RIDING, now no position ──────────
    if not active_position and current_mode == "RIDING":
        asset = state.get("activeAsset", "BTC")
        asset_data = get_asset_data(asset)
        exit_vol = 1.0
        if asset_data:
            candles_5m = asset_data.get("candles", {}).get("5m", [])
            exit_vol = volume_ratio(candles_5m) if candles_5m else 1.0

        state["currentMode"] = "STALKING"
        state["exitState"] = {
            "exitAsset": asset,
            "exitDirection": state.get("lastDirection", "LONG"),
            "exitTimestamp": cfg.now_ts(),
            "exitEntryVolRatio": exit_vol,
        }
        cfg.save_state(state, "condor-state.json")
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"DSL closed {asset} — transitioning to STALKING"})
        return

    # ── MODE 3: STALKING ──────────────────────────────────────
    if current_mode == "STALKING":
        exit_state = state.get("exitState", {})
        if not exit_state:
            state["currentMode"] = "HUNTING"
            cfg.save_state(state, "condor-state.json")
        else:
            should_reload, reasons = evaluate_reload(exit_state, entry_cfg)

            if should_reload:
                asset = exit_state["exitAsset"]
                direction = exit_state["exitDirection"]
                leverage = min(config.get("leverage", {}).get("default", 10), MAX_LEVERAGE)
                margin = round(account_value * 0.35, 2)  # Reload at 35% — thesis confirmed by parent

                state["currentMode"] = "RIDING"
                state["activeAsset"] = asset
                state["lastDirection"] = direction
                state.pop("exitState", None)
                cfg.save_state(state, "condor-state.json")

                cfg.output({
                    "success": True,
                    "action": "reload",
                    "entry": {
                        "coin": asset, "direction": direction,
                        "leverage": leverage, "margin": margin,
                        "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
                    },
                    "dslState": build_dsl_state_template(asset, direction, 10),
                    "reasons": reasons,
                    "note": f"STALKING → RELOAD: re-entering {asset} {direction}",
                })
                return

            # Kill conditions?
            kill_signals = [r for r in reasons if any(k in r for k in
                           ["stalk_timeout", "4h_trend_reversed", "sm_flipped"])]
            if kill_signals:
                state["currentMode"] = "HUNTING"
                state.pop("exitState", None)
                state.pop("activeAsset", None)
                cfg.save_state(state, "condor-state.json")
                cfg.output({"success": True, "heartbeat": "NO_REPLY",
                             "note": f"STALKING → RESET: {kill_signals[0]}"})
                return

            hours = (cfg.now_ts() - exit_state.get("exitTimestamp", cfg.now_ts())) / 3600
            asset = exit_state.get("exitAsset", "?")
            cfg.output({"success": True, "heartbeat": "NO_REPLY",
                         "note": f"STALKING {asset} {hours:.1f}h — waiting for reload"})
            return

    # ── MODE 1: HUNTING (across ALL assets) ───────────────────
    min_score = entry_cfg.get("minScore", 10)
    theses = []

    for asset in ASSETS:
        thesis = build_thesis(asset, entry_cfg)
        if thesis and thesis["score"] >= min_score:
            theses.append(thesis)

    if not theses:
        scores = []
        for asset in ASSETS:
            thesis = build_thesis(asset, entry_cfg)
            if thesis:
                scores.append(f"{asset}:{thesis['score']}")
            else:
                scores.append(f"{asset}:no_thesis")
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"HUNTING — {', '.join(scores)}"})
        return

    # Pick the highest scoring thesis
    theses.sort(key=lambda t: t["score"], reverse=True)
    best = theses[0]

    # Leverage and margin — Condor commits to ONE position across 4 assets.
    # When it enters, this is the best thesis available. Scale margin by conviction.
    leverage = min(config.get("leverage", {}).get("default", 10), MAX_LEVERAGE)
    if best["score"] >= 14:
        margin_pct = 0.45  # Extreme conviction — best of 4 assets, all aligned
    elif best["score"] >= 12:
        margin_pct = 0.35  # Strong thesis across all 4 scored
    else:
        margin_pct = 0.25  # Base conviction
    margin = round(account_value * margin_pct, 2)

    state["currentMode"] = "RIDING"
    state["activeAsset"] = best["asset"]
    state["lastDirection"] = best["direction"]
    cfg.save_state(state, "condor-state.json")

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["asset"], "direction": best["direction"],
            "leverage": leverage, "margin": margin,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "dslState": build_dsl_state_template(best["asset"], best["direction"], best["score"]),
        "constraints": {
            "minLeverage": MIN_LEVERAGE,
            "maxLeverage": MAX_LEVERAGE,
            "stagnationTp": CONDOR_STAGNATION_TP,
            "dslTiers": CONDOR_DSL_TIERS,
        },
        "allTheses": [{"asset": t["asset"], "score": t["score"], "direction": t["direction"]} for t in theses],
        "note": f"HUNTING → ENTER: {best['asset']} {best['direction']} score {best['score']} (best of {len(theses)} candidates)",
    })


if __name__ == "__main__":
    run()
