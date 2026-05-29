---
name: coyote-strategy
description: >-
  COYOTE v1.0.0 — Regime Classifier / Meta-Router. Watches macro conditions
  (BTC 7d trend, BTC realized volatility, cross-asset dispersion) and
  classifies the market into TREND_UP / TREND_DOWN / CHOP. Takes LONG BTC
  in TREND_UP, SHORT BTC in TREND_DOWN, stays out in CHOP. Publishes the
  regime classification in every tick output so downstream tooling (and
  future regime-subscription runtime features) can read Coyote's view.
  NEW archetype #16: Regime classifier / meta-router.
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

# 🐺 COYOTE v1.0.0 — Regime Classifier / Meta-Router

**The agent that asks "what kind of market are we in?" before anything else.** Coyote watches macro conditions and classifies the market into TREND_UP / TREND_DOWN / CHOP. It takes a single positional expression of its regime view — and publishes that view on every tick.

## Why this strategy exists

Every Senpi agent currently re-implements its own version of "should I be active right now?" Vulture has a HEAVY_FLOW gate. Wolverine has a multi-timeframe alignment check. Tortoise just buys regardless. None of them share a *centralized* view of the macro regime — they each make local calls.

Coyote is the start of fixing that. Its classification is **emitted in every signal data block** (and in every tick output, including when no trade fires), so:
- **Today**: operators can read Coyote's regime view directly to inform manual decisions about which agents to enable/pause.
- **Soon (runtime work pending)**: other agents will be able to *subscribe* to Coyote's regime channel as a gating input, so e.g. Tortoise auto-pauses in TREND_DOWN and Stag auto-enables in TREND_UP.

The "meta-router" framing is aspirational for v1 — Coyote also takes its own regime-positional trade so it has skin in its own calls.

## CRITICAL RULES

### RULE 1: Three regimes, two trades
Coyote classifies into exactly three states:

| Regime | Trigger | Action |
|---|---|---|
| **TREND_UP** | BTC 7d move ≥ `trendUpThresholdPct` (default 5%) AND realized vol ≤ `maxVolForTrendPct` (default 80%) | LONG BTC |
| **TREND_DOWN** | BTC 7d move ≤ −`trendDownThresholdPct` (default 5%) AND realized vol ≥ `minVolForCrashPct` (default 60%) | SHORT BTC |
| **CHOP** | otherwise | no trade |

TREND_DOWN explicitly requires *high* volatility — Coyote distinguishes a "panic crash" (sharp drop + vol spike) from a "slow grind down" (small drop, low vol, which is just CHOP). This avoids shorting orderly pullbacks.

### RULE 2: Always publish the regime
Even when no trade is taken (CHOP / UNKNOWN), the producer outputs the regime classification + all three input metrics (BTC 7d move %, realized vol %, dispersion %) in its tick result. Operators can read `tail /tmp/coyote-producer.log | jq .regime` at any moment.

### RULE 3: Dispersion is informational (for now)
Cross-asset dispersion (stdev of recent returns across the universe) is computed and published but doesn't currently enter the gate logic. Future versions may use it to refine regime detection (e.g. high dispersion + flat BTC = "rotation regime"). For v1 it's there for operator visibility.

### RULE 4: Producer NEVER closes
DSL owns exits. Coyote enters at the regime transition; the DSL trail manages the position through the regime's life.

### RULE 5: Regimes are persistent — don't churn
`per_asset_cooldown_minutes: 360` (6h) and `max_entries_per_day: 2` so Coyote doesn't flip back and forth on noise. Real regimes last days or weeks; a clean classification doesn't change ticks-over-ticks.

## How Coyote computes regime

Three pure metrics, all from `market_get_asset_data` 4h candles:

- **`btc_7d_pct`** = `% change of latest BTC close vs the close 42 4h-bars ago` (7 days)
- **`realized_vol_pct`** = `stdev of log returns × sqrt(2190) × 100` (annualized, 4h bars assume 24/7 trading)
- **`dispersion_pct`** = `stdev of recent returns across {BTC, ETH, SOL, HYPE}` (cross-sectional)

Then a 4-way switch: `classify_regime(btc_move, vol, ...thresholds) → TREND_UP / TREND_DOWN / CHOP / UNKNOWN`. UNKNOWN fires when input data is missing.

## DSL preset (`balanced`)

Standard balanced. Regimes hold for days; Coyote's positional bet rides them.

## Scanner pattern

NEW archetype #16: **Regime classifier / meta-router** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP call: `market_get_asset_data` per universe asset (just for close prices). The pure functions (`pct_move`, `realized_vol_pct`, `dispersion_pct`, `classify_regime`, `regime_to_direction`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-29) — initial release

First fleet agent built around **macro-regime classification as a first-class capability**. Introduces archetype #16 (regime classifier / meta-router). Publishes regime on every tick. Takes its own positional bet so it has skin in its own classification. 14/14 unit tests covering all pure functions. Taker-true entry, disown-safe launch.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
