# 🦊 FERAL FOX — Aggressive Trading Strategy

A trading strategy (config override) based on the FOX skill. Same scanner, same scripts, same DSL — different personality. FERAL FOX enters on weaker signals, gives trades more room to develop, trades both directions in any regime, and allocates 80% to autonomous trading.

**Base skill:** FOX v1.0
**Philosophy:** FOX's directional accuracy is 85%. The problem was never signal quality — it was stops too tight and filters too restrictive. FERAL FOX trusts the signals and gives them room.

---

## Deployment

Feral Fox runs on the FOX v1.1 skill. Deploy FOX first, then apply these overrides.

**If deploying fresh:** FOX's `AGENTS.md` contains a mandatory bootstrap gate that creates the copy trading monitor, market regime cron, and all autonomous trading crons on first session. This runs automatically — the agent checks for `config/bootstrap-complete.json` on every session and won't proceed until bootstrap is complete. Feral Fox inherits this behavior. No additional setup needed.

**If switching from standard FOX:** Update the config variables below in `fox-strategies.json` or provide them to your agent. The agent applies them on the next cron cycle. No restart needed.

---

## What Changes vs Standard FOX

| Variable | Standard FOX | FERAL FOX | Why |
|---|---|---|---|
| **Budget split** | 60/40 copy/autonomous | **20/80 copy/autonomous** | Trust the scanner, allocate more to autonomous |
| **minReasons** | 3 | **2** | FOX's 85% directional accuracy means even 2-reason FJs are worth taking |
| **Score threshold (normal)** | 6 | **5** | Lower bar to enter — more trades, same signal quality |
| **Score threshold (NEUTRAL)** | 8 | **6** | Trade NEUTRAL regime more freely |
| **FJ persistence** | Explosive immediate, normal 2 scans | **All signals immediate** | Don't wait. If it's a FJ, enter now. |
| **maxPriceChg4hPct (normal)** | 2.0% | **3.0%** | Allow entries on assets that already moved a bit |
| **maxPriceChg4hPct (high score)** | 4.0% | **5.0%** | Even more room for explosive signals |
| **minVelocity** | 0.03 | **0.01** | Lower velocity gate — catch slower-building moves |
| **Phase 1 floor (score 5-7)** | 0.03/lev (30min) | **0.04/lev (60min)** | Wider floor + longer timeout — let it breathe |
| **Phase 1 floor (score 8-9)** | 0.035/lev (45min) | **0.045/lev (75min)** | More room for medium conviction |
| **Phase 1 floor (score 10+)** | 0.04/lev (60min) | **0.05/lev (90min)** | Maximum room for best signals |
| **Dead weight cut** | 10/15/20min by score | **DISABLED** | 85% directional accuracy — flat trades aren't dead, they just haven't moved yet. Hard timeout handles the real duds. |
| **Weak peak cut** | 15/20/30min by score | **25/35/45min by score** | Don't cut weak peaks early — they may build |
| **maxEntriesPerDay** | 6 | **10** | More trades per day |
| **maxPositions** | 6 (tiered) | **6 (flat $950 each)** | Same slots, simpler sizing |
| **enforceRegimeDirection** | true | **false** | Trade both directions in all regimes |
| **Re-entry** | enabled, 75% margin, score 5 | **enabled, 100% margin, score 4** | Re-enter faster and at full size |
| **maxDailyLossPct** | 8% | **12%** | Higher daily loss tolerance |
| **maxDrawdownPct** | 20% | **25%** | Accept more drawdown |
| **consecutiveLossCooldown** | 60 min | **30 min** | Shorter cooldown — get back in faster |

---

## Config Override File

Deploy FOX with these overrides. The agent reads this and applies on top of FOX defaults:

```json
{
  "basedOn": "fox",
  "version": "1.0",
  "name": "Feral Fox",
  "description": "Aggressive FOX — lower entry bars, wider stops, more trades, both directions",

  "budgetSplit": {
    "copyTradingPct": 20,
    "autonomousPct": 80
  },

  "entryFilters": {
    "minReasons": 2,
    "minScore": 5,
    "minScoreNeutral": 6,
    "minVelocity": 0.01,
    "maxPriceChg4hPct": 3.0,
    "maxPriceChg4hHighScore": 5.0,
    "fjPersistence": "all_immediate",
    "enforceRegimeDirection": false
  },

  "dsl": {
    "convictionTiers": [
      {"minScore": 5,  "floorBase": 0.04, "hardTimeoutMin": 60, "weakPeakCutMin": 25, "deadWeightCutMin": 0},
      {"minScore": 8,  "floorBase": 0.045, "hardTimeoutMin": 75, "weakPeakCutMin": 35, "deadWeightCutMin": 0},
      {"minScore": 10, "floorBase": 0.05, "hardTimeoutMin": 90, "weakPeakCutMin": 45, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 5,   "lockPct": 2},
      {"triggerPct": 10,  "lockPct": 5},
      {"triggerPct": 20,  "lockPct": 14},
      {"triggerPct": 30,  "lockPct": 24},
      {"triggerPct": 40,  "lockPct": 34},
      {"triggerPct": 50,  "lockPct": 44},
      {"triggerPct": 65,  "lockPct": 56},
      {"triggerPct": 80,  "lockPct": 72},
      {"triggerPct": 100, "lockPct": 90}
    ]
  },

  "reentry": {
    "enabled": true,
    "marginPct": 100,
    "minScore": 4,
    "maxOriginalLossROE": 15,
    "windowMin": 120,
    "minContribVelocity": 3
  },

  "risk": {
    "maxEntriesPerDay": 10,
    "maxDailyLossPct": 12,
    "maxDrawdownPct": 25,
    "maxSingleLossPct": 8,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 30,
    "maxPositions": 6
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "takeProfitOrderType": "FEE_OPTIMIZED_LIMIT",
    "_note": "SL and emergency exits MUST be MARKET. Never ALO for stop losses — a 60s delay at -3% ROE can become -6% ROE."
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
- Watchdog margins are fine
- Any reasoning, thinking, or narration

All scanner and monitoring crons run on **isolated sessions** with `agentTurn` payloads. No main session narration.

---

## When To Use Feral Fox vs Standard Fox

| Condition | Use Standard FOX | Use FERAL FOX |
|---|---|---|
| New to Senpi / small budget | ✓ | — |
| Proven profitable on standard FOX, want more trades | — | ✓ |
| High volatility market (lots of FJs firing) | — | ✓ |
| Low volatility / choppy market | ✓ | — |
| Risk-averse / capital preservation focus | ✓ | — |
| Aggressive / growth focus | — | ✓ |
| Budget > $5K | Either | ✓ (more trades to compound) |

---

## Expected Behavior vs Standard FOX

| Metric | Standard FOX | FERAL FOX (expected) |
|---|---|---|
| Trades/day | 3-5 | 6-10 |
| Win rate | ~55-60% | ~45-55% (lower bar = more marginal trades) |
| Avg winner ROE | 8-15% | 10-20% (wider stops let winners run further) |
| Avg loser ROE | -5 to -10% | -8 to -15% (wider stops = bigger losses when wrong) |
| Daily fee drag | ~$12-20 | ~$20-35 (more trades, but ALO helps) |
| Profit factor | ~1.0-1.2 | ~1.0-1.3 (higher variance, potentially higher upside) |

The thesis: Standard FOX's 85% directional accuracy is being wasted by tight stops that kill correct trades too early. FERAL FOX bets that giving trades more room produces better outcomes even if a few more losers slip through. Higher variance, but the big winners more than compensate.
