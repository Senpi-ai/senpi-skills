#!/usr/bin/env python3
# Senpi CHEETAH Scanner v4.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""CHEETAH v4.0 — HYPE Funding Rate + Crowding Fader.

v4.0 — COMPLETE RETOOL from SM consensus to funding rate thesis.
Fleet analysis (April 10-11, 2026) found that on HYPE:
- Momentum (v2.1): 33% WR, -$175 gross. Buying tops.
- Contrarian SM (v3.0): 40% WR, -$39 in first 20h. Fading doesn't work
  in erratic chop because HYPE doesn't immediately reverse — it grinds.

The problem: SM consensus is the wrong signal for HYPE. Both directions fail.

New thesis: HYPE has notoriously extreme funding rates — it's one of the
most crowded assets on Hyperliquid. When funding goes extreme (>0.03%/8h),
the crowd is paying heavily to hold. These extremes mean-revert as the
cost of carry forces capitulation.

Cheetah v4.0:
- PRIMARY signal: extreme HYPE funding rate (not SM consensus)
- SECONDARY: SM divergence from funding direction (smart money fading crowd)
- COLLECT funding every 8h while waiting for mean reversion
- PATIENCE: wide DSL, let the funding thesis play out over hours
- CONSERVATIVE: 5x leverage (HYPE is volatile even when you're right)

Different from Wolverine: Wolverine uses SM consensus + velocity on HYPE.
Cheetah v4.0 uses funding rate extremes. Completely uncorrelated signals.

Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheetah_config as cfg

ASSET = "HYPE"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 2           # Funding extremes are rare — fewer entries, higher quality


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

COOLDOWN_MINUTES = 240          # 4 hours — funding regimes persist
MARGIN_PCT = 0.40               # 40% of account
MIN_SCORE = 6
MIN_FUNDING_RATE = 0.0003       # 0.03%/8h = ~40% annualized
XYZ_BANNED = True

# Conservative leverage — HYPE is volatile
LEVERAGE_TIERS = [
    {"min_score": 9, "leverage": 7},
    {"min_score": 6, "leverage": 5},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 7                # HYPE cap is 10x but we stay conservative


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def evaluate_hype_funding():
    """Score HYPE based on funding rate extremes + SM divergence."""

    # Get funding rate from market data
    asset_data = cfg.mcporter_call("market_get_asset_data",
                                    asset=ASSET,
                                    candle_intervals=["1h"],
                                    include_funding=True,
                                    include_order_book=False)
    if not asset_data:
        return None

    ad = asset_data.get("data", asset_data)
    if not isinstance(ad, dict):
        return None

    asset_ctx = ad.get("asset_context", ad.get("assetContext", {}))
    if not isinstance(asset_ctx, dict):
        return None

    funding = safe_float(asset_ctx.get("funding", 0))

    # Must have extreme funding
    if abs(funding) < MIN_FUNDING_RATE:
        return None

    # Determine crowd direction from funding
    # Positive funding = longs paying shorts = crowd is long
    # Negative funding = shorts paying longs = crowd is short
    crowd_direction = "LONG" if funding > 0 else "SHORT"
    fade_direction = "SHORT" if funding > 0 else "LONG"

    score = 0
    reasons = []

    # ── Funding extremity (2-4 pts) — the core signal ──
    abs_funding = abs(funding)
    annualized = abs_funding * 3 * 365 * 100
    if abs_funding >= 0.001:    # 0.1%/8h = ~130% annualized
        score += 4
        reasons.append(f"EXTREME_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
    elif abs_funding >= 0.0006: # 0.06%/8h = ~80% annualized
        score += 3
        reasons.append(f"HIGH_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
    elif abs_funding >= MIN_FUNDING_RATE:
        score += 2
        reasons.append(f"ELEVATED_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")

    # ── SM divergence from crowd (0-3 pts) ──
    # If SM is fading the crowd, the thesis is higher conviction
    sm_raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if sm_raw:
        markets = sm_raw.get("data", sm_raw)
        if isinstance(markets, dict):
            markets = markets.get("markets", markets)
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
        if isinstance(markets, list):
            for m in markets:
                if not isinstance(m, dict):
                    continue
                if str(m.get("token", "")).upper() != ASSET:
                    continue
                dex = str(m.get("dex", "")).lower()
                if dex == "xyz":
                    continue

                sm_dir = str(m.get("direction", "")).upper()
                sm_pct = safe_float(m.get("pct_of_top_traders_gain", 0))
                sm_traders = int(m.get("trader_count", 0))
                cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))

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
                    # SM is WITH the crowd — riskier
                    if sm_pct >= 15:
                        score -= 2
                        reasons.append(f"SM_WITH_CROWD {sm_pct:.1f}% (dangerous)")
                    elif sm_pct >= 5:
                        score -= 1
                        reasons.append(f"SM_SLIGHT_CROWD {sm_pct:.1f}%")

                # SM velocity fading = crowd starting to break
                if sm_dir == crowd_direction and cc_15m < -0.5:
                    score += 1
                    reasons.append(f"SM_MOMENTUM_FADING {cc_15m:.2f}")

                break

    # ── Price action — has the reversal started? (0-2 pts) ──
    candles = ad.get("candles", {}).get("1h", [])
    if len(candles) >= 2:
        close_now = safe_float(candles[-1].get("close", candles[-1].get("c", 0)))
        close_prev = safe_float(candles[-2].get("close", candles[-2].get("c", 0)))
        if close_prev > 0:
            pct_1h = ((close_now - close_prev) / close_prev) * 100
            # Price moving against the crowd = reversal beginning
            if crowd_direction == "LONG" and pct_1h < -0.5:
                score += 2
                reasons.append(f"PRICE_REVERSING {pct_1h:+.2f}%")
            elif crowd_direction == "SHORT" and pct_1h > 0.5:
                score += 2
                reasons.append(f"PRICE_REVERSING {pct_1h:+.2f}%")
            elif crowd_direction == "LONG" and pct_1h < 0:
                score += 1
                reasons.append(f"PRICE_SOFTENING {pct_1h:+.2f}%")
            elif crowd_direction == "SHORT" and pct_1h > 0:
                score += 1
                reasons.append(f"PRICE_SOFTENING {pct_1h:+.2f}%")

    reasons.insert(0, f"FUNDING_FADE HYPE (crowd is {crowd_direction})")

    return {
        "score": score,
        "funding": funding,
        "annualized": annualized,
        "crowd_direction": crowd_direction,
        "fade_direction": fade_direction,
        "reasons": reasons,
    }


def execute_entry(direction, margin, leverage):
    result = cfg.mcporter_call(
        "create_position",
        coin=ASSET,
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
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def load_tc():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0, "last_entry_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)


def save_tc(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


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
                     "note": "RESTING ORDER: HYPE limit order pending."})
        return

    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                         "note": f"STALKING: HYPE {p.get('direction','?')}. Collecting funding. DSL manages exit.",
                         "_v2_no_thesis_exit": True})
            return

    tc = load_tc()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"HYPE on cooldown ({remaining}min remaining)"})
        return

    # Evaluate HYPE funding
    thesis = evaluate_hype_funding()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "PROWLING: HYPE funding not extreme enough to fade"})
        return

    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"PROWLING: HYPE funding {thesis['funding']*100:.4f}% "
                             f"score {thesis['score']}<{MIN_SCORE}. "
                             f"{', '.join(thesis['reasons'][:3])}"})
        return

    leverage = get_leverage_for_score(thesis["score"])
    margin = round(account_value * MARGIN_PCT, 2)

    success, result = execute_entry(thesis["fade_direction"], margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)

        cfg.output({
            "status": "ok",
            "action": "FUNDING_FADE",
            "signal": {
                "asset": ASSET,
                "crowd_direction": thesis["crowd_direction"],
                "fade_direction": thesis["fade_direction"],
                "funding_rate": thesis["funding"],
                "annualized_pct": round(thesis["annualized"], 1),
                "score": thesis["score"],
                "leverage": leverage,
                "mode": "HYPE_FUNDING_FADE",
                "reasons": thesis["reasons"],
            },
            "execution": {
                "asset": ASSET,
                "direction": thesis["fade_direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "result": result,
            "_cheetah_version": "4.0",
        })
    else:
        cfg.output({
            "status": "ok",
            "action": "FUNDING_FADE_FAILED",
            "signal": {"asset": ASSET, "score": thesis["score"],
                       "reasons": thesis["reasons"]},
            "error": result,
            "_cheetah_version": "4.0",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
