# 🦊 GHOST FOX — High Water Momentum

A trading strategy (config override) based on the FOX skill. Combines Feral Fox v2's high-conviction entry filters with DSL High Water Mode's infinite trailing. The silent hunter — enters rarely, holds patiently, trails to the moon.

**Base skill:** FOX v1.1
**Philosophy:** Feral Fox proved that fewer, higher-conviction entries produce better results. DSL High Water Mode proved that percentage-of-peak trailing captures more upside than fixed ROE locks. GHOST FOX combines both: Feral's entry discipline with High Water's infinite ceiling.

---

## MANDATORY: DSL High Water Mode

**GHOST FOX MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any GHOST FOX position, you MUST include:

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. Always verify the state file contains these fields after creation.**

**DSL default:** Use this strategy's **`ghost-fox-strategy/dsl-profile.json`** as the default when setting up DSL (`dsl-cli.py add-dsl` / `update-dsl` with `--configuration @<path>/ghost-fox-strategy/dsl-profile.json`). Use it unless the user explicitly provides a custom DSL configuration via the agent.

---

## What Changed vs Feral Fox v2

| Variable | Feral Fox v2 | GHOST FOX | Why |
|---|---|---|---|
| **DSL mode** | Fixed ROE tiers | **High Water Mode** | Infinite trailing. Stop = 85% of peak, no ceiling. |
| **Phase 2 trigger** | +15% ROE | **+7% ROE** | Start trailing earlier — catch the move before it retraces |
| **Tier structure** | 8 fixed tiers (15→200% ROE) | **4 HW tiers (7→20% ROE → infinite)** | Simpler. Once past +20% ROE, it's 85% of peak forever. |
| **Phase 1 floors** | 1.5% notional flat | **Conviction-scaled (-20/-25/unrestricted)** | High-score signals get max room. Low-score gets tight leash. |
| **Breakeven lock** | +15% ROE → lock +1% (fixed) | **+7% ROE → lock 40% of HW** | At +7% ROE, stop moves to +2.8% — covers fees and then some |
| **Entry filters** | Score 7+, 3 reasons, vel 0.05 | **Same** | Feral v2 entries already proven |
| **Regime enforcement** | Enforced | **Same** | Already proven |
| **Time exits** | All disabled | **All disabled** | Same — structural only |
| **Budget split** | 20/80 copy/autonomous | **10/90 copy/autonomous** | More capital to autonomous — High Water captures more upside |

Everything else (entry filters, risk safeguards, notification policy, execution) inherits from Feral Fox v2 unchanged.

---

## Config Override File

```json
{
  "basedOn": "fox",
  "version": "1.0",
  "name": "Ghost Fox",
  "description": "High Water momentum — Feral v2 entries + infinite percentage-of-peak trailing",

  "budgetSplit": {
    "copyTradingPct": 10,
    "autonomousPct": 90
  },

  "entryFilters": {
    "minReasons": 3,
    "minScore": 7,
    "minScoreNeutral": 9,
    "minVelocity": 0.05,
    "maxPriceChg4hPct": 3.0,
    "maxPriceChg4hHighScore": 5.0,
    "fjPersistence": "all_immediate",
    "enforceRegimeDirection": true
  },

  "dsl": {
    "_spec": "https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md",
    "lockMode": "pct_of_high_water",
    "phase2TriggerRoe": 7,
    "convictionTiers": [
      {"minScore": 6,  "absoluteFloorRoe": -20, "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0},
      {"minScore": 8,  "absoluteFloorRoe": -25, "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0},
      {"minScore": 10, "absoluteFloorRoe": 0,   "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
      {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
      {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
      {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 10,
      "hwStaleMin": 45
    }
  },

  "reentry": {
    "enabled": true,
    "marginPct": 100,
    "minScore": 6,
    "maxOriginalLossROE": 15,
    "windowMin": 120,
    "minContribVelocity": 5
  },

  "risk": {
    "maxEntriesPerDay": 5,
    "maxDailyLossPct": 8,
    "maxDrawdownPct": 20,
    "maxSingleLossPct": 15,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60,
    "maxPositions": 4
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "takeProfitOrderType": "FEE_OPTIMIZED_LIMIT",
    "_note": "SL and emergency exits MUST be MARKET. Never ALO for stop losses."
  }
}
```

---

## How the Infinite Trail Works on First Jumps

FOX's edge is catching First Jumps — explosive moves where an asset rockets from obscurity into the top ranks. These are exactly the trades that benefit most from High Water Mode:

| Scenario | Feral Fox v2 (fixed) | GHOST FOX (High Water) |
|---|---|---|
| FJ runs to +30% ROE, retraces to +22% | Exit at +22% (lock was +12%) | **Hold** — stop at +12% (40% of 30) |
| FJ runs to +80% ROE | Lock +55% (fixed tier) | Lock +68% (85% of 80) |
| FJ runs to +150% ROE | Lock +110% (fixed tier) | Lock +127.5% (85% of 150) |
| FJ runs to +300% ROE | Lock +160% (CAPPED at T8) | Lock +255% (85% of 300, NO CAP) |

The divergence is biggest on the explosive runners — the exact signal type FOX is designed to catch.

---

## Notification Policy

Same as all Senpi skills:

**ONLY alert the user when:**
- Position OPENED or CLOSED
- Risk guardian triggered
- Critical error

**NEVER alert for:**
- Scanner found nothing
- DSL routine check
- Any reasoning or narration

All crons isolated. `NO_REPLY` for idle cycles.

---

## Deployment

GHOST FOX runs on the FOX v1.1 skill. Deploy FOX first, then apply these overrides.

**Bootstrap:** FOX's `AGENTS.md` bootstrap gate runs automatically — creates copy trading monitor, market regime cron, and autonomous trading crons. GHOST FOX inherits this.

**Critical:** After every position is opened, verify the DSL state file contains `lockMode: "pct_of_high_water"` and the full `tiers` array. Without them, High Water Mode is silently disabled and the position runs on flat retrace — defeating the purpose of GHOST FOX.

---

## Expected Behavior vs Feral Fox v2

| Metric | Feral v2 | GHOST FOX (expected) |
|---|---|---|
| Trades/day | 2-4 | 2-4 (same entries) |
| Win rate | ~55-65% | ~55-65% (same entries) |
| Avg winner | 20-50% ROE | **25-60%+ ROE** (infinite trail captures more) |
| Avg loser | -12 to -15% ROE | -12 to -15% ROE (same floors) |
| Fee drag/day | $8-15 | $8-15 (same trade frequency) |
| Profit factor | ~1.3-1.8 | **~1.4-2.0** (bigger winners, same losers) |

Same entries, same number of trades, same losers. The only difference is winners — High Water Mode captures more of each winning trade's peak. Over time, that compounds.

---

## When To Use

| Condition | Feral Fox v2 | GHOST FOX |
|---|---|---|
| Want predictable fixed exits | ✓ | — |
| Want maximum upside capture | — | ✓ |
| Explosive FJ market (many runners) | — | ✓ |
| Choppy market (small moves) | ✓ | Either (same on small moves) |
| First time running FOX | ✓ | — |
| Proven profitable on Feral v2 | — | ✓ (upgrade path) |
