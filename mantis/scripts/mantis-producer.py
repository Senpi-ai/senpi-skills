#!/usr/bin/env python3
# Senpi MANTIS Producer v6.0.0 — Slipstream
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""MANTIS v6.0.0 Producer — Slipstream, senpi_runtime_helpers migration.

v6.0.0 (2026-05-12): plumbing-only migration from v5.0. NO thesis change.
v5.0 entry filters, sizing tiers, dynamic hard_timeout calculation, and
leader-reversal veto logic preserved verbatim.

Six-layer plumbing flip:
  1. MCP transport: mcporter subprocess (2.5-5s cold-start) →
     SenpiClient.mcp_call() in-process HTTPS (~280ms).
  2. Signal emit: producer no longer self-executes opens; emits via
     client.push_signal() to runtime /signals. Runtime LLM-gated
     mantis_entry action opens via FEE_OPTIMIZED_LIMIT.
  3. Leader-reversal veto: previously emitted as VETO_CLOSE stdout JSON
     that no runtime action consumed (silent no-op in v5.0). v6.0 wires
     this properly — producer calls client.mcp_call("close_position", ...)
     directly when leader reverses. This is intentional: the runtime has
     no way to express "close if a separate asset's price reverses by X%."
  4. Scheduler: openclaw cron (60s) → producer_daemon(interval_seconds=60).
     Long-lived process; scanner_lock with stale-PID auto-recovery.
  5. Risk gates: Python MAX_DAILY_ENTRIES + per-asset cooldown state files
     → declarative risk.guard_rails block in runtime.yaml. State files
     (asset-cooldowns.json) deleted.
  6. Exit fee: DSL exits MARKET (taker 0.045%) → FEE_OPTIMIZED_LIMIT
     (maker-first 0.015%, 60s ALO timeout, taker fallback).

State files retained in v6.0 (producer-authoritative metadata, NOT
runtime-leaky-state):
  - position-metadata.json — leader_pct_at_entry per asset (required
    for veto pass)
  - entry-log.jsonl — observability only (no longer used for daily-cap
    counting; runtime guard_rails enforce that)

Environment / config resolution:
  Strategy wallet is read from config/mantis-config.json (the canonical
  source). MANTIS_WALLET env var is supported as an optional override
  but is NOT required for routine daemon runs.

  SENPI_AUTH_TOKEN       — REQUIRED. Bearer token for MCP + signal POST.
  MANTIS_WALLET          — optional override; defaults to config.wallet
  MANTIS_DECISION_MODEL  — bare LLM model name (no provider prefix);
                           resolved into runtime.yaml at create time
  SENPI_MCP_URL          — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mantis_config as cfg
import mantis_state as state

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "6.0.1"
SCANNER_NAME = "mantis_signals"
SIGNAL_TYPE = "MANTIS_SLIPSTREAM"


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def passes_entry_filters(laggard: Dict[str, Any]) -> bool:
    """All filters must pass — preserved verbatim from v5.0."""
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
    """Follow the leader: positive move → LONG laggard, negative → SHORT."""
    return "LONG" if leader_move_pct >= 0 else "SHORT"


def compute_hard_timeout(avg_lag_minutes: float) -> int:
    """Dynamic hard_timeout = avg_lag_minutes × multiplier, clamped."""
    minutes = max(1.0, safe_float(avg_lag_minutes, 60.0)) * cfg.HARD_TIMEOUT_LAG_MULTIPLIER
    minutes = max(cfg.HARD_TIMEOUT_FLOOR_MINUTES,
                   min(cfg.HARD_TIMEOUT_CEILING_MINUTES, minutes))
    return int(minutes)


def leader_reversed(leader_pct_at_entry: float, current_leader_move_pct: float) -> bool:
    """True if leader has reversed by more than LEADER_REVERSAL_VETO_PCT
    from its move at entry time. Direction matters: entry on +3% leader
    move, now leader at +1.8% → 1.2% reversal against thesis → veto."""
    delta = current_leader_move_pct - leader_pct_at_entry
    if leader_pct_at_entry >= 0:
        return delta < -cfg.LEADER_REVERSAL_VETO_PCT
    else:
        return delta > cfg.LEADER_REVERSAL_VETO_PCT


def _unwrap_flow_response(result: Any) -> Optional[Dict[str, Any]]:
    """MCP returns {success: bool, data: {...}}. Unwrap to inner dict."""
    if not result or not isinstance(result, dict):
        return None
    if "data" in result and isinstance(result["data"], dict):
        return result["data"]
    if "leader" in result or "laggards" in result:
        return result
    return None


def get_current_leader_move(leader_asset: str) -> Optional[float]:
    """Re-call the cross-asset flow tool for the leader and read the
    current 4h move. Cheap because the tool is pre-computed."""
    raw = cfg.get_cross_asset_flows(leader_asset)
    data = _unwrap_flow_response(raw)
    if not data:
        return None
    leader = data.get("leader") or {}
    return safe_float(leader.get("move_pct"))


# ═══════════════════════════════════════════════════════════════
# Leader-reversal veto — closes positions directly via mcp_call
# ═══════════════════════════════════════════════════════════════

def run_leader_reversal_veto(wallet: str, open_positions: List[Dict[str, Any]]) -> int:
    """For each open Mantis position with stored metadata, check if the
    leader has reversed enough to invalidate the catchup thesis. When
    triggered, call close_position directly. Returns count closed.

    v6.0 change: previously emitted VETO_CLOSE stdout JSON that no
    runtime action consumed (silent no-op). Now wired properly — the
    producer is the authoritative voice for the cross-asset reversal
    thesis; runtime has no way to model "close if asset X's 4h move
    reverses by Y%". Direct mcp_call("close_position", ...) is the
    only correct surface."""
    closed = 0
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
        if not leader_reversed(leader_pct_at_entry, current_leader_pct):
            continue

        cfg.log(
            f"LEADER REVERSAL on {asset}: {leader_asset} "
            f"entry={leader_pct_at_entry:.2f}% → now={current_leader_pct:.2f}%"
        )
        state.append_entry_log(
            "LEADER_REVERSAL_EXIT",
            asset=asset,
            side=pos.get("direction"),
            leader_asset=leader_asset,
            leader_pct_at_entry=leader_pct_at_entry,
            current_leader_pct=current_leader_pct,
        )

        # v6.0 fee policy: veto close uses FEE_OPTIMIZED_LIMIT with
        # taker fallback. The veto is "thesis died, get out" — but
        # ensureExecutionAsTaker gives execution certainty (30s ALO
        # then falls to taker). Worst case fee = MARKET would be; best
        # case saves 0.030% per close. Half the DSL exit timeout (30s
        # vs 60s) because vetos are slightly more time-sensitive than
        # retrace-based DSL exits.
        try:
            cfg.mcp_call(
                "close_position",
                strategyWalletAddress=wallet,
                asset=asset,
                orderType="FEE_OPTIMIZED_LIMIT",
                feeOptimizedLimitOptions={
                    "ensureExecutionAsTaker": True,
                    "executionTimeoutSeconds": 30,
                },
            )
            closed += 1
            state.clear_position_metadata(asset)
        except Exception as e:  # noqa: BLE001
            cfg.log(f"VETO_CLOSE_FAILED {asset}: {type(e).__name__}: {e}")
            state.append_entry_log(
                "VETO_CLOSE_FAILED",
                asset=asset,
                error=f"{type(e).__name__}: {e}",
            )
    return closed


# ═══════════════════════════════════════════════════════════════
# Candidate gathering
# ═══════════════════════════════════════════════════════════════

def gather_candidates() -> List[Dict[str, Any]]:
    """Walk each leader's cross-asset flows; return filtered + sorted laggards."""
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
# Strike construction — preserved from v5.0
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
        "direction": direction,
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
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit — push to runtime /signals
# ═══════════════════════════════════════════════════════════════

def push_signal(wallet: str, strike: Dict[str, Any], held_assets: List[str]) -> bool:
    """Emit a STRIKE signal. asset + direction are top-level routing fields;
    sizing + thesis metadata go in data block. LLM gate honors leverage +
    marginUsd as the conviction-tier sizing knobs."""
    # Defense in depth — runtime per_asset_cooldown is authoritative
    if strike["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": strike["confidence"],          # 0..1 confidence is Mantis's score
        "leverage": strike["leverage"],
        "marginUsd": strike["margin_usd"],
        "notionalUsd": strike["notional_usd"],
        "hardTimeoutMinutes": strike["hard_timeout_minutes"],
        "gapPct": strike["gap_pct"],
        "followRate": strike["follow_rate"],
        "avgLagMinutes": strike["avg_lag_minutes"],
        "lagStddevMinutes": strike["lag_stddev_minutes"],
        "smStartingToRotate": strike["sm_starting_to_rotate"],
        "leaderAsset": strike["leader_asset"],
        "leaderMovePct": strike["leader_move_pct"],
        "heldAssets": held_assets,
        "reasons": [
            f"SLIPSTREAM {strike['asset']} follows {strike['leader_asset']}",
            f"leader_move {strike['leader_move_pct']:+.2f}% in 4h",
            f"gap {strike['gap_pct']:+.2f}% behind",
            f"follow_rate {strike['follow_rate']:.0%}",
            f"avg_lag {strike['avg_lag_minutes']:.0f}±{strike['lag_stddev_minutes']:.0f}min",
            f"confidence {strike['confidence']:.2f}",
            f"sizing_tier margin_usd={strike['margin_usd']:.0f} lev={strike['leverage']}x",
        ],
    }

    try:
        cfg._wrapper_client.push_signal(
            address=wallet,
            scanner=SCANNER_NAME,
            asset=strike["asset"],
            direction=strike["direction"],
            score=strike["confidence"],         # already 0..1
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {strike['asset']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {strike['asset']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    cfg.log(f"Mantis v{VERSION} Slipstream — scan starting")
    run_start = time.time()

    wallet, _strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({
            "action": "ERROR",
            "reason": "no_wallet_configured",
            "_mantis_producer_version": VERSION,
        })
        return

    # 1. Pull current positions
    account_value, open_positions = cfg.get_positions(wallet)
    open_assets = [p.get("coin", "").upper() for p in open_positions]

    # 2. Reconcile metadata for closed positions
    closed = state.reconcile_position_metadata(open_assets)
    if closed:
        cfg.log(f"reconciled {len(closed)} closed positions: {closed}")

    # 3. Leader-reversal veto pass (calls close_position directly)
    vetoed = run_leader_reversal_veto(wallet, open_positions)
    if vetoed:
        cfg.output({
            "action": "VETO_CLOSE",
            "closed_count": vetoed,
            "reason": "leader_reversal_veto",
            "_mantis_producer_version": VERSION,
        })
        # Re-pull positions after vetoes to reflect post-close state
        account_value, open_positions = cfg.get_positions(wallet)
        open_assets = [p.get("coin", "").upper() for p in open_positions]

    # 4. Gather candidates from cross-asset flow tool
    candidates = gather_candidates()
    if not candidates:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "no_qualifying_laggards",
            "checked_leaders": cfg.LEADER_UNIVERSE,
            "_mantis_producer_version": VERSION,
        })
        return

    # 5. Walk candidates by confidence; skip any already open
    #    (per-asset cooldown is runtime-enforced via guard_rails)
    pick = None
    for c in candidates:
        asset = (c.get("asset") or "").upper()
        if not asset:
            continue
        if asset in open_assets:
            continue
        pick = c
        break

    if not pick:
        cfg.output({
            "action": "NO_ENTRY",
            "reason": "all_qualifying_already_held",
            "candidates_count": len(candidates),
            "_mantis_producer_version": VERSION,
        })
        return

    # 6. Build the strike
    strike = build_strike(pick, account_value)

    # 7. Stash position metadata BEFORE emitting the signal, so even if
    #    runtime opens the position and the daemon restarts before the
    #    next tick, the metadata is on disk for the next veto pass.
    state.set_position_metadata(strike["asset"], {
        "leader_asset": strike["leader_asset"],
        "leader_pct_at_entry": strike["leader_move_pct"],
        "expected_lag_minutes": strike["avg_lag_minutes"],
        "lag_stddev_minutes": strike["lag_stddev_minutes"],
        "confidence_at_entry": strike["confidence"],
        "gap_pct_at_entry": strike["gap_pct"],
        "direction": strike["direction"],
        "leverage": strike["leverage"],
        "hard_timeout_minutes": strike["hard_timeout_minutes"],
    })
    state.append_entry_log(
        "STRIKE",
        asset=strike["asset"],
        direction=strike["direction"],
        leverage=strike["leverage"],
        margin_usd=strike["margin_usd"],
        confidence=strike["confidence"],
        gap_pct=strike["gap_pct"],
        leader_asset=strike["leader_asset"],
        leader_move_pct=strike["leader_move_pct"],
        avg_lag_minutes=strike["avg_lag_minutes"],
        hard_timeout_minutes=strike["hard_timeout_minutes"],
    )

    # 8. Emit signal
    pushed = push_signal(wallet, strike, open_assets)
    elapsed = time.time() - run_start
    cfg.output({
        "action": "STRIKE" if pushed else "INGEST_FAILED",
        "signals_pushed": 1 if pushed else 0,
        "trade": strike,
        "elapsed_sec": round(elapsed, 2),
        "_mantis_producer_version": VERSION,
        "reasoning": (
            f"{strike['leader_asset']} moved {strike['leader_move_pct']:+.2f}% in 4h. "
            f"{strike['asset']} typically follows in {strike['avg_lag_minutes']:.0f}±"
            f"{strike['lag_stddev_minutes']:.0f}min with {strike['follow_rate']:.0%} reliability. "
            f"Currently {strike['gap_pct']:+.2f}% behind. SM starting to rotate. "
            f"Confidence {strike['confidence']:.2f}. Sizing tier: {strike['margin_usd']:.0f} "
            f"margin @ {strike['leverage']}x. Hard timeout: "
            f"{strike['hard_timeout_minutes']}min (avg lag × {cfg.HARD_TIMEOUT_LAG_MULTIPLIER})."
        ),
    })


if __name__ == "__main__":
    # v6.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery via os.kill(pid, 0).
    wallet, _ = cfg.get_wallet_and_strategy()
    _wallet_lock_id = (
        hashlib.sha256(wallet.lower().encode()).hexdigest()[:12]
        if wallet
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=60,                  # 60s — preserved from v5.0 cron cadence
        name=f"mantis-producer-{_wallet_lock_id}",
        wallet=wallet,
        scanner=SCANNER_NAME,
        tick_timeout=90,
    )
