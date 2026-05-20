---
name: hedgehog-strategy
description: >-
  HEDGEHOG v1.0.0 — Equal-weight major-crypto basket (BTC + ETH + SOL).
  Each asset evaluated independently via SM-confirmed 4h trend (long OR
  short). Up to 3 simultaneous positions, one per asset. Per-position
  DSL — one position going wrong doesn't drag down the others. The
  diversification answer for users who want "exposure to crypto majors"
  without picking a single coin.
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

# 🦔 HEDGEHOG v1.0.0 — Major Crypto Basket

**BTC + ETH + SOL equal-weight, each independently directional.** When BTC is trending and SM agrees, long it. When ETH is trending the other way and SM agrees, short it. Three slots, three independent positions — no forced correlation.

## Why this strategy exists

Most basket strategies force the same direction across all assets ("long-only crypto basket"). Hedgehog doesn't. Each asset gets its own SM-confirmed 4h-trend signal. If BTC is bullish and ETH is bearish, Hedgehog holds BTC long AND ETH short simultaneously. The diversification benefit is real because the per-asset directions are independent.

For new users, this answers: "I want exposure to crypto majors but don't want to pick one." Hedgehog handles the picking — you provide the capital.

## How Hedgehog operates

Same scoring as Beaver, applied per-asset:

**Per-asset gates** (all required):
1. 4h trend non-neutral
2. SM direction agrees with 4h trend
3. SM tilt >= 55%

**Per-asset score** (max ~9):

| Signal | Points |
|---|---|
| 4h trend aligned | +3 |
| 1h trend confirms | +2 |
| SM aligned | +2 |
| SM strongly tilted (>= 70%) | +1 |

Producer iterates universe each tick, takes the highest-scoring qualifying asset that isn't already held, emits ONE signal per tick (max). Over several ticks the basket fills up to 3 positions.

## CRITICAL RULES

### RULE 1: Producer enters. DSL exits. Per-position.
Each position has its own DSL (managed by the runtime's position_tracker). One position hitting Phase 1 max_loss doesn't trigger exits on the others.

### RULE 2: 10% margin per leg (not per total)
With up to 3 simultaneous positions at 10% each, max committed margin = 30% of equity. The other 70% stays in reserve.

### RULE 3: No forced rebalancing
Hedgehog opens positions when the gates pass. It doesn't force-close to "rebalance" — DSL exits each position on its own merits.

## DSL preset (standard)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% (per position) |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | 48h |
| Phase 2 | Bison-pattern wide ladder | |

## Risk guardrails

| Gate | Setting |
|---|---|
| Slots | 3 |
| max_entries_per_day | 4 |
| per_asset_cooldown_minutes | 240 |
| daily_loss_limit_pct | 15 |
| drawdown_halt_pct | 20 |

## Operator install

See [README.md](README.md).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

See `references/skill-attribution.md`.
