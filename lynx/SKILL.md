---
name: lynx-strategy
description: >-
  LYNX v1.0.0 — Adaptive MIN_SCORE Self-Tuner. The Vulture v4.1 story
  productized into a first-class agent pattern. Runs a simple BTC/ETH/SOL/HYPE
  momentum scorer; every 6h pulls its own closed-trade history via
  audit_query, buckets trades by score, and auto-raises MIN_SCORE if any
  bucket at-or-above the floor is bleeding. The same operation Vulture's
  agent ran by hand, but on a scheduled cron. NEW archetype #15:
  Self-tuning / adaptive-threshold agent.
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

# 🐯 LYNX v1.0.0 — Adaptive MIN_SCORE Self-Tuner

**The Vulture v4.1 story productized.** Lynx is a momentum agent that audits its own closed-trade history every 6 hours and raises its own MIN_SCORE when low-conviction buckets bleed.

## Why this strategy exists

When Vulture's autonomous agent ran the [30-trade audit that led to v4.1](https://github.com/Senpi-ai/senpi-skills/pull/337), it manually identified that Score 7–8 trades had been net-negative and raised `MIN_SCORE` 7→9 by hand. The fix worked. The question Lynx asks: *why is that operation manual?*

Lynx bakes the same operation into a scheduled cron. Every `auditEverySec` (default 6h), it:

1. Pulls its own closed-trade history via `audit_query(user_ids=[senpiUserId], action_type="close", limit=auditLimit)`
2. Parses the entry score from each trade's `ai_reasoning` field
3. Buckets trades by score and computes `{n, avg_roe_pct, win_rate_pct}` per bucket
4. Identifies the **highest-scoring bucket at or above the current floor** that has `n >= minBucketN` AND `avg_roe_pct < bucketBleedThresholdPct`
5. If found, recommends `MIN_SCORE = that_bucket + 1` (capped at `maxMinScore`)
6. If the recommendation moves by at least the hysteresis (1), persists the new MIN_SCORE and logs the adjustment

This is the **first fleet agent that modifies its own behavior based on its own trade history**. The same loop the Vulture team ran by hand — but every 6 hours, automatically, on every Lynx instance.

## CRITICAL RULES

### RULE 1: Self-tuning is gated on `senpiUserId`
The audit requires `audit_query` with the operator's Senpi user ID. If `config.senpiUserId` is empty, Lynx **still runs** (using `initialMinScore`) but the audit is skipped and the tick output notes `"no senpiUserId — self-tuning disabled"`. The operator can find their ID via `user_get_me` or `arena_leaderboard`.

### RULE 2: Lynx never *lowers* its own MIN_SCORE
The `should_update_threshold` function only acts on raises (hysteresis ≥ +1). A floor that has worked won't be relaxed by a quiet period. Lynx ratchets up; it never ratchets down.

### RULE 3: The cap is hard
`maxMinScore` (default 7) is an absolute ceiling. Even if every bucket up to 7 bleeds, Lynx won't go above 7 — at which point the operator should diagnose the underlying scoring logic rather than keep raising the floor.

### RULE 4: Adjustments are logged forever
Each successful raise is appended to `state.adjustments` with timestamp, previous and new floor, bleeding-bucket evidence, and the audit's trade-count. The state file is the audit trail.

### RULE 5: Producer NEVER closes
DSL owns exits. Lynx's innovation is in **entry selection**, not exit management — exits use the proven `let_winners_run` ladder.

## How Lynx scores a trade

Composite of 4 components (max ~8):

| Component | Points |
|---|---|
| 4h trend strength: `|move| >= 4%` → +3, `>= 2%` → +2, `>= 1%` → +1 | up to 3 |
| 1h confirmation: 1h move aligned with 4h direction, `|move| >= 0.5%` | +2 |
| Smart Money aligned: SM tilts in direction, `>= smTiltMinPct` (55%) | +2 |
| Volume rising: 12h vol vs 36h baseline ratio `>= 1.3` | +1 |

Initial `MIN_SCORE: 4` (permissive — gives the audit data to work with). Auto-tunes up to `maxMinScore: 7`. Operators see the current MIN_SCORE in every tick's output (`currentMinScore` field) — so they can watch Lynx learn live.

## DSL preset (`let_winners_run` — momentum-class)

Standard momentum-class let-winners-run: max_loss 20%, retrace 12, 7d hard_timeout, no weak_peak_cut, Phase 2 ladder +10/0 → +20/25 → +35/50 → +60/70 → +100/85. The Lynx innovation is in entry, not exit.

## Scanner pattern

NEW archetype #15: **Self-tuning / adaptive-threshold agent** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` per whitelisted asset (the producer signature), `leaderboard_get_markets` (SM gate), `audit_query` (the self-tuning audit — every 6h). The pure functions (`parse_score_from_reasoning`, `compute_bucket_stats`, `recommend_min_score`, `should_update_threshold`, `pct_move`, `trend_direction`, `lynx_score`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent to **modify its own behavior based on its own trade history**. Productizes the Vulture v4.1 manual cull as a scheduled audit. Introduces archetype #15 (self-tuning agent). 19/19 unit tests covering both the self-tuning logic (parse_score, compute_bucket_stats, recommend_min_score, should_update_threshold) and the scoring logic (pct_move, trend_direction, lynx_score). Taker-true entry, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
