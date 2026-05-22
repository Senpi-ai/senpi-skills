---
name: egret-strategy
description: >-
  EGRET v1.0.0 — Smart-Money Divergence Fader. When the Smart-Money crowd
  is extremely concentrated one way (>= 70%) but price is NOT confirming
  (stalled or rolled over against the crowd), the crowded side is
  exhausted — Egret fades it. FADE SHORT into crowded longs price won't
  carry; FADE LONG into crowded shorts. RSI exhaustion confirms. Universe:
  BTC, ETH, SOL, HYPE. Mean-reversion class — TIGHT DSL (T0 +5% / lock 30,
  bank the snapback), MAKER-ONLY entry, time-cuts ENABLED.
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

# 🐦 EGRET v1.0.0 — Smart-Money Divergence Fader

**When the crowd is all-in and price won't follow, fade the crowd.** Egret looks for extreme Smart-Money concentration that price is *not* confirming — the setup that precedes a crowded-side unwind — and takes the other side.

## Why this strategy exists

Smart-Money concentration is usually a *confirmation* signal (the fleet's momentum agents trade *with* it). But at an **extreme** — when 70–85% of top traders are piled onto one side — it flips into a *contrarian* signal, **if price refuses to follow.** A crowd that's maximally long while price stalls or rolls over has no one left to buy; the unwind is the edge.

Egret is the mirror image of the fleet's momentum agents:
- They trade **with** SM when price confirms.
- Egret trades **against** SM when price **diverges** from an extreme crowd.

## CRITICAL RULES

### RULE 1: Crowding extreme AND price divergence — both required
SM concentration must be >= `crowdingMinPct` (default 70%) **and** price must be failing to confirm the crowd over the lookback window (crowded long but price not rising, or crowded short but price not falling). Extreme crowding *with* a confirming price is a working trend — Egret does NOT fade that.

### RULE 2: This is a fader — tuned opposite the momentum agents
Egret is mean-reversion, so its risk profile is the inverse of Badger/Hawk/Bison (the 2026-05-21 per-strategy-class rule):
- **TIGHT Phase 2 ladder** (T0 +5% / lock 30) — a fade is a *bounded* snapback; bank it fast. Holding a fade for a +50% trend would violate the thesis.
- **MAKER-ONLY entry** (`ensure_execution_as_taker: false`) — a fade is non-urgent and fee-sensitive; if the maker can't fill, the trade wasn't ripe.
- **Time-cuts ENABLED** — a fade resolves quickly or the thesis failed.

### RULE 3: Producer enters. DSL exits.
No `close_position` call site. DSL Phase 1 + tight Phase 2 + hard_timeout 48h + weak_peak_cut own all exits.

## How Egret scores a trade

**Gates** (all required):
1. SM concentration >= `crowdingMinPct` (default 70%)
2. Price diverging from the crowd over the lookback (not confirming)

**Score components** (max ~9):

| Signal | Points |
|---|---|
| SM crowded (gate-confirmed) | +2 |
| SM ultra-crowded (≥ `crowdingExtremePct`, default 80%) | +1 |
| Price clearly diverging (≥ 0.5% against the crowd) | +3 |
| Price merely stalled | +2 |
| RSI exhausted (≥ 68 for fade-short / ≤ 32 for fade-long) | +2 |
| RSI stretched (≥ 60 / ≤ 40) | +1 |

**Floor:** `minScore: 5`.

## DSL preset (tight — bank the snapback)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 6 |
| Time cuts | hard_timeout | **48h (ENABLED)** |
| Time cuts | weak_peak_cut | **120min / 2% (ENABLED)** |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | +5% / lock 30% |
| Phase 2 | T1 | +10% / lock 50% |
| Phase 2 | T2 | +15% / lock 65% |
| Phase 2 | T3 | +25% / lock 80% |
| Phase 2 | T4 | +40% / lock 90% |

## Scanner pattern

This strategy uses the **Contrarian fader** archetype (same family as Owl / Bald-eagle / Pangolin) with an SM-vs-price divergence trigger — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `leaderboard_get_markets` (SM concentration), `market_get_asset_data` (price momentum + RSI).

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-21) — initial release

Adds an SM-divergence trigger to the contrarian-fader archetype. Built with the per-strategy-class DSL rule: tight ladder + maker-only entry + enabled time-cuts (the fader profile), the deliberate inverse of the momentum agents' wide ladder + taker-fallback.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
