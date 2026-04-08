# 👻 GHOST FOX v2.0 — Ultra-Selective Infinite Trailing

A trading strategy (config override) based on FOX v1.6. The ghost: you never see it enter, but when it does, it takes everything the move has to give.

**Base skill:** FOX v1.6
**Philosophy:** Ghost Fox v1 failed because it traded too much (354 trades, -26%). Fox won with 56 trades (+16%). The math is clear: Ghost Fox's edge is infinite trailing, not trade frequency. v2.0 makes Ghost Fox the most selective variant in the FOX family — highest score bar, fewest trades, widest High Water locks. Enter like a ghost (rarely, silently). Trail like a ghost (invisibly, infinitely).

---

## What Went Wrong with v1.0

Ghost Fox v1 had 354 trades. Fox had 56. That's the entire diagnosis.

Ghost Fox was supposed to be Feral Fox entries + High Water trailing. Instead it drifted to lower entry bars and traded 6x more than Fox. Every extra trade in a choppy market is a fee + a potential loss. High Water trailing can't save you if you're entering on weak signals — it just trails a losing trade more precisely.

v2.0 fixes this by making entry the hardest gate in the family, then letting High Water do what it does best on the rare trades that survive.

---

## MANDATORY: DSL High Water Mode

**GHOST FOX MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockPct": 40, "breachesRequired": 3},
    {"triggerPct": 10, "lockPct": 60, "breachesRequired": 2},
    {"triggerPct": 15, "lockPct": 75, "breachesRequired": 1},
    {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1},
    {"triggerPct": 40, "lockPct": 90, "breachesRequired": 1}
  ]
}
```

**FALLBACK:** Use `tiersLegacyFallback` from config until engine per-tick fix ships.

---

## What Changed vs Ghost Fox v1.0

| Variable | Ghost Fox v1.0 | Ghost Fox v2.0 | Why |
|---|---|---|---|
| Min score | 7 | **11** | Highest in the FOX family. Only monster signals. |
| Min score (neutral) | 9 | **13** | Maximum skepticism in chop |
| Min reasons | 3 | **5** | Must trigger FJ + IMMEDIATE + CONTRIB_EXPLOSION + at least 2 more |
| Min velocity | 0.05 | **0.15** | SM must be aggressively accelerating, not just drifting |
| Previous rank | No gate | **≥ 30** | Must come from deep obscurity — not just outside top 25 |
| Top-10 block | No | **Yes** | Don't buy the top. Ghost Fox needs the early move. |
| Phase 1 floor | Wide/unrestricted | **-25/-30/unrestricted by score** | Wide but not reckless |
| Phase 1 timeout | Disabled | **Disabled** | Proven pattern — structural only |
| Phase 2 trigger | +7% ROE | **+5% ROE** | Start trailing early — Ghost Fox's edge IS the trailing |
| T5 (new tier) | None (max T4 at 85%) | **+40% ROE locks 90%** | Extra tier for big runners |
| Max entries/day | 5 | **3 base, 5 on profitable days** | Force rarity |
| Max positions | 4 | **2** | Concentrated |
| Re-entry | Enabled | **Disabled** | One shot. If the gauntlet passed, trust it. If it failed, move on. |

### The Key Difference from Every Other FOX Variant

Ghost Fox v2.0 is the only variant with score 11 AND 5 reasons AND velocity 0.15 AND prevRank ≥ 30. This combination will produce 0-2 trades per day. Often zero. That's the point.

When a signal DOES survive this gauntlet, it's a monster — the kind of First Jump that scores 30-40+ points with extreme velocity. High Water trailing at 85-90% of peak captures nearly everything from these moves. One monster trade per day is worth more than ten mediocre trades.

---

## The Ghost Fox v2.0 Trailing Shape

Five tiers instead of four. The fifth tier at +40% ROE locks 90% — for the runners that go beyond +40%, Ghost Fox keeps 90 cents of every additional dollar.

| Tier | Trigger | Lock (% of HW) | Breaches | Notes |
|---|---|---|---|---|
| 1 | +5% ROE | 40% | 3 | Early confirmation — patient |
| 2 | +10% ROE | 60% | 2 | Tightening |
| 3 | +15% ROE | 75% | 1 | Solid lock |
| 4 | +20% ROE | 85% | 1 | Strong lock — standard HW territory |
| 5 | +40% ROE | 90% | 1 | Ultra lock for big runners |

At +100% ROE: floor at +90%. At +200% ROE: floor at +180%. The ghost takes 90% of everything above +40%.

---

## Config Override File

```json
{
  "basedOn": "fox",
  "version": "2.0",
  "name": "Ghost Fox",
  "description": "Ultra-selective infinite trailing — highest entry bar in the FOX family, 5-tier High Water to 90%",

  "budgetSplit": {
    "copyTradingPct": 10,
    "autonomousPct": 90
  },

  "entryFilters": {
    "minReasons": 5,
    "minScore": 11,
    "minScoreNeutral": 13,
    "minVelocity": 0.15,
    "minRankJump": 15,
    "minRankJumpOrVelocity": 15,
    "minPrevRank": 30,
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
    "phase2TriggerRoe": 5,
    "phase1HardTimeoutMin": 0,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "convictionTiers": [
      {"minScore": 11, "absoluteFloorRoe": -25, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 13, "absoluteFloorRoe": -30, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 15, "absoluteFloorRoe": 0,   "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 5,  "lockPct": 40, "breachesRequired": 3},
      {"triggerPct": 10, "lockPct": 60, "breachesRequired": 2},
      {"triggerPct": 15, "lockPct": 75, "breachesRequired": 1},
      {"triggerPct": 20, "lockPct": 85, "breachesRequired": 1},
      {"triggerPct": 40, "lockPct": 90, "breachesRequired": 1}
    ],
    "tiersLegacyFallback": [
      {"triggerPct": 5,  "lockPct": 2},
      {"triggerPct": 10, "lockPct": 6},
      {"triggerPct": 15, "lockPct": 11, "retrace": 0.010},
      {"triggerPct": 20, "lockPct": 17, "retrace": 0.008},
      {"triggerPct": 40, "lockPct": 36, "retrace": 0.005},
      {"triggerPct": 60, "lockPct": 54, "retrace": 0.004},
      {"triggerPct": 80, "lockPct": 72, "retrace": 0.003},
      {"triggerPct": 100, "lockPct": 90, "retrace": 0.003}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 12,
      "hwStaleMin": 60
    }
  },

  "reentry": {
    "enabled": false,
    "_note": "Disabled. The gauntlet is the filter. If it fails, move on."
  },

  "risk": {
    "maxEntriesPerDay": 3,
    "maxDailyLossPct": 6,
    "maxDrawdownPct": 18,
    "maxSingleLossPct": 10,
    "maxPositions": 2,
    "maxConsecutiveLosses": 2,
    "cooldownMinutes": 90,
    "dynamicSlots": {
      "enabled": true,
      "baseMax": 3,
      "absoluteMax": 5,
      "unlockThresholds": [
        {"pnl": 150, "maxEntries": 4},
        {"pnl": 300, "maxEntries": 5}
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

## Ghost Fox's Unique Position in the FOX Family

| | Fox v1.6 | Feral v3.0 | Falcon | Lynx | Ghost Fox v2.0 |
|---|---|---|---|---|---|
| Min score | 9 | 9 | 9 | 10 | **11** |
| Min reasons | 4 | 4 | 4 | 4 | **5** |
| Min velocity | 0.10 | 0.10 | 0.10 | 0.05 | **0.15** |
| Min prevRank | 25 | None | 25 | None | **30** |
| Phase 1 floor | -20% ROE | -50% ROE | -20% ROE | -20% ROE | **-25% ROE** |
| Phase 2 trigger | +7% | +5% | +7% | +7% | **+5%** |
| Top tier lock | 85% | 95% | 85% | 85% | **90%** |
| Number of tiers | 4 | 4 | 4 | 4 | **5** |
| Re-entry | Yes | Yes | Yes | Yes | **No** |
| Max positions | 3 | 6 | 3 | 2 | **2** |
| Expected trades/day | 3-6 | 3-6 | 1-3 | 0-2 | **0-2** |

Ghost Fox v2.0 doesn't compete with Fox or Feral on trade frequency. It competes on capture-per-trade. Fewer entries, higher quality, more locked per winner. The ghost appears rarely but takes everything.

---

## Expected Behavior

| Metric | Ghost Fox v1.0 (actual) | Ghost Fox v2.0 (expected) |
|---|---|---|
| Trades/day | 8-12 (drifted) | **0-2** |
| Win rate | ~35% | **~60-70%** (only monsters pass) |
| Avg winner | Unknown | **30-80%+ ROE (90% captured)** |
| Avg loser | Many small bleeds | **-15 to -25% ROE (fewer, structural)** |
| Fee drag/day | $40-60 | **$3-8** |
| Total trades/week | 50-80 | **5-15** |

---

## Notification Policy

ONLY alert: Position OPENED or CLOSED, risk triggered, critical error.
NEVER alert: Scanner found nothing, DSL routine, any reasoning.
All crons isolated. NO_REPLY for idle cycles.

---

## Deployment

Ghost Fox v2.0 runs on FOX v1.6. Deploy FOX, then apply these overrides. The agent MUST use these entry filters — if it drifts back to score 7, it becomes Ghost Fox v1 again and bleeds.
