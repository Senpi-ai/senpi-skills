# 🦊 FERAL FOX v2.0 — High-Conviction Momentum

A trading strategy (config override) based on the FOX skill. Same scanner, same scripts, same DSL — different personality.

**Base skill:** FOX v1.1
**Philosophy v2:** Feral Fox v1 was aggressive — lower entry bars, more trades, wider stops. The market punished it. v2 flips the identity: fewer trades, higher conviction, tighter structural invalidation, and uncapped upside on runners. Not the wild fox — the disciplined one.

---

## Deployment

Feral Fox runs on the FOX v1.1 skill. Deploy FOX first, then apply these overrides.

**If deploying fresh:** FOX's `AGENTS.md` contains a mandatory bootstrap gate that creates the copy trading monitor, market regime cron, and all autonomous trading crons on first session. This runs automatically — the agent checks for `config/bootstrap-complete.json` on every session and won't proceed until bootstrap is complete. Feral Fox inherits this behavior. No additional setup needed.

**If switching from standard FOX or Feral v1:** Update the config variables below in `fox-strategies.json` or provide them to your agent. The agent applies them on the next cron cycle. No restart needed.

---

## What Changed: v1 → v2

| Variable | Feral v1 | Feral v2 | Why |
|---|---|---|---|
| **minScore** | 5 | **7** | v1 entered too many marginal trades. Quality > quantity. |
| **minReasons** | 2 | **3** | Requires multi-timeframe alignment, not just one signal |
| **minVelocity** | 0.01 | **0.05** | Only enter on verified momentum, skip slow-bleed chop |
| **minScoreNeutral** | 6 | **9** | Near-zero conviction threshold in sideways markets |
| **enforceRegimeDirection** | false | **true** | Both-direction trading wasn't working. Trade with the regime. |
| **maxEntriesPerDay** | 10 | **5** | Half the trades, double the conviction |
| **maxPositions** | 6 | **4** | Concentrated positions, not scattered |
| **floorBase** | 0.04-0.05/lev | **0.015** | 1.5% notional = ~15% ROE max loss. Structural invalidation — if the swing low breaks by 1.5%, the thesis is dead. |
| **Dead weight cut** | Disabled | **Disabled** | Same as v1 — no time-based exits |
| **Weak peak cut** | Disabled | **Disabled** | Same as v1 |
| **Hard timeout** | 60-90 min | **Disabled** | Let winners develop. Only structural invalidation exits. |
| **Breakeven lock** | None | **+15% ROE → lock +1% ROE** | Green trades never go red. Single biggest P&L improvement. |
| **DSL tiers** | Standard 9-tier | **8-tier momentum** | Designed for explosive runners, not scalps |
| **Budget split** | 20/80 copy/autonomous | **20/80 copy/autonomous** | Unchanged |
| **cooldownMinutes** | 30 | **60** | More patience after consecutive losses |

---

## Config Override File

```json
{
  "basedOn": "fox",
  "version": "2.0",
  "name": "Feral Fox",
  "description": "High-conviction momentum — fewer trades, structural invalidation, uncapped upside",

  "budgetSplit": {
    "copyTradingPct": 20,
    "autonomousPct": 80
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
    "convictionTiers": [
      {"minScore": 7,  "floorBase": 0.015, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 10, "floorBase": 0.015, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 12, "floorBase": 0.012, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 15,  "lockPct": 1,   "_note": "breakeven + fees — green never goes red"},
      {"triggerPct": 25,  "lockPct": 10},
      {"triggerPct": 40,  "lockPct": 20},
      {"triggerPct": 60,  "lockPct": 35},
      {"triggerPct": 80,  "lockPct": 55},
      {"triggerPct": 100, "lockPct": 75},
      {"triggerPct": 150, "lockPct": 110},
      {"triggerPct": 200, "lockPct": 160}
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

## Notification Policy

Feral Fox follows the same strict notification rules as standard FOX:

**ONLY alert the user when:**
- Position OPENED or CLOSED
- Risk guardian triggered (gate closed, force close, cooldown)
- Copy trading alert (-20% drawdown, strategy inactive)
- Critical error (3+ DSL failures, MCP auth expired)

**NEVER alert for:**
- Scanner ran and found nothing
- DSL checked positions and nothing changed
- Health check passed
- Any reasoning, thinking, or narration

All scanner and monitoring crons run on **isolated sessions** with `agentTurn` payloads. Use `NO_REPLY` for idle cycles.

---

## How v2 Trades Differently

**Entry:** Waits for score 7+ with 3+ reasons and 0.05+ velocity. In a neutral regime, needs score 9. Most scans produce nothing — that's the point. When Feral enters, the signal is real.

**Downside:** No time-based exits. The only exit trigger is structural invalidation — price breaks 1.5% against entry (notional), which means the momentum thesis failed. At 10x leverage that's ~15% ROE max loss. Clean, binary: the trade works or the structure breaks.

**Breakeven lock:** At +15% ROE, the trailing stop moves to +1% ROE. This covers fees and guarantees a green close. Every trade that reaches +15% ROE is a winner. Period.

**Upside:** 8-tier trailing designed for explosive moves. At +40% ROE, only 20% is locked — the trade has room to run to 60%, 80%, 100%+. The 150% and 200% tiers exist because First Jump signals can produce 10x+ moves and Feral wants to ride them.

**Expected behavior vs v1:**

| Metric | Feral v1 | Feral v2 (expected) |
|---|---|---|
| Trades/day | 6-10 | 2-4 |
| Win rate | ~45-55% | ~55-65% (higher bar = better signals) |
| Avg winner | 10-20% ROE | 20-50%+ ROE (uncapped trailing) |
| Avg loser | -8 to -15% ROE | -12 to -15% ROE (structural invalidation) |
| Fee drag/day | $20-35 | $8-15 (fewer trades) |
| Profit factor | ~1.0-1.3 | ~1.3-1.8 (fewer losers, bigger winners) |
