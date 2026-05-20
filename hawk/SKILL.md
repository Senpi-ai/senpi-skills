---
name: hawk-strategy
description: >-
  HAWK v1.0.0 — 4h Breakout Buyer / Breakdown Seller. LONG when price
  breaks above the 7-day high AND Senpi Smart-Money is > 55% long.
  SHORT when price breaks below the 7-day low AND SM is > 55% short.
  Universe: BTC, ETH, SOL. Tight DSL — Phase 1 max_loss 8% (failed
  breakouts get cut fast); Phase 2 locks fast at +5% (real breakouts
  ratchet-protect immediately). hard_timeout 24h.
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

# 🦅 HAWK v1.0.0 — 4h Breakout Buyer / Breakdown Seller

**Breakouts on majors with Smart-Money confirmation.** When price breaks above the 7-day high AND top traders are net long, Hawk goes LONG. When price breaks below the 7-day low AND top traders are net short, Hawk goes SHORT. Failed breakouts get cut fast — tight Phase 1 max_loss 8%.

## Why this strategy exists

Most retail breakout strategies fail because they:
1. Don't filter for trend agreement (catching a breakout in the wrong direction)
2. Don't validate with Smart Money (catching a fake breakout into a stop-hunt)
3. Don't cut failed breakouts fast (turning small chop losses into max-pain disasters)

Hawk fixes all three:
1. **4h trend must align** with breakout direction
2. **Smart Money must be > 55% in the breakout direction** (gate, not score)
3. **DSL Phase 1 max_loss 8%** — failed breakouts close before they hurt

When a breakout DOES work, Phase 2 locks fast (+5% → 30% lock) so a winning breakout immediately starts ratchet-protecting profit.

## CRITICAL RULES

### RULE 1: Both gates required, no exceptions
- Breakout magnitude > 0 (latest close above 7d high OR below 7d low)
- SM tilt >= smTiltMinPct (default 55%) in the same direction as the breakout

If either gate fails, producer outputs `WAITING — no breakout with SM agreement`. No partial passes.

### RULE 2: Producer enters. DSL exits.
No `close_position` call site in the producer. DSL Phase 1 + Phase 2 + hard_timeout 24h own all exits.

### RULE 3: Tight DSL is intentional
Hawk's DSL is designed for breakouts:
- Phase 1 max_loss **8%** (vs Beaver's 20%) — failed breakouts must be cut fast
- Phase 2 first tier **+5% / lock 30%** — real breakouts lock profit immediately
- hard_timeout **24h** — if a breakout hasn't worked in 24h, it failed

Don't widen this DSL — it would defeat the strategy.

### RULE 4: Universe is BTC, ETH, SOL
Operators can override via `universe` in config, but the default is the three most-liquid majors. Adding low-liquidity coins to the universe will cause noisy "breakouts" that don't follow through.

## How Hawk scores a trade

**Gates** (all required):
1. Latest 1h close > max(7d closes) OR < min(7d closes)
2. SM direction agrees with breakout direction
3. SM tilt >= 55%

**Score components** (max ~9):

| Signal | Points |
|---|---|
| Breakout magnitude ≥ 1.0% | +3 |
| Breakout magnitude 0.3-1.0% | +2 |
| Breakout magnitude < 0.3% | +1 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (>= 70%) | +1 |
| 4h trend aligned with direction | +2 |
| Volume ≥ 1.5× average | +1 |

**Floor:** `minScore: 5`. Typical entry needs breakout magnitude moderate + SM aligned + 4h trend = 7.

## DSL preset (tight Hawk profile)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **8%** (tight) |
| Phase 1 | retrace_threshold | **5** (tight) |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **24h** (kills failed breakouts) |
| Time cuts | weak_peak_cut | **60min @ 3.0% min** (kills stale chop) |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | +5% / lock 30% |
| Phase 2 | T1 | +10% / lock 50% |
| Phase 2 | T2 | +20% / lock 65% |
| Phase 2 | T3 | +35% / lock 75% |
| Phase 2 | T4 | +60% / lock 85% |

## Scanner pattern

This strategy uses the **Multi-asset whitelist scanner with breakout-detection scoring** — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `market_get_asset_data` (per-asset candles), `leaderboard_get_markets` (SM direction).

## Operator install

See [README.md](README.md).

## Changelog

### v1.0.0 (2026-05-20) — initial release

First in the technical-pattern pair (Hawk = breakouts, Salamander = pullbacks). Tight DSL distinguishes from the trend-follower trio (Beaver/Heron/Hummingbird) which use Bison-pattern wide ladders. Hawk's edge requires fast cutting of failed breakouts AND fast locking of winning ones.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
