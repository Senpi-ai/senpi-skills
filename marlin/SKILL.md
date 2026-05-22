---
name: marlin-strategy
description: >-
  MARLIN v1.0.0 — Order-Book Imbalance Momentum. A persistent resting-depth
  imbalance in the L2 book is a near-term pressure tell: bids ≫ asks = buy
  pressure, asks ≫ bids = sell pressure. Marlin enters in the imbalance
  direction WHEN 15m momentum and Smart Money agree — it times the entry on
  the book, then holds the move (it is NOT a scalper). Universe: BTC, ETH,
  SOL, HYPE. Wide "let winners run" DSL + 24h hard_timeout.
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

# 🐟 MARLIN v1.0.0 — Order-Book Imbalance Momentum

**Time the entry on the book; hold the move.** Marlin reads the L2 order book — when resting depth is lopsided (bids ≫ asks, or the reverse), that's directional pressure. It enters in that direction *only when* short-term momentum and Smart Money agree, then hands the position to a wide DSL. It does not scalp.

## Why this strategy exists

Order-book imbalance is a genuine microstructure edge — but trading it as a per-tick scalp just bleeds fees (the fastest way to lose on perps). Marlin uses the imbalance differently: as the **entry-timing signal** on a momentum thesis. The book tells you *which side has pressure right now*; momentum and SM confirm the move is real; then you hold it with a wide stop ladder and let it work, instead of flipping in and out.

- **Bids ≫ asks** (ratio ≥ `imbalanceMin`) + 15m up + SM long → **LONG**
- **Asks ≫ bids** (ratio ≤ 1/`imbalanceMin`) + 15m down + SM short → **SHORT**

## CRITICAL RULES

### RULE 1: Imbalance picks the side, momentum + SM confirm it
The order-book ratio (bid depth / ask depth over the top `levelsN` levels) selects the candidate direction. The trade only fires if 15m momentum is moving that way **and** Smart Money agrees. A lopsided book with no momentum/SM confirmation is noise — skip it.

### RULE 2: Not a scalper — hold the move
The imbalance is entry-*timing*, not an exit signal. Marlin holds with a wide "let winners run" Phase 2 ladder; a 24h `hard_timeout` is the only time-based exit (the microstructure thesis is short-horizon, but the move itself is given room). Turning this into a per-tick scalp would erase the edge in fees.

### RULE 3: Producer enters. DSL exits.
No `close_position` call site. Phase 1 max_loss 18% + Phase 2 wide ladder + 24h hard_timeout own all exits.

### RULE 4: Universe is BTC, ETH, SOL, HYPE
Liquid majors only — thin books give unreliable imbalance readings.

## How Marlin scores a trade

**Gates** (all required):
1. Book imbalance on one side (ratio ≥ `imbalanceMin` for LONG, ≤ 1/`imbalanceMin` for SHORT)
2. 15m momentum confirms that side (≥ `momMinPct`)
3. SM direction agrees, tilt ≥ `smTiltMinPct` (default 55%)

**Score components** (max ~9):

| Signal | Points |
|---|---|
| Book imbalance (gate-confirmed) | +2 |
| Strongly imbalanced (≥ `imbalanceStrong`, default 2.5×) | +1 |
| 15m momentum confirms (gate-confirmed) | +2 |
| 5m momentum aligned | +1 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (≥ 70%) | +1 |
| Volume rising (> 10%) | +1 |

**Floor:** `minScore: 5`.

## DSL preset (wide + short-horizon outer bound)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **24h (ENABLED)** |
| Time cuts | weak_peak_cut / dead_weight_cut | DISABLED |
| Phase 2 | T0 → T5 | +10/0 · +20/25 · +30/40 · +50/60 · +75/75 · +100/85 |

## Scanner pattern

**Microstructure / order-flow** archetype (alongside Piranha) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (L2 `order_book` + candles), `leaderboard_get_markets` (SM). The pure signal functions (`book_imbalance`, `imbalance_direction`, `price_move_pct`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-22) — initial release

Second agent in the microstructure/order-flow family (with Piranha). Trades L2 order-book imbalance as entry-timing on a momentum thesis — deliberately NOT a scalper (fee discipline). Built with the per-class DSL rule: wide "let winners run" ladder + short-horizon 24h hard_timeout, taker-fallback entry, exit timeout 30s, no null numeric signal fields, and unit-tested pure signal functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
