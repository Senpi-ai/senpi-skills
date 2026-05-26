---
name: chameleon-strategy
description: >-
  CHAMELEON v1.0.0 — Relative-Value / Pairs (ratio mean-reversion). When the
  price ratio between two correlated assets extends far from its recent mean
  (high |z-score|), it tends to revert. Chameleon trades the reversion via a
  directional bet on the high-beta leg (the runtime is single-position, so
  not a two-leg spread). Pairs: ETH/BTC (trade ETH), SOL/ETH (trade SOL),
  SOL/BTC (trade SOL). Mean-reversion DSL — tight "bank the snapback" ladder,
  48h hard_timeout, weak_peak_cut.
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

# 🦎 CHAMELEON v1.0.0 — Relative-Value / Pairs (Ratio Mean-Reversion)

**Trade the spread between two coins, not the market.** When ETH gets expensive relative to BTC (or SOL vs ETH), the ratio stretches — and ratios revert far more reliably than outright prices. Chameleon measures how stretched a pair's ratio is (z-score) and bets on the snap-back.

## Why this strategy exists

Correlated majors trade as a pack, but their *ratios* oscillate around a mean. A 3-sigma ETH/BTC extension is a much cleaner edge than guessing ETH's absolute direction — the pair's correlation does the work. This is the first fleet agent to trade **relative value** rather than a single asset's trend, funding, or order flow.

**Single-position note:** a textbook pairs trade is two legs (long one, short the other) for true market-neutrality. The Senpi runtime is single-position, so Chameleon instead takes a **directional bet on the high-beta leg** in the reversion direction. It captures most of the reversion edge without needing spread-level position management.

## CRITICAL RULES

### RULE 1: The z-score is the gate
For each pair (`numerator/denominator`), Chameleon computes the latest ratio's z-score vs its mean/std over `lookbackBars` 1h candles. Entry requires `|z| >= zEntryMin` (default 2.0). No extension, no trade.

### RULE 2: Which leg, which direction
It trades the configured **high-beta leg** (the more volatile asset — ETH in ETH/BTC, SOL in SOL/ETH). Direction profits from reversion:
- **z high** (numerator rich): `leg == numerator → SHORT`; `leg == denominator → LONG`
- **z low** (numerator cheap): `leg == numerator → LONG`; `leg == denominator → SHORT`

### RULE 3: Reversion must be *starting*
A bonus, not a gate, but important: the position only scores its "turning" point when `|z|` this bar is smaller than last bar — i.e., the ratio is already snapping back, not still extending. Don't catch a ratio mid-blowout.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. A ratio reversion is a **bounded** move, so the DSL is the mean-reversion preset — tight ladder (lock 30% at +5%), 48h `hard_timeout` + `weak_peak_cut`. Bank the snap-back; don't hold for a trend.

## How Chameleon scores a trade

**Gate:** `|z| >= zEntryMin` on at least one pair.

**Score components** (max ~7):

| Signal | Points |
|---|---|
| `|z| >= zEntryMin` (gate-confirmed) | +2 |
| `|z| >= zStrong` (default 3.0) | +2 |
| Ratio turning (reversion starting) | +1 |
| Smart Money on the leg confirms the direction (≥ `smTiltMinPct`) | +1 |
| Leg volume rising (> 10%) | +1 |

**Floor:** `minScore: 4`.

## DSL preset (mean-reversion — bank the snapback)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 6 |
| Time cuts | hard_timeout | **48h** |
| Time cuts | weak_peak_cut | **120 min / 2%** |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +5/30 · +10/50 · +15/65 · +25/80 · +40/90 |

## Scanner pattern

This introduces the **Relative-value / pairs** archetype — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (1h candles for *both* legs of each pair), `leaderboard_get_markets` (SM on the leg). The pure functions (`ratio_zscore`, `reversion_direction`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-22) — initial release

First fleet agent to trade relative value (ratio mean-reversion) rather than a single asset's direction. Introduces the relative-value/pairs archetype. Built with the mean-reversion DSL class (tight ladder + time-cuts on), taker-fallback entry (enter at the extreme; maker-only risks CREATE_ORDER_RESTING and a missed reversion window), no null numeric signal fields, and unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
