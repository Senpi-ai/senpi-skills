#!/usr/bin/env python3
# Senpi SCORPION Scanner v3.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SCORPION v3.1 — Multi-Market Active Trader (scalp re-entry).

## v3.1 changes (2026-04-16)

Inspired by pr0br000's 3-week Arena sweep: 21 fills on $HEMI in Week 2,
33 fills in Week 3, total 369 fills across 22 assets. The pattern: exit
on any meaningful wick, re-enter fast when thesis is still intact. We're
matching that behavior.

Critical context: Senpi runtime no longer supports multi-breach DSL
(orders placed onchain → single-breach is enforced). Every DSL exit is
final. The ONLY way to capture more of a trending move is to re-enter
cleanly after an exit.

v3.1 adds SCALP RE-ENTRY logic:
- SCALP_WINDOW_MINUTES = 60: if last entry on an asset was within 60 min,
  treat next entry as a scalp re-entry rather than a fresh entry.
- SCALP_COOLDOWN_MINUTES = 15: short cooldown on scalp re-entries vs the
  120-min standard fresh-entry cooldown.
- MAX_REENTRIES_PER_ASSET = 10: daily cap per asset prevents death-
  spiral re-entry on a breaking thesis. After 10 entries on same asset
  in one day, lockout for 24h.
- Scalp re-entries BYPASS the overall daily cap (they continue a position
  the scanner already validated rather than opening a new thesis).
- Fresh entries still use 120-min cooldown and count against daily cap.

Target behavior: when SCORPION enters $HEMI at 10am and DSL exits at
10:30am with +2% realized PnL, the scanner can re-enter HEMI at 10:45am
(past the 15-min scalp cooldown) if the signal still fires. Instead of
1 entry per day, SCORPION gets 5-10 fills per asset when the move runs.

## v3.0 changes

Arena winner #2/#3 playbook: trade BOTH crypto AND XYZ DEX commodities,
signal-driven LONG and SHORT, up to 3 concurrent positions, short holds.

This is the only Senpi predator that trades both markets simultaneously.
Crypto majors + mid-caps + XYZ commodities/indices in one universe.

Universe:
  Crypto: BTC, ETH, SOL, HYPE, ZEC, LIT, GRASS, FARTCOIN, TAO, ONDO,
          SUI, ARB, WLD, DOGE, AVAX
  XYZ:    CL (crude oil), BRENTOIL, GOLD, SPX

Direction: Signal-driven, BOTH long and short based on SM direction + 4H
           price trend alignment.

Design:
  - MAX 3 concurrent positions (30% margin each = 90% max exposure)
  - Leverage 5-10x, score-scaled
  - MIN_SCORE 6 (lower bar, broader universe, faster cadence)
  - 120-min per-asset cooldown, 6 entries/day
  - Short hold profile: hard_timeout=720min (12h), dead_weight_cut=30min
  - Self-executing: calls create_position directly via mcporter
  - _v2_no_thesis_exit: scanner enters, DSL exits, scanner NEVER exits

XYZ handling:
  - XYZ assets use "xyz:" prefix for create_position (e.g. coin="xyz:CL")
  - XYZ assets require leverageType="ISOLATED"
  - XYZ assets have different price thresholds (commodities move less)
  - leaderboard_get_markets returns XYZ assets with dex="xyz" — we INCLUDE them

Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorpion_config as cfg

# ── CONFIGURATION ──
CRYPTO_ASSETS = {
    "BTC", "ETH", "SOL", "HYPE", "ZEC", "LIT", "GRASS", "FARTCOIN",
    "TAO", "ONDO", "SUI", "ARB", "WLD", "DOGE", "AVAX",
}
XYZ_ASSETS = {"CL", "BRENTOIL", "GOLD", "SPX"}

MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6

# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap. Winners get more trades, losers fewer."""
    if starting_budget <= 0:
        return 6
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand
    elif pnl_pct >= 0:     return 8    # Breakeven+
    elif pnl_pct >= -5:    return 6    # Careful
    elif pnl_pct >= -15:   return 4    # Defensive
    elif pnl_pct >= -25:   return 2    # Preserve
    else:                  return 0    # HARD STOP — circuit breaker


COOLDOWN_MINUTES = 120          # Fresh-entry cooldown (different asset, or >60 min since last entry on same asset)
SCALP_COOLDOWN_MINUTES = 15     # v3.1: short cooldown for scalp re-entries on recently-active assets
SCALP_WINDOW_MINUTES = 60       # v3.1: within this window of last entry, asset is a scalp candidate
MAX_REENTRIES_PER_ASSET = 10    # v3.1: cap per-asset daily entries to prevent death spiral
MARGIN_PCT = 0.30
MIN_SCORE = 6

# 4H price alignment thresholds — XYZ assets move less than crypto
MIN_4H_ALIGNED_PCT_CRYPTO = 1.0
MIN_4H_ALIGNED_PCT_XYZ = 0.5

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
    {"min_score": 6,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(token):
    """Check if a token is an XYZ DEX asset."""
    return token in XYZ_ASSETS


def coin_for_position(token):
    """Return the coin string for create_position — XYZ assets need 'xyz:' prefix."""
    if is_xyz(token):
        return f"xyz:{token}"
    return token


def leverage_type_for(token):
    """XYZ assets require ISOLATED, crypto can use CROSS."""
    if is_xyz(token):
        return "ISOLATED"
    return "CROSS"


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_safe_leverage(wallet, asset, requested_leverage):
    """Query Hyperliquid's max leverage for this asset and clamp.

    Fleet-wide leverage safety fix (batch 4). Scorpion trades a broad
    crypto + XYZ universe; some assets' Hyperliquid max is below the
    scanner's requested tier. For XYZ assets, asset should already carry
    the "xyz:" prefix. Clamping prevents CREATE_INVALID_LEVERAGE
    rejections and the phantom ENTRY logs they cause.
    """
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
                    max_lev = int(float(lev.get("value", 20)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling stale ones >10min."""
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


def evaluate_markets(held_coins):
    """Score all allowed assets for trend-following opportunities.

    Returns a list of candidates sorted by score (best first).
    Excludes assets we already hold positions in.
    """
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return []
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    candidates = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()

        # Determine if this is an allowed asset
        is_xyz_asset = (dex == "xyz" and token in XYZ_ASSETS)
        is_crypto_asset = (dex != "xyz" and token in CRYPTO_ASSETS)

        if not is_xyz_asset and not is_crypto_asset:
            continue

        # Skip assets we already hold
        coin_key = coin_for_position(token) if is_xyz_asset else token
        if coin_key in held_coins or token in held_coins:
            continue

        sm_direction = str(m.get("direction", "")).upper()
        if sm_direction not in ("LONG", "SHORT"):
            continue

        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        p4h = safe_float(m.get("token_price_change_pct_4h", 0))
        p1h = safe_float(m.get("token_price_change_pct_1h",
                         m.get("price_change_1h", 0)))
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
        cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
        cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

        if traders < 5:
            continue

        # 4H price alignment gate — SM direction must match price trend
        min_4h = MIN_4H_ALIGNED_PCT_XYZ if is_xyz_asset else MIN_4H_ALIGNED_PCT_CRYPTO
        price_aligned = (sm_direction == "LONG" and p4h >= min_4h) or \
                        (sm_direction == "SHORT" and p4h <= -min_4h)
        if not price_aligned:
            continue

        score = 0
        reasons = []
        asset_label = f"xyz:{token}" if is_xyz_asset else token

        # SM concentration (0-3)
        if pct >= 15:
            score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
        elif pct >= 10:
            score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
        elif pct >= 5:
            score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

        # 4H price alignment (0-3) — bigger move = stronger trend
        big_move = 3.0 if is_xyz_asset else 5.0
        med_move = 1.5 if is_xyz_asset else 3.0
        if abs(p4h) >= big_move:
            score += 3; reasons.append(f"STRONG_TREND {p4h:+.1f}%")
        elif abs(p4h) >= med_move:
            score += 2; reasons.append(f"TREND {p4h:+.1f}%")
        elif abs(p4h) >= min_4h:
            score += 1; reasons.append(f"ALIGNED {p4h:+.1f}%")

        # 15m SM velocity (0-2) — is SM actively building?
        if cc_15m > 1.0:
            score += 2; reasons.append(f"15M_SM_BUILDING {cc_15m:+.2f}")
        elif cc_15m > 0.3:
            score += 1; reasons.append(f"15M_SM_FRESH {cc_15m:+.2f}")
        elif cc_15m < -0.5:
            score -= 1; reasons.append(f"15M_SM_FADING {cc_15m:+.2f}")

        # 1H acceleration (0-1) — is the move accelerating?
        if sm_direction == "LONG":
            if p1h > 0.5:
                score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")
        else:
            if p1h < -0.5:
                score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")

        # Trader depth (0-1)
        if traders >= 50:
            score += 1; reasons.append(f"DEEP_SM ({traders}t)")

        # 4H contribution shift (0-1) — conviction building over hours
        if abs(cc_4h) >= 5.0:
            score += 1; reasons.append(f"4H_CONVICTION {cc_4h:+.1f}")

        if score >= MIN_SCORE:
            reasons.insert(0, f"TREND_FOLLOW {asset_label} {sm_direction}")
            candidates.append({
                "token": token,
                "is_xyz": is_xyz_asset,
                "score": score,
                "direction": sm_direction,
                "reasons": reasons,
                "p4h": p4h,
                "smPct": pct,
                "smTraders": traders,
            })

    if not candidates:
        return []

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def execute_entry(wallet, token, direction, margin, leverage, is_xyz):
    """Execute a position entry via mcporter."""
    coin = coin_for_position(token) if is_xyz else token
    lev_type = leverage_type_for(token) if is_xyz else "CROSS"

    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": coin,
            "direction": direction,
            "leverage": leverage,
            "leverageType": lev_type,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": True,
                "executionTimeoutSeconds": 30,
            },
        }],
    )
    if result and result.get("success"):
        # Fleet-wide batch-4 inner-order success validation.
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


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "cannot read account"})
        return

    # Check for resting orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    # Check existing positions — DSL manages all exits
    held_coins = set()
    for p in positions:
        held_coins.add(p["coin"])
    pos_count = len(positions)

    if pos_count >= MAX_POSITIONS:
        pos_desc = ", ".join(f"{p['coin']} {p['direction']}" for p in positions)
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"MAX_POSITIONS ({MAX_POSITIONS}): {pos_desc}. DSL manages exits.",
            "_v2_no_thesis_exit": True,
        })
        return

    # Daily limits — note: scalp re-entries bypass the overall daily cap (v3.1)
    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    effective_cap = min(dynamic_cap, MAX_DAILY_ENTRIES)
    # We'll check the daily cap PER-CANDIDATE below based on scalp vs fresh.

    # Evaluate all allowed assets
    candidates = evaluate_markets(held_coins)
    if not candidates:
        pos_desc = ", ".join(f"{p['coin']} {p['direction']}" for p in positions) if positions else "none"
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"SCANNING: no qualifying signal across crypto+XYZ universe. Holding: {pos_desc}"})
        return

    # v3.1: ensure asset_entries dict exists in trade counter
    if "asset_entries" not in tc:
        tc["asset_entries"] = {}

    now_ts = time.time()

    # Try candidates in order until one passes cooldown and executes
    for candidate in candidates:
        token = candidate["token"]
        direction = candidate["direction"]

        # ─── v3.1 SCALP RE-ENTRY LOGIC ──────────────────────────────
        # Determine if this is a scalp candidate (recently active asset)
        # or a fresh entry. Scalp re-entries use a 15-min cooldown and
        # BYPASS the overall daily cap. Fresh entries use 120-min
        # cooldown and count against the daily cap.
        asset_state = tc["asset_entries"].get(token, {})
        last_entry_ts = asset_state.get("last_entry_ts", 0)
        entries_today_for_asset = asset_state.get("entries_today", 0)
        seconds_since_last = now_ts - last_entry_ts if last_entry_ts > 0 else float('inf')

        # Per-asset daily cap (prevents death-spiral re-entry on a breaking thesis)
        if entries_today_for_asset >= MAX_REENTRIES_PER_ASSET:
            continue

        is_scalp_candidate = seconds_since_last < SCALP_WINDOW_MINUTES * 60

        if is_scalp_candidate:
            # Scalp re-entry path: check short cooldown only
            if seconds_since_last < SCALP_COOLDOWN_MINUTES * 60:
                continue  # Still within scalp cooldown
            entry_type = "scalp_reentry"
        else:
            # Fresh entry path: check standard cooldown + daily cap
            if cfg.is_asset_cooled_down(token, COOLDOWN_MINUTES):
                continue
            if tc.get("entries", 0) >= effective_cap:
                continue  # Daily cap blocks fresh entries (scalps bypass)
            entry_type = "fresh_entry"

        requested_leverage = get_leverage_for_score(candidate["score"])
        coin_for_clamp = coin_for_position(token) if candidate["is_xyz"] else token
        leverage = get_safe_leverage(wallet, coin_for_clamp, requested_leverage)
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(
            wallet, token, direction, margin, leverage, candidate["is_xyz"]
        )

        if success:
            # v3.1: update per-asset tracker
            tc["asset_entries"][token] = {
                "last_entry_ts": now_ts,
                "entries_today": entries_today_for_asset + 1,
            }
            # Only fresh entries count against overall daily cap
            if entry_type == "fresh_entry":
                tc["entries"] = tc.get("entries", 0) + 1
            cfg.save_trade_counter(tc)

            # Set cooldown: short for scalp, long for fresh
            if entry_type == "scalp_reentry":
                cfg.set_asset_cooldown(token, SCALP_COOLDOWN_MINUTES, reason="scalp_reentry")
            else:
                cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="fresh_entry")

            coin_label = coin_for_position(token) if candidate["is_xyz"] else token
            cfg.output({
                "status": "ok",
                "action": "ENTRY",
                "entry_type": entry_type,  # v3.1: expose scalp vs fresh
                "asset_entries_today": entries_today_for_asset + 1,
                "signal": {
                    "asset": coin_label,
                    "direction": direction,
                    "score": candidate["score"],
                    "leverage": leverage,
                    "p4h": candidate["p4h"],
                    "is_xyz": candidate["is_xyz"],
                    "mode": "MULTI_MARKET_TREND",
                    "reasons": candidate["reasons"],
                },
                "execution": {
                    "coin": coin_label,
                    "direction": direction,
                    "leverage": leverage,
                    "leverageType": leverage_type_for(token) if candidate["is_xyz"] else "CROSS",
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": True,
                },
                "result": result,
                "positions_held": pos_count + 1,
                "max_positions": MAX_POSITIONS,
                "_scorpion_version": "3.1",
            })
            return
        else:
            cfg.log(f"Entry failed for {token}: {result}")
            continue

    # All candidates either on cooldown or failed
    cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": "All qualifying candidates on cooldown or entry failed."})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
