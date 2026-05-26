---
name: falcon-strategy
description: >-
  FALCON v1.0.0 — Conversion-Event Momentum (XYZ Pre-IPO → equity). A Pre-IPO
  Perpetual (IPOP) carries a structural funding signature (|funding| <= ~1e-7,
  max_leverage <= 5) and is price-throttled by trade.xyz Discovery Bounds. When
  the company IPOs the product CONVERTS to a standard equity perp — funding
  jumps ~100x, the leverage cap lifts, the throttle is removed — opening free
  price discovery. Falcon detects the IPOP→STANDARD flip and trades the
  post-conversion momentum. Distinct from Lemur, which trades the IPOP basket
  while it is still an IPOP. Wide let-winners-run DSL, 7d hard_timeout.
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

# 🦅 FALCON v1.0.0 — Conversion-Event Momentum (XYZ Pre-IPO → Equity)

**Trade the moment a pre-IPO name goes public.** When a company behind a Pre-IPO Perpetual (IPOP) finally IPOs, trade.xyz converts the product to a standard equity perp. The funding rate jumps ~100x, the leverage cap lifts, and the Discovery-Bounds price throttle is removed — flipping a tightly-pinned pre-listing product into a freely-discovering equity. Falcon catches that exact transition and rides the price-discovery move.

## Why this strategy exists

A pre-IPO perp is *deliberately* dampened — Discovery Bounds throttle how fast it can move, so its price hugs a slow drift. Conversion removes those guardrails all at once. The first hours and days after conversion are pure price discovery: a real spot reference arrives, leverage opens up, and the name often trends hard in one direction. That regime change is the edge, and it is **detectable** from the instrument's funding signature.

**Distinct from Lemur:** Lemur trades the IPOP basket *while it is still an IPOP* (moderate DSL, multi-day directional discovery under Discovery Bounds). Falcon does the opposite — it sits out the pre-listing phase entirely and only fires *around the conversion*, when the throttle comes off.

## CRITICAL RULES

### RULE 1: The conversion flip is the gate
Each tick Falcon classifies every `xyz:` instrument as **IPOP** (`|funding| <= ipopFundingMaxAbs` AND `max_leverage <= ipopMaxLeverageCap`) or **STANDARD** (funding normalized OR leverage cap lifted). It caches the classification and only acts when a tracked instrument **flips IPOP → STANDARD** since the last tick. A first sighting is never a flip — Falcon needs a known prior class.

### RULE 2: The conversion window
A detected flip stamps the instrument into a `conversionWindowHours` eligibility window (default 72h). Discovery momentum builds over hours-to-days, so Falcon stays eligible for the whole window — not just the single flip tick. After the window expires, the name is Bobcat's / standard-equity territory.

### RULE 3: Momentum must confirm
Inside the window, Falcon requires real directional momentum: `|move over momentumLookbackBars 1h bars| >= minMomentumPct` (default 3%). It then trades **in the momentum direction** (ride the discovery, don't fade it). No momentum → no trade, even with a fresh conversion.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. A discovery trend can run for days, so the DSL is the **let-winners-run** preset — wide ladder (T0 +10% / lock 0), `max_loss 20%`, time-cuts OFF except a **7d hard_timeout**. No `weak_peak_cut` — it would churn a slow-developing discovery move.

## How Falcon scores a trade

**Gate:** an IPOP→STANDARD flip inside the conversion window **AND** `|momentum| >= minMomentumPct`.

**Score components** (max ~8):

| Signal | Points |
|---|---|
| Inside window + momentum >= min (gate-confirmed) | +3 |
| `|momentum| >= strongMomentumPct` (default 8%) | +2 |
| Smart Money on the name confirms direction (≥ `smTiltMinPct`) | +1 |
| SM strongly tilted (≥ `smStrongTiltPct`) | +1 |
| Volume rising (> 15%) | +1 |

**Floor:** `minScore: 5`. SM is often sparse on a freshly-listed name, so SM is a **bonus, not a gate**.

## DSL preset (let-winners-run — ride the discovery)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 20% |
| Phase 1 | retrace_threshold | 12 |
| Time cuts | hard_timeout | **7d** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +10/0 · +20/40 · +35/60 · +60/75 · +100/85 |

## Scanner pattern

Extends the **Single-asset XYZ specialist** archetype with an event-detection layer — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_list_instruments(dex="xyz")` (classify + detect flips every tick — the producer signature), `market_get_asset_data` (1h candles for momentum/volume), `leaderboard_get_markets` (SM on the name). It carries a class-state cache (`read_class_state`/`write_class_state`) and a conversion-window cache (`record_conversion`/`prune_conversions`), mirroring the Badger/Piranha state-cache pattern. The pure functions (`classify_instrument`, `detect_conversion`, `momentum_pct`, `conversion_direction`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-26) — initial release

First fleet agent to trade an **instrument-lifecycle event** (the IPOP→equity conversion) rather than a price/flow signal. Built with the let-winners-run DSL class (wide ladder, time-cuts off except a 7d hard_timeout), taker-true entry (catch the discovery move; maker-only risks CREATE_ORDER_RESTING and a missed run), no null numeric signal fields, the Badger/Piranha state-cache pattern, and unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
