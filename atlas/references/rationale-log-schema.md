# Atlas Rationale Log — Schema Reference

Every Atlas event writes a typed JSON record to `${STATE_DIR}/atlas-log.jsonl`. This document is the single source of truth for the event taxonomy.

## Design principles

1. **One log file, typed events** — discriminator is the `event` field
2. **Self-describing records** — field names *are* the schema, optimized for human readability
3. **Append-only** — records are never mutated; corrections write new records that reference prior ones by id
4. **Survives session clears** — lives on disk at `${STATE_DIR}/atlas-log.jsonl`
5. **Counterfactuals captured** — decisions store alternatives considered + why rejected, so we can tell whether the agent *chose* vs. defaulted to top score

## Common fields (every record)

| Field | Type | Description |
|---|---|---|
| `ts` | float | Unix epoch seconds |
| `iso` | string | ISO 8601 UTC timestamp |
| `event` | string | Event type discriminator |
| `id` | string | Record id, prefix indicates type (`scan_`, `dec_`, `strk_`, `pilot_`, `abort_`, `warm_`, `attr_`, `err_`) |

## Event types

### `SCAN` — daily scanner output

Always written, even when no decision follows. Captures the inputs the agent saw on a given day.

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
    "atlas_self_excluded": true,
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

The operator-facing artifact. Contains the LLM's free-text reasoning AND structured counterfactuals.

```json
{
  "event": "DECISION",
  "id": "dec_b8e1d4a7f2c0",
  "cycle_id": "2026-04-22T13:00",
  "scan_id": "scan_a3f2c1b0e9d4",
  "decision": "HOLD",
  "mode": "paper",
  "portfolio_before": {
    "anchor": null,
    "basket": [],
    "cash_pct": 100
  },
  "portfolio_intended": {
    "anchor": null,
    "basket": [],
    "cash_pct": 100
  },
  "rationale": "Top anchor is HYPE at 7.8 — qualifying. But this is only the first qualifying scan; persistence requires 2 consecutive. Holding paper position. Will re-evaluate tomorrow. Note: 3 Predators already long HYPE, would cap leverage at 2x even on strike.",
  "alternatives_considered": [
    {
      "option": "STRIKE_HYPE_LONG_3X_PILOT",
      "score_or_conviction": 7.8,
      "why_rejected": "persistence_window_unmet_only_1_qualifying_scan"
    },
    {
      "option": "STRIKE_SOL_LONG_3X_PILOT",
      "score_or_conviction": 6.4,
      "why_rejected": "below_strike_threshold_7.0"
    }
  ],
  "conviction": 7.0,
  "risk_overlay_applied": ["warmup_active_no_live_trades"]
}
```

`decision` enum: `HOLD | SWAP_BASKET_MEMBER | RESIZE | FULL_REBALANCE | GO_TO_CASH`

`mode`: `paper` (warmup) | `live`

### `STRIKE` — first-strike audit

Fires exactly once per Atlas instance lifetime. The forensic record of the agent's first real trade.

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
  "rationale": "First strike. HYPE qualifying for 2 consecutive scans (7.8, 8.1), regime clear, fleet concentration moderate (3 Predators long, leverage cap not triggered at 3x). Entering pilot at 50% target notional ($1000) per cold-start protocol. Will reassess at day 2 for ramp-up to 75% + open basket at 25% hedge."
}
```

### `PILOT_RAMP` — staggered entry milestone

One record per pilot day (1, 2, 3). Day 4+ enters steady state — no `PILOT_RAMP` records after that.

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

Closes the pilot, returns to GO_TO_CASH, resets warmup.

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

Marks transition from paper to live-eligible. Single record per Atlas lifetime (unless reset by ABORT).

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

### `ATTRIBUTION` — weekly retrospective

Written every Sunday 15:00 UTC. Scores past decisions against actual outcomes. References prior decisions by id but never mutates them.

```json
{
  "event": "ATTRIBUTION",
  "id": "attr_a8c3d1e7f2b9",
  "week_start": "2026-04-22T00:00:00Z",
  "week_end": "2026-04-29T00:00:00Z",
  "realized_pnl": 84.20,
  "pnl_by_leg": {
    "HYPE_LONG": 142.50,
    "PEPE_SHORT": -18.30,
    "WIF_SHORT": -22.10,
    "BONK_SHORT": -8.00,
    "SHIB_SHORT": -9.90
  },
  "funding_harvested": 28.40,
  "fees_paid": 12.30,
  "decisions_referenced": ["dec_d1a8e2c4f7b3", "dec_e9f2c1a8b7d4", "dec_b8e1d4a7f2c0"],
  "decision_quality": [
    {
      "decision_id": "dec_d1a8e2c4f7b3",
      "retrospective_score_0_10": 8.5,
      "what_was_right": "HYPE thesis intact, +14% over hold period, basket harvest covered fees with margin",
      "what_was_wrong": "PEPE basket member squeezed +12% mid-week, partial loss on that leg",
      "would_repeat": true
    }
  ],
  "overall_score": 7.8,
  "notes": "Anchor outperformed buy-and-hold benchmark by 0.4% (Atlas 8.4% vs benchmark 8.0%). Basket bled net but funding harvest covered. Net result validates the structure for week 1."
}
```

### `ERROR` — non-fatal errors

Logged for review. Fatal errors should also raise.

```json
{
  "event": "ERROR",
  "id": "err_c4f7a1e8d2b9",
  "cycle_id": "2026-04-22T13:00",
  "where": "anchor_scanner.fetch_arena_leaders",
  "message": "arena_leaderboard returned 0 active traders, below floor of 150",
  "fatal": false,
  "context": {
    "fallback_taken": "sm_consensus_only_with_reduced_conviction"
  }
}
```

## Helper API (`atlas_rationale_log.py`)

```python
from atlas_rationale_log import (
    write_scan, write_decision, write_strike,
    write_pilot_ramp, write_abort, write_warmup_end,
    write_attribution, write_error,
    read_log, filter_events, warmup_status,
)

# Read recent events
records = read_log(STATE_DIR, limit=500)

# Filter by type
all_decisions = filter_events(records, "DECISION")

# Operator dashboard summary
status = warmup_status(STATE_DIR, warmup_days=7)
# => {complete: bool, days_elapsed: float, scans_completed: int,
#     qualifying_strikes_seen: int, suggested_action: str}
```

## Operator review patterns

**Daily during warmup:**
```bash
tail -1 ${STATE_DIR}/atlas-log.jsonl | jq '.rationale'
```

**Strike-readiness check:**
```bash
grep '"event": "SCAN"' ${STATE_DIR}/atlas-log.jsonl \
  | jq -r '"\(.iso) top=\(.anchor_candidates[0].asset) score=\(.anchor_candidates[0].score)"' \
  | tail -7
```

**Weekly grade:**
```bash
grep '"event": "ATTRIBUTION"' ${STATE_DIR}/atlas-log.jsonl \
  | jq '{week: .week_start, pnl: .realized_pnl, score: .overall_score, notes: .notes}'
```
