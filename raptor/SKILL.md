---
name: raptor-strategy
description: >-
  RAPTOR v1.0 — Momentum Event Confluence Scanner. Combines Tier 2 momentum
  events ($5.5M+ delta PnL, 155/day) filtered by trader quality (TCS ELITE/RELIABLE,
  TRP SNIPER/AGGRESSIVE) with SM leaderboard confirmation. Enters when a proven
  trader crosses a profit threshold AND smart money concentration is building in
  that asset. Two independent signals, one entry. Expected 3-5 trades per day.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# 🦅 RAPTOR v1.0 — Momentum Event Confluence Scanner

Enter when proven traders start making real money AND smart money confirms.

## Why RAPTOR Exists

Every profitable scanner in our arena uses one signal source: the SM leaderboard (`leaderboard_get_markets`). Orca watches rank climbing. Bison watches SM alignment with trend. Polar checks SM concentration before entering. They all work — but they all share the same blind spot: they can't distinguish between SM accumulation that leads to a move and SM accumulation that goes nowhere.

Hyperfeed has a second signal source that no scanner uses: **momentum events** (`leaderboard_get_momentum_events`). These fire when a trader's delta PnL crosses a significance threshold — meaning someone is actively making real money right now. Not positioning. Not accumulating. Profiting.

RAPTOR combines both:
- Momentum event says: "a proven trader just made $5.5M+ on this asset"
- Leaderboard says: "SM concentration is building in this asset"
- Together: high-conviction entry with two independent confirmations

## Signal Pipeline

```
Every 90 seconds:

1. Fetch Tier 2 momentum events (last 10 min)
   └─ 155/day average (~1 per 10 min)

2. Filter by trader quality
   └─ TCS: ELITE or RELIABLE (consistent performers)
   └─ TRP: SNIPER, AGGRESSIVE, or BALANCED (active risk-takers)
   └─ Concentration: >0.5 (gains concentrated, not spread thin)
   └─ ~30-40 qualify per day

3. Cross-reference with SM leaderboard
   └─ Asset must be rank 6-30 (not too early, not too late)
   └─ contribution_pct_change_4h must be positive (SM building)
   └─ 20+ traders (reliable SM signal)
   └─ Direction must match (trader and SM agree)
   └─ ~3-5 signals per day

4. Score and enter best signal
```

## Why Tier 2, Not Tier 1

| Tier | Threshold | Frequency | Signal Quality |
|---|---|---|---|
| 1 (Exceptional) | $2M+ | 5,123/day | Too frequent — noise |
| 2 (Rare) | $5.5M+ | 155/day | Usable filter — proven conviction |
| 3 (Extreme) | $10M+ | ~0/day | Too rare — misses everything |

Tier 2 at 155/day means roughly 6-7 events per hour. After TCS/TRP/concentration filtering, ~1-2 per hour. After leaderboard cross-reference, ~0.2 per hour (3-5 per day). That's the frequency we want.

## Trader Quality Filters

| Filter | Values | Why |
|---|---|---|
| TCS (Consistency) | ELITE, RELIABLE | Only traders with proven consistency — STREAKY and CHOPPY filtered out |
| TRP (Risk Profile) | SNIPER, AGGRESSIVE, BALANCED | Active risk-takers, not ultra-conservative holders |
| Concentration | >0.5 | Trader's gains must be concentrated in few positions — not spread across 20 assets |

## Scoring (0-13 range)

| Signal | Points |
|---|---|
| Tier 2 momentum from quality trader | 4 (base) |
| High concentration (>0.7) | 1 |
| SM rank Top 10 | 2 |
| SM rank Top 20 | 1 |
| SM building fast (>5% 4h change) | 2 |
| SM building (>0% 4h change) | 1 |
| Deep SM (100+ traders) | 1 |
| Early entry (price <3% move in 4h) | 1 |
| High leverage on position (10x+) | 1 |

Minimum score: 7.

## Margin Scaling

| Score | Margin | Rationale |
|---|---|---|
| 7-9 | 25% | Base conviction |
| 8-9 | 30% | Strong dual confirmation |
| 10+ | 35% | Exceptional confluence |

## DSL (Same v1.1.1 Pattern)

- `highWaterPrice: null` (engine sets from entry price)
- `phase1MaxMinutes` / `deadWeightCutMin` / `weakPeakCutMinutes` (correct field names)
- `absoluteFloorRoe` (dynamic calculation, no static price floor)
- `consecutiveBreachesRequired: 3`
- Conviction-scaled timing by score

## Notification Policy (STRICT)

**ONLY alert:** Position OPENED (asset, direction, score, momentum event details, leaderboard rank), position CLOSED.

**NEVER alert:** Scanner found no events, events found but no quality match, quality events but no leaderboard confluence, any reasoning or analysis.

## Cron Architecture

| Cron | Interval | Session |
|---|---|---|
| Scanner | 90s | main |
| DSL | 3 min | isolated |

## Files

| File | Purpose |
|---|---|
| `scripts/raptor-scanner.py` | Dual-source confluence scanner |
| `scripts/raptor_config.py` | Standalone config helper |
| `config/raptor-config.json` | Parameters |

## License

MIT — Built by Senpi (https://senpi.ai).
