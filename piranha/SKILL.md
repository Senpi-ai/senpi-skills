---
name: piranha-strategy
description: >-
  PIRANHA v1.0.0 — Liquidation-Cascade / Forced-Flow Hunter. When open
  interest is unwinding fast (positions being force-closed) AND price is
  moving violently, that's forced flow — liquidations begetting
  liquidations. Piranha rides it: OI down + price spiking up = shorts
  squeezed → LONG; OI down + price dropping = longs liquidated → SHORT.
  A thin order book on the side price runs into confirms little resistance.
  Universe: BTC, ETH, SOL, HYPE. Wide DSL (a squeeze can extend far) +
  24h hard_timeout (forced flow is short-horizon).
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

# 🐟 PIRANHA v1.0.0 — Liquidation-Cascade / Forced-Flow Hunter

**Ride the forced flow.** When positions are being force-closed en masse, the liquidation flow pushes price hard in one direction — and the move feeds on itself until the leverage is flushed. Piranha detects that signature and rides it.

## Why this strategy exists

A liquidation cascade is the cleanest momentum in crypto: it's *forced* flow, not opinion. The signature is unmistakable — **open interest dropping fast** (positions closing) **+ a violent price move** + a volume spike + a **thinning order book** on the side price is running into. Most agents react to price; Piranha reads the *order flow underneath* it (OI velocity + book depth) to confirm the move is forced, not discretionary.

- **OI down + price spiking UP** = shorts being squeezed → ride **LONG**
- **OI down + price dropping HARD** = longs being liquidated → ride **SHORT**

At a 3-min cadence Piranha catches the *continuation* of a liquidation event (OI unwind over 15m–1h alongside a >2% move), not the microsecond cascade itself — which is the part that's actually tradeable without co-located infra.

## CRITICAL RULES

### RULE 1: OI must be unwinding — this is the whole edge
Open interest must be falling at least `oiDropMinPct` (default 3%) over the 1h window. Rising or flat OI means the move is normal discretionary flow, not forced — no entry. This is what separates Piranha from a plain momentum chaser.

### RULE 2: OI velocity has a fallback
Uses `market_get_asset_data`'s `oi_velocity` object when present; when null, self-computes the OI delta from a persisted last-OI cache (`state/oi-state.json`). A freshly-started Piranha needs one tick per asset to warm the cache.

### RULE 3: The flow must still be flowing
The 1h move sets the direction, but the **5m candle must still be moving the same way** — Piranha rides flow that's *ongoing*, not one that already reversed.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. Wide Phase 2 ladder lets a squeeze extend; Phase 1 max_loss 18% cuts a V-shaped reversal; 24h `hard_timeout` is the outer bound (forced flow resolves fast).

## How Piranha scores a trade

**Gates** (all required):
1. OI unwinding ≥ `oiDropMinPct` (default 3%) over 1h
2. |1h price move| ≥ `priceMoveMinPct` (default 2%)
3. 5m candle still moving in the 1h direction

**Score components** (max ~9):

| Signal | Points |
|---|---|
| OI unwinding (gate-confirmed) | +2 |
| OI unwinding strongly (≤ −`oiDropStrongPct`, default −6%) | +1 |
| Violent 1h move (gate-confirmed) | +2 |
| 5m acceleration | +1 |
| Book thin on the side price runs into | +1 |
| Volume spike (≥ `volSpikePct`, default 50%) | +1 |
| Smart Money aligned with the flow (≥ 55%) | +1 |

**Floor:** `minScore: 5`.

## DSL preset (wide + short-horizon outer bound)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **24h (ENABLED)** — forced flow is short-horizon |
| Time cuts | weak_peak_cut / dead_weight_cut | DISABLED |
| Phase 2 | T0 → T5 | +10/0 · +20/25 · +30/40 · +50/60 · +75/75 · +100/85 |

## Scanner pattern

This strategy introduces the **Microstructure / order-flow** archetype — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data` (candles + `asset_context.openInterest` + `oi_velocity` + L2 `order_book`), `leaderboard_get_markets` (SM alignment).

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-22) — initial release

First fleet agent to trade on order-flow microstructure (OI velocity + L2 book depth) rather than price/SM alone. Built with the per-strategy-class DSL rule: wide "let winners run" ladder (a squeeze can extend) + a short-horizon 24h hard_timeout, taker-fallback entry (forced-flow entries must fill now), exit timeout 30s, no null numeric signal fields.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
