#!/usr/bin/env python3
# Senpi CONDOR Scanner v3.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CONDOR v3.1 — "One Amazing Trade per Day" (BTC-gate removed).

## v3.1 change (2026-04-16 — same day as v3.0 ship)

The v3.0 hard-gate for BTC macro alignment was too restrictive for the
current regime. Chart evidence: HYPE has been +100% since Jan 2026 while
BTC has been -15%, decoupled 4+ months running. HIP-3 narrative drives
native Hyperliquid assets independent of BTC. The v3.0 gate would have
blocked every HYPE LONG setup despite HYPE being one of the strongest
trending assets on Hyperliquid.

v3.1 changes:
  - BTC macro alignment REMOVED as hard gate
  - BTC macro alignment now a scoring BONUS only (+1 when aligned and strong)
  - No penalty for BTC non-alignment — each asset trades on its own merit
  - MACRO TREND GATE retained (that's about the asset's own trend, not BTC)

## v3.0 description (unchanged)

COMPLETE REWRITE from v2.0. v2.0 was a multi-asset alpha hunter with
generic signals. v3.0 is a pure trend-continuation sniper built from
Kodiak + Wolverine's empirical winning patterns (probe-verified
2026-04-16).

## Thesis

From Kodiak's top 3 lifetime winners (+$133, +$87, +$78 on SOL):
  "The absolute highest predictor of a massive directional swing is when
   the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a
   single direction, AND the Smart Money leaderboard is heavily lopsided
   (>65% directional consensus) in that exact same direction."

From Wolverine's HYPE SHORT post-mortem (-$160 loss same day):
  "We stepped in front of a runaway 32% freight train to catch a 1%
   micro-retrace. The historical winners traded with the massive macro
   shift, not against it. Add a strict MACRO TREND GATE: never let the
   agent short an asset that is up >10% on the 4H candle."

## Architecture — hard gates, then score, then size

HARD GATES (all must pass):
  1. Not XYZ, not stablecoin
  2. OI > $1M USD (PR #196 context-aware read)
  3. trader_count >= 50 (signal validity)
  4. 3TF ALIGNMENT: 4h_price + 1h_price + SM direction + SM velocity
     all aligned in entry direction with minimum magnitudes
  5. MACRO TREND GATE: no counter-trend — block if
     |4h_move| > 10% OPPOSING entry direction
  6. SM consensus >= 65% in entry direction
  7. BTC macro aligned (alts don't fight strong BTC macro)

SCORING (max ~18 pts):
  - 4h move magnitude tier: 1-4 pts
  - 1h confirmation: 1-2 pts
  - 15m SM velocity: 1-2 pts
  - 3TF alignment bonus: +3 (rewards the gate)
  - SM consensus tier: 2-4 pts
  - Trader depth (>=100): +1
  - Funding alignment: +1
  - BTC macro confirmation strong: +1
  - Peak session (13-19 UTC or 00-05 UTC): +1

MIN_SCORE: 11 (Kodiak's minimum historical winner score).

POSITION SIZING (empirical 10x cap):
  Score 11-12: 50% margin, 10x
  Score 13-14: 70% margin, 10x (HIGH)
  Score 15+:   80% margin, 10x (APEX)

  Leverage auto-clamped to per-asset Hyperliquid max (PR #194 fix).

ONE TRADE PER DAY discipline:
  - MAX 1 concurrent position
  - Daily cap = 1 (dynamic circuit breaker still hard-stops at -25%)
  - 2h post-exit cooldown before next entry
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import condor_config as cfg


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — v3.0 "One Trade a Day"
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50                  # Top 50 HL assets by 24h volume
MIN_OI_USD = 1_000_000              # Liquidity floor
MIN_TRADER_COUNT = 50               # Signal validity
XYZ_BANNED = True                   # Crypto only
STABLECOINS_BANNED = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "BUSD"}

# 3TF alignment magnitude thresholds
MIN_4H_MAGNITUDE = 1.0              # 4h price must move >=1% in entry direction
MIN_1H_MAGNITUDE = 0.3              # 1h confirmation
MIN_15M_VELOCITY = 0.2              # SM velocity (proxy for 15m alignment)

# MACRO TREND GATE (Wolverine's addition)
MACRO_GATE_THRESHOLD_PCT = 10.0     # Don't fight moves >10% opposite direction

# SM gates
MIN_SM_CONSENSUS_PCT = 65.0
STRONGLY_TILTED_PCT = 80.0

# Scoring
MIN_SCORE = 11

# Position management
MAX_POSITIONS = 1
POST_EXIT_COOLDOWN_MINUTES = 120    # 2h between trades

# Leverage (Kodiak empirical cap)
MAX_LEVERAGE = 10
LEVERAGE_TIERS = [
    {"min_score": 15, "leverage": 10, "margin_pct": 0.80},
    {"min_score": 13, "leverage": 10, "margin_pct": 0.70},
    {"min_score": 11, "leverage": 10, "margin_pct": 0.50},
]

# Dynamic daily cap
STARTING_BUDGET = 1000.0

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """One trade per day when healthy, 0 when catastrophic drawdown."""
    if starting_budget <= 0:
        return 1
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= -25:
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_utc_hour():
    return datetime.now(timezone.utc).hour


def get_sizing_for_score(score):
    """Returns (leverage, margin_pct) based on score tier."""
    for tier in LEVERAGE_TIERS:  # Already sorted high-to-low
        if score >= tier["min_score"]:
            return tier["leverage"], tier["margin_pct"]
    return 10, 0.50


def get_safe_leverage(wallet, asset, requested_leverage):
    """Clamp leverage to asset's max allowed on Hyperliquid (PR #194 fix)."""
    try:
        limits = cfg.mcporter_call(
            "strategy_get_asset_trading_limits",
            strategy_wallet=wallet,
            coin=asset,
        )
        if limits:
            data = limits.get("data", limits)
            if isinstance(data, dict):
                lev = data.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", MAX_LEVERAGE)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


def has_resting_orders(wallet):
    """Fleet-standard resting-order check with stale-order auto-cancel."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600
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
            continue
        has_fresh = True
    return has_fresh


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_universe():
    """Top 50 HL assets by 24h notional volume, context-aware read (PR #196)."""
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    if not isinstance(instruments, list):
        return []

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = str(inst.get("coin") or inst.get("name", "")).upper()
        if not coin or coin in STABLECOINS_BANNED:
            continue
        dex = str(inst.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue

        # Context-aware read (PR #196 fix)
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                                      inst.get("markPx", inst.get("midPx", 0)))))
        volume_24h = safe_float(ctx.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        funding = safe_float(ctx.get("funding", inst.get("funding", 0)))
        oi_usd = oi * mark_px if mark_px > 0 else 0

        if oi_usd < MIN_OI_USD or mark_px <= 0:
            continue

        assets.append({
            "coin": coin, "oi_usd": oi_usd,
            "volume_24h": volume_24h, "price": mark_px, "funding": funding,
        })

    # Rank by 24h notional volume
    assets.sort(key=lambda x: x["volume_24h"], reverse=True)
    return assets[:UNIVERSE_SIZE]


def fetch_sm_map():
    """Hyperfeed SM leaderboard: direction, consensus, 4h/1h price, 15m velocity."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
    markets = raw
    if isinstance(markets, dict):
        data = markets.get("data", markets)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
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
        if not token:
            continue
        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "consensus_pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0) or 0),
            "p4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "p1h": safe_float(m.get("token_price_change_pct_1h",
                              m.get("price_change_1h", 0))),
            "c15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "c1h": safe_float(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


def get_btc_macro(sm_map):
    """BTC's dominant direction + 4h magnitude as macro context."""
    btc = sm_map.get("BTC")
    if not btc:
        return None
    return {"direction": btc["direction"], "p4h": btc["p4h"]}


# ═══════════════════════════════════════════════════════════════
# SIGNAL EVALUATION — Kodiak + Wolverine pattern
# ═══════════════════════════════════════════════════════════════

def evaluate_trend_continuation(asset_info, sm, btc_macro):
    """Score an asset for trend-continuation apex setup.

    Returns scored signal dict or None if any hard gate fails.
    """
    coin = asset_info["coin"]
    sm_dir = sm["direction"]
    if sm_dir not in ("LONG", "SHORT"):
        return None

    # HARD GATE: SM consensus
    if sm["consensus_pct"] < MIN_SM_CONSENSUS_PCT:
        return None
    # HARD GATE: trader depth
    if sm["traders"] < MIN_TRADER_COUNT:
        return None

    p4h = sm["p4h"]
    p1h = sm["p1h"]
    c15m = sm["c15m"]

    # HARD GATE: 3TF alignment (4h + 1h + 15m velocity)
    if sm_dir == "LONG":
        tf_ok = (p4h >= MIN_4H_MAGNITUDE and
                 p1h >= MIN_1H_MAGNITUDE and
                 c15m >= MIN_15M_VELOCITY)
    else:  # SHORT
        tf_ok = (p4h <= -MIN_4H_MAGNITUDE and
                 p1h <= -MIN_1H_MAGNITUDE and
                 c15m >= MIN_15M_VELOCITY)
    if not tf_ok:
        return None

    # HARD GATE: MACRO TREND (Wolverine's fix)
    if sm_dir == "LONG" and p4h < -MACRO_GATE_THRESHOLD_PCT:
        return None  # SM says LONG but price crashed — block
    if sm_dir == "SHORT" and p4h > MACRO_GATE_THRESHOLD_PCT:
        return None  # SM says SHORT but price ripped — block

    # v3.1: BTC macro alignment REMOVED as hard gate.
    # Chart evidence (2026-04): HYPE +100% while BTC -15%, decoupled 4+ months
    # running. HIP-3 narrative drives native Hyperliquid assets independent
    # of BTC. The v3.0 hard gate would have blocked every HYPE LONG setup
    # despite HYPE being one of the strongest apex-trending assets on HL.
    # BTC alignment is now a scoring BONUS only (see below), not a block.
    # The MACRO TREND GATE still enforces "don't fight the asset's own
    # runaway trend" — Wolverine's fix, orthogonal to BTC correlation.

    # ─── SCORING ───
    score = 0
    reasons = []

    # 4h magnitude
    p4h_abs = abs(p4h)
    if p4h_abs >= 6.0:
        score += 4
        reasons.append(f"4H_MOMENTUM_STRONG {p4h:+.1f}%")
    elif p4h_abs >= 4.0:
        score += 3
        reasons.append(f"4H_MOMENTUM {p4h:+.1f}%")
    elif p4h_abs >= 2.0:
        score += 2
        reasons.append(f"4H_TREND_BUILDING {p4h:+.1f}%")
    else:
        score += 1
        reasons.append(f"4H_TREND_LIGHT {p4h:+.1f}%")

    # 1h confirmation
    p1h_abs = abs(p1h)
    if p1h_abs >= 1.0:
        score += 2
        reasons.append(f"1H_STRONG {p1h:+.2f}%")
    elif p1h_abs >= 0.5:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # 15m SM velocity
    if c15m >= 2.0:
        score += 2
        reasons.append(f"15M_SPIKE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # 3TF alignment bonus
    score += 3
    reasons.append(f"3TF_ALIGNED_{sm_dir}")

    # SM consensus
    if sm["consensus_pct"] >= STRONGLY_TILTED_PCT:
        score += 4
        reasons.append(f"SM_STRONGLY_TILTED {sm['consensus_pct']:.0f}%")
    elif sm["consensus_pct"] >= 75:
        score += 3
        reasons.append(f"SM_CONVERGENT {sm['consensus_pct']:.0f}%")
    else:
        score += 2
        reasons.append(f"SM_ALIGNED {sm['consensus_pct']:.0f}%")

    # Trader depth
    if sm["traders"] >= 100:
        score += 1
        reasons.append(f"DEEP_CONSENSUS ({sm['traders']}t)")

    # Funding alignment
    funding = asset_info.get("funding", 0)
    if (sm_dir == "SHORT" and funding > 0.0002) or (sm_dir == "LONG" and funding < -0.0002):
        score += 1
        reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # BTC macro confirmation bonus
    if btc_macro and coin != "BTC":
        if btc_macro["direction"] == sm_dir and abs(btc_macro["p4h"]) >= 1.5:
            score += 1
            reasons.append(f"BTC_CONFIRMS {btc_macro['p4h']:+.1f}%")

    # Peak session bonus
    h = now_utc_hour()
    if (13 <= h <= 19) or (0 <= h <= 5):
        score += 1
        reasons.append(f"PEAK_SESSION_{h:02d}UTC")

    if score < MIN_SCORE:
        return None

    return {
        "coin": coin,
        "direction": sm_dir,
        "score": score,
        "reasons": reasons,
        "p4h": p4h,
        "p1h": p1h,
        "c15m": c15m,
        "sm_consensus": sm["consensus_pct"],
        "sm_traders": sm["traders"],
        "oi_usd": asset_info["oi_usd"],
        "funding": asset_info.get("funding", 0),
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, asset, direction, leverage, margin):
    """Canonical MCP schema (PR #191) + inner-order validation (PR #194)."""
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
        data = result.get("data", {})
        if isinstance(data, dict):
            orders_result = data.get("orders", data.get("results", []))
            if isinstance(orders_result, list) and orders_result:
                inner = orders_result[0]
                if isinstance(inner, dict) and inner.get("success") is False:
                    err = inner.get("error", "inner order failed")
                    return False, {"error": f"INNER_FAILURE: {err}"}
        return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, _ = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # Position check
    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "cannot read account"})
        return

    # ONE TRADE PER DAY — if anything open, DSL manages
    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_v2_no_thesis_exit": True,
            "_condor_version": "3.1",
        })
        return

    # Resting-order guard
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "RESTING ORDER: limit order pending."})
        return

    # Post-exit cooldown (2h since last entry)
    tc = cfg.load_trade_counter()
    last_entry_ts = tc.get("last_entry_ts", 0)
    try:
        last_entry_ts = float(last_entry_ts)
    except (TypeError, ValueError):
        last_entry_ts = 0
    seconds_since_last = time.time() - last_entry_ts if last_entry_ts > 0 else float('inf')
    if seconds_since_last < POST_EXIT_COOLDOWN_MINUTES * 60:
        remaining = int((POST_EXIT_COOLDOWN_MINUTES * 60 - seconds_since_last) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"post-exit cooldown ({remaining}min remaining)",
                    "_condor_version": "3.1"})
        return

    # Dynamic daily cap
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if dynamic_cap <= 0:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"HARD_STOP pnl={pnl_pct:+.1f}% — circuit breaker"})
        return
    if tc.get("entries", 0) >= dynamic_cap:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap reached: {tc.get('entries', 0)}/{dynamic_cap}. One trade per day."})
        return

    # Fetch data
    universe = fetch_universe()
    if not universe:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No universe — market_list_instruments empty."})
        return

    sm_map = fetch_sm_map()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data from leaderboard_get_markets."})
        return

    btc_macro = get_btc_macro(sm_map)

    # Evaluate every universe asset
    candidates = []
    for asset_info in universe:
        coin = asset_info["coin"]
        sm = sm_map.get(coin)
        if not sm:
            continue
        sig = evaluate_trend_continuation(asset_info, sm, btc_macro)
        if sig:
            candidates.append(sig)

    if not candidates:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"SCANNING {len(universe)} assets — no apex trend-continuation setup >= MIN_SCORE={MIN_SCORE}.",
            "_condor_version": "3.1",
        })
        return

    # Pick the highest-scoring candidate
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # Size & leverage (score-scaled)
    base_leverage, margin_pct = get_sizing_for_score(best["score"])
    leverage = get_safe_leverage(wallet, best["coin"], base_leverage)
    margin = round(account_value * margin_pct, 2)

    # Execute
    success, result = execute_entry(wallet, best["coin"], best["direction"], leverage, margin)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        cfg.save_trade_counter(tc)

        cfg.output({
            "status": "ok",
            "action": "ENTRY",
            "signal": {
                "coin": best["coin"],
                "direction": best["direction"],
                "score": best["score"],
                "reasons": best["reasons"],
                "p4h": best["p4h"],
                "p1h": best["p1h"],
                "c15m": best["c15m"],
                "sm_consensus": best["sm_consensus"],
                "sm_traders": best["sm_traders"],
                "oi_usd": best["oi_usd"],
            },
            "execution": {
                "coin": best["coin"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "margin_pct_of_equity": margin_pct,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "result": result,
            "top_5_candidates": [
                {"coin": c["coin"], "direction": c["direction"], "score": c["score"]}
                for c in candidates[:5]
            ],
            "_condor_version": "3.1",
        })
    else:
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": result,
            "_condor_version": "3.1",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        try:
            cfg.log(f"CRITICAL: {e}")
        except AttributeError:
            pass
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
