---
name: sheep-strategy
description: >-
  SHEEP v1.0.0 — Long-Only Triple-EMA-Stacked Trend. Fires LONG only when the
  fast EMA is above the slow EMA on ALL THREE timeframes (15m + 1h + 4h) for
  an asset in BTC/ETH/SOL/HYPE. Never shorts. A triple-timeframe stack is a
  visual rule a beginner can sanity-check on any chart. Onboarding tier.
  Balanced DSL.
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

# 🐑 SHEEP v1.0.0 — Long-Only Triple-EMA-Stacked Trend

**Buy crypto only when it's clearly going up across every timeframe.** Sheep fires LONG only when the fast EMA is above the slow EMA on all three timeframes (15m + 1h + 4h). Never shorts. Removes the *"what's a short?"* cognitive load.

## Why this strategy exists

Most trend agents in the fleet pick a single timeframe (Beaver/Heron/Hummingbird → 4h; Hawk → 7d breakout; Salamander → 4h pullback). Sheep insists on **agreement across three timeframes** before firing — a stricter filter, fewer trades, but each is "obviously up" by any chart reader's standard. For beginners who want a long-only trend follower without learning what shorts are, Sheep is the answer.

## CRITICAL RULES

### RULE 1: Triple stack OR nothing
For each whitelisted asset, the producer checks `ema(fast) > ema(slow)` on **15m**, **1h**, AND **4h**. By default it requires **all three** (`minStackedFrames: 3`). One timeframe stacked is not enough.

### RULE 2: LONG only
Sheep never shorts. The LLM gate hard-skips any non-LONG direction (defensive).

### RULE 3: Score boosts from spread magnitude + SM
Score = 3 (base, full stack) + bonuses for wider 4h spread (`≥1%`), wider 1h spread (`≥0.5%`), and SM aligned LONG (≥55%, +1 strong if ≥70%). Floor `minScore: 4` so a clean spread or SM agreement clears.

### RULE 4: Producer enters. DSL exits.
No `close_position` call site. DSL is the **balanced** preset — `max_loss 12%`, 72h `hard_timeout`, `weak_peak_cut` enabled at 6h/3% so flat-but-fading trends get cut. Trends extend; Sheep participates in extension while protecting against reversal.

## How Sheep scores a trade

**Gate:** triple stack confirmed (`stack_score >= minStackedFrames`).

**Score components** (max ~8):

| Signal | Points |
|---|---|
| Full triple-stack (gate-confirmed) | +3 |
| 4h fast/slow spread ≥ 1% | +1 |
| 1h fast/slow spread ≥ 0.5% | +1 |
| SM LONG ≥ smTiltMinPct (55%) | +1 |
| SM LONG ≥ smStrongTiltPct (70%) | +1 |

**Floor:** `minScore: 4`.

## DSL preset (balanced — trend-tuned)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 12% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | 72h |
| Time cuts | weak_peak_cut | **6h / 3.0** |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 → T4 | +8/0 · +15/40 · +25/60 · +40/75 · +70/85 |

## Scanner pattern

Archetype #4 (Multi-asset whitelist) — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data(candle_intervals=["15m","1h","4h"])` per asset, `leaderboard_get_markets` (SM bonus). The pure functions (`ema`, `is_stacked_bullish`, `stack_score`, `fast_slow_spread`) are unit-tested in `tests/test_signal.py`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-28) — initial release

First fleet agent to require multi-timeframe agreement before any trade fires. Long-only by design (no short logic, no LLM-gate short branch). Standard balanced DSL with `weak_peak_cut` so stalled trends get cut. Unit-tested pure functions (EMA convergence, stack score, fast/slow spread sign).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
