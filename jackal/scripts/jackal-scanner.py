#!/usr/bin/env python3
# Senpi JACKAL Scanner v1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/996
"""JACKAL v1.1 — The Smart Stalker (promotion loosen).

## v1.1 changes (2026-04-22) — DORMANT-FIX

v1.0 has been dormant (0 trades) because the active pool never fills:
PROMOTE_SCORE_THRESHOLD=70 + sustain >= 6h is too strict against the
actual distribution of Senpi user quality scores. v1.1 loosens to 65 +
3h to unblock promotions. Active pool still capped at
MAX_ACTIVE_POOL_SIZE, so over-promotion still self-corrects.

No scanner logic, signal scoring, or DSL changes.

---

## v1.0 (original) — The Smart Stalker

The fleet's first SECONDARY-SIGNAL agent. While every other predator
(Kodiak, Condor, Python, Wolverine, Polar, Grizzly, Scorpion, etc.)
generates signals from market data, Jackal generates signals from
other traders' observed actions.

Core thesis: the moves of top-performing Senpi users are high-value
signals. But naive "copy the winner" fails because (a) winners
regress, (b) signals lag, (c) style-mismatches amplify risk. Jackal
filters aggressively — consensus + source quality + independent TA
confirmation — and executes with its OWN sizing and OWN DSL.

## Architecture — two-tier tracking

WATCHLIST (~100-200): Arena participants + top Senpi points users
  - Polled every 5 min for re-eligibility
  - Jackal does NOT act on their trades

ACTIVE POOL (~20-30): Qualified by quality_score >= 70 for 6h+
  - Polled every scan (3 min) for position changes with forceFetch
  - Jackal acts on their trades, subject to filters

## Emerging-mover detection

Scores on TRAJECTORY, not LEVEL. A trader going -$500→+$100 scores
higher than one going +$500→+$300. Rank velocity weighted by real
PnL backing. Rising stars are promoted BEFORE they hit rank 1.

## Signal filtering

A detected new position from an Active Pool member becomes a candidate.
Scored by:
  - Source trader quality_score
  - Position freshness (sweet spot 15min-4h)
  - Consensus (3+ pool members same trade = GOLD SIGNAL)
  - Asset concentration (source's historical winners)
  - Independent TA confirmation (4h + 1h + SM gate)

## Execution

Own sizing (3-7x leverage, 20-70% margin by score).
Own DSL (patience profile with consensus-aware Phase 1 width).
Max 3 concurrent. Per-source exposure cap 40%.

Runs every 3 minutes.
"""

import sys
import os
import time
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jackal_config as cfg
import jackal_pool as pool_mod


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — Jackal's own trading parameters
# ═══════════════════════════════════════════════════════════════

MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 5
MAX_LEVERAGE = 7                       # Patient hold profile
MIN_LEVERAGE = 3
PER_SOURCE_EXPOSURE_CAP_PCT = 40       # Max % of budget from any one source

# Signal scoring thresholds
MIN_SIGNAL_SCORE = 65                  # Below this, skip
GOLD_SIGNAL_SCORE = 85                 # Above this, max sizing

# Position age gates (seconds) — sweet spot for copying
POSITION_AGE_MIN = 15 * 60             # Don't copy at 0-15min (noise)
POSITION_AGE_SWEET = 60 * 60           # Score peaks at ~1h
POSITION_AGE_MAX = 8 * 3600            # Don't copy >8h old (stale)

# Consensus window
CONSENSUS_WINDOW_SECONDS = 2 * 3600    # 2h window for "same trade"

# Cooldowns
ASSET_COOLDOWN_MINUTES = 360           # 6h per-asset after our exit
SAME_DIR_COOLDOWN_MINUTES = 240        # 4h same-direction cooldown

# Consensus multipliers
CONSENSUS_MULTIPLIER = {
    1: 1.0,
    2: 1.8,
    3: 3.0,
    4: 3.5,
    5: 4.0,
}

# Sizing tiers by signal_score
SIZING_TIERS = [
    # (min_score, margin_pct, leverage)
    (GOLD_SIGNAL_SCORE, 0.55, 7),   # Gold — max commit
    (75, 0.35, 5),                   # Strong
    (MIN_SIGNAL_SCORE, 0.20, 3),     # Base
]

STARTING_BUDGET = 1000.0


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# Position detection — diff against last known state
# ═══════════════════════════════════════════════════════════════

def position_key(pos):
    """Unique key for a position in cache."""
    return f"{pos.get('coin', '')}:{pos.get('direction', '')}"


def scan_active_pool_for_new_positions(active_pool, position_cache):
    """For each Active Pool member, fetch current positions and diff
    against cache to detect NEW positions. Returns list of detections."""
    detections = []
    now = cfg.now_ts()

    for user_id, entry in active_pool.items():
        wallet = entry["wallet"]
        # Force-fetch ensures <60s cache staleness
        _, positions = cfg.get_positions(wallet, force_fetch=True)

        cache_entry = position_cache.get(user_id, {})
        last_positions = cache_entry.get("positions", [])
        last_keys = {position_key(p) for p in last_positions}

        current_keys = {position_key(p) for p in positions}
        new_keys = current_keys - last_keys

        for pos in positions:
            pk = position_key(pos)
            if pk in new_keys:
                detections.append({
                    "source_user_id": user_id,
                    "source": entry,
                    "coin": pos["coin"],
                    "direction": pos["direction"],
                    "entry_price": pos["entryPrice"],
                    "size": pos["size"],
                    "detected_ts": now,
                })

        # Update cache
        position_cache[user_id] = {
            "wallet": wallet,
            "last_fetched_ts": now,
            "positions": [
                {
                    "coin": p["coin"],
                    "direction": p["direction"],
                    "entryPrice": p["entryPrice"],
                    "first_seen_ts": _first_seen_ts(cache_entry, p, now),
                }
                for p in positions
            ],
        }

    return detections


def _first_seen_ts(cache_entry, pos, now):
    """Preserve first_seen_ts from cache if position was already known."""
    last = cache_entry.get("positions", [])
    for p in last:
        if p.get("coin") == pos.get("coin") and p.get("direction") == pos.get("direction"):
            return p.get("first_seen_ts", now)
    return now


# ═══════════════════════════════════════════════════════════════
# Candidate scoring — combine detections + context
# ═══════════════════════════════════════════════════════════════

def group_detections_by_candidate(detections):
    """Group by (coin, direction) — multiple sources on same trade = consensus."""
    groups = defaultdict(list)
    for d in detections:
        groups[(d["coin"], d["direction"])].append(d)
    return groups


def score_position_age(age_seconds):
    """0-100. Peaks at ~1h, declines past 4h."""
    if age_seconds < POSITION_AGE_MIN:
        return 0    # too fresh — noise risk
    if age_seconds <= POSITION_AGE_SWEET:
        # 15min → 100, 60min → 100
        return 100
    if age_seconds <= 4 * 3600:
        # 1h → 100, 4h → 50
        frac = (age_seconds - POSITION_AGE_SWEET) / (4 * 3600 - POSITION_AGE_SWEET)
        return 100 - int(frac * 50)
    if age_seconds <= POSITION_AGE_MAX:
        # 4h → 50, 8h → 10
        frac = (age_seconds - 4 * 3600) / (POSITION_AGE_MAX - 4 * 3600)
        return 50 - int(frac * 40)
    return 0


def score_consensus(n_sources):
    """Returns score boost (multiplicative with source quality)."""
    multiplier = CONSENSUS_MULTIPLIER.get(min(n_sources, 5), 4.0)
    return min(30 * multiplier / 3.0, 30)    # max 30 points from consensus alone


def ta_confirms(coin, direction):
    """Independent Senpi TA gate. Returns (passes, reason)."""
    data = cfg.mcporter_call(
        "market_get_asset_data", asset=coin,
        candle_intervals=["15m", "1h", "4h"],
        include_funding=True, include_order_book=False
    )
    if not data or not data.get("success"):
        return False, "no_market_data"

    asset_data = data.get("data", data)
    candles_15m = asset_data.get("candles", {}).get("15m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])

    if len(candles_4h) < 6 or len(candles_1h) < 6 or len(candles_15m) < 6:
        return False, "insufficient_candles"

    # 4h trend structure
    def trend_structure(candles, lookback=6):
        if len(candles) < lookback:
            return "NEUTRAL"
        lows = [safe_float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
        highs = [safe_float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
        higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
        lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
        total = lookback - 1
        if higher_lows >= total * 0.55:
            return "BULLISH"
        if lower_highs >= total * 0.55:
            return "BEARISH"
        return "NEUTRAL"

    trend_4h = trend_structure(candles_4h)

    # 4h opposes direction = HARD BLOCK
    if direction == "LONG" and trend_4h == "BEARISH":
        return False, "4h_opposes_LONG"
    if direction == "SHORT" and trend_4h == "BULLISH":
        return False, "4h_opposes_SHORT"

    # 1h momentum doesn't strongly oppose
    def momentum(candles, n=2):
        if len(candles) < n + 1:
            return 0
        old = safe_float(candles[-(n + 1)].get("close", candles[-(n + 1)].get("c", 0)))
        new = safe_float(candles[-1].get("close", candles[-1].get("c", 0)))
        if old == 0:
            return 0
        return ((new - old) / old) * 100

    mom_1h = momentum(candles_1h, 2)
    if direction == "LONG" and mom_1h < -0.5:
        return False, f"1h_neg_{mom_1h:+.2f}%"
    if direction == "SHORT" and mom_1h > 0.5:
        return False, f"1h_pos_{mom_1h:+.2f}%"

    return True, "ok"


def get_sm_direction(coin):
    """Smart money direction from leaderboard_get_markets."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data:
        return None
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    long_pct = 0
    short_pct = 0
    for m in markets:
        if not isinstance(m, dict):
            continue
        if str(m.get("token", m.get("coin", ""))).upper() != coin.upper():
            continue
        d = m.get("direction", "").lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        if d == "long":
            long_pct = pct
        elif d == "short":
            short_pct = pct

    total = long_pct + short_pct
    if total == 0:
        return "NEUTRAL"
    ratio = (long_pct / total) * 100
    if ratio > 58:
        return "LONG"
    if ratio < 42:
        return "SHORT"
    return "NEUTRAL"


def score_candidate(coin, direction, sources, position_cache):
    """Score a candidate trade. Returns (score, reasons_dict)."""
    reasons = {}

    # 1. Source quality — avg of all source quality_scores, weighted by count
    source_scores = [s["source"]["quality_score"] for s in sources]
    avg_source_quality = sum(source_scores) / len(source_scores)
    max_source_quality = max(source_scores)
    source_score = (avg_source_quality * 0.7 + max_source_quality * 0.3)
    reasons["source_quality"] = round(source_score, 1)

    # 2. Consensus bonus
    n_sources = len(sources)
    consensus_score = score_consensus(n_sources)
    reasons["consensus"] = f"{n_sources}_traders_+{consensus_score:.0f}"

    # 3. Position freshness (worst-case — use oldest detection)
    now = cfg.now_ts()
    # Use first_seen from position cache when possible
    ages = []
    for s in sources:
        user_id = s["source_user_id"]
        ce = position_cache.get(user_id, {}).get("positions", [])
        for p in ce:
            if (p.get("coin") == coin and p.get("direction") == direction):
                ages.append(now - p.get("first_seen_ts", now))
                break
        else:
            ages.append(now - s.get("detected_ts", now))
    oldest_age = max(ages) if ages else 0
    freshness = score_position_age(oldest_age)
    reasons["freshness"] = f"{oldest_age/60:.0f}min_+{freshness}"

    # 4. Compute composite (pre-TA)
    # Weighted: source_quality 40% + consensus 30% + freshness 30%
    composite = source_score * 0.4 + consensus_score + freshness * 0.3

    # 5. GOLD SIGNAL detection — newly promoted source + existing pool consensus
    gold_signal = False
    for s in sources:
        promoted_at = s["source"].get("promoted_at", 0)
        hours_since_promote = (now - promoted_at) / 3600
        if hours_since_promote < 24 and n_sources >= 2:
            gold_signal = True
            composite += 15   # gold boost
            reasons["GOLD_SIGNAL"] = f"{s['source'].get('user_name', s['source_user_id'])}_promoted<24h"
            break

    return composite, reasons, gold_signal


# ═══════════════════════════════════════════════════════════════
# Sizing
# ═══════════════════════════════════════════════════════════════

def get_size_for_score(signal_score, account_value, coin, wallet, is_gold=False):
    """Determine margin + leverage based on signal score."""
    margin_pct = 0.2
    leverage = MIN_LEVERAGE
    tier_name = "BASE"

    for min_score, mpct, lev in SIZING_TIERS:
        if signal_score >= min_score:
            margin_pct = mpct
            leverage = lev
            tier_name = "GOLD" if is_gold else ("STRONG" if min_score == 75 else "BASE")
            break

    # Clamp leverage to HL max
    safe_lev = _get_safe_leverage(wallet, coin, leverage)
    margin = round(account_value * margin_pct, 2)

    return margin, safe_lev, tier_name


def _get_safe_leverage(wallet, coin, desired):
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
# Execution
# ═══════════════════════════════════════════════════════════════

def has_resting_orders(wallet):
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

    data = result.get("data", result)
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if orders and isinstance(orders, list):
        first = orders[0] if isinstance(orders[0], dict) else {}
        if first and not first.get("success", True):
            return False, {"error": first.get("error", "inner_order_failed"), "raw": result}

    return True, result


# ═══════════════════════════════════════════════════════════════
# Daily cap
# ═══════════════════════════════════════════════════════════════

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    if starting_budget <= 0:
        return 3
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 20:      return 7
    elif pnl_pct >= 5:     return 5
    elif pnl_pct >= -5:    return 3
    elif pnl_pct >= -15:   return 2
    elif pnl_pct >= -25:   return 1
    else:                  return 0    # circuit breaker


# ═══════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # Need own user_id to exclude from pool
    own_user_id = None
    me = cfg.mcporter_call("user_get_me")
    if me:
        own_user_id = me.get("data", {}).get("id") or me.get("id")

    tc = cfg.load_trade_counter()
    if tc.get("gate") == "HARD_STOP":
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                    "note": f"gate=HARD_STOP: {tc.get('gateReason', '')}"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    pool = cfg.load_pool()
    maint_state = cfg.load_maintenance_state()
    now = cfg.now_ts()

    # ═══ POOL MAINTENANCE ════════════════════════════════════
    # Slow loops — run only when intervals elapsed

    pool_maintenance_actions = []

    if pool_mod.needs_watchlist_refresh(maint_state):
        cfg.log("Refreshing watchlist...")
        candidates = pool_mod.refresh_watchlist(pool, own_user_id=own_user_id)
        pool = pool_mod.refresh_pool_scores(pool, candidates, own_user_id=own_user_id)
        maint_state["last_watchlist_refresh_ts"] = now
        maint_state["last_score_refresh_ts"] = now
        pool_maintenance_actions.append(f"watchlist_refresh:{len(candidates)}_candidates")
    elif pool_mod.needs_score_refresh(maint_state):
        cfg.log("Re-scoring existing pool...")
        # Rescore only existing members (cheaper)
        existing_ids = set(pool.get("active_pool", {}).keys()) | set(pool.get("watchlist", {}).keys())
        existing_candidates = [{"user_id": uid, "user_name": "", "rank": 0} for uid in existing_ids]
        pool = pool_mod.refresh_pool_scores(pool, existing_candidates, own_user_id=own_user_id)
        maint_state["last_score_refresh_ts"] = now
        pool_maintenance_actions.append(f"rescore:{len(existing_ids)}_members")

    cfg.save_pool(pool)
    cfg.save_maintenance_state(maint_state)

    active_pool = pool.get("active_pool", {})

    if not active_pool:
        cfg.output({
            "success": True,
            "heartbeat": "NO_REPLY",
            "note": "Pool empty — waiting for qualified traders",
            "watchlist_size": len(pool.get("watchlist", {})),
            "maintenance": pool_maintenance_actions,
        })
        return

    # ═══ POSITION MANAGEMENT ═════════════════════════════════
    # If at max positions, skip scanning
    if len(positions) >= MAX_POSITIONS:
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"RIDING {len(positions)}/{MAX_POSITIONS} — DSL manages",
            "positions": [f"{p['coin']} {p['direction']}" for p in positions],
            "_v2_no_thesis_exit": True,
        })
        return

    # Daily cap
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"Daily cap {tc['entries']}/{dynamic_cap}",
        })
        return

    if has_resting_orders(wallet):
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": "Resting order pending",
        })
        return

    # ═══ SCAN ACTIVE POOL FOR NEW POSITIONS ═══════════════════
    position_cache = cfg.load_position_cache()
    detections = scan_active_pool_for_new_positions(active_pool, position_cache)
    cfg.save_position_cache(position_cache)

    if not detections:
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"Scanned {len(active_pool)} sources — no new positions",
            "pool_stats": {
                "active_pool": len(active_pool),
                "watchlist": len(pool.get("watchlist", {})),
            },
            "maintenance": pool_maintenance_actions,
        })
        return

    # ═══ GROUP BY (coin, direction) FOR CONSENSUS ═════════════
    groups = group_detections_by_candidate(detections)

    # Score each candidate, filter + rank
    open_coins = {p["coin"].upper() for p in positions}
    candidates = []

    for (coin, direction), sources in groups.items():
        # Skip if we already have position
        if coin.upper() in open_coins:
            continue
        # Skip if cooldown
        if cfg.is_asset_cooled_down(coin, ASSET_COOLDOWN_MINUTES):
            continue
        # Per-source exposure check
        source_ids = [s["source_user_id"] for s in sources]
        existing_from_source = sum(
            1 for p in positions if position_cache.get(source_ids[0], {})
        )

        score, reasons, is_gold = score_candidate(coin, direction, sources, position_cache)

        if score < MIN_SIGNAL_SCORE:
            continue

        # Independent TA gate — even a 4-trader consensus gets blocked if 4h opposes
        ta_passes, ta_reason = ta_confirms(coin, direction)
        reasons["ta"] = ta_reason
        if not ta_passes:
            continue

        # SM consensus alignment (not required, but adds/removes weight)
        sm_dir = get_sm_direction(coin)
        reasons["sm_dir"] = sm_dir
        if sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
            score -= 15   # SM disagrees with sources — reduce score
            reasons["sm_penalty"] = -15
            if score < MIN_SIGNAL_SCORE:
                continue

        candidates.append({
            "coin": coin,
            "direction": direction,
            "score": round(score, 1),
            "reasons": reasons,
            "sources": sources,
            "is_gold": is_gold,
        })

    if not candidates:
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"{len(detections)} detections, 0 passed filters",
            "detections_by_coin": [
                f"{d['coin']} {d['direction']} ({d['source']['user_name']})"
                for d in detections[:5]
            ],
            "maintenance": pool_maintenance_actions,
        })
        return

    # Sort by score, act on best
    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]

    # ═══ EXECUTE ══════════════════════════════════════════════
    margin, leverage, tier_name = get_size_for_score(
        best["score"], account_value, best["coin"], wallet, is_gold=best["is_gold"]
    )

    # Extra: cap leverage for GOLD signals — don't let size run wild on hype
    if best["is_gold"]:
        leverage = min(leverage, 7)
        cfg.log(f"GOLD SIGNAL on {best['coin']} {best['direction']} — max sizing applied")

    success, result = execute_entry(wallet, best["coin"], best["direction"], margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = now
        cfg.save_trade_counter(tc)

        # Log signal for feedback loop
        signal_log = cfg.load_signal_log()
        signal_log["signals"].append({
            "ts": now,
            "coin": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "is_gold": best["is_gold"],
            "sources": [
                {"user_id": s["source_user_id"],
                 "user_name": s["source"].get("user_name", ""),
                 "quality_score": s["source"]["quality_score"]}
                for s in best["sources"]
            ],
            "reasons": best["reasons"],
            "margin": margin,
            "leverage": leverage,
            "tier": tier_name,
        })
        cfg.save_signal_log(signal_log)

        cfg.output({
            "success": True,
            "action": "ENTRY",
            "signal": {
                "asset": best["coin"],
                "direction": best["direction"],
                "score": best["score"],
                "tier": tier_name,
                "is_gold": best["is_gold"],
                "reasons": best["reasons"],
                "n_sources": len(best["sources"]),
                "source_names": [s["source"].get("user_name", s["source_user_id"]) for s in best["sources"]],
            },
            "execution": {
                "asset": best["coin"], "direction": best["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "pool_stats": {
                "active_pool": len(active_pool),
                "watchlist": len(pool.get("watchlist", {})),
            },
            "top_3_candidates": [
                {"coin": c["coin"], "direction": c["direction"], "score": c["score"],
                 "n_sources": len(c["sources"]), "is_gold": c["is_gold"]}
                for c in candidates[:3]
            ],
            "maintenance": pool_maintenance_actions,
            "_jackal_version": "1.1",
        })
    else:
        cfg.output({
            "success": True,
            "action": "ENTRY_FAILED",
            "signal": {
                "asset": best["coin"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"],
            },
            "error": result,
            "_jackal_version": "1.1",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"success": False, "error": str(e)})
