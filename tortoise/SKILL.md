---
name: tortoise-strategy
description: >-
  TORTOISE v1.0.0 — DCA Scheduler. Slow and steady wins the race. Buys a fixed
  % of budget on a strict time cadence (every intervalHours) on a small basket
  — BTC alone, or BTC/ETH/SOL. No price prediction, no scoring, no timing —
  the oldest-overdue asset wins each tick. THE most beginner-accessible trade
  in crypto. Onboarding tier. Let-winners-run DSL, 30d hard_timeout so
  accumulation compounds.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐢 TORTOISE v1.0.0 — DCA Scheduler

**Slow and steady wins the race.** Tortoise buys a fixed % of your budget on a strict time cadence — every 24 hours by default, on BTC/ETH/SOL by default. No prediction, no timing, no second-guessing. The most-overdue asset wins each tick. Everything else is silent.

## Why this strategy exists

DCA (dollar-cost averaging) is the single most accessible trade in crypto and it had no fleet representative. Every other Senpi agent makes a prediction (trend, breakout, contrarian, funding, basis, lag). Tortoise predicts nothing — it just buys on cadence. For users intimidated by "which signal, which timeframe, which side," Tortoise is the answer: *"I just want to accumulate over time without thinking about it."*

## CRITICAL RULES

### RULE 1: Cadence is the only signal
Each asset has a per-asset "last DCA timestamp" persisted in state. When `elapsed_seconds_since_last_dca >= intervalHours × 3600`, that asset is due. A never-DCA'd asset is **always due** (so a fresh setup starts buying immediately, then the cadence takes over).

### RULE 2: Most-overdue wins
If multiple assets are due in the same tick, the one whose elapsed time exceeds the interval by the most wins. Never-DCA'd assets out-rank any DCA'd asset.

### RULE 3: Always LONG
DCA = accumulate. Tortoise never shorts. The LLM gate hard-skips any non-LONG direction (defensive — the producer only emits LONG, but the gate enforces it).

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. DCA is meant to compound, so the DSL is the **let-winners-run** preset with a **30-day** `hard_timeout` — accumulated longs are only released when:
- They retrace from a peak (Phase 1 trailing),
- They breach `max_loss_pct: 15%` (Phase 1 floor),
- They hit a Phase 2 lock tier and roll back to it,
- Or they hit 30 days old (the staleness cap).

No `weak_peak_cut` — flat-but-not-failing accumulation should not be churned.

## How Tortoise sizes a buy

Every fire is the same: `margin_pct × account_value` (default 8% per buy) at the configured leverage (default 2x). No conviction tiers. The "scoring" is a fixed score=5/leverage=2x/margin=8% — confidence comes from cadence repetition, not signal magnitude.

A representative DCA program at defaults (BTC+ETH+SOL, 24h cadence, 8% margin, 2x):
- 3 buys per day total (one per asset)
- ~21 buys per week
- After 1 week: ~170% of account in *margin* (assuming none have exited). The 2x leverage gives ~340% notional exposure.
- The DSL Phase-2 ladder locks gains as positions appreciate, so old accumulation gets banked while new buys keep adding.

## DSL preset (let-winners-run — accumulation-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **30d (compound, don't churn)** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +25/50 · +50/70 · +100/85 · +200/92 |

## Scanner pattern

A **time-trigger variant** of archetype #4 (Multi-asset whitelist) — see `senpi-trading-runtime/references/producer-patterns.md`. Unlike Bison/Hedgehog/Hawk/Salamander which score price action per tick, Tortoise's "scanner" is purely a clock — `market_get_asset_data` isn't even called. Primary state: a persisted DCA-history cache (`read_dca_history` / `record_dca`). The pure functions (`seconds_since`, `is_dca_due`, `pick_next_dca_asset`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent that makes **no price prediction**. Time-trigger DCA on a small whitelist, persisted history cache for cadence tracking, always-LONG, let-winners-run DSL with a 30-day hard_timeout so accumulation compounds. Unit-tested pure functions (oldest-overdue selection + never-DCA'd-wins guarantee).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
