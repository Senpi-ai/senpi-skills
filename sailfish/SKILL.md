---
name: sailfish-strategy
description: >-
  SAILFISH v1.0.0 — Relative-Strength Rotator (Crypto Majors). Ranks
  BTC/ETH/SOL/HYPE by ~2.7d relative strength each tick and longs the leader,
  with a margin-vs-runner-up gate to avoid whipsaw on tight races. Runtime is
  single-position and producer never closes — when the held position exits
  via the DSL trail, the next tick re-evaluates and enters the new leader.
  That's the "rotation". Distinct from Chameleon (pair mean-reversion);
  Sailfish is momentum-rotation.
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

# 🐠 SAILFISH v1.0.0 — Relative-Strength Rotator (Crypto Majors)

**Always hold the strongest major.** Sailfish ranks BTC/ETH/SOL/HYPE by ~2.7-day relative strength every tick and longs the leader. When the held position eventually exits via the DSL trail, the next tick re-evaluates — and *that's* the rotation.

## Why this strategy exists

Chameleon trades **mean-reversion** between paired majors (when ratios stretch, they revert). Sailfish trades the **momentum** half of relative strength — when one major is decisively outperforming the others, leadership tends to extend. The fleet had no momentum-rotation agent; Sailfish fills it.

## CRITICAL RULES

### RULE 1: Relative strength is the rank, not the threshold
Each tick the producer computes `% change over rsLookbackBars × 4h` per asset, sorts descending. The top of the list is the candidate — but candidacy alone isn't enough (see Rule 2).

### RULE 2: Two gates protect against whipsaw
- **Leader RS gate**: the leader's *own* RS must be `>= minLeaderRsPct` (default 1.0%). A flat or negative leader (everyone's down) is no leader at all.
- **Margin gate**: leader must beat the runner-up by `>= leaderMarginPct` (default 1.5pp). A 0.2pp lead is whipsaw bait — Sailfish stays out.

### RULE 3: "Rotation" via DSL exit + re-entry
The runtime is single-position. The producer never closes. So when leadership changes while we hold the old leader, Sailfish does **nothing immediately** — the existing position's DSL trail (max_loss, retrace, weak_peak_cut at 8h/3%) eventually exits the stalled holdover, and the next tick re-evaluates. If the new leader still clears the gates, Sailfish enters it. This is intentional: rotating mid-position would mean trading taker-on-taker on every leadership change, paying spreads + fees for whipsaw. DSL-mediated rotation only fires when the old leader is genuinely done.

### RULE 4: Producer enters. DSL exits.
Same as every fleet agent. DSL is **balanced** preset + **96h `hard_timeout`** so leadership has room to extend.

## How Sailfish scores a trade

**Gate:** leader passes both RS and margin thresholds.

**Score components** (max ~6):

| Signal | Points |
|---|---|
| Leader clears both gates (gate-confirmed) | +3 |
| Leader's RS `>= 4%` | +1 |
| Margin vs runner-up `>= 3pp` | +1 |
| Re-entering after a prior DSL exit (post-rotation) | flag only |

**Floor:** `minScore: 4`.

## DSL preset (balanced — leadership-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **96h** |
| Time cuts | weak_peak_cut | **8h / 3.0** |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +8/0 · +15/40 · +25/60 · +40/75 · +70/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist) with relative-strength scoring — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP call: `market_get_asset_data(candle_intervals=["4h"])` per asset. Stateless ranking. The pure functions (`relative_strength`, `rank_assets`, `leader_above_runner_up`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent for momentum **rotation** across the majors. Margin-vs-runner-up gate prevents whipsaw on tight races. DSL-mediated rotation (no mid-position closes — leadership change is realized via the DSL trail's natural exit + Sailfish's next-tick re-evaluation). Taker-true entry, disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
