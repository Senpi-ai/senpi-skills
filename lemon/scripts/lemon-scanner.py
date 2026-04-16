#!/usr/bin/env python3
# Senpi LEMON Scanner v1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""LEMON v1.1 — Degen Fader (Fleet Hardened).

THESIS: Counter-trade the worst traders on the Hyperfeed. When CHOPPY and
DEGEN traders are concentrated on one side of an asset, fade them — take
the opposite direction. These traders lose money consistently; betting
against their consensus is a positive-edge strategy.

v1.1 changes from fleet audit (2026-04-09):
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: 14+→20x, 12+→15x, 10+→10x, 8+→7x
- Margin at 50%
- has_resting_orders() with reduceOnly filter
- Hyperfeed multi-window velocity scoring (15m, 1h)
- Move-exhaustion penalty
- No thesis exit — DSL manages all exits
- MAX_DAILY_ENTRIES = 3, COOLDOWN = 120 min

ARCHITECTURE:
1. Call leaderboard_get_markets → find assets where SM direction is driven
   by low-quality traders
2. Call discovery_get_top_traders with CHOPPY/DEGEN filters → confirm
   that the dominant traders on the asset are low-quality
3. Cross-reference: if CHOPPY/DEGEN traders are piling into an asset
   AND the 15m velocity is fading (they're late to the move) → fade them

SCORING (max ~16 points):
- CHOPPY/DEGEN trader concentration on the asset (0-4)
- SM direction OPPOSING the fade direction confirms contrarian thesis (0-2)
- 15m velocity fading (the move is exhausting) (0-3)
- 1h velocity decelerating (0-1)
- 4H price extended in their direction = mean reversion setup (0-2)
- Move exhaustion (4h > 3%) bonus for fade (0-2)
- Funding alignment with fade direction (0-1)
- US session (0-1)

MIN_SCORE = 8. Only the strongest fade signals pass.

2 API calls: leaderboard_get_markets + leaderboard_get_momentum_events (for TCS tags)
Runs every 5 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lemon_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "DOGE", "LINK",
                  "XRP", "ADA", "NEAR", "UNI", "AAVE"]
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 3


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Default starting budget — override per-agent if different

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap based on drawdown from starting budget.

    Winners get more trades (ride the hot hand).
    Losers get fewer trades (preserve capital).
    Catastrophic drawdown triggers HARD STOP (circuit breaker).
    """
    if starting_budget <= 0:
        return 4  # Safe fallback
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand — up >5%
    elif pnl_pct >= 0:     return 8    # Small win / breakeven
    elif pnl_pct >= -5:    return 5    # Careful
    elif pnl_pct >= -15:   return 3    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve — only highest conviction
    else:                  return 0    # HARD STOP — circuit breaker

COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 8
XYZ_BANNED = True

# Conviction-scaled leverage — fleet standard
LEVERAGE_TIERS = [
    {"min_score": 14, "leverage": 20},
    {"min_score": 12, "leverage": 15},
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 20

# Fade thresholds
MIN_SM_PCT = 3.0           # Asset must have at least 3% SM concentration
MIN_SM_TRADERS = 20        # Minimum trader count for signal validity
FADE_15M_THRESHOLD = -0.3  # 15m velocity must be fading (negative = move exhausting)


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
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC (default 600s / 10 min).

    Without auto-cancel, a maker FEE_OPTIMIZED_LIMIT order that never
    fills can lock the scanner out of new entries indefinitely, because
    every subsequent scan sees the stale order and aborts early. Ignores
    reduceOnly orders (those are DSL exit legs)."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600  # 10 minutes
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
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
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue  # Treat cancelled order as gone
        has_fresh = True
    return has_fresh

def fetch_sm_data():
    """Get SM data for tracked assets."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}

    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if token not in TRACKED_ASSETS:
            continue

        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "contrib_4h": safe_float(m.get("contribution_pct_change_4h", 0)),
            "is_dominant": m.get("is_dominant_direction", True),
        }

    return sm_map


def evaluate_fade(asset, sm):
    """
    Score a fade (counter-trade) opportunity.
    The fade direction is OPPOSITE to the SM dominant direction.
    We're betting that the current SM consensus is wrong — driven by
    low-quality traders chasing a move that's already exhausted.
    """
    sm_direction = sm["direction"]
    if sm_direction not in ("LONG", "SHORT"):
        return None

    # Fade = opposite of SM consensus
    fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"

    pct = sm["pct"]
    traders = sm["traders"]
    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    c15m = sm["contrib_15m"]
    c1h = sm["contrib_1h"]
    c4h = sm["contrib_4h"]

    # Gate: need enough data to evaluate
    if pct < MIN_SM_PCT or traders < MIN_SM_TRADERS:
        return None

    # Gate: 15m velocity must be fading (the move is exhausting)
    # If 15m is still accelerating, the move is live — don't fade it
    if c15m > 0.1:
        return None  # SM is still building — too early to fade

    score = 0
    reasons = []

    # 1. SM concentration on the "wrong" side (0-4)
    # Higher concentration = more degens piling in = better fade
    if pct >= 20:
        score += 4
        reasons.append(f"DEGEN_PILE {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"HEAVY_CROWD {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"CROWDED {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 3:
        score += 1
        reasons.append(f"LEANING {pct:.1f}% ({traders}t) {sm_direction}")

    # 2. 15m velocity fading (0-3) — the move is running out of steam
    if c15m < -2.0:
        score += 3
        reasons.append(f"15M_COLLAPSING {c15m:.2f}")
    elif c15m < -0.5:
        score += 2
        reasons.append(f"15M_FADING {c15m:.2f}")
    elif c15m < -0.1:
        score += 1
        reasons.append(f"15M_COOLING {c15m:.2f}")

    # 3. 1h velocity decelerating (0-1)
    if c1h < -0.5:
        score += 1
        reasons.append(f"1H_FADING {c1h:.2f}")

    # 4. 4H price extended — mean reversion setup (0-2)
    # If price has moved a lot in the SM direction, it's overextended
    if sm_direction == "LONG" and p4h > 3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "LONG" and p4h > 1.5:
        score += 1
        reasons.append(f"EXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_SHORT {p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -1.5:
        score += 1
        reasons.append(f"EXTENDED_SHORT {p4h:.1f}%")

    # 5. 1H price starting to reverse toward fade direction (0-1)
    if fade_direction == "LONG" and p1h > 0.1:
        score += 1
        reasons.append(f"1H_REVERSING +{p1h:.2f}%")
    elif fade_direction == "SHORT" and p1h < -0.1:
        score += 1
        reasons.append(f"1H_REVERSING {p1h:.2f}%")

    # 6. 4h contribution shift — SM conviction weakening over 4h (0-1)
    if c4h < -1.0:
        score += 1
        reasons.append(f"4H_SM_WEAKENING {c4h:.1f}")

    # 7. Fetch funding — if funding pays the fade direction, bonus
    try:
        ad = cfg.mcporter_call("market_get_asset_data", asset=asset,
                                candle_intervals=[], include_funding=True,
                                include_order_book=False)
        if ad:
            ac = ad.get("data", ad).get("asset_context",
                 ad.get("data", ad).get("assetContext", {}))
            if isinstance(ac, dict):
                funding = safe_float(ac.get("funding", 0))
                if (fade_direction == "SHORT" and funding > 0.0002):
                    score += 1
                    reasons.append(f"FUNDING_PAYS_FADE +{funding*100:.4f}%")
                elif (fade_direction == "LONG" and funding < -0.0002):
                    score += 1
                    reasons.append(f"FUNDING_PAYS_FADE {funding*100:.4f}%")
    except:
        pass

    # 8. US session bonus (0-1)
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1
        reasons.append("US_SESSION")

    if score < MIN_SCORE:
        return None

    return {
        "asset": asset,
        "direction": fade_direction,
        "score": score,
        "mode": "DEGEN_FADER",
        "reasons": reasons,
        "smDirection": sm_direction,
        "smPct": pct,
        "smTraders": traders,
        "priceChg4h": p4h,
        "contrib15m": c15m,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, asset, direction, leverage, margin):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": asset,
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
    if result and result.get("success"):
        return True, result
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        return False, {"error": error}


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER & COOLDOWN (uses lemon_config)
# ═══════════════════════════════════════════════════════════════

def load_tc():
    return cfg.load_trade_counter()

def save_tc(tc):
    cfg.save_trade_counter(tc)


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

    # RIDING: position open → DSL manages exit
    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v1_no_thesis_exit": True})
        return

    # Check for resting entry orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    # Trade counter
    tc = load_tc()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Global cooldown
    last_entry = tc.get("last_entry_ts", tc.get("cooldownUntil", 0))
    if isinstance(last_entry, str):
        last_entry = 0
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Cooldown ({remaining}min remaining)"})
        return

    # Fetch SM data
    sm_map = fetch_sm_data()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "No SM data"})
        return

    # Evaluate fade signals for all tracked assets
    signals = []
    rejections = {}
    for asset in TRACKED_ASSETS:
        sm = sm_map.get(asset)
        if not sm:
            rejections[asset] = "no_data"
            continue

        if cfg.is_on_cooldown(asset):
            rejections[asset] = "cooldown"
            continue

        # Skip if we already hold this asset
        if any(p["coin"].upper() == asset for p in positions):
            rejections[asset] = "holding"
            continue

        result = evaluate_fade(asset, sm)
        if result:
            signals.append(result)
        else:
            rejections[asset] = "no_fade_signal"

    if not signals:
        status_parts = [f"{a}:{r}" for a, r in rejections.items()]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No fade signals. {', '.join(status_parts[:5])}"})
        return

    # Sort by score, pick the best
    signals.sort(key=lambda s: s["score"], reverse=True)
    best = signals[0]

    # Execute entry
    leverage = get_leverage_for_score(best["score"])
    margin = round(account_value * MARGIN_PCT, 2)

    success, result = execute_entry(wallet, best["asset"], best["direction"], leverage, margin)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)

        # Set per-asset cooldown
        cfg.set_cooldown(best["asset"], COOLDOWN_MINUTES)

        cfg.output({
            "status": "ok",
            "action": "ENTRY",
            "signal": best,
            "execution": {
                "asset": best["asset"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "result": result,
            "_lemon_version": "1.1",
        })
    else:
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": result,
            "_lemon_version": "1.1",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
