# 🐺 JACKAL — First Jump Pyramider

A trading strategy (config override) based on FOX v1.6. Uses FOX's exact scanner and five-layer entry gauntlet — the proven #1 signal on the leaderboard — combined with RHINO's pyramiding mechanic. Enter small on a First Jump, add as it confirms.

**Base skill:** FOX v1.6
**Philosophy:** FOX is +11.6% because its entry signals are right ~85% of the time on strong scores. The only weakness: every entry is full size, so the 15% that fail cost the same as the 85% that win. JACKAL fixes this — enter at 30%, add at +10% ROE and +20% ROE if the thesis holds. Losers die small, winners reach full size. Same proven signals, better risk geometry.

---

## MANDATORY: DSL High Water Mode

**JACKAL MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockPct": 40, "breachesRequired": 3},
    {"triggerPct": 12, "lockPct": 55, "breachesRequired": 2},
    {"triggerPct": 15, "lockPct": 75, "breachesRequired": 1},
    {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine per-tick fix ships.

---

## Why This Should Win

FOX v1.6 on $1,000: enters with ~$250 margin. If the trade fails at -20% ROE = -$50 loss. If it runs to +50% ROE = +$125 win.

JACKAL on $1,000: enters with ~$75 margin (30% of $250 max). If the trade fails at -20% ROE = **-$15 loss**. If it reaches +10% ROE, adds $100 more. If it reaches +20% ROE, adds the final $75. Full position at +50% ROE = **+$125 win on the same signal**.

Same upside on winners. 70% less risk on losers. The math:
- FOX at 85% win rate: expected per trade = (0.85 × $125) - (0.15 × $50) = **+$98.75**
- JACKAL at 85% win rate: expected per trade = (0.85 × $125) - (0.15 × $15) = **+$103.94**

The edge is small per trade but compounds. Over 50 trades (what FOX did in 3 days): FOX expected +$4,937, JACKAL expected +$5,197. The difference comes entirely from smaller losers.

---

## The Five-Layer Entry Gauntlet (Identical to FOX v1.6)

Every signal must survive all five layers. No changes from FOX v1.6:

| Layer | Filter | Value |
|---|---|---|
| 1 | First Jump | 10+ ranks from outside Top 25. Top-10 blocked. |
| 2 | Score thresholds | minScore ≥ 9, minReasons ≥ 4, minVelocity > 0.10 |
| 3 | Asset trend alignment | 4h/1h must agree with direction. Hard block. |
| 4 | Leverage floor | Max leverage ≥ 7x |
| 5 | Time-of-day modifier | +1 during 04:00-14:00 UTC, -2 during 18:00-02:00 UTC |

**The scanner is not changed.** FOX's emerging-movers.py runs exactly as it does today. The only difference is what happens after a signal passes.

---

## The Three Pyramid Stages

### Stage 1 — SCOUT (30% of max position)

Signal passes the five-layer gauntlet. Enter with 30% of normal FOX margin.

At $1,000 account with 25% max margin = $250 max position. Scout enters with $75.

### Stage 2 — CONFIRM (add 40% at +10% ROE)

Requirements before adding:
- Position is at +10% ROE
- Asset's 4h trend still intact (same direction as entry)
- SM has not flipped against the position
- Volume still present (ratio ≥ 0.5x average)

Adds $100 to the $75 scout. Position is now $175 (70% of max).

### Stage 3 — CONVICTION (add final 30% at +20% ROE)

Same re-validation as Stage 2. Adds $75. Position is now $250 (100% of max).

### What If It Never Reaches +10%?

The scout position rides with DSL High Water protection. If the First Jump fails, the scout hits its conviction-scaled floor (-20% ROE on $75 = -$15 loss). That's the whole point — failed signals cost $15, not $50.

---

## Config Override File

```json
{
  "basedOn": "fox",
  "version": "2.0",
  "name": "Jackal",
  "description": "First Jump pyramider — FOX v1.6 entry gauntlet + RHINO pyramid mechanic",

  "budgetSplit": {
    "copyTradingPct": 10,
    "autonomousPct": 90
  },

  "pyramid": {
    "enabled": true,
    "scoutPct": 30,
    "stages": [
      {"stage": 2, "triggerRoe": 10, "addPct": 40, "_note": "CONFIRM: FJ working, add 40% of max"},
      {"stage": 3, "triggerRoe": 20, "addPct": 30, "_note": "CONVICTION: FJ running, add final 30%"}
    ],
    "revalidateBeforeAdd": {
      "check4hTrend": true,
      "checkSmAlignment": true,
      "checkVolumeAlive": true,
      "minVolRatio": 0.5
    }
  },

  "entryFilters": {
    "minReasons": 4,
    "minScore": 9,
    "minScoreNeutral": 11,
    "minVelocity": 0.10,
    "minRankJump": 15,
    "minRankJumpOrVelocity": 15,
    "minPrevRank": 25,
    "topTenBlock": true,
    "minLeverage": 7,
    "maxPriceChg4hPct": 3.0,
    "fjPersistence": "all_immediate",
    "enforceRegimeDirection": true,
    "enforceHourlyTrend": true,
    "timeOfDay": {
      "bonusHoursUTC": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
      "bonusPoints": 1,
      "penaltyHoursUTC": [18, 19, 20, 21, 22, 23, 0, 1],
      "penaltyPoints": -2
    }
  },

  "dsl": {
    "_spec": "https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md",
    "lockMode": "pct_of_high_water",
    "phase2TriggerRoe": 7,
    "phase1HardTimeoutMin": 0,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "convictionTiers": [
      {"minScore": 9,  "absoluteFloorRoe": -20, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 12, "absoluteFloorRoe": -25, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 15, "absoluteFloorRoe": 0,   "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 7,  "lockPct": 40, "breachesRequired": 3},
      {"triggerPct": 12, "lockPct": 55, "breachesRequired": 2},
      {"triggerPct": 15, "lockPct": 75, "breachesRequired": 1},
      {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1}
    ],
    "tiersLegacyFallback": [
      {"triggerPct": 5,  "lockPct": 1.5},
      {"triggerPct": 10, "lockPct": 5},
      {"triggerPct": 15, "lockPct": 10, "retrace": 0.012},
      {"triggerPct": 25, "lockPct": 18, "retrace": 0.010},
      {"triggerPct": 40, "lockPct": 32, "retrace": 0.008},
      {"triggerPct": 60, "lockPct": 50, "retrace": 0.006},
      {"triggerPct": 80, "lockPct": 68, "retrace": 0.005},
      {"triggerPct": 100, "lockPct": 88, "retrace": 0.004}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 10,
      "hwStaleMin": 45
    }
  },

  "reentry": {
    "enabled": true,
    "marginPct": 75,
    "minScore": 8,
    "maxOriginalLossROE": 15,
    "windowMin": 120,
    "minContribVelocity": 5
  },

  "risk": {
    "maxEntriesPerDay": 4,
    "maxDailyLossPct": 6,
    "maxDrawdownPct": 18,
    "maxSingleLossPct": 12,
    "maxPositions": 3,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60,
    "dynamicSlots": {
      "enabled": true,
      "baseMax": 3,
      "absoluteMax": 5,
      "unlockThresholds": [
        {"pnl": 100, "maxEntries": 4},
        {"pnl": 250, "maxEntries": 5}
      ]
    }
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "takeProfitOrderType": "FEE_OPTIMIZED_LIMIT"
  }
}
```

---

## FOX vs JACKAL: Same Signal, Different Risk

| | FOX v1.6 | JACKAL |
|---|---|---|
| Scanner | Emerging movers (FJ) | **Same** |
| Entry gauntlet | Five-layer | **Same** |
| Entry size | 100% of allocation | **30% of allocation** |
| Size at +10% ROE | 100% (no change) | **70%** (added 40%) |
| Size at +20% ROE | 100% (no change) | **100%** (added final 30%) |
| Cost of a failed trade | ~$50 on $1K account | **~$15 on $1K account** |
| Upside on a full runner | ~$125 | **~$125** (same — full size by +20%) |
| Re-validation before add | N/A | 4h trend + SM + volume |
| DSL | High Water | **Same** |
| Phase 1 floors | Conviction-scaled | **Same** |
| Time exits | Disabled | **Disabled** |

The only difference: JACKAL risks 30% at entry instead of 100%. Everything else is identical to what's already winning.

---

## The Risk: What Could Go Wrong

The pyramid adds could get chopped. A First Jump runs to +12% ROE, JACKAL adds 40% more, then the trade pulls back to +5% before continuing. Now you have 70% of max position with a lower average entry and the DSL floor is based on the full position.

FOX wouldn't have this problem — it entered at full size at the original price and rode through the pullback without adding.

Mitigation: the re-validation check before adding (4h trend intact, SM aligned, volume alive) should filter out adds into fading moves. But this IS the untested variable.

---

## Expected Behavior

| Metric | FOX v1.6 | JACKAL (expected) |
|---|---|---|
| Trades/day | 3-6 | **3-6** (same scanner) |
| Adds/day | 0 | **2-4** (adds on winners) |
| Win rate | ~85% on strong signals | **~85%** (same signals) |
| Avg winner (full pyramid) | +$125 | **+$125** (same — full size by +20%) |
| Avg loser | -$50 | **-$15** (30% size) |
| Expected per trade | +$98.75 | **+$103.94** |
| Fee drag | Same entries | **Slightly higher** (add orders cost fees) |

---

## Notification Policy

ONLY alert:
- Stage 1 SCOUT opened (asset, direction, score, margin)
- Stage 2 CONFIRM added (asset, +ROE at add, new total)
- Stage 3 CONVICTION added (asset, full position)
- Position CLOSED (total P&L across all stages)
- Risk triggered, critical error

NEVER alert: Scanner found nothing, DSL routine, pyramid evaluation, any reasoning.
All crons isolated. NO_REPLY for idle cycles.

---

## Deployment

JACKAL runs on FOX v1.6. Deploy FOX, then apply these overrides. The agent uses FOX's existing scanner crons — the pyramid logic is applied to the scanner output before execution.
