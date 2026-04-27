#!/usr/bin/env python3
# Senpi VULTURE Scanner v2.4
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""VULTURE v2.4 — Long-Tail Momentum Rider (1h alignment gate added).

## v2.4 changes (2026-04-27)

Diagnostic on the prior 22 trades found 13/17 losses came from
dead_weight_cut firings — entries that never built momentum within
the 90-min window. High-water ROE on those was 0.0%-5.2%. Pattern
analysis showed the failure mode was `4h up + 1h rejecting + 15m
spike = false breakout`: 4h trend hard-gate passed, 15m velocity
hard-gate passed, but the 1h was already rejecting at entry. Position
stalled instantly, dead_weight_cut fired, repeat.

Fix: add 1h price confirmation gate matching Orca v3.1 / Roach v1.2
pattern. Reject signal if `p1h_aligned < 0.1` (require 1h aligned in
SM direction by at least 0.1% — strict, not "not strongly opposing").

This is asset-agnostic — applies the same gate to every asset in the
universe. Catches ZEC's whipsaw failures the same way it catches
similar patterns on any small-cap.

Also added priceChg1h, priceChg15m to the JSON signal output for
post-fix telemetry: lets us measure whether the 1h gate's value
holds across the next 20 trades.

## v2.1 changes (2026-04-16)

Senpi runtime now enforces single-breach DSL exits (orders onchain).
v2.0's Phase 2 floors (5%/20%) were exiting on normal small-cap wicks.
v2.1 widens Phase 2 tiers so floors survive normal volatility:

  +15% peak → +3% floor (was +1% floor on a +7% peak in v2.0)
  +30% peak → +12% floor
  +50% peak → +30% floor

Also: Phase 1 max_loss 20→25%, retrace 10→15%; dead_weight_cut 60→90 min;
weak_peak_cut 120→180 min. Scanner logic unchanged — only DSL widened.

Complements Scorpion v3.1's scalp re-entry approach: VULTURE tries to
hold THROUGH wicks; SCORPION tries to re-enter AFTER exits. A/B test.

## v2.0 scanner logic (unchanged in v2.1)

COMPLETE REWRITE. v1.0 was a contrarian SM-exhaustion fader on 4 crypto majors
(BTC/ETH/SOL/HYPE) gated at MIN_4H_MOVE_PCT=3.0, which made the entry gate
effectively unreachable. Result: 0 trades in 14+ days.

v2.0 is a totally different agent. Designed from the #1 Arena winner's
playbook (3-week reigning champion, 38.6% win rate, 6.15x profit factor,
~80% of profit from 6 trades out of 102, hold times 24h to 8 days).

Thesis:
  Small-cap Hyperliquid perps have thicker trader-count signal-to-noise
  ratios than the majors. When Smart Money concentration on a small cap
  ALIGNS with a fresh 4h price trend AND 15m velocity is accelerating,
  enter in the direction of the trend and hold it via the DSL tier curve
  for days, not hours.

Philosophy (copied from Arena winner #1):
  - Low win rate is fine (~38%) if winners are 5-10x bigger than losers
  - Tight loss cuts via DSL dead_weight_cut
  - Long hold on winners via hard_timeout=10080min (7 days)
  - Moderate leverage (5-7x) because small caps can't handle 20x
    (slippage kills you on low-liquidity books)
  - Signal-driven direction, NOT long-only bias. The Arena winner
    was long because April 2026 was a bullish regime, not because
    LONG is a rule. Vulture v2.0 takes SHORTs when signals support.
  - Max 2 concurrent positions

Universe: 25 small/mid-cap Hyperliquid perps that no current Senpi predator
scans (most predators are locked to 4-12 majors or single assets). The
long tail is a fleet-wide blind spot that the Arena winner exploits.

Differences from Lemon (also a fader but profitable):
  - Lemon fades 12 majors. Vulture rides 25 small caps. Different universe.
  - Lemon fades against SM direction. Vulture follows SM direction.
  - Lemon holds ~235 min average. Vulture holds 24h to 7 days.
  - Lemon uses 20x at apex. Vulture caps at 7x (small-cap liquidity).

Uses: leaderboard_get_markets + strategy_get_open_orders + create_position
Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vulture_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS — Long-Tail Momentum Rider
# ═══════════════════════════════════════════════════════════════

# 25 small/mid-cap Hyperliquid perps. Excludes the majors that other
# predators already cover (BTC/ETH/SOL are banned here — let Polar/Grizzly
# handle those). Includes the small caps the #1 Arena winner actually
# traded and hit on (HEMI, WLD, MON, XPL, AIXBT, ARB) plus other liquid
# small caps with sufficient SM trader counts.
TRACKED_ASSETS = [
    "HYPE", "HEMI", "WLD", "MON", "XPL", "AIXBT", "ARB", "ASTER",
    "POLYX", "LDO", "APT", "DYDX", "ONDO", "SUI", "kBONK", "kPEPE",
    "TAO", "GRASS", "ZEC", "LIT", "FARTCOIN", "MORPHO", "NEAR", "INJ",
    "AVAX", "LINK", "DOGE",
]

# Banned — crypto majors handled by other predators; XYZ handled by Bald Eagle
BANNED_ASSETS = {"BTC", "ETH", "SOL"}

MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
XYZ_BANNED = True


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker — fleet standard)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 8    # Hot hand — up >5%
    elif pnl_pct >= 0:     return 6    # Small win / breakeven
    elif pnl_pct >= -5:    return 4    # Careful
    elif pnl_pct >= -15:   return 2    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve
    else:                  return 0    # HARD STOP


COOLDOWN_MINUTES = 240          # 4h per-asset cooldown (sniper cadence)
MARGIN_PCT = 0.40               # 40% of account per trade, 2 positions max = 80%
MIN_SCORE = 7                   # Entry floor; scoring dimensions do the real work
MIN_SM_PCT = 3.0                # Asset must have at least 3% SM concentration
MIN_SM_TRADERS = 15             # Small caps need lower threshold than Lemon (20)
MIN_4H_ALIGNED_PCT = 1.0        # 4h price must be aligned ≥1% in SM direction
MIN_15M_VELOCITY = 0.3          # 15m velocity must exceed 0.3% (actively building)

# Conviction-scaled leverage — capped at 7x for small caps (slippage)
# At apex the trade gets 7x; weaker confluence gets 5x or 3x.
LEVERAGE_TIERS = [
    {"min_score": 11, "leverage": 7},
    {"min_score": 9,  "leverage": 5},
    {"min_score": 7,  "leverage": 3},
]
DEFAULT_LEVERAGE = 3
MAX_LEVERAGE = 7


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

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
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


def get_safe_leverage(wallet, asset, requested_leverage):
    """Query Hyperliquid's max leverage for this asset and clamp.

    Fleet-wide leverage safety fix (batch 4). Vulture's small-cap universe
    frequently contains assets whose Hyperliquid max is below the scanner's
    requested tier (e.g. MON max=5x). Clamping prevents
    CREATE_INVALID_LEVERAGE rejections and the phantom ENTRY logs they
    cause.
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
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC (default 600s). Fleet-standard pattern."""
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
# SIGNAL SCORING — momentum following (opposite philosophy from Lemon)
# ═══════════════════════════════════════════════════════════════

def _get_funding_regime():
    """v2.2: market-wide funding regime via new MCP tool."""
    try:
        r = cfg.mcporter_call("market_get_funding_regime")
        if r:
            return r.get("data", r).get("regime")
    except Exception:
        pass
    return None


def _get_funding_history(asset):
    """v2.2: per-asset persistence + trend via new MCP tool."""
    try:
        r = cfg.mcporter_call("market_get_funding_history", asset=asset)
        if r:
            data = r.get("data", r)
            return {
                "persistence_hours": data.get("persistence_hours"),
                "trend": data.get("trend"),
            }
    except Exception:
        pass
    return None


def fetch_sm_data():
    """Get Hyperfeed SM data for all tracked small caps."""
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
        if token in BANNED_ASSETS:
            continue
        # Match against TRACKED_ASSETS (case-preserved for kPEPE/kBONK)
        matched = None
        for tracked in TRACKED_ASSETS:
            if token == tracked.upper():
                matched = tracked
                break
        if matched is None:
            continue

        sm_map[matched] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "price_chg_15m": safe_float(m.get("token_price_change_pct_15m",
                                        m.get("price_change_15m", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "contrib_4h": safe_float(m.get("contribution_pct_change_4h", 0)),
        }

    return sm_map


def evaluate_momentum(asset, sm):
    """Score a small-cap momentum trade. Entry direction follows SM direction
    when confluence is strong. Returns scored signal or None.

    Key difference from Lemon: Lemon FADES the SM consensus.
    Vulture FOLLOWS it on small caps (momentum rider philosophy).
    """
    direction = sm["direction"]
    if direction not in ("LONG", "SHORT"):
        return None

    pct = sm["pct"]
    traders = sm["traders"]
    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    p15m = sm.get("price_chg_15m", 0)
    c15m = sm["contrib_15m"]
    c1h = sm["contrib_1h"]
    c4h = sm["contrib_4h"]

    # ─── HARD GATES ───
    if pct < MIN_SM_PCT or traders < MIN_SM_TRADERS:
        return None

    # 4h price must be ALIGNED with SM direction (momentum confirmation).
    # If SM is LONG but price is down, that's a fading signal — skip.
    price_aligned = (direction == "LONG" and p4h >= MIN_4H_ALIGNED_PCT) or \
                    (direction == "SHORT" and p4h <= -MIN_4H_ALIGNED_PCT)
    if not price_aligned:
        return None

    # v2.4: 1h price must be ALIGNED with SM direction (Orca v3.1 pattern).
    # Catches the "4h up + 1h rejecting + 15m spike" false-breakout pattern
    # that was driving most dead_weight_cut losses. Diagnosed 2026-04-27:
    # 13 of 17 losses had HW ROE < 5% within 90 min — entries that never
    # built momentum because the 1h was already fading at entry time.
    p1h_aligned = p1h if direction == "LONG" else -p1h
    if p1h_aligned < 0.1:
        return None

    # 15m velocity must be actively building in SM direction.
    # For a LONG entry we need positive 15m contribution velocity.
    # For SHORT we also need positive velocity (SM is PILING IN on the short side).
    # In both cases cc_15m > 0 means the trend is accelerating, not fading.
    if c15m < MIN_15M_VELOCITY:
        return None

    score = 0
    reasons = []

    # ─── SM CONCENTRATION TIER (0-4) ───
    # Bigger crowd = more conviction, but on small caps crowds can be
    # wrong faster than on majors. Cap at 4 points.
    if pct >= 18:
        score += 4
        reasons.append(f"HEAVY_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"STRONG_FLOW {pct:.1f}% ({traders}t) {direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"MODERATE_FLOW {pct:.1f}% ({traders}t) {direction}")
    else:
        score += 1
        reasons.append(f"LIGHT_FLOW {pct:.1f}% ({traders}t) {direction}")

    # ─── 4H PRICE MOMENTUM (0-3) ───
    # Bigger aligned move = stronger trend. Small caps can run 5-15%
    # in a session so don't cap the top tier at 3% like BTC/ETH agents.
    p4h_aligned = p4h if direction == "LONG" else -p4h
    if p4h_aligned >= 8.0:
        score += 3
        reasons.append(f"TREND_RUNNING {p4h:+.1f}%")
    elif p4h_aligned >= 4.0:
        score += 2
        reasons.append(f"TREND_STRONG {p4h:+.1f}%")
    elif p4h_aligned >= 2.0:
        score += 1
        reasons.append(f"TREND_BUILDING {p4h:+.1f}%")

    # ─── 15M VELOCITY TIER (0-3) ───
    # Higher 15m velocity = SM is actively piling in right now.
    if c15m >= 3.0:
        score += 3
        reasons.append(f"15M_EXPLOSIVE +{c15m:.2f}")
    elif c15m >= 1.0:
        score += 2
        reasons.append(f"15M_STRONG +{c15m:.2f}")
    elif c15m >= 0.5:
        score += 1
        reasons.append(f"15M_BUILDING +{c15m:.2f}")

    # ─── 1H ACCELERATION (0-2) ───
    # If 15m > 1h > 0, velocity is accelerating (the ideal entry point).
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"ACCELERATING 15m({c15m:.2f})>1h({c1h:.2f})")
    elif c1h >= 1.0:
        score += 1
        reasons.append(f"1H_POSITIVE +{c1h:.2f}")

    # ─── 4H CONTRIBUTION (0-1) ───
    # c4h positive = SM has been flowing in this direction over 4 hours
    # (trend continuation signal, not just a spike).
    if c4h >= 2.0:
        score += 1
        reasons.append(f"4H_CONTINUATION +{c4h:.1f}")

    # ─── TRADER DEPTH (0-1) ───
    # More independent traders = higher signal validity
    if traders >= 50:
        score += 1
        reasons.append(f"DEEP_DEPTH ({traders}t)")

    # ─── MOVE EXHAUSTION PENALTY ───
    # If the 4h move is already extreme (>12%), we're late.
    # This is the flip of Lemon's signal — Lemon ENTERS on extreme moves
    # to fade them; Vulture PENALIZES extreme moves because momentum
    # follows get steamrolled by reversals.
    if p4h_aligned >= 15.0:
        score -= 3
        reasons.append(f"LATE_ENTRY_PENALTY {p4h:+.1f}%")
    elif p4h_aligned >= 12.0:
        score -= 2
        reasons.append(f"MOVE_EXTENDED {p4h:+.1f}%")

    # ── v2.2: Regime alignment (momentum thesis — not a hard gate) ──
    # Vulture FOLLOWS momentum. Unlike Dog (which fades crowding),
    # Vulture wants the macro trend behind it.
    #   Regime matches direction      → trend has macro backing (+1)
    #   Regime opposes direction      → fighting the macro tide (-1)
    #   Regime NEUTRAL / unavailable  → no adjustment
    regime = evaluate_momentum._regime_cache  # populated by caller per scan
    if regime == "LONG_CROWDED" and direction == "LONG":
        score += 1; reasons.append("REGIME_LONG_CROWDED_aligned")
    elif regime == "SHORT_CROWDED" and direction == "SHORT":
        score += 1; reasons.append("REGIME_SHORT_CROWDED_aligned")
    elif regime == "LONG_CROWDED" and direction == "SHORT":
        score -= 1; reasons.append("REGIME_LONG_CROWDED_fighting")
    elif regime == "SHORT_CROWDED" and direction == "LONG":
        score -= 1; reasons.append("REGIME_SHORT_CROWDED_fighting")
    elif regime is not None:
        reasons.append(f"REGIME_{regime}")

    # ── v2.2: Persistence validates macro trend durability ──
    # If funding has been crowded in our direction for hours, the macro
    # trend is mature — Vulture's momentum ride has runway.
    fh = evaluate_momentum._fh_cache.get(asset) if hasattr(evaluate_momentum, "_fh_cache") else None
    if fh:
        ph = fh.get("persistence_hours")
        try:
            ph_val = float(ph) if ph is not None else None
        except (TypeError, ValueError):
            ph_val = None
        if ph_val is not None and ph_val >= 6:
            score += 1; reasons.append(f"TREND_PERSISTENT_{ph_val:.0f}h")

    if score < MIN_SCORE:
        return None

    return {
        "asset": asset,
        "direction": direction,
        "score": score,
        "mode": "LONG_TAIL_MOMENTUM",
        "reasons": reasons,
        "smPct": pct,
        "smTraders": traders,
        "priceChg4h": p4h,
        "priceChg1h": p1h,           # v2.4: telemetry for post-fix evaluation
        "priceChg15m": p15m,         # v2.4
        "contrib15m": c15m,
        "contrib1h": c1h,
        "regime": regime,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, asset, direction, leverage, margin):
    """Call create_position directly via mcporter (Wolverine/Lemon pattern)."""
    # Handle k-prefixed tokens (kBONK, kPEPE) — Hyperliquid expects exact casing
    coin = asset  # TRACKED_ASSETS already preserves case
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
    if result and result.get("success"):
        # Fleet-wide batch-4 inner-order success validation. Outer envelope
        # lies when a per-order rejection (e.g. CREATE_INVALID_LEVERAGE)
        # happens — dig into data.orders[0].success before claiming success.
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
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # RIDING: position(s) open → DSL manages exit
    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_v2_no_thesis_exit": True,
            "_vulture_version": "2.4",
        })
        return

    # Resting order guard
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    # Trade counter
    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Fetch SM data for all tracked assets
    sm_map = fetch_sm_data()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data on tracked small caps"})
        return

    # v2.2: populate regime + funding_history caches once per scan
    evaluate_momentum._regime_cache = _get_funding_regime()
    evaluate_momentum._fh_cache = {}

    # Evaluate momentum signals
    signals = []
    rejections = {}
    active_coins = {p["coin"].upper() for p in positions}

    for asset in TRACKED_ASSETS:
        sm = sm_map.get(asset)
        if not sm:
            rejections[asset] = "no_data"
            continue

        # Skip assets we already hold
        if asset.upper() in active_coins:
            rejections[asset] = "holding"
            continue

        # Per-asset cooldown
        if cfg.is_asset_cooled_down(asset, COOLDOWN_MINUTES):
            rejections[asset] = "cooldown"
            continue

        # v2.2: fetch per-asset funding_history for persistence scoring
        evaluate_momentum._fh_cache[asset] = _get_funding_history(asset)

        result = evaluate_momentum(asset, sm)
        if result:
            signals.append(result)
        else:
            rejections[asset] = "no_signal"

    if not signals:
        status_parts = [f"{a}:{r}" for a, r in rejections.items()]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No momentum signals. {', '.join(status_parts[:6])}"})
        return

    # Sort by score, pick the best
    signals.sort(key=lambda s: s["score"], reverse=True)
    best = signals[0]

    # Execute entry
    requested_leverage = get_leverage_for_score(best["score"])
    # Fleet-wide batch-4 leverage safety: clamp to asset max to avoid
    # CREATE_INVALID_LEVERAGE rejections on low-cap Hyperliquid assets.
    leverage = get_safe_leverage(wallet, best["asset"], requested_leverage)
    margin = round(account_value * MARGIN_PCT, 2)

    success, result = execute_entry(wallet, best["asset"], best["direction"], leverage, margin)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        cfg.save_trade_counter(tc)

        # Per-asset cooldown to prevent re-entering immediately after DSL cuts
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
            },
            "result": result,
            "allSignals": [
                {"asset": s["asset"], "direction": s["direction"], "score": s["score"]}
                for s in signals[:5]
            ],
            "_vulture_version": "2.4",
        })
    else:
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": result,
            "_vulture_version": "2.4",
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
