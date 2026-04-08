# 🦊 FERAL FOX v3.0 — Production-Tuned Momentum

A trading strategy (config override) based on FOX v1.6. This is the exact configuration running live on the Senpi Predators tracker — every filter, every tier, every threshold captured from the agent's production tuning.

**Base skill:** FOX v1.6
**Philosophy:** Enter only on massive, multi-reason momentum spikes. Give those spikes deep room to pull back (-50% ROE / 2 hours). The second they go green (+5% ROE), throw a net over them that gets infinitely tighter the higher they go.

---

## MANDATORY: DSL High Water Mode

**FERAL FOX MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 10, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 15, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
    {"triggerPct": 25, "lockHwPct": 95, "consecutiveBreachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine supports `pct_of_high_water`.

---

## What Changed vs Feral Fox v2.0

| Variable | Feral v2.0 | Feral v3.0 (live) | Why |
|---|---|---|---|
| **Score gate** | 7 | **9** | Only strong composite signals survive |
| **Min reasons** | 3 | **4** | Must trigger multiple deep signals (FJ + RANK_UP + IMMEDIATE + ACCEL) |
| **Top-10 block** | Yes | **Removed** | If velocity and score are overwhelming, follow the signal regardless of rank |
| **Phase 1 floor** | 1.5% notional (~15% ROE) | **5% notional (~50% ROE)** | Massive breathing room — stops getting chopped before the move |
| **Phase 1 timeout** | Disabled | **120 min** | 2 hours to figure itself out. Still negative = cut. |
| **Phase 2 trigger** | +7% ROE | **+5% ROE** | Start trailing earlier — the net drops sooner |
| **T1 lock** | 40% HW | **50% HW** | Tighter immediately — lock half the profit from the start |
| **T2 lock** | 55% HW | **75% HW, 1 breach** | Aggressive — one breach and you're out |
| **T3 lock** | 75% HW | **85% HW, 1 breach** | |
| **T4 lock** | 85% HW | **95% HW, 1 breach** | Near-total lock on runners |
| **Max slots** | 4 | **6** | More positions running simultaneously |
| **Margin per slot** | Variable | **$254 fixed** | Standardized per-slot allocation |

### The Key Insight

Feral v2.0 used wide Phase 1 + wide Phase 2. Feral v3.0 uses **wide Phase 1 + tight Phase 2**. The trade gets massive room to develop (-50% ROE), but the moment it turns profitable, the trailing locks are the tightest in the FOX family. At +25% ROE, the stop is at +23.75% (95% of HW). Almost no giveback on runners.

---

## Entry Gauntlet

Every 3 minutes, the scanner checks SM flow. All filters must pass:

| Layer | Filter | Value |
|---|---|---|
| 1 | Rank jump minimum | ≥ 15 ranks OR velocity > 15 |
| 2 | Min reasons | ≥ 4 (FIRST_JUMP + IMMEDIATE_MOVER + CONTRIB_EXPLOSION + more) |
| 3 | Min score | ≥ 9 composite |
| 4 | Time-of-day | +1 pt during 04:00–14:00 UTC, -2 pts during 18:00–02:00 UTC |
| 5 | Asset trend alignment | 4h/1h chart must agree with signal direction. Hard block. |
| 6 | Leverage floor | Max leverage ≥ 7x required |
| 7 | Top-10 | **NOT blocked** — if score and velocity are overwhelming, follow the signal |

Execution: FEE_OPTIMIZED_LIMIT (maker). Never MARKET for entries.

### Note on Top-10 Block

Feral v2.0 blocked top-10 assets. Feral v3.0 removes this block. The reasoning: if an asset is already at #3 but scored 43 points with velocity > 20 and 5+ reasons, the momentum is real and ongoing. The other filters (score 9, 4 reasons, velocity > 0.10, trend alignment) are strong enough to prevent buying dead tops. The top-10 block was killing valid signals.

---

## Phase 1: Deep Breathing Room

| Setting | Value |
|---|---|
| Absolute floor | **5% notional** (~50% ROE at 10x) |
| Hard timeout | **120 min** |
| Weak peak | Disabled |
| Dead weight | Disabled |

This is the widest Phase 1 in the FOX family and among the widest in the entire zoo. The thesis: if the five-layer entry gauntlet passed, the signal is real. Give it maximum room to survive market maker wicks, initial pullbacks, and consolidation. If it's still negative after 2 full hours, the thesis is wrong — cut it.

The 120-minute timeout is a Feral v3.0 specific choice. FOX v1.6 and FALCON disable all time exits. Feral v3.0 keeps one time exit because the -50% ROE floor is so wide that a slowly bleeding trade could sit at -30% ROE for hours without hitting the floor. The 2-hour clock catches those.

---

## Phase 2: Infinite Tightening

Once the trade crosses +5% ROE:

| Tier | Trigger | Lock (% of HW) | Breaches | Behavior |
|---|---|---|---|---|
| 1 | +5% ROE | **50%** | 2 | Half the profit locked immediately |
| 2 | +10% ROE | **75%** | 1 | Three-quarters locked. One breach = exit. |
| 3 | +15% ROE | **85%** | 1 | Tight lock on strong runners |
| 4 | +25% ROE+ | **95%** | 1 | Near-total lock. Infinite trail at 95% of peak. |

At +50% ROE: stop at +47.5% (95%). At +100% ROE: stop at +95%. At +200% ROE: stop at +190%.

This is the tightest Phase 2 in the entire zoo. The tradeoff: runners have almost no room to pull back before the stop triggers. But the Phase 1 already proved the trade works — by the time it reaches +25% ROE, it's a confirmed runner and you want maximum lock.

---

## Config Override File

```json
{
  "basedOn": "fox",
  "version": "3.0",
  "name": "Feral Fox",
  "description": "Production-tuned momentum — deep Phase 1 breathing room, tight Phase 2 infinite trailing",

  "budgetSplit": {
    "copyTradingPct": 10,
    "autonomousPct": 90
  },

  "slots": {
    "maxSlots": 6,
    "marginPerSlot": 254,
    "defaultLeverage": 10
  },

  "entryFilters": {
    "minReasons": 4,
    "minScore": 9,
    "minScoreNeutral": 11,
    "minVelocity": 0.10,
    "minRankJump": 15,
    "minRankJumpOrVelocity": 15,
    "topTenBlock": false,
    "minLeverage": 7,
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
    "phase2TriggerRoe": 5,
    "phase1HardTimeoutMin": 120,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "floorBase": 0.05,
    "convictionTiers": [
      {"minScore": 9,  "absoluteFloorRoe": -50, "hardTimeoutMin": 120, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 12, "absoluteFloorRoe": -50, "hardTimeoutMin": 120, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 15, "absoluteFloorRoe": 0,   "hardTimeoutMin": 180, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 5,  "lockHwPct": 50, "consecutiveBreachesRequired": 2},
      {"triggerPct": 10, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
      {"triggerPct": 15, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
      {"triggerPct": 25, "lockHwPct": 95, "consecutiveBreachesRequired": 1}
    ],
    "tiersLegacyFallback": [
      {"triggerPct": 5,  "lockPct": 2.5},
      {"triggerPct": 10, "lockPct": 7.5},
      {"triggerPct": 15, "lockPct": 12.75, "retrace": 0.008},
      {"triggerPct": 25, "lockPct": 23.75, "retrace": 0.005},
      {"triggerPct": 40, "lockPct": 38, "retrace": 0.004},
      {"triggerPct": 60, "lockPct": 57, "retrace": 0.003},
      {"triggerPct": 80, "lockPct": 76, "retrace": 0.003},
      {"triggerPct": 100, "lockPct": 95, "retrace": 0.002}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 8,
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
    "maxEntriesPerDay": 6,
    "maxDailyLossPct": 8,
    "maxDrawdownPct": 20,
    "maxSingleLossPct": 15,
    "maxPositions": 6,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60
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

## FOX Family Phase Comparison

| | FOX v1.6 | Feral v3.0 | Falcon | Lynx |
|---|---|---|---|---|
| **Phase 1 floor** | -20% ROE | **-50% ROE** | -20% ROE | -20% ROE |
| **Phase 1 timeout** | Disabled | **120 min** | Disabled | Disabled |
| **Phase 2 trigger** | +7% ROE | **+5% ROE** | +7% ROE | +7% ROE |
| **T1 lock** | 40% HW | **50% HW** | 40% HW | 40% HW |
| **Top tier lock** | 85% HW | **95% HW** | 85% HW | 85% HW |
| **Top-10 block** | Yes | **No** | Yes | No |
| **Max positions** | 3 | **6** | 3 | 2 |

Feral v3.0 is the most aggressive configuration in the family: widest Phase 1 (-50% ROE), tightest Phase 2 (95% lock), most positions (6), and no top-10 block. The conviction gauntlet is just as tight as FOX v1.6 (score 9, 4 reasons, velocity 0.10) — the difference is what happens after entry.

---

## Expected Behavior

| Metric | Feral v2.0 | Feral v3.0 (expected) |
|---|---|---|
| Trades/day | 2-4 | **3-6** (6 slots, no top-10 block) |
| Win rate | ~55-65% | **~55-65%** (same entry quality) |
| Avg winner | 20-50% ROE | **20-50% ROE, but 95% captured** |
| Avg loser | -12 to -15% ROE | **-30 to -50% ROE** (wider floor) |
| Avg loser (timeout) | N/A | **-5 to -15% ROE** (2h timeout catches slow bleed) |
| Profit factor | ~1.3-1.8 | **Target 1.5-2.5** (tighter capture on winners) |

The tradeoff: losers are bigger (-50% ROE floor vs -20%). But winners keep 95% of their peak instead of 85%. Over enough trades, the tighter capture on every winner more than compensates for the wider losers — if the entry gauntlet is doing its job.

---

## Notification Policy

**ONLY alert:** Position OPENED or CLOSED, risk triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

---

## Deployment

Feral Fox v3.0 runs on FOX v1.6. Deploy FOX, then apply these overrides.
