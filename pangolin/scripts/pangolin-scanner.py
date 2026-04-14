#!/usr/bin/env python3
# Senpi PANGOLIN Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PANGOLIN v1.0 — Extreme Funding Rate Fader.

Thesis: When funding rates are extreme (>0.03%/8h = ~40% annualized),
the crowd is paying heavily to hold their position. History shows these
extremes mean-revert within 24-48h as the cost of carry forces liquidation
or position reduction. Pangolin enters opposite to the funding direction
(collecting funding while waiting for the crowd to capitulate).

This is different from:
- Barracuda (follows trend + collects funding as a bonus)
- Lemon (fades degen traders based on their track record)
- Vulture (fades SM consensus exhaustion on price action)

Pangolin specifically fades EXTREME FUNDING — it's a pure crowding
mean-reversion play using a quantitative funding threshold. The edge
comes from two sources:
1. Collecting funding every 8 hours while waiting for mean reversion
2. Price reversal when the overcrowded side unwinds

Design:
- Scan all crypto instruments for extreme funding (>0.03%/8h)
- Confirm with SM positioning (SM should be fading the crowd)
- Enter opposite to the funding direction (collect funding)
- Conservative leverage (3-5x) — crowded unwinds are violent
- Very wide DSL (8-12 hours) — mean reversion takes time
- Max 2 positions (diversify across uncorrelated funding extremes)

Assets: Top 20 by volume (crypto only, no XYZ)
Runs every 5 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pangolin_config as cfg

# ── CONFIGURATION ──
MAX_POSITIONS = 2
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

COOLDOWN_MINUTES = 240          # 4 hours between same-asset entries
MARGIN_PCT = 0.25               # 25% per position (conservative)
MIN_SCORE = 7
MIN_FUNDING_RATE = 0.0003       # 0.03%/8h = ~40% annualized
XYZ_BANNED = True

# Very conservative leverage — crowded unwinds are violent
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 5},
    {"min_score": 7,  "leverage": 3},
]
DEFAULT_LEVERAGE = 3

# Top assets by volume — only trade liquid markets
ALLOWED_ASSETS = {
    "BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "AVAX", "LINK",
    "ADA", "DOT", "NEAR", "UNI", "LTC", "BCH", "TAO", "INJ",
    "AAVE", "ZEC", "WIF", "PEPE",
}


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
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


def scan_funding_extremes():
    """Find assets with extreme funding rates and score them."""
    # Get all instruments with funding data
    raw = cfg.mcporter_call("market_list_instruments")
    if not raw:
        return []

    instruments = raw.get("data", raw)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return []

    # Also get SM data for confirmation
    sm_raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    sm_map = {}
    if sm_raw:
        sm_markets = sm_raw.get("data", sm_raw)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", sm_markets)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", [])
        if isinstance(sm_markets, list):
            for m in sm_markets:
                if isinstance(m, dict):
                    token = str(m.get("token", "")).upper()
                    dex = str(m.get("dex", "")).lower()
                    if dex != "xyz":
                        sm_map[token] = m

    candidates = []

    for inst in instruments:
        if not isinstance(inst, dict):
            continue

        name = str(inst.get("name", inst.get("coin", ""))).upper()
        if name not in ALLOWED_ASSETS:
            continue

        funding = safe_float(inst.get("funding", 0))

        # Must have extreme funding
        if abs(funding) < MIN_FUNDING_RATE:
            continue

        # Determine crowd direction from funding sign
        # Positive funding = longs paying shorts = crowd is long
        # Negative funding = shorts paying longs = crowd is short
        crowd_direction = "LONG" if funding > 0 else "SHORT"
        fade_direction = "SHORT" if funding > 0 else "LONG"

        score = 0
        reasons = []

        # ── Funding extremity (1-4 pts) ──
        abs_funding = abs(funding)
        annualized = abs_funding * 3 * 365 * 100  # rough annualized %
        if abs_funding >= 0.001:  # 0.1%/8h = ~130% annualized
            score += 4
            reasons.append(f"EXTREME_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0006:  # 0.06%/8h = ~80% annualized
            score += 3
            reasons.append(f"HIGH_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0003:  # 0.03%/8h = ~40% annualized
            score += 2
            reasons.append(f"ELEVATED_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")

        # ── SM confirmation (0-3 pts) ──
        # If SM is positioned opposite to the crowd, the fade is higher conviction
        sm = sm_map.get(name)
        if sm:
            sm_dir = str(sm.get("direction", "")).upper()
            sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
            sm_traders = int(sm.get("trader_count", 0))

            if sm_dir == fade_direction:
                # SM agrees with our fade — they're already fading the crowd
                if sm_pct >= 10:
                    score += 3
                    reasons.append(f"SM_FADING_CROWD {sm_pct:.1f}% ({sm_traders}t)")
                elif sm_pct >= 5:
                    score += 2
                    reasons.append(f"SM_ALIGNED_FADE {sm_pct:.1f}% ({sm_traders}t)")
                else:
                    score += 1
                    reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
            elif sm_dir == crowd_direction:
                # SM is WITH the crowd — riskier fade
                if sm_pct >= 10:
                    score -= 2
                    reasons.append(f"SM_WITH_CROWD {sm_pct:.1f}% (dangerous)")
                else:
                    score -= 1
                    reasons.append(f"SM_SLIGHT_CROWD {sm_pct:.1f}%")

            # SM velocity — is SM momentum fading? (good for us)
            cc_15m = safe_float(sm.get("contribution_pct_change_15m", 0))
            if sm_dir == crowd_direction and cc_15m < -0.5:
                score += 1
                reasons.append(f"SM_MOMENTUM_FADING {cc_15m:.2f}")

        # ── Open interest concentration ──
        oi = safe_float(inst.get("openInterest", inst.get("oi", 0)))
        volume_24h = safe_float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        if oi > 0 and volume_24h > 0:
            oi_turnover = volume_24h / oi if oi > 0 else 0
            if oi_turnover < 0.5:
                # Low turnover = positions are sticky = more crowded
                score += 1
                reasons.append(f"STICKY_OI (turnover {oi_turnover:.2f}x)")

        # ── Price action — has the move already started reversing? ──
        p4h = safe_float(sm.get("token_price_change_pct_4h", 0)) if sm else 0
        if crowd_direction == "LONG" and p4h < -0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")
        elif crowd_direction == "SHORT" and p4h > 0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")

        reasons.insert(0, f"FADE_FUNDING {name} (crowd is {crowd_direction})")

        candidates.append({
            "token": name,
            "funding": funding,
            "crowd_direction": crowd_direction,
            "fade_direction": fade_direction,
            "score": score,
            "reasons": reasons,
            "annualized_pct": annualized,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def execute_entry(token, direction, margin, leverage):
    result = cfg.mcporter_call(
        "create_position",
        coin=token,
        direction=direction,
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 45,
        },
    )
    if result and result.get("success"):
        return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"CURLED UP: {coins}. DSL manages exit. Collecting funding.",
                     "_v2_no_thesis_exit": True})
        return

    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Scan for extreme funding
    candidates = scan_funding_extremes()
    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "SNIFFING: no extreme funding on allowed assets"})
        return

    # Already-held coins
    held_coins = {p["coin"].upper() for p in positions}

    for cand in candidates:
        token = cand["token"]

        if token in held_coins:
            continue

        if cand["score"] < MIN_SCORE:
            continue

        if cfg.is_asset_cooled_down(token, COOLDOWN_MINUTES):
            continue

        leverage = get_leverage_for_score(cand["score"])
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(token, cand["fade_direction"], margin, leverage)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            cfg.save_trade_counter(tc)
            cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="entry")

            cfg.output({
                "status": "ok",
                "action": "FUNDING_FADE",
                "signal": {
                    "asset": token,
                    "crowd_direction": cand["crowd_direction"],
                    "fade_direction": cand["fade_direction"],
                    "funding_rate": cand["funding"],
                    "annualized_pct": round(cand["annualized_pct"], 1),
                    "score": cand["score"],
                    "leverage": leverage,
                    "mode": "FUNDING_FADE",
                    "reasons": cand["reasons"],
                },
                "execution": {
                    "asset": token,
                    "direction": cand["fade_direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "result": result,
                "_pangolin_version": "1.0",
            })
            return
        else:
            cfg.output({
                "status": "ok",
                "action": "FUNDING_FADE_FAILED",
                "signal": {"asset": token, "score": cand["score"],
                           "reasons": cand["reasons"]},
                "error": result,
                "_pangolin_version": "1.0",
            })
            return

    # No candidates passed
    best = candidates[0] if candidates else None
    if best:
        note = (f"SNIFFING: best {best['token']} funding {best['funding']*100:.4f}% "
                f"score {best['score']}<{MIN_SCORE}")
    else:
        note = "SNIFFING: no extreme funding detected"
    cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
