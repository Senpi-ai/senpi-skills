---
name: cuckoo-strategy
description: >-
  CUCKOO v1.0.0 — Copy-the-Copiers (meta-strategy follower). Auto-discovers the
  top strategies by realized performance (discovery_get_top_strategies), pulls
  each one's current positions, and trades the asset + direction the top
  performers agree on most — performance-weighted, so a stronger strategy gets
  more say (capped so one outlier can't dominate). Because the strategies it
  follows are themselves copy/algo strategies, Cuckoo copies the copiers.
  Distinct from Remora (operator-picked whales) and the individual trader-
  followers. NEW archetype #14. Let-winners-run DSL with a staleness cap.
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

# 🐦 CUCKOO v1.0.0 — Copy-the-Copiers (Meta-Strategy Follower)

**Let the best strategies do the work.** A cuckoo lays its eggs in other birds' nests. Cuckoo lets the platform's top-performing *strategies* find the trades, then rides whatever they agree on most — weighted by how well each one is actually doing.

## Why this strategy exists

Following a single trader (Remora) or a leaderboard universe (Raptor/Jackal/Spider) is one layer. Cuckoo is a layer *above* that: it follows the top-performing **strategies** — which are themselves copy/algo/trader-following strategies — so it captures the *consensus of the consensus*. When the best strategies on the platform are independently piling into the same asset+direction, that agreement is a strong, self-cleaning signal: underperformers fall out of the top-N automatically, so the pool refreshes toward whatever is working.

## CRITICAL RULES

### RULE 1: Auto-discovered, not hand-picked
Cuckoo pulls the top `topN` strategies by realized performance every tick (`discovery_get_top_strategies`). There is no operator whale list (that's Remora) — the pool is *discovered* and refreshes as performance changes.

### RULE 2: Performance-weighted consensus
Each top strategy casts one vote per `(asset, direction)` it holds above `minNotionalUsd`, weighted by `performance_weight(roi) = clamp(1 + roi/50, 0.5, weightCap)`. A flat strategy weighs 1.0; a +100% strategy hits the cap; a losing strategy floors at 0.5. The cap stops one outlier from dominating.

### RULE 3: Consensus is the gate
Votes are tallied into `(asset, direction)` candidates. Entry requires at least `minStrategies` (default 2) top strategies in agreement — one strategy's position is never enough.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. The top strategies hold conviction positions, so the DSL is the **let-winners-run** preset — wide ladder (T0 +10% / lock 0), `max_loss 18%`, time-cuts OFF except a **96h hard_timeout** staleness cap. ⚠️ Cuckoo re-evaluates the consensus each tick but does **not yet mirror an EXIT** — the hard_timeout prevents holding a stale consensus indefinitely. Exit-mirroring is a planned v1.1 enhancement.

## How Cuckoo scores a trade

**Gate:** `>= minStrategies` top strategies hold the same `(asset, direction)`.

**Score components** (max ~6):

| Signal | Points |
|---|---|
| Held by at least one top strategy | +2 |
| Consensus: 2 strategies agree | +1 |
| Consensus: 3 strategies agree | +2 |
| Consensus: 4+ strategies agree | +3 |
| Aggregate consensus weight ≥ `highWeight` (default 6.0) | +1 |

**Floor:** `minScore: 4` (so a 2-strategy consensus needs the high-weight bonus, or 3+ strategies, to clear).

## DSL preset (let-winners-run — with a staleness cap)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 10 |
| Time cuts | hard_timeout | **96h (staleness cap)** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +20/45 · +35/65 · +55/78 · +90/88 |

## Scanner pattern

Introduces the **Meta-strategy follower / copy-the-copiers** archetype (#14) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `discovery_get_top_strategies` (the producer signature; defensive multi-key unwrap of the strategy list), `leaderboard_get_trader_positions` (each top strategy's positions; unwraps the nested `data.positions.positions` shape). The pure functions (`performance_weight`, `mirror_direction`, `tally_consensus`, `consensus_score`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to follow the auto-discovered top STRATEGIES (not individual traders) and trade their performance-weighted consensus — the copy-the-copiers meta-layer, a new archetype. Let-winners-run DSL class (wide ladder, time-cuts off except a 96h staleness cap), taker-true entry, no null numeric signal fields, defensive multi-key shape unwrapping on `discovery_get_top_strategies` + `leaderboard_get_trader_positions`, and unit-tested pure functions. Exit-mirroring is a planned v1.1 enhancement.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
