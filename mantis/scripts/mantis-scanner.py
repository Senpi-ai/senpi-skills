#!/usr/bin/env python3
# Senpi MANTIS Scanner v5.0 — Slipstream
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""MANTIS v5.0 — Slipstream.

Cross-asset catchup hunter. Strikes correlated alts that haven't yet
responded to a leader's move, before the catchup completes. Built
around the new market_get_cross_asset_flows MCP tool.

Decision flow per scan tick (every 60s):

  1. Reconcile open positions vs. tracked metadata
  2. Leader-reversal veto check on each open position
     - If leader reversed >LEADER_REVERSAL_VETO_PCT from entry, close immediately
  3. Concurrency check (max 2 open Mantis positions)
  4. Daily cap check (max 6 entries per UTC day)
  5. For each leader in LEADER_UNIVERSE:
       call market_get_cross_asset_flows(leader_asset=leader)
       collect laggards passing all entry filters
  6. Sort candidates by confidence desc
  7. Pick top candidate that is NOT in cooldown
  8. Determine sizing tier from confidence score
  9. Determine direction by following leader's move sign
  10. Compute dynamic hard_timeout = avg_lag_minutes × 1.5 (clamped)
  11. Open position with create_position + DSL preset + metadata
  12. Mark cooldown + log STRIKE event

The thesis is statistical: laggards historically catch up within their
typical lag window with their historical follow_rate. Mantis takes the
quantified bet AND respects the hard time window.
"""

import sys
import os
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mantis_config as cfg
import mantis_state as state


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def passes_entry_filters(laggard: Dict[str, Any]) -> bool:
    """All filters must pass."""
    follow_rate = safe_float(laggard.get("follow_rate"))
    if follow_rate < cfg.MIN_FOLLOW_RATE:
        return False

    confidence = safe_float(laggard.get("confidence"))
    if confidence < cfg.MIN_CONFIDENCE:
        return False

    gap_pct = safe_float(laggard.get("gap_pct"))
    if abs(gap_pct) < cfg.MIN_GAP_PCT:
        return False

    if cfg.REQUIRE_SM_ROTATION and not laggard.get("sm_starting_to_rotate"):
        return False

    lag_stddev = safe_float(laggard.get("lag_stddev_minutes"))
    if lag_stddev <= 0 or lag_stddev > cfg.MAX_LAG_STDDEV_MINUTES:
        return False

    return True


def sizing_tier_for(confidence: float) -> Dict[str, Any]:
    """Pick highest tier the confidence qualifies for."""
    for tier in cfg.SIZING_TIERS:
        if confidence >= tier["confidence_min"]:
            return tier
    return cfg.SIZING_TIERS[-1]


def direction_from_leader_move(leader_move_pct: float) -> str:
    """Follow the leader: positive move → LONG the laggard, negative → SHORT."""
    return "LONG" if leader_move_pct >= 0 else "SHORT"


def compute_hard_timeout(avg_lag_minutes: float) -> int:
    """Dynamic hard_timeout = avg_lag_minutes × multiplier, clamped."""
    minutes = max(1.0, safe_float(avg_lag_minutes, 60.0)) * cfg.HARD_TIMEOUT_LAG_MULTIPLIER
    minutes = max(cfg.HARD_TIMEOUT_FLOOR_MINUTES, min(cfg.HARD_TIMEOUT_CEILING_MINUTES, minutes))
    return int(minutes)


def leader_reversed(leader_pct_at_entry: float, current_leader_move_pct: float) -> bool:
    """True if leader has reversed by more than LEADER_REVERSAL_VETO_PCT
    from its move at entry time. Direction matters: if entry was on a
    +3% leader move and leader is now at +1.8%, that's a 1.2% reversal
    against the thesis → veto."""
    delta = current_leader_move_pct - leader_pct_at_entry
    # Reversal direction is opposite to the entry direction
    if leader_pct_at_entry >= 0:
        return delta < -cfg.LEADER_REVERSAL_VETO_PCT
    else:
        return delta > cfg.LEADER_REVERSAL_VETO_PCT


def _unwrap_flow_response(result: Any) -> Optional[Dict[str, Any]]:
    """The MCP tool returns {success: bool, data: {...}}. Unwrap to the
    inner data dict. Returns None on any unexpected shape."""
    if not result or not isinstance(result, dict):
        return None
    if "data" in result and isinstance(result["data"], dict):
        return result["data"]
    # Fallback: tool may have returned the data dict directly
    if "leader" in result or "laggards" in result:
        return result
    return None


def get_current_leader_move(leader_asset: str) -> Optional[float]:
    """Re-call the cross-asset flow tool for the leader and read
    the current 4h move. Cheap because the tool is pre-computed."""
    raw = cfg.get_cross_asset_flows(leader_asset)
    data = _unwrap_flow_response(raw)
    if not data:
        return None
    leader = data.get("leader") or {}
    return safe_float(leader.get("move_pct"))


# ═══════════════════════════════════════════════════════════════
# Leader-reversal veto pass
# ═══════════════════════════════════════════════════════════════

def run_leader_reversal_veto(open_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For each open Mantis position with stored metadata, check if the
    leader has reversed enough to invalidate the catchup thesis.
    Returns list of veto-close instructions (asset, reason) — execution
    is delegated to the runtime via the action output."""
    vetos = []
    for pos in open_positions:
        asset = pos.get("coin", "").upper()
        meta = state.get_position_metadata(asset)
        if not meta:
            continue
        leader_asset = meta.get("leader_asset")
        leader_pct_at_entry = safe_float(meta.get("leader_pct_at_entry"))
        if not leader_asset:
            continue
        current_leader_pct = get_current_leader_move(leader_asset)
        if current_leader_pct is None:
            continue
        if leader_reversed(leader_pct_at_entry, current_leader_pct):
            cfg.log(
                f"LEADER REVERSAL on {asset}: {leader_asset} "
                f"entry={leader_pct_at_entry:.2f}% → now={current_leader_pct:.2f}%"
            )
            state.append_entry_log(
                "LEADER_REVERSAL_EXIT",
                asset=asset,
                leader_asset=leader_asset,
                leader_pct_at_entry=leader_pct_at_entry,
                current_leader_pct=current_leader_pct,
            )
            vetos.append({
                "asset": asset,
                "side": pos.get("direction"),
                "size": pos.get("size"),
                "reason": "leader_reversal_veto",
                "leader_asset": leader_asset,
                "leader_pct_at_entry": leader_pct_at_entry,
                "current_leader_pct": current_leader_pct,
            })
    return vetos


# ═══════════════════════════════════════════════════════════════
# Candidate gathering
# ═══════════════════════════════════════════════════════════════

def gather_candidates() -> List[Dict[str, Any]]:
    candidates = []
    for leader in cfg.LEADER_UNIVERSE:
        raw = cfg.get_cross_asset_flows(leader)
        data = _unwrap_flow_response(raw)
        if not data:
            cfg.log(f"no flow data for leader={leader}")
            continue
        leader_block = data.get("leader") or {}
        leader_move_pct = safe_float(leader_block.get("move_pct"))
        laggards = data.get("laggards", []) or []
        if not laggards:
            cfg.log(f"empty laggards for leader={leader} (move={leader_move_pct:.2f}%)")
            continue
        for laggard in laggards:
            if not passes_entry_filters(laggard):
                continue
            laggard["_leader_asset"] = leader
            laggard["_leader_move_pct"] = leader_move_pct
            candidates.append(laggard)
    candidates.sort(key=lambda x: safe_float(x.get("confidence")), reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════════
# Strike construction
# ═══════════════════════════════════════════════════════════════

def build_strike(candidate: Dict[str, Any], account_value: float) -> Dict[str, Any]:
    confidence = safe_float(candidate.get("confidence"))
    tier = sizing_tier_for(confidence)
    margin_pct = tier["margin_pct"]
    leverage = min(tier["leverage"], cfg.MAX_LEVERAGE)

    margin_usd = round(account_value * (margin_pct / 100.0), 2)
    notional_cap = account_value * (cfg.MAX_POSITION_NOTIONAL_PCT / 100.0)
    notional_usd = min(margin_usd * leverage, notional_cap)

    direction = direction_from_leader_move(candidate.get("_leader_move_pct", 0))
    avg_lag = safe_float(candidate.get("avg_lag_minutes"), 60)
    hard_timeout = compute_hard_timeout(avg_lag)

    return {
        "asset": candidate.get("asset"),
        "side": direction,
        "leverage": leverage,
        "margin_usd": margin_usd,
        "notional_usd": round(notional_usd, 2),
        "hard_timeout_minutes": hard_timeout,
        "confidence": confidence,
        "gap_pct": safe_float(candidate.get("gap_pct")),
        "follow_rate": safe_float(candidate.get("follow_rate")),
        "avg_lag_minutes": avg_lag,
        "lag_stddev_minutes": safe_float(candidate.get("lag_stddev_minutes")),
        "sm_starting_to_rotate": bool(candidate.get("sm_starting_to_rotate")),
        "leader_asset": candidate.get("_leader_asset"),
        "leader_move_pct": candidate.get("_leader_move_pct"),
        "order_type": "FEE_OPTIMIZED_LIMIT",
    }


# ═══════════════════════════════════════════════════════════════
# Main scan
# ═══════════════════════════════════════════════════════════════

def main():
    cfg.log("Mantis v5.0 Slipstream — scan starting")

    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"action": "ERROR", "reason": "no_wallet_configured"})
        return

    # 1. Pull current positions
    account_value, open_positions = cfg.get_positions(wallet)
    open_assets = [p.get("coin", "").upper() for p in open_positions]

    # 2. Reconcile metadata for closed positions (DSL exits, external closes, etc.)
    closed = state.reconcile_position_metadata(open_assets)
    if closed:
        cfg.log(f"reconciled {len(closed)} closed positions: {closed}")

    # 3. Leader-reversal veto pass — these are signals to the runtime
    #    to close the position. The runtime will execute close_position.
    vetos = run_leader_reversal_veto(open_positions)
    if vetos:
        cfg.output({
            "action": "VETO_CLOSE",
            "positions": vetos,
            "reason": "leader_reversal_veto",
        })
        # After issuing vetos, the runtime will close them; clear metadata
        for v in vetos:
            state.clear_position_metadata(v["asset"])
        # Don't open a new position in the same tick as a veto — wait for next scan
        return

    # 4. Concurrency check
    if len(open_positions) >= cfg.MAX_CONCURRENT_POSITIONS:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "max_concurrent_positions",
            "open_count": len(open_positions),
            "cap": cfg.MAX_CONCURRENT_POSITIONS,
        })
        return

    # 5. Daily cap check
    daily_count = state.count_entries_today()
    if daily_count >= cfg.MAX_DAILY_ENTRIES:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "daily_cap_reached",
            "today_count": daily_count,
            "cap": cfg.MAX_DAILY_ENTRIES,
        })
        state.append_entry_log("DAILY_CAP_REACHED", count=daily_count)
        return

    # 6. Gather candidates from cross-asset flow tool
    candidates = gather_candidates()
    if not candidates:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "no_qualifying_laggards",
            "checked_leaders": cfg.LEADER_UNIVERSE,
        })
        return

    # 7. Walk candidates by confidence; skip any in cooldown
    pick = None
    for c in candidates:
        asset = (c.get("asset") or "").upper()
        if not asset:
            continue
        if asset in open_assets:
            continue
        if state.is_asset_in_cooldown(asset):
            continue
        pick = c
        break

    if not pick:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "all_qualifying_in_cooldown_or_open",
            "candidates_count": len(candidates),
        })
        return

    # 8. Build the strike
    strike = build_strike(pick, account_value)

    # 9. Stash position metadata BEFORE emitting the strike, so that even
    #    if the runtime opens the position and the scanner crashes, the
    #    metadata is on disk for the next leader-reversal veto pass.
    state.set_position_metadata(strike["asset"], {
        "leader_asset": strike["leader_asset"],
        "leader_pct_at_entry": strike["leader_move_pct"],
        "expected_lag_minutes": strike["avg_lag_minutes"],
        "lag_stddev_minutes": strike["lag_stddev_minutes"],
        "confidence_at_entry": strike["confidence"],
        "gap_pct_at_entry": strike["gap_pct"],
        "side": strike["side"],
        "leverage": strike["leverage"],
        "hard_timeout_minutes": strike["hard_timeout_minutes"],
    })

    # 10. Mark cooldown + log
    state.mark_asset_cooldown(strike["asset"], reason="strike")
    state.append_entry_log(
        "STRIKE",
        asset=strike["asset"],
        side=strike["side"],
        leverage=strike["leverage"],
        margin_usd=strike["margin_usd"],
        confidence=strike["confidence"],
        gap_pct=strike["gap_pct"],
        leader_asset=strike["leader_asset"],
        leader_move_pct=strike["leader_move_pct"],
        avg_lag_minutes=strike["avg_lag_minutes"],
        hard_timeout_minutes=strike["hard_timeout_minutes"],
    )

    # 11. Emit strike for the runtime to execute via create_position
    cfg.output({
        "action": "STRIKE",
        "trade": strike,
        "reasoning": (
            f"{strike['leader_asset']} moved {strike['leader_move_pct']:+.2f}% in 4h. "
            f"{strike['asset']} typically follows in {strike['avg_lag_minutes']:.0f}±"
            f"{strike['lag_stddev_minutes']:.0f}min with {strike['follow_rate']:.0%} reliability. "
            f"Currently {strike['gap_pct']:+.2f}% behind. SM starting to rotate. "
            f"Confidence {strike['confidence']:.2f}. Sizing tier: {strike['margin_usd']:.0f} margin "
            f"@ {strike['leverage']}x. Hard timeout: {strike['hard_timeout_minutes']}min "
            f"(avg lag × {cfg.HARD_TIMEOUT_LAG_MULTIPLIER})."
        ),
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        cfg.log(f"FATAL: {type(e).__name__}: {e}")
        cfg.output({"action": "ERROR", "error": str(e), "type": type(e).__name__})
        sys.exit(1)
