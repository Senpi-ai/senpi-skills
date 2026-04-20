#!/usr/bin/env python3
"""DIRE v1.0 — BRENTOIL XYZ Specialist Scanner.

Single-asset specialist on xyz:BRENTOIL. News-driven momentum breakouts on oil
with tight DSL protection against sharp geopolitical reversals.

Architecture: Kodiak-family port with XYZ-specific adaptations.
Execution: Wolverine pattern (Python → mcporter CLI → MCP, no LLM parse loop).

Scanner is authoritative. Scanner does not exit positions — DSL owns all exits.
When a BRENTOIL position is open, scanner emits NO_REPLY.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT

import math
import sys
import time
import traceback
from datetime import datetime, timezone

# Local imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from dire_config import (
    ASSET,
    LEVERAGE_TYPE,
    load_config,
    get_wallet_and_strategy,
    load_trade_counter,
    save_trade_counter,
    increment_entry,
    update_peak_7d,
    check_drawdown_gate,
    check_daily_cap,
    get_positions,
    get_asset_data,
    create_position,
    attach_ratchet_stop,
    close_position_market,
    validate_entry_response,
    validate_dsl_response,
    output,
    no_reply,
    now_ts,
    now_iso,
    load_state,
    save_state,
)

VERSION = "1.1"


# ─── Momentum & Signal Evaluation ────────────────────────────

def safe_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def candle_close(c):
    """Extract close price from a candle dict, handling multiple field names."""
    return safe_float(c.get("close", c.get("c", 0)))


def candle_open(c):
    return safe_float(c.get("open", c.get("o", 0)))


def candle_high(c):
    return safe_float(c.get("high", c.get("h", 0)))


def candle_low(c):
    return safe_float(c.get("low", c.get("l", 0)))


def candle_volume(c):
    return safe_float(c.get("volume", c.get("v", c.get("vlm", 0))))


def trend_direction(candles, n=5):
    """Determine trend direction from last n candles.

    Returns "BULLISH", "BEARISH", or "FLAT", and the % change over the window.
    """
    if not candles or len(candles) < n:
        return "FLAT", 0.0
    recent = candles[-n:]
    first = candle_close(recent[0])
    last = candle_close(recent[-1])
    if first <= 0:
        return "FLAT", 0.0
    pct = (last - first) / first * 100
    if pct > 0.15:
        return "BULLISH", pct
    if pct < -0.15:
        return "BEARISH", pct
    return "FLAT", pct


def check_4tf_alignment(candles_by_tf):
    """4TF alignment hard gate.

    Returns (direction, aligned, trend_by_tf, details).
    aligned=True only if 5m, 15m, 1h, 4h all agree on direction.
    """
    required_tfs = ["5m", "15m", "1h", "4h"]
    trends = {}
    for tf in required_tfs:
        candles = candles_by_tf.get(tf, [])
        n = 6 if tf in ("5m", "15m") else 4
        trend, pct = trend_direction(candles, n=n)
        trends[tf] = {"trend": trend, "pct": pct}
    directions = {trends[tf]["trend"] for tf in required_tfs}
    if "BULLISH" in directions and "BEARISH" not in directions and "FLAT" not in directions:
        return "LONG", True, trends, "all_bullish"
    if "BEARISH" in directions and "BULLISH" not in directions and "FLAT" not in directions:
        return "SHORT", True, trends, "all_bearish"
    return None, False, trends, f"mixed:{directions}"


def extract_oi_velocity_1h(asset_data):
    """Flat-path extraction of oi_velocity.oi_change_pct_1h.

    NOTE: Do not use nested oi_velocity["1h"]["change_pct"] — that path does
    not exist in the MCP response. Silent-None bug documented in
    reference_cobra_antipattern.md.
    """
    oi_vel = asset_data.get("oi_velocity")
    if not isinstance(oi_vel, dict):
        return None
    val = oi_vel.get("oi_change_pct_1h")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_oi_velocity(oi_vel_change):
    """OI velocity scoring.

    > +5%  → +2 (accelerating)
    > +2%  → +1 (rising)
    < -3%  → -1 (draining)
    null   → pass (0)
    """
    if oi_vel_change is None:
        return 0, None
    if oi_vel_change > 5:
        return 2, f"OI_ACCELERATING_{oi_vel_change:+.1f}%"
    if oi_vel_change > 2:
        return 1, f"OI_rising_{oi_vel_change:+.1f}%"
    if oi_vel_change < -3:
        return -1, f"OI_draining_{oi_vel_change:+.1f}%"
    return 0, None


def volume_spike_score(candles_15m, candles_1h, threshold=2.5, strong_threshold=5.0):
    """News-impact proxy: is the latest 15m volume > threshold × 1h average?

    Tiered scoring:
      > strong_threshold (5x default) → +2 (extreme news spike, "something big happened")
      > threshold (2.5x default)      → +1 (moderate news activity)
      otherwise                        → 0

    Returns (score, reason).
    """
    if not candles_15m or not candles_1h:
        return 0, None
    last_15m_vol = candle_volume(candles_15m[-1])
    # 1h average from the last several 1h candles
    recent_1h = candles_1h[-4:] if len(candles_1h) >= 4 else candles_1h
    vols = [candle_volume(c) for c in recent_1h if candle_volume(c) > 0]
    if not vols:
        return 0, None
    # Average per-15m-equivalent volume = (avg 1h vol) / 4
    avg_15m_equiv = (sum(vols) / len(vols)) / 4
    if avg_15m_equiv <= 0:
        return 0, None
    ratio = last_15m_vol / avg_15m_equiv
    if ratio > strong_threshold:
        return 2, f"VOL_EXTREME_{ratio:.1f}x"
    if ratio > threshold:
        return 1, f"VOL_SPIKE_{ratio:.1f}x"
    return 0, None


def sm_conviction_score(premium_pct_abs, moderate_threshold=0.001, strong_threshold=0.003):
    """Score Smart Money conviction strength based on absolute premium magnitude.

    Premium is (markPx - oraclePx) / oraclePx — positive means longs aggressive,
    negative means shorts aggressive. Absolute magnitude measures conviction.

    Tiered scoring:
      |premium| > strong_threshold (0.3% default)   → +2 (extreme tilt)
      |premium| > moderate_threshold (0.1% default) → +1 (meaningful tilt)
      otherwise                                      → 0 (direction detected but weak)

    Returns (score, reason).
    """
    if premium_pct_abs is None:
        return 0, None
    if premium_pct_abs > strong_threshold:
        return 2, f"SM_EXTREME_{premium_pct_abs * 100:.3f}%"
    if premium_pct_abs > moderate_threshold:
        return 1, f"SM_STRONG_{premium_pct_abs * 100:.3f}%"
    return 0, None


def price_cleanliness_score(candles_5m, direction, max_wick_pct=1.5, lookback_minutes=30):
    """Check last 30 min of 5m candles for adverse wicks > 1.5%.

    Returns (score, reason).
    A "clean" approach (no significant wicks against direction) scores +1.
    """
    if not candles_5m or not direction:
        return 0, None
    n_candles = max(6, lookback_minutes // 5)  # 30 min = 6 × 5m candles
    recent = candles_5m[-n_candles:]
    if not recent:
        return 0, None
    for c in recent:
        o = candle_open(c)
        h = candle_high(c)
        l = candle_low(c)
        cl = candle_close(c)
        if o <= 0:
            continue
        if direction == "LONG":
            # Wick against direction = downward wick = (min(open, close) - low) / open
            wick = (min(o, cl) - l) / o * 100
        else:  # SHORT
            wick = (h - max(o, cl)) / o * 100
        if wick > max_wick_pct:
            return 0, f"DIRTY_wick_{wick:.2f}%"
    return 1, "CLEAN_PX"


# ─── SM Direction (Smart Money) ──────────────────────────────

def get_sm_direction(asset_data):
    """Derive Smart Money direction from market data.

    For XYZ assets we use a proxy: markPx vs oraclePx premium from asset_context.
    Positive premium → mark > oracle → longs aggressive → SM LONG.
    Negative premium → mark < oracle → shorts aggressive → SM SHORT.

    Returns (direction, premium_abs, reason).
    - direction: "LONG", "SHORT", or None (ambiguous → HARD BLOCK)
    - premium_abs: absolute premium as a decimal (e.g. 0.002 = 0.2%). Used
      downstream by sm_conviction_score() to add conviction points.
    - reason: human-readable detail string.

    Direction is detected at any premium magnitude > 0.0005 (0.05%) in either
    direction. Below that, SM is treated as ambiguous.
    """
    ctx = asset_data.get("asset_context") or {}
    try:
        premium = float(ctx.get("premium", 0) or 0)
        mark_px = float(ctx.get("markPx", 0) or 0)
    except (TypeError, ValueError):
        return None, 0.0, "parse_error"

    if mark_px <= 0:
        return None, 0.0, "no_mark_px"

    # Premium arrives as a decimal (e.g. 0.00293 = 0.293%). Keep as decimal
    # internally; present as % in reason strings.
    if abs(premium) < 0.0005:
        return None, abs(premium), f"sm_ambiguous_premium_{premium * 100:+.3f}%"

    direction = "LONG" if premium > 0 else "SHORT"
    return direction, abs(premium), f"premium_{premium * 100:+.3f}%"


# ─── Scoring ─────────────────────────────────────────────────

def evaluate_setup(asset_data, config):
    """Full scoring pipeline for a BRENTOIL entry setup.

    Returns dict with keys: direction, score, reasons, blocked, block_reason,
    trends, sm, oi_vel, volume_ratio, mark_px.
    """
    reasons = []
    candles = asset_data.get("candles") or {}
    candles_by_tf = {
        "5m": candles.get("5m", []) or [],
        "15m": candles.get("15m", []) or [],
        "1h": candles.get("1h", []) or [],
        "4h": candles.get("4h", []) or [],
    }

    # Gate 1: 4TF alignment
    direction, aligned, trends, align_detail = check_4tf_alignment(candles_by_tf)
    if not aligned:
        return {
            "direction": None,
            "score": 0,
            "reasons": [],
            "blocked": True,
            "block_reason": f"4TF_MISALIGNED:{align_detail}",
            "trends": trends,
        }
    reasons.append(f"4TF_aligned_{direction}_{align_detail}")

    # Gate 2: SM HARD BLOCK
    sm_dir, sm_premium_abs, sm_detail = get_sm_direction(asset_data)
    if sm_dir is None:
        return {
            "direction": direction,
            "score": 0,
            "reasons": reasons,
            "blocked": True,
            "block_reason": f"SM_HARD_BLOCK:{sm_detail}",
            "trends": trends,
        }
    if sm_dir != direction:
        return {
            "direction": direction,
            "score": 0,
            "reasons": reasons,
            "blocked": True,
            "block_reason": f"SM_CONTRADICTS:setup={direction}_sm={sm_dir}",
            "trends": trends,
            "sm": sm_detail,
        }
    reasons.append(f"SM_aligned_{sm_dir}_{sm_detail}")

    # Base score from 4TF + SM alignment (both hard gates passed)
    score = 6  # baseline for any aligned setup

    # Gate 3: SM conviction strength (soft score, tiered)
    sm_mod = float(config.get("smPremiumModerateAbsPct", 0.001))
    sm_str = float(config.get("smPremiumStrongAbsPct", 0.003))
    sm_score, sm_reason = sm_conviction_score(sm_premium_abs, moderate_threshold=sm_mod, strong_threshold=sm_str)
    score += sm_score
    if sm_reason:
        reasons.append(sm_reason)

    # Gate 4: OI velocity (soft score)
    oi_vel = extract_oi_velocity_1h(asset_data)
    oi_score, oi_reason = score_oi_velocity(oi_vel)
    score += oi_score
    if oi_reason:
        reasons.append(oi_reason)

    # Gate 5: Volume spike (soft score, tiered)
    vol_threshold = float(config.get("volumeSpikeThreshold", 2.5))
    vol_strong = float(config.get("volumeSpikeStrongThreshold", 5.0))
    vol_score, vol_reason = volume_spike_score(
        candles_by_tf["15m"], candles_by_tf["1h"],
        threshold=vol_threshold, strong_threshold=vol_strong,
    )
    score += vol_score
    if vol_reason:
        reasons.append(vol_reason)

    # Gate 6: Price cleanliness (soft score)
    clean_score, clean_reason = price_cleanliness_score(
        candles_by_tf["5m"],
        direction,
        max_wick_pct=float(config.get("priceCleanlinessMaxWickPct", 1.5)),
        lookback_minutes=int(config.get("priceCleanlinessLookbackMinutes", 30)),
    )
    score += clean_score
    if clean_reason:
        reasons.append(clean_reason)

    # Mark price for execution
    mark_px = safe_float((asset_data.get("asset_context") or {}).get("markPx"))

    return {
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "blocked": False,
        "block_reason": None,
        "trends": trends,
        "sm": sm_detail,
        "sm_premium_abs": sm_premium_abs,
        "oi_vel": oi_vel,
        "mark_px": mark_px,
    }


# ─── Sizing (conviction-scaled) ──────────────────────────────

# v1.1: leverage and margin scale with score. Higher conviction = bigger
# position. This is the fix to the "tight DSL + capped winners = fee churn
# death spiral" failure mode. Cobra lesson applied: let winners run big when
# conviction is there, stay small on weak setups.
#
# Default tiers (overridable via config.sizingTiers):
#   score 9  → 3x  × 20% = 0.6x  notional  (cautious — just cleared MIN)
#   score 10 → 5x  × 25% = 1.25x notional  (standard — fleet baseline)
#   score 11 → 7x  × 30% = 2.1x  notional  (conviction — above standard)
#   score 12+ → 10x × 30% = 3.0x notional  (apex — maximum deployed)
#
# Hyperliquid's BRENTOIL max leverage is 20x. Our 10x cap is 50% of that;
# leaves safety margin for tail events. ISOLATED margin means each trade's
# risk is bounded by its own margin allocation.

_DEFAULT_SIZING_TIERS = [
    {"minScore": 9,  "leverage": 3,  "marginPct": 0.20, "label": "cautious"},
    {"minScore": 10, "leverage": 5,  "marginPct": 0.25, "label": "standard"},
    {"minScore": 11, "leverage": 7,  "marginPct": 0.30, "label": "conviction"},
    {"minScore": 12, "leverage": 10, "marginPct": 0.30, "label": "apex"},
]


def resolve_sizing_tier(score, config):
    """Resolve the highest-applicable sizing tier for a given score.

    Returns dict with keys: leverage, marginPct, label, minScore.
    Returns None if score doesn't meet any tier's minScore.
    """
    tiers = config.get("sizingTiers") or _DEFAULT_SIZING_TIERS
    applicable = [t for t in tiers if score >= int(t.get("minScore", 0))]
    if not applicable:
        return None
    # Pick the tier with the highest minScore that the score clears
    return max(applicable, key=lambda t: int(t.get("minScore", 0)))


def compute_leverage(score, config):
    """Return the leverage for this score, hard-capped at maxLeverage."""
    max_lev = int(config.get("maxLeverage", 10))
    tier = resolve_sizing_tier(score, config)
    if not tier:
        return 0
    lev = int(tier.get("leverage", 3))
    return min(lev, max_lev)


def compute_margin(account_value, score, config):
    """Compute margin allocation based on the resolved sizing tier for this score.

    Returns (margin_usd, tier_label).
    """
    tier = resolve_sizing_tier(score, config)
    if not tier:
        return 0.0, "none"
    pct = float(tier.get("marginPct", 0.20))
    return round(account_value * pct, 2), str(tier.get("label", ""))


# ─── Main Scanner Loop ───────────────────────────────────────

def run_scan():
    """Single scan cycle. Emits one JSON line to stdout."""
    scan_start = now_ts()
    config = load_config()
    wallet, strategy_id = get_wallet_and_strategy()

    if not wallet:
        output({
            "status": "error",
            "heartbeat": "NO_REPLY",
            "note": "wallet_not_configured",
            "version": VERSION,
        })
        return

    # Load trade counter + update peak
    tc = load_trade_counter()

    # Fetch positions + account value
    account_value, positions = get_positions(wallet)
    if account_value <= 0:
        output({
            "status": "error",
            "heartbeat": "NO_REPLY",
            "note": f"account_value_zero_or_unreadable account={account_value}",
            "version": VERSION,
        })
        return

    update_peak_7d(tc, account_value)

    # RULE 2: scanner does not exit positions. If position exists, NO_REPLY.
    if positions:
        p = positions[0]
        no_reply(
            f"RIDING: position_open coin={p['coin']} direction={p['direction']} "
            f"upnl=${p['upnl']:.2f} roe={p['roe']:.2%} margin=${p['margin']:.2f}"
        )
        return

    # HUNTING mode — check gates
    # Gate: drawdown circuit breaker
    gate_open, dd_reason = check_drawdown_gate(tc, account_value, config)
    if not gate_open:
        output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"HUNTING_PAUSED: {dd_reason}",
            "account_value": account_value,
            "peak_7d": tc.get("peak_7d"),
            "version": VERSION,
        })
        return

    # Gate: daily entry cap
    cap_ok, cap_reason = check_daily_cap(tc, config)
    if not cap_ok:
        output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"DAILY_CAP_REACHED: {cap_reason}",
            "version": VERSION,
        })
        return

    # Fetch BRENTOIL market data
    asset_data_raw = get_asset_data()
    if not asset_data_raw:
        output({
            "status": "error",
            "heartbeat": "NO_REPLY",
            "note": "market_get_asset_data_failed",
            "version": VERSION,
        })
        return

    # Normalize response shape — mcporter may wrap in data.{...}
    asset_data = asset_data_raw.get("data", asset_data_raw) if isinstance(asset_data_raw, dict) else {}

    # Evaluate setup
    setup = evaluate_setup(asset_data, config)
    if setup["blocked"]:
        output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"HUNTING: gate_blocked {setup['block_reason']}",
            "trends": setup.get("trends"),
            "version": VERSION,
        })
        return

    # Score check
    min_score = int(config.get("minScore", 9))
    if setup["score"] < min_score:
        output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"HUNTING: score_low {setup['score']}/{min_score}",
            "direction": setup["direction"],
            "reasons": setup["reasons"],
            "version": VERSION,
        })
        return

    # ── EXECUTION (Wolverine pattern) ──
    direction = setup["direction"]
    score = setup["score"]
    mark_px = setup["mark_px"]

    # v1.1: conviction-scaled sizing. Higher score → more leverage AND more margin.
    # Lets apex setups (score 12+) deploy up to 3x account notional to capture
    # big winners that pay for fee drag + small losers.
    leverage = compute_leverage(score, config)
    margin, sizing_label = compute_margin(account_value, score, config)
    if leverage <= 0 or margin < 10:
        output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"SIZING_TIER_UNRESOLVED_OR_TOO_SMALL: lev={leverage} margin=${margin:.2f} score={score}",
            "version": VERSION,
        })
        return

    # Fire create_position
    entry_response = create_position(
        wallet=wallet,
        direction=direction,
        leverage=leverage,
        margin_amount=margin,
    )
    filled, filled_size, fill_price, order_id, entry_reason = validate_entry_response(entry_response)

    if not filled:
        # Entry failed — log and return without incrementing counter
        output({
            "status": "error",
            "action": "ENTRY_FAILED",
            "direction": direction,
            "score": score,
            "reasons": setup["reasons"],
            "entry_reason": entry_reason,
            "margin": margin,
            "leverage": leverage,
            "version": VERSION,
        })
        return

    # Entry succeeded — increment trade counter immediately
    increment_entry(tc)

    # Now attach DSL — mandatory inline
    dsl_tiers = config.get("dslTiers", [
        {"triggerRoe": 5, "lockRoe": 25},
        {"triggerRoe": 10, "lockRoe": 50},
        {"triggerRoe": 20, "lockRoe": 70},
        {"triggerRoe": 35, "lockRoe": 80},
        {"triggerRoe": 50, "lockRoe": 90},
    ])

    entry_px_for_dsl = fill_price if fill_price > 0 else mark_px
    dsl_response = attach_ratchet_stop(
        wallet=wallet,
        strategy_id=strategy_id,
        direction=direction,
        entry_price=entry_px_for_dsl,
        size=filled_size,
        leverage=leverage,
        tiers=dsl_tiers,
    )
    dsl_ok, dsl_reason = validate_dsl_response(dsl_response)

    if not dsl_ok:
        # CRITICAL: DSL failed to attach. Close the position immediately to
        # prevent unprotected exposure.
        close_response = close_position_market(wallet, slippage_pct=2.0)
        output({
            "status": "critical",
            "action": "DSL_ATTACH_FAILED_EMERGENCY_CLOSE",
            "direction": direction,
            "fill_size": filled_size,
            "fill_price": fill_price,
            "order_id": order_id,
            "dsl_reason": dsl_reason,
            "close_triggered": close_response is not None,
            "version": VERSION,
        })
        return

    # SUCCESS — full entry + DSL
    output({
        "status": "ok",
        "action": "ENTRY",
        "direction": direction,
        "coin": ASSET,
        "score": score,
        "reasons": setup["reasons"],
        "execution": {
            "asset": ASSET,
            "direction": direction,
            "leverage": leverage,
            "margin": margin,
            "sizing_tier": sizing_label,
            "notional_vs_account": round((leverage * margin) / account_value, 2) if account_value > 0 else None,
            "fill_size": filled_size,
            "fill_price": fill_price,
            "order_id": order_id,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "ensureExecutionAsTaker": True,
            "leverageType": LEVERAGE_TYPE,
        },
        "dsl": {
            "attached": True,
            "tiers": dsl_tiers,
            "entry_price": entry_px_for_dsl,
        },
        "account": {
            "value": account_value,
            "peak_7d": tc.get("peak_7d"),
            "entries_today": tc.get("entries"),
            "daily_cap": config.get("dailyEntryCap"),
        },
        "duration_ms": int((now_ts() - scan_start) * 1000),
        "version": VERSION,
        "timestamp": now_iso(),
    })


def main():
    try:
        run_scan()
    except Exception as e:
        output({
            "status": "error",
            "heartbeat": "NO_REPLY",
            "note": f"scanner_exception: {type(e).__name__}: {str(e)[:200]}",
            "traceback": traceback.format_exc()[-500:],
            "version": VERSION,
        })


if __name__ == "__main__":
    main()
