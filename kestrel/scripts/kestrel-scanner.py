#!/usr/bin/env python3
# Senpi KESTREL Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""KESTREL v1.0 — XYZ Macro Breakout Rider.

Thesis: When a commodity, equity, or precious metal moves >1.5% in 1 hour
something macro happened (war, rate decision, OPEC,
earnings). Unlike crypto where big moves exhaust quickly, commodity macro
moves tend to CONTINUE for hours or days. Kestrel catches the breakout
early and rides the trend.

Key insight from fleet analysis: Bald Eagle failed because it used SM
consensus as the PRIMARY signal. SM on XYZ is thin (26-181 traders vs
300+ on crypto) and lags. Kestrel inverts the priority:
- PRICE ACTION is the trigger (breakout detection)
- SM is confirmation only (is smart money starting to build?)

This is a MOMENTUM strategy on XYZ, not a contrarian one. Commodity
trends persist — oil doesn't mean-revert after a war starts.

Design:
- Watch commodities (CL, BRENTOIL, GOLD, SILVER) + indices (SP500, XYZ100)
  + high-volume equities (AAPL, NVDA, GOOGL, TSLA, AMZN)
- Trigger: 1H price move >1.5% (breakout detected)
- Confirmation: SM building in breakout direction (early, not exhausted)
- Anti-confirmation: SM AGAINST breakout direction = even stronger signal
  (crowd is wrong, breakout will squeeze them)
- Market hours only: 9:45 AM - 3:30 PM ET
- Conservative leverage: 3-5x (XYZ can gap)
- Wide DSL: 480-min hard timeout, let macro trends run

Runs every 5 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kestrel_config as cfg

# ── CONFIGURATION ──
# Commodities + precious metals + indices + high-volume equities
ALLOWED_ASSETS = {
    # Commodities
    "CL", "BRENTOIL",
    # Precious metals
    "GOLD", "SILVER",
    # Indices
    "SP500", "XYZ100",
    # High-volume equities
    "AAPL", "NVDA", "GOOGL", "TSLA", "AMZN", "META", "MSFT",
}

MAX_POSITIONS = 2               # Can hold 2 uncorrelated macro bets
MAX_DAILY_ENTRIES = 4


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

COOLDOWN_MINUTES = 180          # 3 hours between same-asset entries
MARGIN_PCT = 0.30               # 30% per position
MIN_SCORE = 6                   # Lower bar — price action is the primary signal
BREAKOUT_THRESHOLD_1H = 1.5     # 1.5% move in 1 hour = breakout
BREAKOUT_THRESHOLD_4H = 3.0     # 3.0% move in 4 hours = strong trend

LEVERAGE_TIERS = [
    {"min_score": 9, "leverage": 5},
    {"min_score": 6, "leverage": 3},
]
DEFAULT_LEVERAGE = 3

# No market hours gate — Hyperliquid XYZ trades 24/7.
# Weekend and overnight moves (e.g., Iran/oil) are the whole point.


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


def scan_xyz_breakouts():
    """Detect XYZ assets with significant price breakouts.
    Uses candle data as primary signal, SM data as confirmation."""

    # Get SM data for confirmation scoring
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
                    if dex == "xyz":
                        sm_map[token] = m

    candidates = []

    for asset in ALLOWED_ASSETS:
        # Fetch candle data — this is the PRIMARY signal
        data = cfg.mcporter_call("market_get_asset_data",
                                  asset=f"xyz:{asset}",
                                  candle_intervals=["1h", "4h"],
                                  include_funding=True,
                                  include_order_book=True,
                                  dex="xyz")
        if not data:
            continue

        ad = data.get("data", data)
        if not isinstance(ad, dict):
            continue

        candles = ad.get("candles", {})
        candles_1h = candles.get("1h", [])
        candles_4h = candles.get("4h", [])

        if len(candles_1h) < 3:
            continue

        # ── BREAKOUT DETECTION (primary signal) ──
        # Calculate 1H price change from the last closed candle
        last_1h = candles_1h[-1]
        prev_1h = candles_1h[-2]
        close_now = safe_float(last_1h.get("close", last_1h.get("c", 0)))
        close_prev = safe_float(prev_1h.get("close", prev_1h.get("c", 0)))

        if close_prev <= 0 or close_now <= 0:
            continue

        pct_1h = ((close_now - close_prev) / close_prev) * 100

        # Also check 4H trend
        pct_4h = 0
        if len(candles_4h) >= 2:
            close_4h_now = safe_float(candles_4h[-1].get("close", candles_4h[-1].get("c", 0)))
            close_4h_prev = safe_float(candles_4h[-2].get("close", candles_4h[-2].get("c", 0)))
            if close_4h_prev > 0:
                pct_4h = ((close_4h_now - close_4h_prev) / close_4h_prev) * 100

        # Must have a meaningful 1H breakout
        if abs(pct_1h) < BREAKOUT_THRESHOLD_1H:
            continue

        # Determine breakout direction from price action
        breakout_dir = "LONG" if pct_1h > 0 else "SHORT"

        score = 0
        reasons = []

        # ── 1H Breakout magnitude (2-4 pts) — the core signal ──
        if abs(pct_1h) >= 3.0:
            score += 4
            reasons.append(f"MASSIVE_BREAKOUT_1H {pct_1h:+.2f}%")
        elif abs(pct_1h) >= 2.0:
            score += 3
            reasons.append(f"STRONG_BREAKOUT_1H {pct_1h:+.2f}%")
        elif abs(pct_1h) >= BREAKOUT_THRESHOLD_1H:
            score += 2
            reasons.append(f"BREAKOUT_1H {pct_1h:+.2f}%")

        # ── 4H trend alignment (0-2 pts) ──
        if abs(pct_4h) >= BREAKOUT_THRESHOLD_4H:
            if (breakout_dir == "LONG" and pct_4h > 0) or \
               (breakout_dir == "SHORT" and pct_4h < 0):
                score += 2
                reasons.append(f"4H_TREND_CONFIRMS {pct_4h:+.2f}%")
        elif abs(pct_4h) >= 1.0:
            if (breakout_dir == "LONG" and pct_4h > 0) or \
               (breakout_dir == "SHORT" and pct_4h < 0):
                score += 1
                reasons.append(f"4H_ALIGNED {pct_4h:+.2f}%")

        # ── Volume surge (0-2 pts) ──
        if len(candles_1h) >= 4:
            vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))
                    for c in candles_1h[-4:]]
            if len(vols) >= 4 and vols[-2] > 0:
                vol_ratio = vols[-1] / vols[-2] if vols[-2] > 0 else 0
                avg_prev = sum(vols[:-1]) / len(vols[:-1]) if vols[:-1] else 1
                vol_vs_avg = vols[-1] / avg_prev if avg_prev > 0 else 0
                if vol_vs_avg >= 2.0:
                    score += 2
                    reasons.append(f"VOLUME_SURGE {vol_vs_avg:.1f}x avg")
                elif vol_vs_avg >= 1.3:
                    score += 1
                    reasons.append(f"VOLUME_UP {vol_vs_avg:.1f}x avg")

        # ── SM confirmation (0-2 pts, can be negative) ──
        sm = sm_map.get(asset)
        if sm:
            sm_dir = str(sm.get("direction", "")).upper()
            sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
            sm_traders = int(sm.get("trader_count", 0))

            if sm_dir == breakout_dir and sm_pct >= 3:
                # SM agrees with breakout — confirmation
                score += 2
                reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
            elif sm_dir == breakout_dir and sm_pct >= 1:
                score += 1
                reasons.append(f"SM_BUILDING {sm_pct:.1f}% ({sm_traders}t)")
            elif sm_dir != breakout_dir and sm_pct >= 5:
                # SM is AGAINST the breakout — crowd is wrong, squeeze potential
                score += 1
                reasons.append(f"SM_TRAPPED_{sm_dir} {sm_pct:.1f}% (squeeze potential)")

        # ── Spread gate ──
        ob = ad.get("order_book", ad.get("orderBook", {}))
        spread_ok = True
        if isinstance(ob, dict):
            bids = ob.get("bids", ob.get("bid", []))
            asks = ob.get("asks", ob.get("ask", []))
            if bids and asks:
                best_bid = safe_float(bids[0][0] if isinstance(bids[0], list)
                                      else bids[0].get("price", 0))
                best_ask = safe_float(asks[0][0] if isinstance(asks[0], list)
                                      else asks[0].get("price", 0))
                if best_bid > 0 and best_ask > 0:
                    mid = (best_bid + best_ask) / 2
                    spread_pct = (best_ask - best_bid) / mid
                    if spread_pct > 0.002:  # 0.2% max spread
                        spread_ok = False
                        reasons.append(f"SPREAD_WIDE {spread_pct*100:.3f}%")
                    else:
                        reasons.append(f"SPREAD_OK {spread_pct*100:.3f}%")

        if not spread_ok:
            continue

        # ── Funding alignment (0-1 pts) ──
        asset_ctx = ad.get("asset_context", ad.get("assetContext", {}))
        if isinstance(asset_ctx, dict):
            funding = safe_float(asset_ctx.get("funding", 0))
            if (breakout_dir == "LONG" and funding < -0.001) or \
               (breakout_dir == "SHORT" and funding > 0.001):
                score += 1
                reasons.append(f"FUNDING_ALIGNED {funding*100:.4f}%")

        reasons.insert(0, f"BREAKOUT xyz:{asset} {breakout_dir}")

        candidates.append({
            "token": asset,
            "breakout_direction": breakout_dir,
            "pct_1h": pct_1h,
            "pct_4h": pct_4h,
            "score": score,
            "reasons": reasons,
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
        dex="xyz",
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
                     "note": "RESTING ORDER: entry pending."})
        return

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"PERCHED: {coins}. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Scan for breakouts
    candidates = scan_xyz_breakouts()
    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "SCANNING: no XYZ breakouts >1.5% detected"})
        return

    # Filter and enter
    held_coins = {p["coin"].upper().replace("XYZ:", "") for p in positions}

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

        success, result = execute_entry(token, cand["breakout_direction"],
                                         margin, leverage)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            tc["last_entry_ts"] = cfg.now_ts()
            cfg.save_trade_counter(tc)
            cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="entry")

            cfg.output({
                "status": "ok",
                "action": "BREAKOUT_ENTRY",
                "signal": {
                    "asset": f"xyz:{token}",
                    "direction": cand["breakout_direction"],
                    "pct_1h": round(cand["pct_1h"], 2),
                    "pct_4h": round(cand["pct_4h"], 2),
                    "score": cand["score"],
                    "leverage": leverage,
                    "mode": "XYZ_BREAKOUT",
                    "reasons": cand["reasons"],
                },
                "execution": {
                    "asset": f"xyz:{token}",
                    "direction": cand["breakout_direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "result": result,
                "_kestrel_version": "1.0",
            })
            return
        else:
            cfg.output({
                "status": "ok",
                "action": "BREAKOUT_ENTRY_FAILED",
                "signal": {"asset": f"xyz:{token}",
                           "score": cand["score"],
                           "reasons": cand["reasons"]},
                "error": result,
                "_kestrel_version": "1.0",
            })
            return

    # No candidates passed
    best = candidates[0] if candidates else None
    if best:
        note = (f"SCANNING: best xyz:{best['token']} {best['breakout_direction']} "
                f"{best['pct_1h']:+.1f}% 1H, score {best['score']}<{MIN_SCORE}")
    else:
        note = "SCANNING: no XYZ breakouts detected"
    cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
