# FOX v1.0 DSL (Dynamic Stop Loss) Rules

## Philosophy: Catastrophic-Only

v1.0 uses a **catastrophic-only** floor. The DSL tier system handles normal profit-taking; the exchange stop loss is a last-resort safety net.

## Exchange Stop Loss

- **absoluteFloor**: `0.15 / leverage` (caps max loss at -15% ROE)
- Set immediately on entry via Hyperliquid native SL order
- Example at 10x: entry × (1 ± 0.015) depending on direction

## Phase 1 (Pre-Tier) Settings

- **retraceThreshold**: 0.15 (15% ROE — matches absoluteFloor)
- **consecutiveBreachesRequired**: 10 (30min sustained breach at 3min intervals)
- **No deadWeight timeout** (removed in v1.0)
- **No weakPeak timeout** (removed in v1.0)
- **greenIn10Enabled**: false (removed in v1.0)

### Hard Timeout (by score)
| Score | Hard Timeout |
|-------|-------------|
| 7-8 | 45 min |
| 8-9 | 60 min |
| 10+ | 90 min |

## Phase 2 (Post-Tier) Settings

- **retraceThreshold**: 0.015 (1.5%)
- **consecutiveBreachesRequired**: 2

## 9-Tier Aggressive Configuration

| Tier | Trigger (ROE%) | Floor (ROE%) | Breaches |
|------|---------------|-------------|----------|
| 1 | 5% | 2% | 2 |
| 2 | 10% | 5% | 2 |
| 3 | 20% | 14% | 2 |
| 4 | 30% | 24% | 2 |
| 5 | 40% | 34% | 1 |
| 6 | 50% | 44% | 1 |
| 7 | 65% | 56% | 1 |
| 8 | 80% | 72% | 1 |
| 9 | 100% | 90% | 1 |

**Tiers are FLOORS, not exits.** The position holds through tier upgrades. Exit only happens when price retraces below the current tier's floor.

## DSL State File Schema

Located at: `dsl/{strategyId}/{ASSET}.json`

Required fields:
- `asset`, `direction`, `entryPrice`, `leverage`, `size`, `wallet`
- `phase`, `highWaterPrice`, `currentTierIndex`, `tierFloorPrice`
- `currentBreachCount`, `consecutiveFetchFailures`
- `lastPrice`, `lastSyncedFloorPrice`, `slOrderId`, `pendingClose`
- Phase 1: `absoluteFloor`, `retraceThreshold`, `consecutiveBreachesRequired`, `hardTimeoutMinutes`
- Phase 2: `retraceThreshold`, `consecutiveBreachesRequired`
- `active` (boolean), `entryTime` (ISO timestamp)

## DSL Cron Lifecycle

1. **On position open**: Create DSL state file + cron job (3min, isolated)
2. **On position close**: Deactivate state (`active: false`), remove cron
3. **Health check** reconciles orphaned states every 10min

## Key Bugs / Lessons

- State file MUST include `asset` field (script crashes without it)
- `DSL_STATE_DIR` should NOT include strategy ID (script appends it)
- Always run DSL script manually after setup to verify `sl_synced: true`
- Race condition: when ANY job closes a position → immediately deactivate DSL state
