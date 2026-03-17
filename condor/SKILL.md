---
name: condor-strategy
description: >-
  CONDOR v1.0.1 — Multi-asset alpha hunter. Grizzly's three-mode lifecycle
  (HUNTING → RIDING → STALKING → RELOAD) across BTC, ETH, SOL, and HYPE.
  Evaluates all four every scan, commits to the single strongest thesis.
  One position at a time. Maximum conviction. Always in the best trade.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.1"
  platform: senpi
  exchange: hyperliquid
  based_on: grizzly-v2.1
---

# 🦅 CONDOR v1.0.1 — Multi-Asset Alpha Hunter

Four assets. One position. Always the strongest thesis.

## Why CONDOR Exists

Polar (+28%, ETH only), Wolverine (+0%, HYPE only), and Grizzly (BTC only) each proved that the three-mode lifecycle works on single assets. But they sit idle when their asset isn't moving. Polar waited through thousands of scans while HYPE ran 40%.

Condor watches BTC, ETH, SOL, and HYPE simultaneously. Every 3 minutes, it scores all four and commits to the single highest-conviction thesis. When that thesis dies, it evaluates all four again. Always in the best trade available.

## Three-Mode Lifecycle (Same as Grizzly)

**MODE 1 — HUNTING:** Score all 4 assets across 4 timeframes, SM positioning, funding, volume, OI, and correlation. Enter the highest score (10+ required). If none qualify, wait.

**MODE 2 — RIDING:** Monitor the active position's thesis every scan. If SM flips, 4H trend breaks, funding goes extreme, or volume dies — thesis exit immediately. If DSL closes the position — transition to MODE 3.

**MODE 3 — STALKING:** Watch the SAME asset for reload conditions (fresh impulse, volume alive, SM aligned, 4H intact). If reload passes, re-enter. If thesis dies during stalk, reset to MODE 1 and evaluate all 4 assets fresh.

## Correlation Map

| Asset | Correlation Asset | Relationship |
|---|---|---|
| BTC | ETH | Must confirm |
| ETH | BTC | Must confirm |
| SOL | BTC | Must confirm |
| HYPE | BTC | **Bonus only** — HYPE moves independently |

HYPE's BTC correlation is never a block or thesis exit signal. When BTC confirms HYPE, it's a +2 score bonus. When BTC diverges, it's ignored. The other three assets require correlation confirmation.

## DSL (Same Orca v1.1 Pattern)

Scanner outputs `dslState` per signal. Agent writes it directly as the state file. No dsl-profile.json merging.

- Phase 1: consecutiveBreachesRequired 3, dead weight 15/20/30 min by score
- Phase 2: 7/12/15/20% tiers, 40/55/75/85% locks
- Stagnation TP: 10% ROE / 45 min

## Conviction-Scaled Margin

Condor evaluates all 4 assets and picks the single best. When it enters, this is the highest-conviction trade available across the entire market. Margin scales accordingly:

| Score | Margin | Rationale |
|---|---|---|
| 10-11 | 25% | Base conviction |
| 12-13 | 35% | Strong thesis — best of 4 assets |
| 14+ | 45% | Extreme conviction — rare, maximum deployment |
| RELOAD | 35% | Thesis confirmed by parent trade |

At 10x leverage and 45% margin on a $1,000 account, that's $4,500 notional. On an 85% ROE trade (like Polar's ETH long), that's +$382. On a -30% absolute floor exit, that's -$135. The risk:reward is appropriate for a score-14 thesis that beat three other assets.

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, score, all 4 asset scores), position CLOSED, thesis exit, risk guardian. **NEVER:** Scanner found nothing, STALKING status, HUNTING status.

## Cron Architecture

| Cron | Interval | Session |
|---|---|---|
| Scanner | 3 min | main |
| DSL | 3 min | isolated |

## Files

| File | Purpose |
|---|---|
| `scripts/condor-scanner.py` | Multi-asset lifecycle scanner |
| `scripts/condor_config.py` | Self-contained config helper |
| `config/condor-config.json` | All parameters |

## License

MIT — Built by Senpi (https://senpi.ai).
