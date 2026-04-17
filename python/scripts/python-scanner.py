#!/usr/bin/env python3
# Senpi PYTHON Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PYTHON v1.0 — The Patience Hunter.

A new predator archetype for the Senpi fleet. Every other predator
(Kodiak, Condor, Wolverine, Polar, Grizzly, Scorpion, etc.) scans,
strikes, and rotates out within 1-12 hours. Python holds for DAYS.

Derived from pr0br000's Arena Weeks 2-3 winning pattern:
  - 101 trades across 62 assets, $800 → $2,573 in 27 days (+221%)
  - 36% win rate, 3.14:1 win/loss ratio (losses outnumber wins 2:1)
  - Top 5 trades held 20-114 hours, generated 90% of gross profit
  - All top winners: LONG + mid-beta assets + 3-5x leverage
  - Multi-day holds are the EDGE, not the risk

Python is the fleet's first multi-day hold agent. While other predators
chase rotation, Python waits for the weekly trend champion.

## Architecture

MODE 1 — HUNTING: scans top 50 assets every 10 min, LONG-biased
MODE 2 — RIDING:  position open, DSL manages, scanner does NOT re-evaluate
MODE 3 — MONITORING: max 2 concurrent, asset cooldown 12h post-exit

## Differences from Condor (the universe-wide thesis picker)

| Dimension          | Condor v3.1      | Python v1.0      |
|--------------------|------------------|------------------|
| Universe           | Top 50 (majors)  | Top 50 (wider)   |
| MIN_SCORE          | 11               | 8                |
| Leverage           | 10x (apex)       | 5x default, 7x apex |
| Margin per trade   | 50-80%           | 25-40%           |
| Max positions      | 1                | 2                |
| Daily entries      | 1                | 3                |
| Hold target        | <24h             | 48-96h           |
| Direction          | Either           | LONG-biased      |
| Scan interval      | 3 min            | 10 min           |

## Philosophy

Win 1 big trade per week. Lose 5-10 small trades per week. The ratio does
the work. Don't try to win every trade — try to NOT miss the one big
multi-day winner.

Runs every 10 minutes.
"""

import sys
import os
import time
from datetime import datetime, timezone
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import python_config as cfg


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — tuned from pr0br000 data
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000      # Same floor as Condor/Pangolin (post-PR#195 lesson)
MIN_TRADER_COUNT = 30       # Lower than Condor's 50 — we want wider universe
MIN_SCORE = 8               # Lower than Condor (cast wide net)
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 3
ASSET_COOLDOWN_MINUTES = 720  # 12h per-asset cooldown after exit
SAME_DIR_COOLDOWN_MINUTES = 360  # 6h after ANY trade before re-entering same direction

# Leverage tiers — intentionally LOW (pr0br000 averaged 4-5x)
LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 7},   # Apex only: 7x
    {"min_score": 10, "leverage": 5},   # Strong: 5x
    {"min_score": 8,  "leverage": 3},   # Base: 3x
]
MAX_LEVERAGE = 7            # Hard cap — never exceed
DEFAULT_LEVERAGE = 3

# Conviction-scaled margin (more conservative than Condor)
MARGIN_PCT_BASE = 0.25      # 25% base (Condor starts 50%)
MARGIN_PCT_STRONG = 0.30    # Score 10-11
MARGIN_PCT_APEX = 0.40      # Score 12+

# Macro trend gate threshold — same as Condor, don't fight freight trains
MACRO_GATE_THRESHOLD_PCT = 10.0

# LONG bias: +1 score bonus for LONG setups (pr0br000's top 5 were all LONG)
LONG_BIAS_BONUS = 1

STARTING_BUDGET = 1000.0


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
# Universe discovery
# ═══════════════════════════════════════════════════════════════

def get_universe():
    """Top 50 HL perps by 24h notional volume. Crypto only, no XYZ DEX."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data:
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    if not isinstance(instruments, list):
        return []

    filtered = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name or name.startswith("xyz:"):
            continue
        # Skip stablecoins
        if name.upper() in ("USDC", "USDT", "USDE", "FDUSD", "DAI"):
            continue

        # Context-nested read (PR #196 lesson)
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        day_ntl_vlm = safe_float(ctx.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", inst.get("markPx", 0)))
        oi_usd = oi * mark_px

        if oi_usd < MIN_OI_USD:
            continue
        if day_ntl_vlm < 1_000_000:
            continue

        filtered.append({
            "coin": name,
            "volume": day_ntl_vlm,
            "oi_usd": oi_usd,
            "markPx": mark_px,
            "maxLeverage": int(inst.get("maxLeverage", 10) or 10),
        })

    filtered.sort(key=lambda x: -x["volume"])
    return filtered[:UNIVERSE_SIZE]


# ═══════════════════════════════════════════════════════════════
# Technical analysis
# ═══════════════════════════════════════════════════════════════

def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = safe_float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = safe_float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [safe_float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [safe_float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.55:  # Slightly looser than Kodiak's 0.6
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.55:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = safe_float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
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


# ═══════════════════════════════════════════════════════════════
# Smart Money aggregator
# ═══════════════════════════════════════════════════════════════

def get_sm_map():
    """Build a coin → (direction, pct, trader_count, cc_15m) map from
    leaderboard_get_markets. One call serves the whole universe scan."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data:
        return {}
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    # Aggregate long vs short per coin
    by_coin = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token:
            continue
        direction = m.get("direction", "").lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))

        entry = by_coin.setdefault(token, {"long_pct": 0, "short_pct": 0, "traders": 0, "cc_15m": 0})
        if direction == "long":
            entry["long_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m
        elif direction == "short":
            entry["short_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m

    result = {}
    for token, data in by_coin.items():
        total = data["long_pct"] + data["short_pct"]
        if total == 0 or data["traders"] < MIN_TRADER_COUNT:
            continue
        long_ratio = (data["long_pct"] / total) * 100
        if long_ratio > 58:
            result[token] = ("LONG", long_ratio, data["traders"], data["cc_15m"])
        elif long_ratio < 42:
            result[token] = ("SHORT", 100 - long_ratio, data["traders"], data["cc_15m"])
        else:
            result[token] = ("NEUTRAL", 50, data["traders"], data["cc_15m"])
    return result


# ═══════════════════════════════════════════════════════════════
# Thesis Builder — Patience Hunter scoring
# ═══════════════════════════════════════════════════════════════

def build_thesis(coin, max_lev, sm_info):
    """Score a single coin for multi-day hold potential."""

    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["15m", "1h", "4h", "1d"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    asset_data = data.get("data", data)

    candles_15m = asset_data.get("candles", {}).get("15m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    candles_1d = asset_data.get("candles", {}).get("1d", [])
    asset_ctx = asset_data.get("asset_context", asset_data.get("assetContext", {}))
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_15m) < 8 or len(candles_1h) < 6 or len(candles_4h) < 6:
        return None

    price = safe_float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0)))

    # ── 4h trend structure (REQUIRED) ─────────────────────────
    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    # ── 1h trend must agree ────────────────────────────────────
    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1d = price_momentum(candles_1d, 1) if len(candles_1d) >= 2 else 0

    # ── MACRO TREND GATE (Wolverine lesson, inherited) ──────────
    # Don't fight runaway trends >10% in opposite direction
    if abs(mom_4h) > MACRO_GATE_THRESHOLD_PCT:
        if (direction == "LONG" and mom_4h < 0) or (direction == "SHORT" and mom_4h > 0):
            return None

    # ── 15m must confirm direction (loose gate — 0.1% is enough) ─
    if direction == "LONG" and mom_15m < 0.1:
        return None
    if direction == "SHORT" and mom_15m > -0.1:
        return None

    score = 0
    reasons = []

    # 4h trend (2-4 pts by strength)
    if trend_strength_4h >= 0.8:
        score += 4
        reasons.append(f"4h_strong_{trend_4h}")
    elif trend_strength_4h >= 0.6:
        score += 3
        reasons.append(f"4h_{trend_4h}")
    else:
        score += 2
        reasons.append(f"4h_weak_{trend_4h}")

    # 1h momentum (0-2 pts)
    if abs(mom_1h) > 1.0:
        score += 2
        reasons.append(f"1h_strong_{mom_1h:+.2f}%")
    elif abs(mom_1h) > 0.5:
        score += 1
        reasons.append(f"1h_ok_{mom_1h:+.2f}%")

    # Daily trend bonus — pr0br000's winners had daily-candle support
    if len(candles_1d) >= 3:
        if direction == "LONG" and mom_1d > 1.0:
            score += 2
            reasons.append(f"1d_bullish_{mom_1d:+.1f}%")
        elif direction == "SHORT" and mom_1d < -1.0:
            score += 2
            reasons.append(f"1d_bearish_{mom_1d:+.1f}%")
        elif direction == "LONG" and mom_1d > 0:
            score += 1
            reasons.append("1d_up")
        elif direction == "SHORT" and mom_1d < 0:
            score += 1
            reasons.append("1d_down")

    # LONG bias bonus (pr0br000's top 5 were all LONG)
    if direction == "LONG":
        score += LONG_BIAS_BONUS
        reasons.append("LONG_bias")

    # ── Smart Money alignment ────────────────────────────────
    if sm_info:
        sm_dir, sm_pct, sm_count, sm_cc_15m = sm_info
        if sm_dir == direction:
            score += 2
            reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}t")
            if sm_pct > 70:
                score += 1
                reasons.append("sm_strongly_tilted")
        elif sm_dir != "NEUTRAL" and sm_dir != direction:
            # HARD BLOCK — smart money actively opposed
            return None

        # Fresh velocity bonus
        if sm_cc_15m > 0.3:
            score += 1
            reasons.append(f"15m_fresh +{sm_cc_15m:.2f}")

    # ── Funding alignment ─────────────────────────────────────
    if direction == "LONG" and funding < 0:
        score += 1
        reasons.append(f"funding_pays_long_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 1
        reasons.append(f"funding_pays_short_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.01):
        score -= 1
        reasons.append(f"funding_crowded_{funding:+.4f}")

    # ── Volume confirmation ───────────────────────────────────
    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.3:
        score += 1
        reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.6:
        score -= 1
        reasons.append("vol_weak")

    # ── RSI extremes filter ──────────────────────────────────
    closes_1h = [safe_float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 78:
        return None  # Too overbought for multi-day hold
    if direction == "SHORT" and rsi < 22:
        return None  # Too oversold
    if (direction == "LONG" and 50 < rsi < 68) or (direction == "SHORT" and 32 < rsi < 50):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # ── Move-exhaustion penalty (don't chase parabolic) ───────
    if abs(mom_4h) >= 6.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2
            reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1
            reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "price": price,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "mom_1h": mom_1h,
        "mom_4h": mom_4h,
        "mom_1d": mom_1d,
        "funding": funding,
        "rsi": rsi,
        "vol_ratio": vol_1h,
        "max_lev": max_lev,
    }


# ═══════════════════════════════════════════════════════════════
# Sizing
# ═══════════════════════════════════════════════════════════════

def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_margin_pct(score):
    if score >= 12:
        return MARGIN_PCT_APEX
    elif score >= 10:
        return MARGIN_PCT_STRONG
    else:
        return MARGIN_PCT_BASE


def get_safe_leverage(wallet, coin, desired):
    try:
        r = cfg.mcporter_call("strategy_get_asset_trading_limits",
                              strategy_wallet=wallet, coin=coin)
        if r:
            d = r.get("data", r)
            max_lev = int(d.get("maxLeverage", d.get("max_leverage", MAX_LEVERAGE)))
            return min(desired, max_lev, MAX_LEVERAGE)
    except Exception:
        pass
    return min(desired, MAX_LEVERAGE)


# ═══════════════════════════════════════════════════════════════
# Order guards
# ═══════════════════════════════════════════════════════════════

def has_resting_orders(wallet):
    """Non-reduceOnly orders block entry. Auto-cancel stale (>10min) maker orders."""
    STALE_ORDER_MAX_AGE_SEC = 600
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call("cancel_order",
                                      strategyWalletAddress=wallet,
                                      orderId=int(oid))
                except Exception:
                    pass
            continue
        has_fresh = True
    return has_fresh


def execute_entry(wallet, coin, direction, margin, leverage):
    """Place entry via canonical MCP schema with inner-order validation."""
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": coin,
            "direction": direction,
            "leverage": leverage,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": False,
                "executionTimeoutSeconds": 30,
            },
        }],
    )
    if not result:
        return False, {"error": "mcporter_call returned None"}
    if not result.get("success"):
        return False, {"error": result.get("error", "outer_envelope_failed"), "raw": result}

    # Inner-order validation
    data = result.get("data", result)
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if orders and isinstance(orders, list):
        first = orders[0] if isinstance(orders[0], dict) else {}
        if first and not first.get("success", True):
            return False, {"error": first.get("error", "inner_order_failed"), "raw": result}

    return True, result


# ═══════════════════════════════════════════════════════════════
# Dynamic daily cap
# ═══════════════════════════════════════════════════════════════

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """Low base (3/day), expand on hot hand, shrink on drawdown."""
    if starting_budget <= 0:
        return 3
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 20:      return 5
    elif pnl_pct >= 5:     return 4
    elif pnl_pct >= -5:    return 3
    elif pnl_pct >= -15:   return 2
    elif pnl_pct >= -25:   return 1
    else:                  return 0    # HARD_STOP circuit breaker


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    tc = cfg.load_trade_counter()
    if tc.get("gate") == "HARD_STOP":
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"gate=HARD_STOP: {tc.get('gateReason', 'circuit breaker')}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # ── MODE 2: RIDING — DSL manages, scanner does NOT re-evaluate ──
    if len(positions) >= MAX_POSITIONS:
        position_summary = ", ".join(f"{p['coin']} {p['direction']}" for p in positions)
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"RIDING {len(positions)}/{MAX_POSITIONS}: {position_summary} — DSL manages.",
                    "_v2_no_thesis_exit": True})
        return

    # ── Daily cap check ──
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. PnL: {pnl_pct:+.1f}%. Entries: {tc['entries']}/{dynamic_cap}"})
        return

    if has_resting_orders(wallet):
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": "RESTING ORDER: maker order pending."})
        return

    # ── Build universe ────────────────────────────────────────
    universe = get_universe()
    if not universe:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no universe"})
        return

    # Skip coins already in position
    open_coins = {p["coin"].upper() for p in positions}

    # Pull SM map once for the whole scan
    sm_map = get_sm_map()

    # ── Scan universe, build candidates ───────────────────────
    candidates = []
    skipped_reasons = Counter()

    for asset in universe:
        coin = asset["coin"]
        coin_upper = coin.upper()

        if coin_upper in open_coins:
            skipped_reasons["in_position"] += 1
            continue

        if cfg.is_asset_cooled_down(coin, ASSET_COOLDOWN_MINUTES):
            skipped_reasons["cooldown"] += 1
            continue

        sm_info = sm_map.get(coin_upper)
        thesis = build_thesis(coin, asset["maxLeverage"], sm_info)

        if not thesis:
            skipped_reasons["no_thesis"] += 1
            continue

        if thesis["score"] < MIN_SCORE:
            skipped_reasons[f"score<{MIN_SCORE}"] += 1
            continue

        candidates.append(thesis)

    if not candidates:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"HUNTING: no candidates. {dict(skipped_reasons)}"})
        return

    # ── Sort by score, pick the best ──────────────────────────
    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]

    # ── Size the position ─────────────────────────────────────
    leverage = get_safe_leverage(wallet, best["coin"], get_leverage_for_score(best["score"]))
    margin_pct = get_margin_pct(best["score"])
    margin = round(account_value * margin_pct, 2)

    # ── Execute ───────────────────────────────────────────────
    success, result = execute_entry(wallet, best["coin"], best["direction"], margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        tc["positionsOpenedByCoin"][best["coin"]] = time.time()
        cfg.save_trade_counter(tc)

        cfg.output({
            "success": True,
            "action": "ENTRY",
            "signal": best,
            "execution": {
                "asset": best["coin"], "direction": best["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "universe_size": len(universe),
            "candidates_found": len(candidates),
            "top_5_candidates": [
                {"coin": c["coin"], "direction": c["direction"], "score": c["score"]}
                for c in candidates[:5]
            ],
            "_python_version": "1.0",
        })
    else:
        cfg.output({
            "success": True,
            "action": "ENTRY_FAILED",
            "signal": {"asset": best["coin"], "direction": best["direction"],
                       "score": best["score"], "reasons": best["reasons"]},
            "error": result,
            "_python_version": "1.0",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"success": False, "error": str(e)})
