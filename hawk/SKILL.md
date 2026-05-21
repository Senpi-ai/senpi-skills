---
name: hawk-strategy
description: >-
  HAWK v1.0.0 — 4h Breakout Buyer / Breakdown Seller. LONG when price
  breaks above the 7-day high AND Senpi Smart-Money is > 55% long.
  SHORT when price breaks below the 7-day low AND SM is > 55% short.
  Universe: BTC, ETH, SOL. Asymmetric DSL — Phase 1 max_loss 8% (failed
  breakouts get cut fast) but Phase 2 wide "let winners run" ladder
  (T0 +10% / lock 0); a real breakout can run far. hard_timeout 24h.
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

When a breakout DOES work, Phase 2 uses a wide "let winners run" ladder — no profit lock until +10%, then a trailing ratchet out to +100%. A real breakout can run far; the old +5% / lock-30 design chopped the position out on the first pullback. Phase 1's 8% max_loss still cuts a FAILED breakout fast.

## CRITICAL RULES

### RULE 1: Both gates required, no exceptions
- Breakout magnitude > 0 (latest close above 7d high OR below 7d low)
- SM tilt >= smTiltMinPct (default 55%) in the same direction as the breakout

If either gate fails, producer outputs `WAITING — no breakout with SM agreement`. No partial passes.

### RULE 2: Producer enters. DSL exits.
No `close_position` call site in the producer. DSL Phase 1 + Phase 2 + hard_timeout 24h own all exits.

### RULE 3: Asymmetric DSL — tight Phase 1, wide Phase 2
Hawk's DSL is designed for breakouts:
- Phase 1 max_loss **8%** (vs Beaver's 20%) — failed breakouts must be cut fast
- Phase 2 wide **"let winners run" ladder** (T0 +10% / lock 0 → T5 +100% / lock 85) — a real breakout can run far; the old tight T0 (+5% / lock 30) locked a floor after ~1-2% price and chopped winners out on the first pullback (HYPE 40→60 post-mortem, 2026-05-21)
- hard_timeout **24h** — if a breakout hasn't worked in 24h, it failed

Keep Phase 1 tight (cut failed breakouts), but never re-tighten Phase 2 — the rare breakout that runs is what pays for the strategy.

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

## DSL preset (asymmetric Hawk profile — tight Phase 1, wide Phase 2)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | **8%** (tight) |
| Phase 1 | retrace_threshold | **5** (tight) |
| Phase 1 | consecutive_breaches | 1 |
| Time cuts | hard_timeout | **24h** (kills failed breakouts) |
| Time cuts | weak_peak_cut | **60min @ 3.0% min** (kills stale chop) |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0 | **+10% / lock 0** (let it breathe) |
| Phase 2 | T1 | +20% / lock 25% |
| Phase 2 | T2 | +30% / lock 40% |
| Phase 2 | T3 | +50% / lock 60% |
| Phase 2 | T4 | +75% / lock 75% |
| Phase 2 | T5 | +100% / lock 85% |

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
