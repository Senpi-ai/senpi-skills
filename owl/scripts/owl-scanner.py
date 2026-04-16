#!/usr/bin/env python3
# Senpi OWL Scanner v6.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0 — attribution required for derivative works
# Source: https://github.com/Senpi-ai/senpi-skills
"""OWL v6.1 — Pure Contrarian (universe expansion).

One scanner. One thesis: the crowd is wrong.

v6.1 changes (2026-04-16 — per Owl's self-diagnosis):
  - REMOVED top-30 OI truncation in get_all_assets(). Owl was blinded
    to mid-cap extreme-funding setups ranked 30-60 in OI — exactly
    where the most violent liquidation unwinds happen. ZEC, MON, LIT
    hit >1000% annualized funding at probe time but were filtered out.
    $3M OI minimum remains as the sole liquidity gate.

v6.0 changes (fleet-fix: only 7 trades in 30 days → over-gated):
  - minPersistHours reduced 4 → 1 in owl-config.json. The 4-hour persistence
    requirement combined with the 14 minScore gate was effectively unreachable;
    7 entries in 30+ days proved it. Reducing to 1h lets Owl actually act on
    real crowding signals without waiting for them to persist through noise.
  - entry.minScore reduced 14 → 12 in owl-config.json. Previous 14 required
    near-perfect crowding + exhaustion confluence, which happened 2-3x/month
    at best. 12 still requires strong confluence but is achievable weekly.
  - runtime.yaml DSL Phase 2 first tier tightened 10% → 5% trigger (Lemon-
    pattern learning: capture the first leg of the unwind before the market
    reverses).
  - runtime.yaml dead_weight_cut 45 → 30 min (Lemon-pattern: faster loser
    cut, though still wider than Lemon's 20 min to preserve contrarian
    retrace tolerance).
  - recrowding_exit preserved — the 14-day audit found 0 fires in window
    and 2 lifetime fires net-neutral (+5.73% / -7.44%). Not harmful.

v5.3 changes (fixes 30-day zero-trade drought):
  - SELF-EXECUTING: scanner now calls create_position via mcporter directly,
    matching Wolverine/Phoenix pattern. Previously the scanner only output a
    signal JSON expecting an external action layer to execute — but Owl has
    no runtime.yaml action layer wired up, so signals died at output. This
    was the #1 reason Owl had traded zero times since 2026-03-15.
  - PERSISTENCE TOLERANCE: clear_persistence no longer fires on a single dip
    below minCrowdingScore. New belowThresholdCount allows up to 2 consecutive
    below-threshold scans (30 min at 15-min cadence) before clearing. Old
    pattern reset the 4-hour timer on any noise; no coin ever accumulated 4
    uninterrupted hours in 30 days.
  - Fleet-standard drawdown circuit breaker layered on top of existing
    dynamicSlots earning-forward logic. Preserves capital during drawdowns.
  - has_resting_orders() auto-cancels stale maker orders >10 min old
    (fleet-standard pattern from PR #177).
  - Added runtime.yaml for standard deployment.

v5.2 changes (preserved):
  - minFundingAnnualizedPct lowered from 20% to 12%. Below-floor assets score
    0 on funding but continue through SM/OI checks.
  - Observability logging: top 3 crowding scores + persistence timers to stderr.

Monitors crowding across top 30 assets (funding extremity, OI concentration,
SM tilt). When crowding persists 4+ hours AND exhaustion signals fire (volume
declining, price stalling, RSI divergence), enters AGAINST the crowd to ride
the liquidation unwind. 1-2 trades per day max.

Re-evaluates held positions every scan: if the crowd comes BACK (re-crowding),
exit immediately — the unwind thesis is dead.

Runs every 15 minutes.
"""

import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as cfg


# ═══════════════════════════════════════════════════════════════
# FLEET-STANDARD GUARDRAILS (v5.3)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Owl's starting capital baseline
STALE_ORDER_MAX_AGE_SEC = 600  # 10 min — matches fleet PR #177


def get_effective_entry_cap(account_value, tc, entry_cfg):
    """Fleet-standard drawdown circuit breaker layered on top of Owl's
    existing dynamicSlots earning-forward logic.

    Below -5% drawdown: hard clamps preserve capital.
    Above -5%: use Owl's existing dynamicSlots logic (unlock more entries as
    realized PnL grows) so the contrarian thesis can compound when winning.
    """
    starting = STARTING_BUDGET
    pnl_pct = ((account_value - starting) / starting) * 100 if starting > 0 else 0

    # Drawdown floor — hard clamps
    if pnl_pct < -25:
        return 0  # HARD STOP — circuit breaker
    if pnl_pct < -15:
        return 1  # Preserve — highest conviction only
    if pnl_pct < -5:
        return 2  # Defensive

    # Above -5%, use Owl's existing dynamicSlots logic
    dynamic = entry_cfg.get("dynamicSlots", {})
    if not dynamic.get("enabled", True):
        return entry_cfg.get("maxEntriesPerDay", 3)

    base_max = dynamic.get("baseMax", 2)
    day_pnl = tc.get("realizedPnl", 0)
    effective_max = base_max
    for t in dynamic.get("unlockThresholds", []):
        if day_pnl >= t.get("pnl", 999999):
            effective_max = t.get("maxEntries", effective_max)
    return min(effective_max, dynamic.get("absoluteMax", 4))


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC. Matches fleet-standard pattern from PR #177.

    Without auto-cancel, a maker FEE_OPTIMIZED_LIMIT order that never fills
    locks the scanner out of new entries indefinitely. Reduce-only orders
    (DSL exit legs) are ignored.
    """
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


def execute_entry(wallet, signal, account_value, entry_cfg, leverage_cfg):
    """Self-execute the entry via Senpi MCP create_position.

    v5.3: Scanner now calls create_position directly instead of emitting a
    signal JSON for an external action layer. Matches Wolverine/Phoenix
    self-executing pattern. Previously Owl relied on a main-session action
    layer that was never wired up, which is why signals died at output and
    Owl traded zero times between 2026-03-15 and 2026-04-14.
    """
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if signal["score"] >= 18:
        margin_pct = base_margin_pct * 1.5
    elif signal["score"] >= 16:
        margin_pct = base_margin_pct * 1.25
    else:
        margin_pct = base_margin_pct
    margin = round(account_value * margin_pct, 2)
    leverage = leverage_cfg.get("default", 8)

    order = {
        "coin": signal["coin"],
        "direction": signal["direction"],
        "leverage": leverage,
        "marginAmount": margin,
        "orderType": "FEE_OPTIMIZED_LIMIT",
        "feeOptimizedLimitOptions": {
            "ensureExecutionAsTaker": True,
            "executionTimeoutSeconds": 30,
        },
    }

    reason = (
        f"OWL v6.1 contrarian fade: score={signal['score']}, "
        f"crowd={signal['crowdDirection']}, "
        f"persist={signal['persistHours']:.1f}h"
    )
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[order],
        reason=reason,
    )

    success = bool(result and result.get("success"))
    return success, result, margin, leverage


# ─── Crowding Analysis ────────────────────────────────────────

def get_all_assets():
    """All assets with OI > $3M for crowding scan.

    v6.1 — UNIVERSE EXPANSION. Previously truncated to top 30 by OI,
    which blinded Owl to mid-cap extreme-funding setups. Owl's self-
    diagnosis (2026-04-16): "The most violent unwinds (extreme funding
    + heavy tilt) are on assets ranked 30-60 in OI. ZEC, MON, LIT hit
    >1000% annualized funding but get filtered out by the top-30 cap."
    The $3M OI minimum IS the real liquidity gate — the 30-cap was
    redundant and actively harmful.
    """
    data = cfg.mcporter_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        oi = float(inst.get("openInterest", 0))
        mark_px = float(inst.get("markPx", inst.get("midPx", 0)))
        funding = float(inst.get("funding", 0))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if coin and oi_usd > 3_000_000:
            assets.append({
                "coin": coin, "oi": oi, "oi_usd": oi_usd,
                "price": mark_px, "funding": funding,
            })
    assets.sort(key=lambda x: x["oi_usd"], reverse=True)
    return assets  # v6.1: no truncation


def get_sm_positioning(coin):
    """Get SM long/short split for an asset."""
    data = cfg.mcporter_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return 50, 0
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != coin:
            continue
        # API returns separate long/short entries per asset
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        trader_count = int(m.get("trader_count", m.get("traderCount", 0)))
        if direction == "long":
            return pct * 100, trader_count  # scale to 0-100 range
        elif direction == "short":
            return (1 - pct) * 100, trader_count  # invert for short
    return 50, 0


def score_crowding(asset, crowding_cfg):
    """Score how crowded an asset is. Higher = more one-sided. Returns (score, direction, details)."""
    funding = asset["funding"]
    funding_ann = abs(funding) * 8760
    sm_long_pct, sm_count = get_sm_positioning(asset["coin"])

    score = 0
    details = []
    crowd_direction = None  # the direction the CROWD is positioned

    # Funding extremity (biggest signal — funding IS the crowd's positioning)
    # v5.2: lowered minFundingAnnualizedPct from 20→12. Below-floor assets
    # now score 0 on funding but continue to SM/OI checks instead of early-returning.
    # The minCrowdingScore of 8 remains the real quality gate.
    min_funding = crowding_cfg.get("minFundingAnnualizedPct", 12)
    if funding_ann >= 60:
        score += 4
        details.append(f"funding_extreme_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= 40:
        score += 3
        details.append(f"funding_high_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    elif funding_ann >= min_funding:
        score += 2
        details.append(f"funding_elevated_{funding_ann:.0f}%ann")
        crowd_direction = "LONG" if funding > 0 else "SHORT"
    else:
        # v5.2: funding below floor — score 0 on this component but let SM/OI run.
        # Still infer crowd direction from funding sign for SM confirmation check.
        details.append(f"funding_below_floor_{funding_ann:.0f}%ann")
        if funding != 0:
            crowd_direction = "LONG" if funding > 0 else "SHORT"

    # SM concentration (top traders tilted one way)
    sm_tilt = abs(sm_long_pct - 50)
    if sm_tilt > 20:
        score += 3
        sm_dir = "LONG" if sm_long_pct > 50 else "SHORT"
        details.append(f"sm_tilted_{sm_dir}_{sm_long_pct:.0f}%")
        # SM should be tilted in SAME direction as funding (crowd is all-in)
        if (funding > 0 and sm_long_pct > 50) or (funding < 0 and sm_long_pct < 50):
            score += 1
            details.append("sm_confirms_funding")
    elif sm_tilt > 12:
        score += 1
        details.append(f"sm_leaning_{sm_long_pct:.0f}%")

    # OI concentration (high OI relative to volume = positions building, not churning)
    if asset["oi_usd"] > 20_000_000:
        score += 2
        details.append(f"oi_concentrated_{asset['oi_usd']/1e6:.0f}M")
    elif asset["oi_usd"] > 10_000_000:
        score += 1
        details.append(f"oi_moderate_{asset['oi_usd']/1e6:.0f}M")

    return score, crowd_direction, details


# ─── Exhaustion Detection ─────────────────────────────────────

def detect_exhaustion(coin, crowd_direction, exhaustion_cfg):
    """Check if the crowded trade is showing exhaustion signals."""
    data = cfg.mcporter_call("market_get_asset_data", asset=coin,
                              candle_intervals=["1h", "4h"],
                              include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return 0, []

    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    asset_ctx = data.get("data", {}).get("asset_context", data.get("data", {}))
    funding = float(asset_ctx.get("funding", data.get("data", {}).get("funding", 0)))

    if len(candles_1h) < 12 or len(candles_4h) < 6:
        return 0, []

    score = 0
    signals = []

    # SIGNAL 1: Funding declining from peak (crowd starting to unwind)
    # Compare current funding to 6h ago via candle-derived proxy
    if len(candles_1h) >= 8:
        # Volume declining while funding stays extreme = exhaustion building
        recent_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:]) / 3
        earlier_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-8:-3]) / 5
        if earlier_vol > 0 and recent_vol < earlier_vol * 0.7:
            score += 3
            signals.append(f"volume_declining_{recent_vol/earlier_vol:.0%}")

    # SIGNAL 2: Price stalling despite extreme positioning
    # If the crowd is all-in long but price stopped going up = exhaustion
    closes_4h = [float(c.get("close", c.get("c", 0))) for c in candles_4h[-4:]]
    if len(closes_4h) >= 4:
        price_change = ((closes_4h[-1] - closes_4h[-4]) / closes_4h[-4]) * 100 if closes_4h[-4] > 0 else 0
        if crowd_direction == "LONG" and price_change < 0.5:
            score += 3
            signals.append(f"price_stalled_crowd_long_{price_change:+.1f}%")
        elif crowd_direction == "SHORT" and price_change > -0.5:
            score += 3
            signals.append(f"price_stalled_crowd_short_{price_change:+.1f}%")

    # SIGNAL 3: Volume spike without price follow-through (capitulation wick)
    if len(candles_1h) >= 3:
        latest_vol = float(candles_1h[-1].get("volume", candles_1h[-1].get("v", candles_1h[-1].get("vlm", 0))))
        avg_vol = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-1]) / 5 if len(candles_1h) >= 6 else 1
        latest_close = float(candles_1h[-1].get("close", candles_1h[-1].get("c", 0)))
        prev_close = float(candles_1h[-2].get("close", candles_1h[-2].get("c", 0)))
        price_move = ((latest_close - prev_close) / prev_close * 100) if prev_close > 0 else 0

        if avg_vol > 0 and latest_vol > avg_vol * 2.0 and abs(price_move) < 1.0:
            score += 2
            signals.append(f"vol_spike_{latest_vol/avg_vol:.1f}x_no_follow_through")

    # SIGNAL 4: 4h RSI divergence (price flat/up but RSI declining = momentum dying)
    closes_4h_full = [float(c.get("close", c.get("c", 0))) for c in candles_4h]
    if len(closes_4h_full) >= 15:
        # Simple RSI
        gains, losses = [], []
        for i in range(1, len(closes_4h_full)):
            d = closes_4h_full[i] - closes_4h_full[i - 1]
            gains.append(max(0, d))
            losses.append(max(0, -d))
        period = 14
        if len(gains) >= period:
            avg_g = sum(gains[-period:]) / period
            avg_l = sum(losses[-period:]) / period
            rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100

            if crowd_direction == "LONG" and rsi < 55:
                score += 2
                signals.append(f"rsi_divergence_crowd_long_rsi_{rsi:.0f}")
            elif crowd_direction == "SHORT" and rsi > 45:
                score += 2
                signals.append(f"rsi_divergence_crowd_short_rsi_{rsi:.0f}")

    return score, signals


# ─── Persistence Tracking ─────────────────────────────────────

def check_persistence(state, coin, crowd_score, min_persist_hours):
    """Track how long crowding has been elevated. Returns (persisted, hours).

    v5.3: Resets belowThresholdCount to 0 when score is back above threshold
    so the tolerance window only applies to consecutive dips.
    """
    tracking = state.get("crowdingHistory", {})
    now_ts = cfg.now_ts()

    if coin not in tracking:
        tracking[coin] = {
            "firstSeen": cfg.now_iso(),
            "ts": now_ts,
            "peakScore": crowd_score,
            "belowThresholdCount": 0,
        }
        state["crowdingHistory"] = tracking
        return False, 0

    entry = tracking[coin]
    hours = (now_ts - entry.get("ts", now_ts)) / 3600

    if crowd_score > entry.get("peakScore", 0):
        entry["peakScore"] = crowd_score
    # v5.3: reset tolerance counter when back above threshold
    entry["belowThresholdCount"] = 0

    state["crowdingHistory"] = tracking
    return hours >= min_persist_hours, hours


def mark_below_threshold(state, coin, max_tolerance=2):
    """Mark a coin as temporarily below threshold. Returns True if persistence
    should be cleared (exceeded tolerance), False to keep tracking.

    v5.3: The old pattern cleared persistence instantly on any dip below
    minCrowdingScore. At 15-min cadence, a single 15-min noisy dip would
    reset the 4-hour timer, and Owl never accumulated persistence across a
    full 4h window in 30 days. New pattern tolerates up to max_tolerance
    consecutive below-threshold scans (30 min at 15-min cadence) before
    clearing. This is the #1 reason Owl traded zero times since 2026-03-15.
    """
    tracking = state.get("crowdingHistory", {})
    if coin not in tracking:
        return True  # nothing to track, safe to "clear" (no-op)
    entry = tracking[coin]
    below_count = entry.get("belowThresholdCount", 0) + 1
    entry["belowThresholdCount"] = below_count
    state["crowdingHistory"] = tracking
    return below_count > max_tolerance


def clear_persistence(state, coin):
    """Clear tracking when crowding score drops below threshold for longer
    than the tolerance window (see mark_below_threshold)."""
    tracking = state.get("crowdingHistory", {})
    tracking.pop(coin, None)
    state["crowdingHistory"] = tracking


# ─── Re-Crowding Detection (for held positions) ──────────────

def check_recrowding(coin, our_direction, crowding_cfg):
    """If we're in a contrarian trade and the crowd comes BACK, thesis is dead."""
    assets = get_all_assets()
    asset = next((a for a in assets if a["coin"] == coin), None)
    if not asset:
        return False, []

    crowd_score, crowd_direction, details = score_crowding(asset, crowding_cfg)

    # Re-crowding: crowd is rebuilding in the SAME direction we're betting against
    # Our direction is opposite to the original crowd. If crowd rebuilds = our thesis is dead.
    original_crowd_dir = "LONG" if our_direction == "SHORT" else "SHORT"

    if crowd_direction == original_crowd_dir and crowd_score >= crowding_cfg.get("reCrowdingMinScore", 6):
        return True, [f"re_crowding_{crowd_direction}_{crowd_score}pts"] + details

    return False, []


# ─── Observability (v5.2) ────────────────────────────────────

def _log_observability(all_scores, state):
    """Log top 3 crowding scores and active persistence timers to stderr.
    This goes to the agent's internal log, NOT to notifications/Telegram.
    Lets us answer 'is OWL seeing anything?' without config changes."""
    import sys as _sys

    top3 = sorted(all_scores, key=lambda x: x["score"], reverse=True)[:3]
    lines = ["OWL SCAN — top crowding:"]
    for s in top3:
        lines.append(
            f"  {s['coin']}: score={s['score']} dir={s['direction']} "
            f"funding={s['funding_ann']:.0f}%ann {' '.join(s['details'][:3])}"
        )

    tracking = state.get("crowdingHistory", {})
    if tracking:
        now_ts = cfg.now_ts()
        lines.append("  persistence timers:")
        for coin, entry in tracking.items():
            hours = (now_ts - entry.get("ts", now_ts)) / 3600
            lines.append(f"    {coin}: {hours:.1f}h (peak={entry.get('peakScore', '?')})")

    print("\n".join(lines), file=_sys.stderr)


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
    crowding_cfg = config.get("crowding", {})
    exhaustion_cfg = config.get("exhaustion", {})
    active_coins = {p["coin"]: p for p in positions}
    state = cfg.load_state("owl-state.json")

    # ── CHECK 1: Re-crowding detection for held positions ─────
    for pos in positions:
        is_recrowding, reasons = check_recrowding(
            pos["coin"], pos["direction"], crowding_cfg
        )
        if is_recrowding:
            cfg.save_state(state, "owl-state.json")
            cfg.output({
                "success": True,
                "action": "recrowding_exit",
                "exits": [{
                    "coin": pos["coin"],
                    "direction": pos["direction"],
                    "reasons": reasons,
                    "upnl": pos.get("upnl", 0),
                }],
                "note": "crowd is back — unwind thesis dead, exit immediately",
            })
            return

    # ── CHECK 2: Entry cap (v5.3: fleet-standard drawdown circuit breaker) ──
    max_positions = config.get("maxPositions", 2)
    max_entries = get_effective_entry_cap(account_value, tc, entry_cfg)

    if max_entries <= 0:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "success": True, "heartbeat": "NO_REPLY",
            "note": f"HARD_STOP pnl={pnl_pct:+.1f}% — circuit breaker",
        })
        cfg.save_state(state, "owl-state.json")
        return

    if tc.get("entries", 0) >= max_entries:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": f"max entries ({max_entries})"})
        cfg.save_state(state, "owl-state.json")
        return

    if len(positions) >= max_positions:
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "max positions"})
        cfg.save_state(state, "owl-state.json")
        return

    # ── CHECK 2.5: Don't stack new entries while a maker order is still resting ──
    if has_resting_orders(wallet):
        cfg.output({"success": True, "heartbeat": "NO_REPLY", "note": "resting order pending"})
        cfg.save_state(state, "owl-state.json")
        return

    # ── CHECK 3: Scan for crowded assets ──────────────────────
    assets = get_all_assets()
    min_crowd_score = crowding_cfg.get("minCrowdingScore", 8)
    min_exhaust_score = exhaustion_cfg.get("minExhaustionScore", 5)
    min_persist_hours = crowding_cfg.get("minPersistHours", 4)
    min_total_score = entry_cfg.get("minScore", 14)

    candidates = []
    all_scores = []  # v5.2: observability — track ALL crowding scores

    for asset in assets:
        # Phase 1: Score crowding (score everything for observability)
        crowd_score, crowd_direction, crowd_details = score_crowding(asset, crowding_cfg)
        all_scores.append({
            "coin": asset["coin"], "score": crowd_score,
            "direction": crowd_direction, "details": crowd_details,
            "funding_ann": abs(asset["funding"]) * 8760,
        })

        if asset["coin"] in active_coins:
            continue

        if crowd_score < min_crowd_score or not crowd_direction:
            # v5.3: tolerate brief dips (up to 2 consecutive scans = 30 min
            # at 15-min cadence) before clearing the persistence timer
            if mark_below_threshold(state, asset["coin"], max_tolerance=2):
                clear_persistence(state, asset["coin"])
            continue

        # Phase 2: Check persistence (must be crowded for 4h+)
        persisted, hours = check_persistence(state, asset["coin"], crowd_score, min_persist_hours)
        if not persisted:
            continue

        # Phase 3: Detect exhaustion
        exhaust_score, exhaust_signals = detect_exhaustion(
            asset["coin"], crowd_direction, exhaustion_cfg
        )
        if exhaust_score < min_exhaust_score:
            continue

        # Total score = crowding + exhaustion
        total_score = crowd_score + exhaust_score

        if total_score < min_total_score:
            continue

        # Entry direction: OPPOSITE to the crowd
        entry_direction = "SHORT" if crowd_direction == "LONG" else "LONG"

        candidates.append({
            "coin": asset["coin"],
            "direction": entry_direction,
            "score": total_score,
            "crowdScore": crowd_score,
            "exhaustScore": exhaust_score,
            "crowdDirection": crowd_direction,
            "persistHours": hours,
            "reasons": crowd_details + exhaust_signals + [f"crowded_{hours:.1f}h"],
            "price": asset["price"],
            "funding": asset["funding"],
        })

    # ── v5.2 OBSERVABILITY: log top 3 crowding scores + persistence timers ──
    _log_observability(all_scores, state)

    cfg.save_state(state, "owl-state.json")

    if not candidates:
        cfg.output({"success": True, "heartbeat": "NO_REPLY",
                     "note": f"scanned {len(assets)}, no exhausted crowding"})
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    # v5.3: SELF-EXECUTE the entry (don't just emit a signal and hope an
    # external action layer picks it up — Owl has no action layer wired up)
    success, result, margin, leverage = execute_entry(
        wallet, best, account_value, entry_cfg, config.get("leverage", {})
    )

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        cfg.save_trade_counter(tc)
        cfg.output({
            "success": True,
            "action": "ENTRY",
            "signal": best,
            "execution": {
                "coin": best["coin"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "result": result,
            "scanned": len(assets),
            "candidates": len(candidates),
            "_owl_version": "5.3",
        })
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        cfg.output({
            "success": False,
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": error,
            "scanned": len(assets),
            "candidates": len(candidates),
            "_owl_version": "5.3",
        })


if __name__ == "__main__":
    run()
