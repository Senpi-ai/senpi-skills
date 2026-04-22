# Spider Rationale Log — Schema Reference

Every Spider event writes a typed JSON record to `${STATE_DIR}/spider-log.jsonl`. This document is the single source of truth for the event taxonomy.

## Design principles

1. **One log file, typed events** — discriminator is the `event` field
2. **Self-describing records** — field names *are* the schema, optimized for human readability
3. **Append-only** — records are never mutated; corrections write new records that reference prior ones by id
4. **Survives session clears** — lives on disk at `${STATE_DIR}/spider-log.jsonl`
5. **Counterfactuals captured** — decisions store alternatives considered + why rejected, so we can tell whether the agent *chose* vs. defaulted to top score
6. **Pathologies surface as dedicated WARNING events** (v2.0) — fee bleed, fill-rate creep, repeated forced exits all get their own first-class events

## Common fields (every record)

| Field | Type | Description |
|---|---|---|
| `ts` | float | Unix epoch seconds |
| `iso` | string | ISO 8601 UTC timestamp |
| `event` | string | Event type discriminator |
| `id` | string | Record id, prefix indicates type |

ID prefixes: `scan_`, `dec_`, `strk_`, `pilot_`, `abort_`, `warm_`, `exit_`, `attr_`, `feewarn_`, `sswarn_`, `fillwarn_`, `err_`

## Event types

### `SCAN` — daily scanner output

Always written, even when no decision follows.

```json
{
  "event": "SCAN",
  "id": "scan_a3f2c1b0e9d4",
  "cycle_id": "2026-04-22T13:00",
  "anchor_candidates": [
    {
      "asset": "HYPE",
      "score": 7.8,
      "components": {
        "arena_leaders": 8.5,
        "sm_consensus_7d_delta": 7.2,
        "funding_favorability": 6.8,
        "relative_strength_30d": 7.9
      },
      "reasons": [
        "7 of top 10 Arena ROE leaders long HYPE, avg 28% account weight",
        "SM trader count +18% over 7d on HYPE",
        "Funding 8h: -0.003% (cheap to be long)",
        "30d rel strength: +14% vs majors basket"
      ],
      "suggested_leverage": 3
    }
  ],
  "basket_candidates": [
    {
      "asset": "PEPE",
      "score": 8.1,
      "components": {
        "funding_descending": 9.2,
        "sm_rotation_negative": 7.8,
        "relative_weakness_vs_anchor": 8.5,
        "squeeze_risk_inverse": 6.0
      },
      "reasons": [
        "Funding 8h: +0.045% (longs paying ~49% APR)",
        "SM trader count -12% over 7d, short concentration rising",
        "PEPE -8% while HYPE +14% over 7d",
        "OI elevated but not extreme"
      ],
      "current_funding_8h": 0.00045,
      "est_weekly_harvest_per_$1k": 9.45
    }
  ],
  "fleet_overlap": {
    "spider_self_excluded": true,
    "by_asset": {
      "HYPE": { "long_n": 3, "short_n": 0, "net_notional_pct": 12.4 },
      "PEPE": { "long_n": 1, "short_n": 4, "net_notional_pct": -8.1 }
    }
  },
  "regime": {
    "btc_trend": "neutral",
    "btc_drawdown_48h": 2.1,
    "funding_breadth": "broadly_positive",
    "vol_regime": "normal",
    "catastrophic_flags": []
  }
}
```

### `DECISION` — agent's daily decision

The operator-facing artifact. Contains the LLM's free-text reasoning, structured counterfactuals, and (v2.0) the agent's reported `fill_budget_remaining_7d`.

```json
{
  "event": "DECISION",
  "id": "dec_b8e1d4a7f2c0",
  "cycle_id": "2026-04-22T13:00",
  "scan_id": "scan_a3f2c1b0e9d4",
  "decision": "HOLD",
  "mode": "paper",
  "portfolio_before": { "anchor": null, "basket": [], "cash_pct": 100 },
  "portfolio_intended": { "anchor": null, "basket": [], "cash_pct": 100 },
  "rationale": "Top anchor is HYPE at 7.8 — qualifying. But this is only the first qualifying scan; persistence requires 2 consecutive. Holding paper position. Will re-evaluate tomorrow.",
  "alternatives_considered": [
    { "option": "STRIKE_HYPE_LONG_3X_PILOT", "score_or_conviction": 7.8, "why_rejected": "persistence_window_unmet_only_1_qualifying_scan" },
    { "option": "STRIKE_SOL_LONG_3X_PILOT", "score_or_conviction": 6.4, "why_rejected": "below_strike_threshold_7.0" }
  ],
  "conviction": 7.0,
  "risk_overlay_applied": ["warmup_active_no_live_trades"],
  "fill_budget_remaining_7d": 12
}
```

`decision` enum: `HOLD | SWAP_BASKET_MEMBER | RESIZE | FULL_REBALANCE | GO_TO_CASH`
`mode`: `paper` (warmup) | `live`

### `STRIKE` — first-strike audit

Fires exactly once per Spider instance lifetime.

```json
{
  "event": "STRIKE",
  "id": "strk_c9f2e3a1b8d7",
  "cycle_id": "2026-04-29T13:00",
  "decision_id": "dec_d1a8e2c4f7b3",
  "anchor": {
    "asset": "HYPE",
    "side": "LONG",
    "leverage": 3,
    "target_notional": 2000,
    "pilot_notional_day_1": 1000
  },
  "persistence_window": [
    { "scan_id": "scan_e4f1c2a8b7d3", "ts": 1745832000, "top_anchor_asset": "HYPE", "top_anchor_score": 7.8 },
    { "scan_id": "scan_a3f2c1b0e9d4", "ts": 1745918400, "top_anchor_asset": "HYPE", "top_anchor_score": 8.1 }
  ],
  "regime_check": {
    "btc_drawdown_48h": 1.4,
    "funding_breadth": "broadly_positive",
    "vol_regime": "normal",
    "all_vetoes_clear": true
  },
  "rationale": "First strike. HYPE qualifying for 2 consecutive scans (7.8, 8.1), regime clear, fleet concentration moderate. Entering pilot at 50% target notional ($1000)."
}
```

### `PILOT_RAMP` — staggered entry milestone

```json
{
  "event": "PILOT_RAMP",
  "id": "pilot_d7e9c3a1f5b2",
  "cycle_id": "2026-04-30T13:00",
  "pilot_day": 2,
  "target_anchor_pct": 0.75,
  "target_basket_pct": 0.25,
  "thesis_check": {
    "anchor_pnl_pct": 1.8,
    "anchor_score_current": 8.0,
    "anchor_score_at_entry": 8.1,
    "thesis_status": "confirming"
  },
  "action": "ramp_up"
}
```

`thesis_status`: `confirming` | `ambiguous` | `broken`
`action`: `ramp_up` | `hold_pilot` | `abort`

### `ABORT` — first-strike abort

```json
{
  "event": "ABORT",
  "id": "abort_e1c8a3d7f9b4",
  "cycle_id": "2026-04-30T13:00",
  "decision_id": "dec_d1a8e2c4f7b3",
  "reason": "anchor_drawdown_5.8_pct_exceeds_5_pct_trigger",
  "pnl_at_abort": -58.0,
  "warmup_reset_days": 3
}
```

### `WARMUP_END` — paper-trading phase complete

```json
{
  "event": "WARMUP_END",
  "id": "warm_f5b2c8e3a1d7",
  "days_observed": 7,
  "paper_decisions_summary": {
    "total_scans": 7,
    "days_with_qualifying_anchor": 4,
    "most_picked_anchor": "HYPE",
    "most_picked_basket_members": ["PEPE", "WIF", "BONK", "SHIB"],
    "would_have_struck_n_times": 1
  },
  "threshold_calibration": {
    "top_score_max": 8.1,
    "top_score_p50": 6.8,
    "top_score_p10": 5.4,
    "suggested_threshold_adjustment": "current_7.0_threshold_appropriate"
  }
}
```

### `EXIT` — leg close (NEW in v2.0)

Every leg close writes one. The `exit_reason` discriminates agent-controlled (good) from forced (bad). Repeated `safety_stop` exits trigger `SAFETY_STOP_WARNING`.

```json
{
  "event": "EXIT",
  "id": "exit_a7f3c8e2d1b9",
  "cycle_id": "2026-05-06T13:00",
  "leg_role": "basket_member",
  "asset": "PEPE",
  "side": "SHORT",
  "entry_ts": 1746028800,
  "exit_ts": 1746547200,
  "hold_duration_hours": 144.0,
  "entry_notional": 200,
  "exit_notional": 188,
  "realized_pnl": 12.40,
  "funding_accrued": 14.20,
  "fees_paid": 1.80,
  "exit_reason": "funding_flip",
  "forced": false,
  "exit_rationale": "PEPE 8h funding flipped from +0.045% to -0.012% over 24h. Carry thesis broken. Closing leg per daily decision loop, will reselect basket member from candidate slate."
}
```

`exit_reason` enum:
- **Agent-controlled** (good): `agent_chosen`, `thesis_break`, `funding_flip`, `weekly_review`, `min_hold_expiry`
- **Forced** (bad): `safety_stop`, `circuit_breaker`

### `ATTRIBUTION` — weekly retrospective (v2.0 expanded)

Written every Sunday 15:00 UTC. v2.0 adds gross/fees/funding/net breakdown, `fee_pct_of_gross` as primary health metric, exit-reason counts, and benchmark comparison.

```json
{
  "event": "ATTRIBUTION",
  "id": "attr_a8c3d1e7f2b9",
  "week_start": "2026-04-22T00:00:00Z",
  "week_end": "2026-04-29T00:00:00Z",
  "gross_pnl": 102.50,
  "fees_paid": 12.30,
  "funding_harvested": 28.40,
  "realized_pnl": 118.60,
  "fee_pct_of_gross": 12.0,
  "fee_ceiling_breached": false,
  "pnl_by_leg": {
    "HYPE_LONG": 142.50,
    "PEPE_SHORT": -18.30,
    "WIF_SHORT": -22.10,
    "BONK_SHORT": -8.00,
    "SHIB_SHORT": -9.90
  },
  "exit_reason_counts": {
    "agent_chosen": 0,
    "thesis_break": 1,
    "funding_flip": 2,
    "weekly_review": 0,
    "safety_stop": 0,
    "circuit_breaker": 0,
    "min_hold_expiry": 0
  },
  "forced_exits_this_week": 0,
  "agent_controlled_exits_this_week": 3,
  "decisions_referenced": ["dec_d1a8e2c4f7b3", "dec_e9f2c1a8b7d4", "dec_b8e1d4a7f2c0"],
  "decision_quality": [
    {
      "decision_id": "dec_d1a8e2c4f7b3",
      "retrospective_score_0_10": 8.5,
      "what_was_right": "HYPE thesis intact, +14% over hold period, basket harvest covered fees",
      "what_was_wrong": "PEPE basket member squeezed +12% mid-week, partial loss on that leg",
      "would_repeat": true
    }
  ],
  "overall_score": 7.8,
  "benchmark_buy_and_hold_pnl": 110.20,
  "beat_benchmark": true,
  "notes": "Anchor outperformed buy-and-hold benchmark by 7.6% (Spider 118.60 vs benchmark 110.20). Basket bled net but funding harvest covered. Fee pct healthy at 12%. No forced exits."
}
```

### `FEE_CEILING_WARNING` — fee bleed alert (NEW in v2.0)

Fires when rolling 4-week `fee_pct_of_gross` breaches 25%. Agent must address in next week's rationale.

```json
{
  "event": "FEE_CEILING_WARNING",
  "id": "feewarn_b8c4e1d7a3f2",
  "window_weeks": 4,
  "gross_pnl_window": 412.30,
  "fees_paid_window": 118.90,
  "fee_pct_of_gross": 28.84,
  "ceiling_pct": 25.0,
  "required_action": "agent_must_address_fee_pathology_in_next_DECISION_rationale_propose_concrete_remediation"
}
```

### `SAFETY_STOP_WARNING` — repeated forced exit alert (NEW in v2.0)

Fires when >1 per-leg `safety_stop` fires in rolling 30d.

```json
{
  "event": "SAFETY_STOP_WARNING",
  "id": "sswarn_c7e2a8d1f4b9",
  "window_days": 30,
  "safety_stop_count": 2,
  "events": [
    { "exit_id": "exit_x1", "asset": "WIF", "exit_pnl": -50.0, "ts": 1746028800 },
    { "exit_id": "exit_x2", "asset": "BONK", "exit_pnl": -50.0, "ts": 1746288000 }
  ],
  "ceiling": 1,
  "required_action": "agent_must_review_entry_sizing_and_score_collapse_handling_in_next_DECISION_rationale"
}
```

### `FILL_CAP_WARNING` — turnover creep alert (NEW in v2.0)

Fires when fills_in_7d crosses soft warning (8) or hard cap (12).

```json
{
  "event": "FILL_CAP_WARNING",
  "id": "fillwarn_d9c1e3a7b2f4",
  "cycle_id": "2026-05-06T13:00",
  "fills_in_7d": 11,
  "soft_warning_at": 8,
  "hard_cap": 12,
  "status": "soft_warning"
}
```

`status`: `soft_warning` | `hard_cap_reached`

### `ERROR` — non-fatal errors

```json
{
  "event": "ERROR",
  "id": "err_c4f7a1e8d2b9",
  "cycle_id": "2026-04-22T13:00",
  "where": "anchor_scanner.fetch_arena_leaders",
  "message": "arena_leaderboard returned 0 active traders, below floor of 150",
  "fatal": false,
  "context": { "fallback_taken": "sm_consensus_only_with_reduced_conviction" }
}
```

## Helper API (`spider_rationale_log.py`)

```python
from spider_rationale_log import (
    write_scan, write_decision, write_strike, write_pilot_ramp,
    write_abort, write_warmup_end, write_exit, write_attribution,
    write_fee_ceiling_warning, write_safety_stop_warning,
    write_fill_cap_warning, write_error,
    read_log, filter_events,
    count_fills_in_window,
    count_safety_stops_in_window,
    fee_pct_rolling_4w,
    warmup_status,
)

# Read recent events
records = read_log(STATE_DIR, limit=500)

# Filter by type
all_decisions = filter_events(records, "DECISION")
all_exits = filter_events(records, "EXIT")

# Pre-decision health checks (call before every DECISION write)
fills_7d = count_fills_in_window(STATE_DIR, window_hours=168)
safety_stops_30d = count_safety_stops_in_window(STATE_DIR, window_days=30)
fee_health = fee_pct_rolling_4w(STATE_DIR)

# Operator dashboard
status = warmup_status(STATE_DIR, warmup_days=7)
```

## Operator review patterns

**Daily during warmup:**
```bash
tail -1 ${STATE_DIR}/spider-log.jsonl | jq '.rationale'
```

**Strike-readiness check:**
```bash
grep '"event": "SCAN"' ${STATE_DIR}/spider-log.jsonl \
  | jq -r '"\(.iso) top=\(.anchor_candidates[0].asset) score=\(.anchor_candidates[0].score)"' \
  | tail -7
```

**Weekly grade:**
```bash
grep '"event": "ATTRIBUTION"' ${STATE_DIR}/spider-log.jsonl \
  | jq '{week: .week_start, gross: .gross_pnl, fees: .fees_paid, fee_pct: .fee_pct_of_gross, net: .realized_pnl, beat_bench: .beat_benchmark, score: .overall_score}'
```

**Health-warning watch (the v2.0 monitors):**
```bash
grep -E '"event": "(FEE_CEILING_WARNING|SAFETY_STOP_WARNING|FILL_CAP_WARNING)"' \
  ${STATE_DIR}/spider-log.jsonl | jq '{ts: .iso, event: .event, action: .required_action}'
```

**Exit-reason distribution (forced vs agent-controlled):**
```bash
grep '"event": "EXIT"' ${STATE_DIR}/spider-log.jsonl \
  | jq -r '.exit_reason' | sort | uniq -c | sort -rn
```

If `safety_stop` or `circuit_breaker` lines show up here, Spider is bleeding — investigate immediately.
