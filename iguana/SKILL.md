---
name: iguana-strategy
description: >-
  IGUANA v1.0.0 — XYZ Macro Index Trend. Trend-follows the broad XYZ indices
  xyz:SP500 + xyz:XYZ100. No stock-picking, no commodities — just the broad
  indices, in whichever direction has the stronger 4-day move. The simplest
  possible XYZ exposure, closest thing to an index-fund equivalent (but 24/7
  on Hyperliquid). Onboarding tier. Balanced DSL + 48h hard_timeout for
  weekend pricing-gap risk.
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

# 🦎 IGUANA v1.0.0 — XYZ Macro Index Trend

**Stock-market exposure on Hyperliquid without picking stocks.** Iguana trend-follows `xyz:SP500` + `xyz:XYZ100`. Two assets, one decision per tick. The closest thing to an index-fund equivalent, but 24/7.

## Why this strategy exists

The fleet covers XYZ from several angles: **Bobcat** picks 12 big-tech stocks; **Bald-Eagle / Kestrel** do XYZ macro multi-asset contrarian fade; **Dire** specializes in a single commodity (BRENTOIL); **Lemur / Falcon** handle pre-IPO and conversion events. None of them deliver the **simplest** equity exposure — *"I just want the broad market."* Iguana is that.

## CRITICAL RULES

### RULE 1: Indices only, no picks
Whitelist defaults to **just two assets**: `xyz:SP500` and `xyz:XYZ100`. No individual stocks, no commodities, no pre-IPO names. Iguana is the index-fund equivalent.

### RULE 2: 4-day trend strength gates entry
For each index the producer computes `% change over the last 24 × 4h bars` (4 days). The strongest move **past `minTrendPct` (default 1.5%)** is the candidate. Both indices below threshold → no signal.

### RULE 3: Direction follows the trend
LONG if the 4d move is positive, SHORT if negative. Iguana is direction-agnostic on the way in — it just trades whichever side the broad market is taking.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. DSL is **balanced** preset + **48h `hard_timeout`** to accommodate the XYZ weekend pricing-gap risk + **`weak_peak_cut` 8h/3%** to bail from flat-but-tired positions before the weekend.

## How Iguana scores a trade

**Gate:** at least one index has `|4d trend| >= minTrendPct`.

**Score components** (max ~6):

| Signal | Points |
|---|---|
| Trend past `minTrendPct` (gate-confirmed) | +3 |
| Trend past `strongTrendPct` (default 4%) | +2 |
| Volume rising > 15% | +1 |

**Floor:** `minScore: 4`.

## DSL preset (balanced — XYZ-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **48h (weekend gap)** |
| Time cuts | weak_peak_cut | **8h / 3.0** |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +5/0 · +10/40 · +18/60 · +30/75 · +50/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist, XYZ subset). Primary MCP call: `market_get_asset_data(candle_intervals=["4h"])` per index. Stateless. The pure functions (`trend_strength`, `trend_direction`, `pick_strongest_trend`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-28) — initial release

The simplest possible XYZ exposure in the fleet. No stock-picking; just broad indices in trend direction. Balanced DSL + 48h hard_timeout for weekend gap. Taker-true entry, disown-safe launch, unit-tested pure functions.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
