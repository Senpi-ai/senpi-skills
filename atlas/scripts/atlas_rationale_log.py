#!/usr/bin/env python3
# Senpi ATLAS Rationale Log v0.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""ATLAS v0.1 — Rationale Log

The rationale log is the artifact that makes Atlas's warmup phase work.
Every daily decision (paper or live) is written to disk as a structured
JSONL record. The operator reviews these during the 7-day warmup before
any capital is at risk; the agent itself reads them post-strike to score
its own past decisions.

Design principles
-----------------
1. **One log file, typed events.** All events live in atlas-log.jsonl.
   Discriminator is the `event` field. Easier to grep, replay, and
   archive than multiple files.

2. **Every record is self-describing.** No external schema needed to
   read a record — the field names ARE the schema. Optimized for
   eyeball-readable JSON, not compactness.

3. **Decisions capture counterfactuals.** A decision record stores the
   alternatives considered and rejected, with the score gap. This is
   how we tell whether the agent actually *chose* vs. just took the
   top-scored candidate by reflex.

4. **Survives session clears.** Lives at
   `${STATE_DIR}/atlas-log.jsonl`. Same pattern as Wolverine v2.3's
   entry-log.jsonl. `openclaw sessions clear --current` does not touch it.

5. **Append-only.** Records are never mutated. Outcome attribution writes
   a new ATTRIBUTION record that references prior decisions by id —
   never edits them.

Event types
-----------
SCAN          — daily scanner output (anchor + basket candidate slates)
DECISION      — agent's daily decision (HOLD, SWAP, RESIZE, REBALANCE, GO_TO_CASH)
STRIKE        — first-strike audit (one-time per Atlas instance lifetime)
PILOT_RAMP    — staggered entry milestone (day 1 → 50%, day 2 → 75%, etc.)
ABORT         — first-strike abort with reason + warmup reset
WARMUP_END    — marks end of paper-trading phase
ATTRIBUTION   — weekly retrospective scoring of past decisions
ERROR         — non-fatal scanner/decision errors logged for review
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG_FILENAME = "atlas-log.jsonl"


# ═══════════════════════════════════════════════════════════════
# WRITER
# ═══════════════════════════════════════════════════════════════

def _log_path(state_dir: str) -> str:
    return os.path.join(state_dir, LOG_FILENAME)


def _now() -> Dict[str, Any]:
    return {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
    }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _write(state_dir: str, record: Dict[str, Any]) -> Dict[str, Any]:
    os.makedirs(state_dir, exist_ok=True)
    path = _log_path(state_dir)
    try:
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (IOError, OSError) as e:
        # Logging failure must not crash the agent. Re-emit to stderr.
        import sys
        sys.stderr.write(f"[atlas-log] write failed: {e}\n")
    return record


# ── SCAN ─────────────────────────────────────────────────────────

def write_scan(
    state_dir: str,
    cycle_id: str,
    anchor_candidates: List[Dict[str, Any]],
    basket_candidates: List[Dict[str, Any]],
    fleet_overlap: Dict[str, Any],
    regime: Dict[str, Any],
) -> Dict[str, Any]:
    """Daily scanner output. Always written, even when no decision follows.

    anchor_candidates: top 5, each with at minimum:
        {asset, score, components: {rel_strength, sm_consensus, funding_fav,
         oi_trend, vol_regime}, reasons: [str], suggested_leverage}

    basket_candidates: top 10, each with at minimum:
        {asset, score, components: {funding, sm_rotation, rel_weakness,
         squeeze_risk, correlation}, reasons: [str], current_funding_8h,
         est_weekly_harvest_per_$1k}

    fleet_overlap: {atlas_self_excluded: bool, by_asset: {asset: {long_n,
        short_n, net_notional_pct}}}

    regime: {btc_trend, btc_drawdown_48h, funding_breadth, vol_regime,
        catastrophic_flags: [str]}
    """
    return _write(state_dir, {
        **_now(),
        "event": "SCAN",
        "id": _new_id("scan"),
        "cycle_id": cycle_id,
        "anchor_candidates": anchor_candidates,
        "basket_candidates": basket_candidates,
        "fleet_overlap": fleet_overlap,
        "regime": regime,
    })


# ── DECISION ─────────────────────────────────────────────────────

def write_decision(
    state_dir: str,
    cycle_id: str,
    scan_id: str,
    decision: str,
    mode: str,
    portfolio_state_before: Dict[str, Any],
    intended_portfolio_after: Dict[str, Any],
    rationale: str,
    alternatives_considered: List[Dict[str, Any]],
    conviction: float,
    risk_overlay_applied: List[str],
) -> Dict[str, Any]:
    """Agent's daily decision.

    decision: one of HOLD | SWAP_BASKET_MEMBER | RESIZE | FULL_REBALANCE | GO_TO_CASH
    mode: 'paper' (warmup) | 'live'
    rationale: free-text written by the LLM. The operator-facing artifact.
    alternatives_considered: list of {option, score_or_conviction,
        why_rejected}. Captures counterfactuals so we can tell whether
        the agent actually chose vs. defaulted to top score.
    conviction: 0.0–10.0 self-rated by the agent. Sized accordingly.
    risk_overlay_applied: list of risk rules that fired (e.g.
        ['leverage_capped_fleet_concentration', 'cash_floor_enforced']).
    """
    return _write(state_dir, {
        **_now(),
        "event": "DECISION",
        "id": _new_id("dec"),
        "cycle_id": cycle_id,
        "scan_id": scan_id,
        "decision": decision,
        "mode": mode,
        "portfolio_before": portfolio_state_before,
        "portfolio_intended": intended_portfolio_after,
        "rationale": rationale,
        "alternatives_considered": alternatives_considered,
        "conviction": conviction,
        "risk_overlay_applied": risk_overlay_applied,
    })


# ── STRIKE / PILOT_RAMP / ABORT ─────────────────────────────────

def write_strike(
    state_dir: str,
    cycle_id: str,
    decision_id: str,
    anchor: Dict[str, Any],
    persistence_window: List[Dict[str, Any]],
    regime_check: Dict[str, Any],
    rationale: str,
) -> Dict[str, Any]:
    """First-strike audit. Fires exactly once per Atlas instance lifetime.

    persistence_window: list of {scan_id, ts, top_anchor_asset,
        top_anchor_score} for the 2 (or more) consecutive qualifying scans.
    regime_check: snapshot of catastrophic-regime check at strike time.
    """
    return _write(state_dir, {
        **_now(),
        "event": "STRIKE",
        "id": _new_id("strk"),
        "cycle_id": cycle_id,
        "decision_id": decision_id,
        "anchor": anchor,
        "persistence_window": persistence_window,
        "regime_check": regime_check,
        "rationale": rationale,
    })


def write_pilot_ramp(
    state_dir: str,
    cycle_id: str,
    pilot_day: int,
    target_anchor_pct: float,
    target_basket_pct: float,
    thesis_check: Dict[str, Any],
    action: str,
) -> Dict[str, Any]:
    """Staggered entry milestone.

    pilot_day: 1, 2, or 3 (4+ enters steady state, no PILOT_RAMP record).
    thesis_check: {anchor_pnl_pct, anchor_score_current, anchor_score_at_entry,
        thesis_status: 'confirming' | 'ambiguous' | 'broken'}
    action: 'ramp_up' | 'hold_pilot' | 'abort'
    """
    return _write(state_dir, {
        **_now(),
        "event": "PILOT_RAMP",
        "id": _new_id("pilot"),
        "cycle_id": cycle_id,
        "pilot_day": pilot_day,
        "target_anchor_pct": target_anchor_pct,
        "target_basket_pct": target_basket_pct,
        "thesis_check": thesis_check,
        "action": action,
    })


def write_abort(
    state_dir: str,
    cycle_id: str,
    decision_id: str,
    reason: str,
    pnl_at_abort: float,
    warmup_reset_days: int,
) -> Dict[str, Any]:
    """First-strike abort. Closes pilot, returns to GO_TO_CASH, resets warmup."""
    return _write(state_dir, {
        **_now(),
        "event": "ABORT",
        "id": _new_id("abort"),
        "cycle_id": cycle_id,
        "decision_id": decision_id,
        "reason": reason,
        "pnl_at_abort": pnl_at_abort,
        "warmup_reset_days": warmup_reset_days,
    })


# ── WARMUP_END ───────────────────────────────────────────────────

def write_warmup_end(
    state_dir: str,
    days_observed: int,
    paper_decisions_summary: Dict[str, Any],
    threshold_calibration: Dict[str, Any],
) -> Dict[str, Any]:
    """End of warmup phase. Atlas transitions from paper to live-eligible.

    paper_decisions_summary: {total_scans, days_with_qualifying_anchor,
        most_picked_anchor, most_picked_basket_members, would_have_struck_n_times}
    threshold_calibration: {top_score_max, top_score_p50, top_score_p10,
        suggested_threshold_adjustment}
    """
    return _write(state_dir, {
        **_now(),
        "event": "WARMUP_END",
        "id": _new_id("warm"),
        "days_observed": days_observed,
        "paper_decisions_summary": paper_decisions_summary,
        "threshold_calibration": threshold_calibration,
    })


# ── ATTRIBUTION ──────────────────────────────────────────────────

def write_attribution(
    state_dir: str,
    week_start_iso: str,
    week_end_iso: str,
    realized_pnl: float,
    pnl_by_leg: Dict[str, float],
    funding_harvested: float,
    fees_paid: float,
    decisions_referenced: List[str],
    decision_quality: List[Dict[str, Any]],
    overall_score: float,
    notes: str,
) -> Dict[str, Any]:
    """Weekly retrospective. Scores past decisions against actual outcomes.

    decisions_referenced: list of decision ids scored in this attribution.
    decision_quality: list of {decision_id, retrospective_score_0_10,
        what_was_right, what_was_wrong, would_repeat: bool}.
    overall_score: 0–10 weekly grade for the agent's judgment.

    Never mutates prior records. Pure post-hoc analysis.
    """
    return _write(state_dir, {
        **_now(),
        "event": "ATTRIBUTION",
        "id": _new_id("attr"),
        "week_start": week_start_iso,
        "week_end": week_end_iso,
        "realized_pnl": realized_pnl,
        "pnl_by_leg": pnl_by_leg,
        "funding_harvested": funding_harvested,
        "fees_paid": fees_paid,
        "decisions_referenced": decisions_referenced,
        "decision_quality": decision_quality,
        "overall_score": overall_score,
        "notes": notes,
    })


# ── ERROR ────────────────────────────────────────────────────────

def write_error(
    state_dir: str,
    cycle_id: Optional[str],
    where: str,
    message: str,
    fatal: bool,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Non-fatal scanner/decision errors. Fatal errors should also raise."""
    return _write(state_dir, {
        **_now(),
        "event": "ERROR",
        "id": _new_id("err"),
        "cycle_id": cycle_id,
        "where": where,
        "message": message,
        "fatal": fatal,
        "context": context or {},
    })


# ═══════════════════════════════════════════════════════════════
# READER
# ═══════════════════════════════════════════════════════════════

def read_log(state_dir: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Read the most recent N records, newest last."""
    p = _log_path(state_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            lines = f.readlines()
    except (IOError, OSError):
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def filter_events(records: List[Dict[str, Any]], event: str) -> List[Dict[str, Any]]:
    return [r for r in records if r.get("event") == event]


def warmup_status(state_dir: str, warmup_days: int = 7) -> Dict[str, Any]:
    """Summarize warmup progress for the operator dashboard.

    Returns: {complete: bool, days_elapsed: int, scans_completed: int,
        qualifying_strikes_seen: int, suggested_action: str}
    """
    records = read_log(state_dir, limit=2000)
    if not records:
        return {
            "complete": False,
            "days_elapsed": 0,
            "scans_completed": 0,
            "qualifying_strikes_seen": 0,
            "suggested_action": "boot_atlas_to_begin_warmup",
        }

    warmup_end = next((r for r in records if r.get("event") == "WARMUP_END"), None)
    if warmup_end:
        return {
            "complete": True,
            "days_elapsed": warmup_end.get("days_observed", warmup_days),
            "scans_completed": len(filter_events(records, "SCAN")),
            "qualifying_strikes_seen": warmup_end.get(
                "paper_decisions_summary", {}).get("would_have_struck_n_times", 0),
            "suggested_action": "warmup_complete_strike_when_qualifying",
        }

    first_ts = records[0].get("ts", time.time())
    days_elapsed = (time.time() - first_ts) / 86400
    scans = filter_events(records, "SCAN")

    # Count qualifying strike windows: 2 consecutive scans with top anchor ≥ 7.0
    qualifying = 0
    last_was_qualifying = False
    for s in scans:
        top = s.get("anchor_candidates", [])
        if top and top[0].get("score", 0) >= 7.0:
            if last_was_qualifying:
                qualifying += 1
            last_was_qualifying = True
        else:
            last_was_qualifying = False

    return {
        "complete": False,
        "days_elapsed": round(days_elapsed, 2),
        "scans_completed": len(scans),
        "qualifying_strikes_seen": qualifying,
        "suggested_action": (
            "warmup_in_progress"
            if days_elapsed < warmup_days
            else "warmup_duration_met_close_with_warmup_end_event"
        ),
    }
