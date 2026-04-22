#!/usr/bin/env python3
# Senpi SPIDER Rationale Log v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SPIDER v2.0 — Rationale Log

The rationale log is the artifact that makes Spider's warmup phase work.
Every daily decision (paper or live) is written to disk as a structured
JSONL record. The operator reviews these during the 7-day warmup before
any capital is at risk; the agent itself reads them post-strike to score
its own past decisions.

Design principles
-----------------
1. **One log file, typed events.** All events live in spider-log.jsonl.
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
   `${STATE_DIR}/spider-log.jsonl`. Same pattern as Wolverine v2.3's
   entry-log.jsonl. `openclaw sessions clear --current` does not touch it.

5. **Append-only.** Records are never mutated. Outcome attribution writes
   a new ATTRIBUTION record that references prior decisions by id —
   never edits them.

Event types
-----------
SCAN                  — daily scanner output (anchor + basket candidate slates)
DECISION              — agent's daily decision (HOLD, SWAP, RESIZE, REBALANCE, GO_TO_CASH)
STRIKE                — first-strike audit (one-time per Spider instance lifetime)
PILOT_RAMP            — staggered entry milestone (day 1 → 50%, day 2 → 75%, etc.)
ABORT                 — first-strike abort with reason + warmup reset
WARMUP_END            — marks end of paper-trading phase
EXIT                  — leg close with typed exit_reason (NEW in v2.0)
ATTRIBUTION           — weekly retrospective scoring of past decisions
FEE_CEILING_WARNING   — fee_pct_of_gross breached 25% on rolling 4w (NEW in v2.0)
SAFETY_STOP_WARNING   — >1 per-leg safety stop in rolling 30d (NEW in v2.0)
FILL_CAP_WARNING      — approached or breached 12 fills/7d ceiling (NEW in v2.0)
ERROR                 — non-fatal scanner/decision errors logged for review
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG_FILENAME = "spider-log.jsonl"

# v2.0 thresholds — informational; enforcement lives in caller logic
FEE_PCT_OF_GROSS_CEILING = 25.0          # rolling 4w
SAFETY_STOP_30D_CEILING = 1              # more than 1 in 30d = bleeder signal
FILLS_PER_7D_HARD_CAP = 12
FILLS_PER_7D_SOFT_WARNING = 8

# Exit-reason taxonomy (v2.0)
EXIT_REASONS = {
    "agent_chosen",        # daily decision loop rotated/closed the leg
    "thesis_break",        # score collapsed, agent closed
    "funding_flip",        # basket member's funding flipped, agent closed
    "weekly_review",       # regular rebalance
    "safety_stop",         # per-leg 25% hard stop fired — BAD outcome flag
    "circuit_breaker",     # portfolio drawdown triggered basket close
    "min_hold_expiry",     # anchor or member min-hold window expired
}

AGENT_CONTROLLED_EXITS = {"agent_chosen", "thesis_break", "funding_flip", "weekly_review", "min_hold_expiry"}
FORCED_EXITS = {"safety_stop", "circuit_breaker"}


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
        import sys
        sys.stderr.write(f"[spider-log] write failed: {e}\n")
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
    """Daily scanner output. Always written, even when no decision follows."""
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
    fill_budget_remaining_7d: Optional[int] = None,
) -> Dict[str, Any]:
    """Agent's daily decision.

    fill_budget_remaining_7d: v2.0 — how many fills Spider has left in its
    rolling 7d window before hitting the soft warning / hard cap. If this
    is low (< 4), the agent should bias toward HOLD unless conviction is
    very high. Captured in the decision record for retrospective review.
    """
    rec = {
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
    }
    if fill_budget_remaining_7d is not None:
        rec["fill_budget_remaining_7d"] = fill_budget_remaining_7d
    return _write(state_dir, rec)


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
    return _write(state_dir, {
        **_now(),
        "event": "WARMUP_END",
        "id": _new_id("warm"),
        "days_observed": days_observed,
        "paper_decisions_summary": paper_decisions_summary,
        "threshold_calibration": threshold_calibration,
    })


# ── EXIT (v2.0) ──────────────────────────────────────────────────

def write_exit(
    state_dir: str,
    cycle_id: Optional[str],
    leg_role: str,                       # 'anchor' | 'basket_member'
    asset: str,
    side: str,
    entry_ts: float,
    exit_ts: float,
    hold_duration_hours: float,
    entry_notional: float,
    exit_notional: float,
    realized_pnl: float,
    funding_accrued: float,
    fees_paid: float,
    exit_reason: str,
    exit_rationale: str,
) -> Dict[str, Any]:
    """Leg close with typed exit_reason.

    exit_reason: one of EXIT_REASONS. Forced exits (safety_stop,
    circuit_breaker) are the loud negative signal — they mean the
    agent lost control of the leg. Repeated forced exits are the
    bleeder signature and should trigger SAFETY_STOP_WARNING.
    """
    if exit_reason not in EXIT_REASONS:
        exit_reason = "unknown"
    return _write(state_dir, {
        **_now(),
        "event": "EXIT",
        "id": _new_id("exit"),
        "cycle_id": cycle_id,
        "leg_role": leg_role,
        "asset": asset,
        "side": side,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "hold_duration_hours": hold_duration_hours,
        "entry_notional": entry_notional,
        "exit_notional": exit_notional,
        "realized_pnl": realized_pnl,
        "funding_accrued": funding_accrued,
        "fees_paid": fees_paid,
        "exit_reason": exit_reason,
        "forced": exit_reason in FORCED_EXITS,
        "exit_rationale": exit_rationale,
    })


# ── ATTRIBUTION ──────────────────────────────────────────────────

def write_attribution(
    state_dir: str,
    week_start_iso: str,
    week_end_iso: str,
    gross_pnl: float,
    fees_paid: float,
    funding_harvested: float,
    realized_pnl: float,
    pnl_by_leg: Dict[str, float],
    decisions_referenced: List[str],
    exit_reason_counts: Dict[str, int],
    decision_quality: List[Dict[str, Any]],
    overall_score: float,
    notes: str,
    benchmark_buy_and_hold_pnl: Optional[float] = None,
) -> Dict[str, Any]:
    """Weekly retrospective. Scores past decisions against actual outcomes.

    v2.0 additions (fleet-learning hypothesis: "bleeders have positive
    gross edge eaten by fees"):
      - gross_pnl: sum of per-leg realized PnL *before* fees
      - fees_paid: total fees across all legs that closed this week
      - fee_pct_of_gross: derived — PRIMARY health metric. If >25% on
        rolling 4w, FEE_CEILING_WARNING fires separately.
      - exit_reason_counts: {agent_chosen: N, safety_stop: N, ...}
        — reveals whether the agent is in control (high agent_chosen
        count) or bleeding (high safety_stop count).
      - benchmark_buy_and_hold_pnl: what the anchor alone would have
        made. Spider must beat this to justify the basket.
    """
    fee_pct = 0.0
    if gross_pnl > 0:
        fee_pct = round((fees_paid / gross_pnl) * 100, 2)
    elif gross_pnl < 0:
        fee_pct = -1.0  # sentinel: gross negative, fee ratio undefined in the bad sense

    forced_exits = exit_reason_counts.get("safety_stop", 0) + exit_reason_counts.get("circuit_breaker", 0)
    agent_exits = sum(v for k, v in exit_reason_counts.items() if k in AGENT_CONTROLLED_EXITS)

    rec = {
        **_now(),
        "event": "ATTRIBUTION",
        "id": _new_id("attr"),
        "week_start": week_start_iso,
        "week_end": week_end_iso,
        "gross_pnl": gross_pnl,
        "fees_paid": fees_paid,
        "funding_harvested": funding_harvested,
        "realized_pnl": realized_pnl,
        "fee_pct_of_gross": fee_pct,
        "fee_ceiling_breached": fee_pct > FEE_PCT_OF_GROSS_CEILING,
        "pnl_by_leg": pnl_by_leg,
        "exit_reason_counts": exit_reason_counts,
        "forced_exits_this_week": forced_exits,
        "agent_controlled_exits_this_week": agent_exits,
        "decisions_referenced": decisions_referenced,
        "decision_quality": decision_quality,
        "overall_score": overall_score,
        "notes": notes,
    }
    if benchmark_buy_and_hold_pnl is not None:
        rec["benchmark_buy_and_hold_pnl"] = benchmark_buy_and_hold_pnl
        rec["beat_benchmark"] = realized_pnl > benchmark_buy_and_hold_pnl
    return _write(state_dir, rec)


# ── Warning events (v2.0) ────────────────────────────────────────

def write_fee_ceiling_warning(
    state_dir: str,
    window_weeks: int,
    gross_pnl_window: float,
    fees_paid_window: float,
    fee_pct_of_gross: float,
    required_action: str,
) -> Dict[str, Any]:
    """Fires when rolling 4-week fee_pct_of_gross breaches 25%.

    The agent must address this in next week's rationale — not ignorable.
    """
    return _write(state_dir, {
        **_now(),
        "event": "FEE_CEILING_WARNING",
        "id": _new_id("feewarn"),
        "window_weeks": window_weeks,
        "gross_pnl_window": gross_pnl_window,
        "fees_paid_window": fees_paid_window,
        "fee_pct_of_gross": fee_pct_of_gross,
        "ceiling_pct": FEE_PCT_OF_GROSS_CEILING,
        "required_action": required_action,
    })


def write_safety_stop_warning(
    state_dir: str,
    window_days: int,
    safety_stop_events: List[Dict[str, Any]],
    required_action: str,
) -> Dict[str, Any]:
    """Fires when >1 per-leg safety stop fires in rolling 30d.

    Signal that either entry sizing is wrong or the agent is ignoring
    score collapses until the hard stop bails it out — i.e. becoming
    a bleeder. Loud enough that the agent must acknowledge in rationale.
    """
    return _write(state_dir, {
        **_now(),
        "event": "SAFETY_STOP_WARNING",
        "id": _new_id("sswarn"),
        "window_days": window_days,
        "safety_stop_count": len(safety_stop_events),
        "events": safety_stop_events,
        "ceiling": SAFETY_STOP_30D_CEILING,
        "required_action": required_action,
    })


def write_fill_cap_warning(
    state_dir: str,
    cycle_id: Optional[str],
    fills_in_7d: int,
    soft_warning_at: int = FILLS_PER_7D_SOFT_WARNING,
    hard_cap: int = FILLS_PER_7D_HARD_CAP,
) -> Dict[str, Any]:
    """Fires when fills_in_7d crosses soft warning or hard cap.

    Hard cap does not block — it demands the agent justify every
    additional fill in its DECISION rationale. The constraint is
    behavioral, not runtime-enforced.
    """
    status = "soft_warning" if fills_in_7d < hard_cap else "hard_cap_reached"
    return _write(state_dir, {
        **_now(),
        "event": "FILL_CAP_WARNING",
        "id": _new_id("fillwarn"),
        "cycle_id": cycle_id,
        "fills_in_7d": fills_in_7d,
        "soft_warning_at": soft_warning_at,
        "hard_cap": hard_cap,
        "status": status,
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


def count_fills_in_window(state_dir: str, window_hours: float = 168.0) -> int:
    """Count EXIT events in the rolling window (fills = entries + exits, but
    EXIT is a proxy — each closed leg counts once. Entry-side fills are
    captured separately when the agent writes STRIKE or SWAP records.

    For the v2.0 12-fills/7d constraint, we count every market action:
    EXIT events + PILOT_RAMP 'ramp_up' actions + SWAP implicit entries.
    Simplest practical proxy: count all EXITs + STRIKEs + DECISION-of-type-
    SWAP_BASKET_MEMBER in window.
    """
    cutoff = time.time() - window_hours * 3600
    records = read_log(state_dir, limit=2000)
    n = 0
    for r in records:
        if r.get("ts", 0) < cutoff:
            continue
        ev = r.get("event")
        if ev == "EXIT":
            n += 1
        elif ev == "STRIKE":
            n += 1                       # anchor open
        elif ev == "PILOT_RAMP" and r.get("action") == "ramp_up":
            n += 1                       # ramp step adds a leg
        elif ev == "DECISION" and r.get("decision") in ("SWAP_BASKET_MEMBER", "FULL_REBALANCE"):
            # implicit: swap = 1 close + 1 open = 2 fills; full rebalance = many
            n += 2 if r.get("decision") == "SWAP_BASKET_MEMBER" else 6
    return n


def count_safety_stops_in_window(state_dir: str, window_days: int = 30) -> List[Dict[str, Any]]:
    """Return list of safety_stop EXIT events in rolling window."""
    cutoff = time.time() - window_days * 86400
    records = read_log(state_dir, limit=2000)
    out = []
    for r in records:
        if r.get("event") != "EXIT":
            continue
        if r.get("ts", 0) < cutoff:
            continue
        if r.get("exit_reason") == "safety_stop":
            out.append(r)
    return out


def fee_pct_rolling_4w(state_dir: str) -> Dict[str, Any]:
    """Aggregate fee % of gross over trailing 4 weeks of ATTRIBUTION records."""
    cutoff = time.time() - 28 * 86400
    records = read_log(state_dir, limit=2000)
    gross = 0.0
    fees = 0.0
    weeks = 0
    for r in records:
        if r.get("event") != "ATTRIBUTION":
            continue
        if r.get("ts", 0) < cutoff:
            continue
        gross += float(r.get("gross_pnl", 0) or 0)
        fees += float(r.get("fees_paid", 0) or 0)
        weeks += 1
    pct = round((fees / gross) * 100, 2) if gross > 0 else None
    return {
        "window_weeks": weeks,
        "gross_pnl": round(gross, 2),
        "fees_paid": round(fees, 2),
        "fee_pct_of_gross": pct,
        "ceiling_breached": (pct is not None and pct > FEE_PCT_OF_GROSS_CEILING),
    }


def warmup_status(state_dir: str, warmup_days: int = 7) -> Dict[str, Any]:
    """Summarize warmup progress for the operator dashboard."""
    records = read_log(state_dir, limit=2000)
    if not records:
        return {
            "complete": False,
            "days_elapsed": 0,
            "scans_completed": 0,
            "qualifying_strikes_seen": 0,
            "suggested_action": "boot_spider_to_begin_warmup",
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
