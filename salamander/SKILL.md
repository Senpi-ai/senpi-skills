---
name: salamander-strategy
description: >-
  SALAMANDER v1.0.0 — Pullback catcher in established 4h trends.
  LONG when 4h trend is BULLISH AND 1h price pulled back 3-7% from
  recent high AND Smart Money is > 55% long. SHORT when 4h trend is
  BEARISH AND 1h price rallied 3-7% from recent low AND SM is > 55%
  short. Universe: BTC, ETH, SOL. Wide DSL throughout — Phase 1 10%
  max_loss (pullbacks need room to develop) AND Phase 2 "let winners
  run" ladder (T0 +10% / lock 0); the resumed trend can run far.
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

# 🦎 SALAMANDER v1.0.0 — Pullback Catcher

**Buy dips in uptrends, short rallies in downtrends.** Salamander only acts when the 4h trend is already established AND there's been a 3-7% counter-move on 1h timeframe AND Smart Money agrees with the original trend direction.

## Why this strategy exists

Pullbacks in established trends are one of the highest-edge setups in trading — the trend has already proved itself, the counter-move provides a better entry, and Smart Money confirmation filters out trend-break risk.

**The 3-7% band is the sweet spot:**
- < 3% pullback: noise, not a real entry
- 3-7% pullback: tradeable counter-move within trend
- > 7% pullback: the trend may be breaking; don't catch a falling knife

Salamander requires ALL three:
1. 4h trend BULLISH (or BEARISH)
2. 1h pullback in the 3-7% band
3. Smart Money agreeing with the 4h trend direction (> 55% tilt)

If any one fails, no entry.

## CRITICAL RULES

### RULE 1: 4h trend gate, not score
The 4h trend must be NON-NEUTRAL. Salamander does not trade chop. If higher-lows-vs-lower-highs is too close to call, the producer outputs `WAITING`.

### RULE 2: Pullback band is hard
The pullback must fall in `[pullbackMinPct, pullbackMaxPct]` (default 3-7%). Outside that band, no entry. Configurable per asset volatility (smaller bands for stable majors, wider for HYPE-tier).

### RULE 3: Wide DSL throughout
- **Phase 1 max_loss 10%** (wider than Hawk's 8%) — pullbacks within a trend need a few percent of room to develop
- **Phase 2 wide "let winners run" ladder** (T0 +10% / lock 0 → T5 +100% / lock 85) — when the pullback resolves, the resumed trend can run far. The old tight T0 (+5% / lock 30) locked a floor after ~1-2% price and chopped the continuation out on the first wiggle (HYPE 40→60 post-mortem, 2026-05-21).

If you tighten Phase 1 below 10%, you'll cut legitimate pullback continuations as noise — and never re-tighten Phase 2, or you cap the very continuation Salamander exists to ride.

### RULE 4: Producer enters. DSL exits.
No close_position call site. DSL Phase 1 + Phase 2 + hard_timeout 48h own all exits.

## How Salamander scores a trade

**Gates** (all required):
1. 4h trend non-neutral
2. 1h pullback (vs prior 24h max for LONG, prior 24h min for SHORT) in [3%, 7%] band
3. SM direction agrees with 4h trend
4. SM tilt >= 55%

**Score components** (max ~9):

| Signal | Points |
|---|---|
| 4h trend aligned (gate-confirmed) | +3 |
| Pullback in 3-7% band (gate-confirmed) | +2 |
| Pullback in 4-6% midpoint sweet spot | +1 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (>= 70%) | +1 |

**Floor:** `minScore: 5`. Typical entry clears 5-7.

## DSL preset (wide throughout — Phase 1 roomy, Phase 2 let-winners-run)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **10%** (wider — pullbacks need room) |
| Phase 1 | retrace_threshold | 6 |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **48h** (pullback resolution can take time) |
| Time cuts | weak_peak_cut | 90min / 3% min |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | **+10% / lock 0** (let the continuation breathe) |
| Phase 2 | T1 | +20% / lock 25% |
| Phase 2 | T2 | +30% / lock 40% |
| Phase 2 | T3 | +50% / lock 60% |
| Phase 2 | T4 | +75% / lock 75% |
| Phase 2 | T5 | +100% / lock 85% |

## Scanner pattern

This strategy uses the **Multi-asset whitelist scanner with pullback-detection scoring** — see `senpi-trading-runtime/references/producer-patterns.md`. Primary MCP calls: `market_get_asset_data`, `leaderboard_get_markets`.

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-20) — initial release

Second in the technical-pattern pair (Hawk = breakouts, Salamander = pullbacks). Asymmetric DSL distinguishes from Hawk's tight breakout profile — pullback theses need wider Phase 1 floor.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version`. See `references/skill-attribution.md`.
