#!/usr/bin/env python3
# Senpi RHINO Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""RHINO Scanner — Momentum Pyramider.

Scouts top 10 assets by OI + volume. Enters small on high conviction (30% of
max position). Adds to winners at +10% ROE (40% more) and +20% ROE (final 30%).
DSL High Water Mode trails the full position.

The only skill in the zoo that adds to winning positions instead of
entering full size and hoping.

Three stages:
  1. SCOUT — 30% position on score 10+ convergence
  2. CONFIRM — +40% at +10% ROE if thesis still holds
  3. CONVICTION — +30% at +20% ROE if trend intact

Runs every 3 minutes.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rhino_config as cfg


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


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


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
    """Top N assets by combined OI + volume ranking."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    assets = []
    for inst in instruments:
        coin = inst.get("coin") or inst.get("name", "")
        oi = float(inst.get("openInterest", 0))
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        vol = float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        # Handle nested context structure
        ctx = inst.get("context", {})
        if ctx:
            mark_px = float(ctx.get("markPx", mark_px))
            vol = float(ctx.get("dayNtlVlm", vol))
            oi = float(ctx.get("openInterest", oi))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if coin and oi_usd > 5_000_000 and vol > 0:
            assets.append({"coin": coin, "oi_usd": oi_usd, "volume": vol, "price": mark_px})
    # Rank by combined OI + volume
    assets.sort(key=lambda x: x["oi_usd"] + x["volume"], reverse=True)
    return assets[:n]


def get_sm_direction(coin):
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))
    for m in markets:
        if m.get("coin", m.get("asset", "")) == coin:
            long_pct = float(m.get("longPct", m.get("pctOfGainsLong", 50)))
            if long_pct > 58:
                return "LONG", long_pct
            elif long_pct < 42:
                return "SHORT", 100 - long_pct
            return "NEUTRAL", 50
    return None, 0


# ─── Thesis Builder ───────────────────────────────────────────

def build_thesis(coin, entry_cfg):
    """Multi-signal convergence thesis — same quality bar as BISON/GRIZZLY."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m", "1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    funding = float(data.get("data", {}).get("funding", 0))

    if len(candles_1h) < 8 or len(candles_4h) < 6:
        return None

    price = float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0))) if candles_15m else 0

    # REQUIRED: 4h trend
    trend_4h, strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # REQUIRED: 1h agrees
    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    # REQUIRED: 1h momentum
    mom_1h = price_momentum(candles_1h, 2)
    if direction == "LONG" and mom_1h < 0.3:
        return None
    if direction == "SHORT" and mom_1h > -0.3:
        return None

    score = 0
    reasons = []

    # 4h trend (3pts)
    score += 3
    reasons.append(f"4h_{trend_4h.lower()}_{strength_4h:.0%}")

    # 1h confirms (2pts)
    score += 2
    reasons.append(f"1h_confirms_{mom_1h:+.2f}%")

    # SM alignment
    sm_dir, sm_pct = get_sm_direction(coin)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return None  # SM hard block

    # Funding
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_aligned_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.008) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append("funding_crowded")

    # Volume
    vol = volume_ratio(candles_1h)
    if vol >= 1.3:
        score += 1
        reasons.append(f"vol_{vol:.1f}x")

    # RSI
    closes_1h = [float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 74:
        return None
    if direction == "SHORT" and rsi < 26:
        return None
    if (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4h momentum bonus
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.0:
        score += 1
        reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "price": price, "trend_4h": trend_4h, "momentum_1h": mom_1h,
        "sm_direction": sm_dir, "funding": funding, "rsi": rsi,
    }


# ─── Add-to-Winner Evaluation ────────────────────────────────

def evaluate_add(coin, direction, current_roe, stage, entry_cfg):
    """Check if conditions are met to add to a winning position.
    Returns (should_add, add_stage, reasons)."""

    pyramid_cfg = entry_cfg.get("pyramid", {})
    if not pyramid_cfg.get("enabled", True):
        return False, None, ["pyramid_disabled"]

    stages = pyramid_cfg.get("stages", [])
    next_stage = None

    for s in stages:
        if s["stage"] > stage and current_roe >= s["triggerRoe"]:
            next_stage = s
            break

    if not next_stage:
        return False, None, ["no_stage_triggered"]

    # Re-validate thesis before adding
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h", "4h"],
                              include_funding=False, include_order_book=False)
    if not data or not data.get("success"):
        return False, None, ["data_unavailable"]

    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles_4h) < 6:
        return False, None, ["insufficient_data"]

    # 4h trend must still be intact
    trend_4h, _ = trend_structure(candles_4h)
    expected_trend = "BULLISH" if direction == "LONG" else "BEARISH"
    if trend_4h != expected_trend:
        return False, None, ["4h_trend_broken"]

    # SM must not have flipped
    sm_dir, _ = get_sm_direction(coin)
    if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        return False, None, ["sm_flipped"]

    # Volume must still be present (not a dying move)
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    if candles_1h:
        vol = volume_ratio(candles_1h)
        if vol < 0.5:
            return False, None, ["volume_died"]

    reasons = [
        f"stage_{next_stage['stage']}_triggered",
        f"roe_{current_roe:+.1f}%",
        f"add_{next_stage['addPct']}%_of_max",
        "4h_trend_intact",
        f"sm_{sm_dir or 'unknown'}",
    ]

    return True, next_stage, reasons


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
    state = cfg.load_state("rhino-state.json")
    active_coins = {}
    for p in positions:
        active_coins[p["coin"]] = p

    # ── CHECK 1: Add to winning positions (pyramid) ───────────
    pyramid_state = state.get("pyramids", {})

    for pos in positions:
        coin = pos["coin"]
        if coin not in pyramid_state:
            continue

        ps = pyramid_state[coin]
        current_stage = ps.get("stage", 1)
        margin = float(pos.get("margin", 0))
        upnl = float(pos.get("upnl", 0))
        roe = (upnl / margin * 100) if margin > 0 else 0

        should_add, next_stage, reasons = evaluate_add(
            coin, pos["direction"], roe, current_stage, entry_cfg
        )

        if should_add and next_stage:
            add_margin = round(account_value * next_stage["addPct"] / 100, 2)

            pyramid_state[coin]["stage"] = next_stage["stage"]
            state["pyramids"] = pyramid_state
            cfg.save_state(state, "rhino-state.json")

            cfg.output({
                "success": True,
                "action": "pyramid_add",
                "add": {
                    "coin": coin,
                    "direction": pos["direction"],
                    "leverage": config.get("leverage", {}).get("default", 10),
                    "margin": add_margin,
                    "stage": next_stage["stage"],
                    "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
                },
                "reasons": reasons,
                "note": f"Stage {next_stage['stage']}: adding {next_stage['addPct']}% at {roe:+.1f}% ROE — thesis intact, trend confirmed",
            })
            return

    # ── CHECK 2: Entry cap ────────────────────────────────────
    max_positions = config.get("maxPositions", 3)
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
        cfg.save_state(state, "rhino-state.json")
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        cfg.save_state(state, "rhino-state.json")
        return

    # ── CHECK 3: Scout new positions ──────────────────────────
    top_n = config.get("topAssets", 10)
    candidates = get_top_assets(top_n)
    min_score = entry_cfg.get("minScore", 10)
    signals = []

    for asset in candidates:
        if asset["coin"] in active_coins:
            continue
        thesis = build_thesis(asset["coin"], entry_cfg)
        if thesis and thesis["score"] >= min_score:
            signals.append(thesis)

    if not signals:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"scanned top {len(candidates)}, no thesis at score {min_score}+"})
        cfg.save_state(state, "rhino-state.json")
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Stage 1: Scout entry — 30% of max position
    pyramid_cfg = entry_cfg.get("pyramid", {})
    scout_pct = pyramid_cfg.get("scoutPct", 30)
    max_margin_pct = entry_cfg.get("marginPctMax", 0.30)
    scout_margin = round(account_value * max_margin_pct * scout_pct / 100, 2)

    leverage = config.get("leverage", {}).get("default", 10)

    # Initialize pyramid state for this position
    pyramid_state[best["coin"]] = {
        "stage": 1,
        "direction": best["direction"],
        "scoutedAt": cfg.now_iso(),
        "scoutScore": best["score"],
        "maxMargin": round(account_value * max_margin_pct, 2),
    }
    state["pyramids"] = pyramid_state
    cfg.save_state(state, "rhino-state.json")

    cfg.output({
        "success": True,
        "signal": best,
        "entry": {
            "coin": best["coin"],
            "direction": best["direction"],
            "leverage": leverage,
            "margin": scout_margin,
            "stage": 1,
            "orderType": config.get("execution", {}).get("entryOrderType", "FEE_OPTIMIZED_LIMIT"),
        },
        "note": f"Stage 1 SCOUT: {scout_pct}% of max position. Will add at +10% ROE and +20% ROE if thesis holds.",
    })


if __name__ == "__main__":
    run()
