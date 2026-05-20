---
name: bobcat-strategy
description: >-
  BOBCAT v1.0.0 — Big Tech equity perp trend follower on Hyperliquid
  XYZ. Universe: NVDA, TSLA, AAPL, META, MSFT, GOOGL, AMZN, AMD, MU,
  INTC, TSM, ORCL. LONG OR SHORT on 4h trend + Smart Money direction.
  Standard DSL — Phase 1 15% max_loss, Phase 2 standard ladder,
  hard_timeout 48h (equities have weekend pricing gaps).
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

# 🐈 BOBCAT v1.0.0 — Big Tech Trend Follower

**Trade NVDA / TSLA / AAPL / META / MSFT / GOOGL / AMZN / AMD / MU / INTC / TSM / ORCL as perpetuals on Hyperliquid XYZ.** Same names retail already knows from brokerage accounts — except here they're perps with leverage and 23/5 trading hours.

## Why this strategy exists

Big tech is the most-traded equity sector. Hyperliquid XYZ surfaces these as perps with 23/5 trading hours (vs traditional cash-session-only). Bobcat captures directional moves with the same trend + Smart-Money confluence pattern as Beaver, but on the highest-liquidity XYZ equities instead of crypto majors.

The XYZ pricing methodology means:
- **Cash session** (9:30am - 4pm ET, M-F): oracle = spot index value
- **Extended session** (Sun 6pm ET - Fri 5pm ET, with 5-6pm ET daily gaps): oracle = futures price discounted to implied spot via the 1-hour EMA discount rate
- **Weekend gap** (Fri 5pm ET - Sun 6pm ET): no external pricing — internal oracle mechanism

Bobcat's `hard_timeout: 48h` ensures positions don't camp through the full weekend gap — the post-Friday-close → pre-Sunday-reopen window is where internal-oracle drift can accumulate.

## CRITICAL RULES

### RULE 1: Producer enters. DSL exits.
Phase 1 max_loss 15% + Phase 2 standard ladder + hard_timeout 48h own all exits.

### RULE 2: hard_timeout 48h is intentional
Equity perps have a weekly cycle (cash session opens Sunday 6pm ET). 48h cap ensures we don't hold through the full Fri-Sun gap where internal-oracle drift accumulates without external price discovery.

### RULE 3: Universe is whitelisted, not auto-discovered
Unlike Lemur (which auto-discovers IPOPs by funding signature), Bobcat uses a fixed 12-ticker whitelist. Operators add/remove via `universe` config. Default list is the 12 most-liquid US big-tech names on XYZ.

## How Bobcat scores a trade

**Gates** (all required):
1. 4h trend non-neutral
2. SM direction agrees with 4h trend
3. SM tilt >= 55%

**Score components** (max ~9):

| Signal | Points |
|---|---|
| 4h trend aligned | +3 |
| 1h trend confirms 4h | +2 |
| SM aligned (gate-confirmed) | +2 |
| SM strongly tilted (>= 70%) | +1 |

## DSL preset (standard)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 8 |
| Time cuts | hard_timeout | **48h** |
| Time cuts | weak_peak_cut | DISABLED |
| Time cuts | dead_weight_cut | DISABLED |
| Phase 2 | T0-T5 | Bison-pattern wide ladder |

## Risk guardrails

| Gate | Setting |
|---|---|
| max_entries_per_day | 4 |
| per_asset_cooldown_minutes | 240 (4h) |
| Slots | 3 |

## Operator install

See [README.md](README.md).

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

See `references/skill-attribution.md`.
