#!/usr/bin/env python3
# Senpi BISON Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BISON Scanner v2.0 — Conviction Holder (Hardened for Runtime).

v2.0 changes from fleet audit + hardening pass:
- ALL hard gates converted to score contributors. Nothing kills a signal
  before it can be scored. The score threshold (minScore=8) is the ONLY gate.
- Thesis exit REMOVED. Scanner enters. RatchetStop exits. Scanner must NEVER
  re-evaluate or close open positions. Wolverine v1.1 lost -22.7% because
  the scanner killed 25 of 27 trades on "thesis invalidation."
- Scanner calls create_position internally via mcporter (Wolverine pattern).
  The cron does NOT parse output and execute — the scanner handles execution.
- ensureExecutionAsTaker: false — explicit in every entry call.
- 4H trend alignment: was HARD GATE → now +3 score points.
- 1H trend agreement: was HARD GATE → now +2 score points.
- 1H momentum: was HARD GATE → now +1/+2 score points by strength.
- SM alignment: was HARD BLOCK → now +2 (aligned) or -2 (opposing) score.
- XYZ ban, leverage cap, daily entry cap, cooldowns: unchanged.
- DSL state template: unchanged (same Bison-specific wide tiers).

The big-game hunter. Fewer trades, longer holds, bigger moves.
Runs every 5 minutes.
"""

import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bison_config as cfg

# ─── Hardcoded Constants ─────────────────────────────────────
MAX_LEVERAGE = 10
MIN_LEVERAGE = 7
XYZ_BANNED = True
MAX_DAILY_LOSS_PCT = 10
MAX_POSITIONS = 3

# Bison-specific DSL — wider tiers than Orca for conviction holds
BISON_DSL_TIERS = [
    {"triggerPct": 10, "lockHwPct": 0,  "consecutiveBreachesRequired": 3, "_note": "confirms working, no lock — BISON breathes"},
    {"triggerPct": 20, "lockHwPct": 25, "consecutiveBreachesRequired": 3, "_note": "light lock, wide room for pullbacks"},
    {"triggerPct": 30, "lockHwPct": 40, "consecutiveBreachesRequired": 2, "_note": "starting to protect"},
    {"triggerPct": 50, "lockHwPct": 60, "consecutiveBreachesRequired": 2, "_note": "meaningful lock"},
    {"triggerPct": 75, "lockHwPct": 75, "consecutiveBreachesRequired": 1, "_note": "tightening"},
    {"triggerPct": 100,"lockHwPct": 85, "consecutiveBreachesRequired": 1, "_note": "infinite trail at 85%"},
]

BISON_CONVICTION_TIERS = [
    {"minScore": 6,  "absoluteFloorRoe": -25, "hardTimeoutMin": 60,  "weakPeakCutMin": 30, "deadWeightCutMin": 30},
    {"minScore": 8,  "absoluteFloorRoe": -30, "hardTimeoutMin": 90,  "weakPeakCutMin": 45, "deadWeightCutMin": 45},
    {"minScore": 10, "absoluteFloorRoe": -35, "hardTimeoutMin": 120, "weakPeakCutMin": 60, "deadWeightCutMin": 60},
]

BISON_STAGNATION_TP = {"enabled": True, "roeMin": 15, "hwStaleMin": 120}


# ─── Per-Asset Cooldown ──────────────────────────────────────
COOLDOWN_FILE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "bison-strategy" / "state" / "asset-cooldowns.json"

def load_cooldowns():
    try:
        if COOLDOWN_FILE.exists():
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}

def is_asset_cooled_down(coin, cooldown_minutes=120):
    cooldowns = load_cooldowns()
    if coin not in cooldowns:
        return False
    exit_ts = cooldowns[coin].get("exitTimestamp", 0)
    elapsed = (time.time() - exit_ts) / 60
    return elapsed < cooldown_minutes


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
    """Check if candles form higher lows (bullish) or lower highs (bearish)."""
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


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


# ─── Data Fetchers ────────────────────────────────────────────

def get_top_assets(n=10):
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        vol = float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        if coin and vol > 0:
            assets.append({"coin": coin, "volume": vol, "price": mark_px})
    assets.sort(key=lambda x: x["volume"], reverse=True)
    return assets[:n]


def get_sm_direction(coin):
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    coin_long_pct = 0
    coin_short_pct = 0
    found = False

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != coin:
            continue
        found = True
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if direction == "long":
            coin_long_pct = pct
        elif direction == "short":
            coin_short_pct = pct

    if not found:
        return None, 0

    total = coin_long_pct + coin_short_pct
    if total == 0:
        return "NEUTRAL", 50
    long_ratio = (coin_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return "LONG", long_ratio
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio
    return "NEUTRAL", 50


# ─── Thesis Builder (v2.0 — no hard gates, everything scores) ─

def build_thesis(coin, entry_cfg):
    """Build conviction thesis. ALL signals are score contributors.
    Nothing returns None before scoring. The minScore threshold is the only gate."""

    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    asset_ctx = data.get("data", {}).get("asset_context", data.get("data", {}))
    funding = float(asset_ctx.get("funding", data.get("data", {}).get("funding", 0)))

    # Need minimum candle data to score anything
    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None

    price = float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0))) if candles_15m else 0

    # ── Determine direction from strongest available signal ──
    # Priority: 4H trend > SM direction > 1H momentum
    trend_4h, trend_strength = trend_structure(candles_4h)
    trend_1h, trend_1h_strength = trend_structure(candles_1h)
    sm_dir, sm_pct = get_sm_direction(coin)
    mom_1h = price_momentum(candles_1h, 2)

    direction = None
    direction_source = None

    if trend_4h == "BULLISH":
        direction = "LONG"
        direction_source = "4h_trend"
    elif trend_4h == "BEARISH":
        direction = "SHORT"
        direction_source = "4h_trend"
    elif sm_dir and sm_dir != "NEUTRAL":
        direction = sm_dir
        direction_source = "sm_direction"
    elif mom_1h > 0.5:
        direction = "LONG"
        direction_source = "1h_momentum"
    elif mom_1h < -0.5:
        direction = "SHORT"
        direction_source = "1h_momentum"

    if direction is None:
        # No signal at all — genuinely nothing to score
        return None

    # ── SCORING — everything contributes, nothing gates ──
    score = 0
    reasons = []

    # 4H trend structure (0-3 pts)
    if trend_4h != "NEUTRAL":
        if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
            score += 3
            reasons.append(f"4h_{trend_4h.lower()}_{trend_strength:.0%}")
        else:
            # 4H trend opposes direction — penalty
            score -= 1
            reasons.append(f"4h_opposing_{trend_4h.lower()}")

    # 1H trend agreement (0-2 pts)
    if trend_1h != "NEUTRAL":
        if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
            score += 2
            reasons.append(f"1h_confirms_{trend_1h.lower()}")
        else:
            score -= 1
            reasons.append(f"1h_opposing_{trend_1h.lower()}")

    # 1H momentum (0-2 pts)
    if direction == "LONG":
        if mom_1h >= 1.0:
            score += 2
            reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h >= 0.5:
            score += 1
            reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h < -0.5:
            score -= 1
            reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")
    else:  # SHORT
        if mom_1h <= -1.0:
            score += 2
            reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h <= -0.5:
            score += 1
            reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h > 0.5:
            score -= 1
            reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")

    # SM alignment (±2 pts)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        score -= 2
        reasons.append(f"sm_opposing_{sm_dir}")

    # Funding alignment (±2 pts)
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_aligned_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append("funding_crowded")

    # Volume trend (0-1 pts)
    vol_1h = volume_trend(candles_1h)
    if vol_1h > entry_cfg.get("minVolTrendPct", 10):
        score += 1
        reasons.append(f"vol_rising_{vol_1h:+.0f}%")

    # OI proxy — volume acceleration (0-1 pts)
    vol_recent = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:])
    vol_earlier = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # RSI filter — only penalty for extreme, bonus for room (±1 pt)
    closes_1h = [float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > entry_cfg.get("rsiMaxLong", 72):
        score -= 1
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif direction == "SHORT" and rsi < entry_cfg.get("rsiMinShort", 28):
        score -= 1
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4H momentum strength (0-1 pts)
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score += 1
            reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "directionSource": direction_source,
        "price": price, "trend_4h": trend_4h, "momentum_1h": mom_1h,
        "momentum_4h": mom_4h, "sm_direction": sm_dir, "funding": funding,
        "rsi": rsi, "volume_trend": vol_1h,
    }


# ─── DSL State Builder ────────────────────────────────────────

def build_dsl_state_template(signal):
    """Build the EXACT DSL state file for a Bison signal.
    Agent writes this directly — no merging with dsl-profile.json."""
    score = signal.get("score", 6)

    tier = BISON_CONVICTION_TIERS[0]
    for ct in BISON_CONVICTION_TIERS:
        if score >= ct["minScore"]:
            tier = ct

    return {
        "active": True,
        "asset": signal.get("coin", ""),
        "direction": signal.get("direction", ""),
        "score": score,
        "phase": 1,
        "highWaterPrice": None,
        "highWaterRoe": None,
        "currentTierIndex": -1,
        "consecutiveBreaches": 0,
        "lockMode": "pct_of_high_water",
        "phase2TriggerRoe": 10,
        "phase1": {
            "enabled": True,
            "retraceThreshold": 0.03,
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
            "retraceThreshold": 0.015,
            "consecutiveBreachesRequired": 2,
        },
        "tiers": BISON_DSL_TIERS,
        "stagnationTp": BISON_STAGNATION_TP,
        "convictionTiers": BISON_CONVICTION_TIERS,
        "execution": {
            "phase1SlOrderType": "MARKET",
            "phase2SlOrderType": "MARKET",
            "breachCloseOrderType": "MARKET",
        },
        "_bison_version": "2.0",
        "_note": "Generated by bison-scanner.py. Do not modify. Do not merge with dsl-profile.json.",
    }


# ─── Execute Trade (Wolverine pattern) ────────────────────────

def execute_entry(signal, margin, leverage, wallet, strategy_id):
    """Call create_position directly from the scanner.
    The cron does NOT parse output and execute — we do it here."""
    coin = signal["coin"]
    direction = signal["direction"]

    result = cfg.mcporter_call(
        "create_position",
        coin=coin,
        direction=direction,
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 30,
        },
    )

    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


# ─── Main ─────────────────────────────────────────────────────

def run():
    config = cfg.load_config()
    wallet, strategy_id = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") != "OPEN":
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"gate={tc['gate']}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    max_positions = config.get("maxPositions", MAX_POSITIONS)
    active_coins = {p["coin"]: p for p in positions}
    entry_cfg = config.get("entry", {})
    cooldown_min = config.get("risk", {}).get("cooldownMinutes", 120)

    # v2.0: NO thesis re-evaluation. Scanner enters. RatchetStop exits.
    # The evaluate_held_position function has been REMOVED.

    # Dynamic entry cap (v1.1 — batch reload when profitable)
    dynamic = entry_cfg.get("dynamicSlots", {})
    if dynamic.get("enabled", True):
        base_max = dynamic.get("baseMax", 3)
        day_pnl = tc.get("realizedPnl", 0)
        entries_used = tc.get("entries", 0)

        if day_pnl >= 0 and entries_used >= base_max:
            batches_used = entries_used // base_max
            effective_max = (batches_used + 1) * base_max
            hard_max = dynamic.get("absoluteMax", 6)
            for t in dynamic.get("unlockThresholds", []):
                if day_pnl >= t.get("pnl", 999999):
                    hard_max = max(hard_max, t.get("maxEntries", hard_max))
            max_entries = effective_max
        elif day_pnl < 0:
            effective_max = base_max
            for t in dynamic.get("unlockThresholds", []):
                if day_pnl >= t.get("pnl", 999999):
                    effective_max = t.get("maxEntries", effective_max)
            max_entries = min(effective_max, dynamic.get("absoluteMax", 6))
        else:
            max_entries = base_max
    else:
        max_entries = config.get("risk", {}).get("maxEntriesPerDay", 4)

    if tc.get("entries", 0) >= max_entries:
        pnl_status = "positive" if tc.get("realizedPnl", 0) >= 0 else "negative"
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"max entries ({max_entries}), pnl={pnl_status}"})
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        return

    # Scan top assets for conviction thesis
    top_n = config.get("topAssets", 10)
    candidates = get_top_assets(top_n)
    min_score = entry_cfg.get("minScore", 8)
    signals = []

    for asset in candidates:
        coin = asset["coin"]

        # HARDCODED: XYZ ban
        if coin.lower().startswith("xyz:"):
            continue

        if coin in active_coins:
            continue

        # Per-asset cooldown after losses
        if is_asset_cooled_down(coin, cooldown_min):
            continue

        thesis = build_thesis(coin, entry_cfg)
        if thesis and thesis["score"] >= min_score:
            thesis["dslState"] = build_dsl_state_template(thesis)
            signals.append(thesis)

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"WAITING — scanned top {len(candidates)}, no conviction thesis (min score {min_score})"})
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Conviction-scaled margin
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["score"] >= 12:
        margin_pct = base_margin_pct * 1.5
    elif best["score"] >= 10:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)

    # HARDCODED: leverage cap
    leverage = min(config.get("leverage", {}).get("default", 10), MAX_LEVERAGE)

    # Execute trade directly (Wolverine pattern)
    success, result = execute_entry(best, margin, leverage, wallet, strategy_id)

    if success:
        # Increment trade counter
        cfg.increment_entry(tc)

        cfg.output({
            "success": True,
            "action": "ENTRY",
            "signal": best,
            "execution": {
                "coin": best["coin"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "result": result,
            "scanned": len(candidates),
            "candidates": len(signals),
            "constraints": {
                "minLeverage": MIN_LEVERAGE,
                "maxLeverage": MAX_LEVERAGE,
                "maxPositions": max_positions,
                "xyzBanned": XYZ_BANNED,
                "_dslNote": "Use signal.dslState as the DSL state file. Do NOT merge with dsl-profile.json.",
            },
            "_bison_version": "2.0",
        })
    else:
        cfg.output({
            "success": False,
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": result,
            "_bison_version": "2.0",
        })


if __name__ == "__main__":
    run()
