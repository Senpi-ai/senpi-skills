# 🐺 DIRE WOLF v2 — Sniper Mode + High Water

**DIRE WOLF is not an optional overlay. It replaces WOLF's configuration entirely.** When you deploy DIRE WOLF, the agent must overwrite WOLF's entry filters, DSL settings, guard rails, and execution config with the values below. Standard WOLF settings must not persist alongside DIRE WOLF — they conflict, and the agent will revert to WOLF defaults if both exist.

**Base skill:** WOLF v6.1.1
**Philosophy:** Standard WOLF picks direction correctly (gross PnL +$137) but over-trades (16.7/day) and fees ($395) kill it. DIRE WOLF v2 fixes both problems: Sniper Mode halves trade frequency, and DSL High Water Mode captures more from every winner. Fewer trades, bigger captures.

---

## MANDATORY: Replace WOLF Config

When deploying DIRE WOLF, the agent MUST:

1. **Replace** `wolf-strategies.json` guard rails with DIRE WOLF values (not merge — replace)
2. **Replace** DSL tier configuration with High Water Mode tiers in every new state file
3. **Replace** the scanner cron mandate with DIRE WOLF entry filters
4. **Disable** rotation in the cron mandate and config (`maxRotationsPerDay: 0`)
5. **Verify** no standard WOLF defaults persist — DIRE WOLF and WOLF cannot run simultaneously on the same strategy

If the agent creates a position using standard WOLF DSL tiers or entry filters, DIRE WOLF is not active. Check the config after deployment.

---

## MANDATORY: DSL High Water Mode

**DIRE WOLF MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any DIRE WOLF position, you MUST include:

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

**FALLBACK (until DSL engine supports `pct_of_high_water`):** If the DSL engine does not yet support `lockMode: "pct_of_high_water"`, use these wide fixed tiers instead:

```json
{
  "tiers": [
    {"triggerPct": 5,  "lockPct": 1.5},
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 15, "lockPct": 10, "retrace": 0.012},
    {"triggerPct": 25, "lockPct": 18, "retrace": 0.010},
    {"triggerPct": 40, "lockPct": 32, "retrace": 0.008},
    {"triggerPct": 60, "lockPct": 50, "retrace": 0.006},
    {"triggerPct": 80, "lockPct": 68, "retrace": 0.005},
    {"triggerPct": 100, "lockPct": 88, "retrace": 0.004}
  ]
}
```

Switch to High Water tiers the moment the engine supports them.

---

## What Changes vs Standard WOLF

| Variable | Standard WOLF | DIRE WOLF v2 | Why |
|---|---|---|---|
| **DSL mode** | Fixed ROE tiers (4 tiers, 5-20%) | **High Water Mode (4 HW tiers → 85% infinite trail)** | Captures more from every winner. No ceiling. |
| **Signal type** | FIRST_JUMP + IMMEDIATE_MOVER | **FIRST_JUMP only** | IMMEDIATE_MOVER generates most marginal entries. FJ is the money signal. |
| **Min reasons** | 2+ | **3+** | Below 3 reasons = noise |
| **Previous rank** | No gate | **Must be ≥ 20 before jump** | Don't chase assets already in the top 20 |
| **4h price move** | No gate | **Skip if \|priceChg4h\| > 2%** | If it already moved 2%+, the easy money is gone |
| **Velocity** | vel > 0 | **contribVelocity > 0.03** | Minimum velocity gate filters out noise |
| **Top-10 block** | No gate | **Skip if currentRank ≤ 10 AND prevRank ≤ 20** | Don't chase peaked assets. Allow monster jumps (prevRank ≥ 20 passes). |
| **Rotation** | Enabled (cooldown 45min) | **DISABLED** | Every rotation = 2 trades = 5-7% ROE in fees. |
| **Max entries/day** | 8 | **6 (dynamic: 3 base, +1 per $100 profit, cap 6)** | Quality over quantity. Earn more slots by winning. |
| **Phase 1 floors** | 0.10/leverage flat | **Conviction-scaled (-20/-25/unrestricted by score)** | High-conviction FJs get maximum room |
| **Phase 1 time exits** | 90min hard, 45min weak peak, 30min dead weight | **All disabled** | Structural invalidation only |
| **Entry order type** | MARKET | **FEE_OPTIMIZED_LIMIT** | Saves 3 bps per entry |
| **G2 Drawdown halt** | Not implemented | **30% from peak → CLOSED** | Prevents slow multi-day bleed |
| **G5 Per-position cap** | Not implemented | **Loss > 5% of account → force close** | DSL is the mechanical stop. G5 is the risk limit. |
| **Daily loss limit** | 15% | **10%** | Tighter daily cap |
| **Stagnation TP** | None | **8% ROE stale 45min → close** | Don't round-trip green into red |

---

## Config Override File

```json
{
  "basedOn": "wolf",
  "version": "2.0",
  "name": "Dire Wolf",
  "description": "Sniper mode + High Water — fewer trades, zero rotation, maker fees, infinite trailing",

  "entryFilters": {
    "allowedSignals": ["FIRST_JUMP"],
    "contribExplosionRequiresFJ": true,
    "minReasons": 3,
    "minPrevRank": 20,
    "maxPriceChg4hPct": 2.0,
    "minVelocity": 0.03,
    "topTenBlock": true,
    "enforceRegimeDirection": true
  },

  "rotation": {
    "enabled": false,
    "maxRotationsPerDay": 0
  },

  "dsl": {
    "_spec": "https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md",
    "lockMode": "pct_of_high_water",
    "phase2TriggerRoe": 7,
    "phase1HardTimeoutMin": 0,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "convictionTiers": [
      {"minScore": 6,  "absoluteFloorRoe": -20, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 8,  "absoluteFloorRoe": -25, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 10, "absoluteFloorRoe": 0,   "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
      {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
      {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
      {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 8,
      "hwStaleMin": 45
    }
  },

  "guardRails": {
    "maxEntriesPerDay": 6,
    "bypassOnProfit": true,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60,
    "maxRotationsPerDay": 0,
    "maxSingleLossPct": 5,
    "drawdownHaltPct": 30,
    "dailyLossLimitPct": 10,
    "dynamicSlots": {
      "enabled": true,
      "baseMax": 3,
      "absoluteMax": 6,
      "unlockThresholds": [
        {"pnl": 100, "maxEntries": 4},
        {"pnl": 200, "maxEntries": 5},
        {"pnl": 300, "maxEntries": 6}
      ]
    }
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "smFlipCutOrderType": "MARKET",
    "riskGuardianOrderType": "MARKET",
    "_note": "Maker orders for planned entries. MARKET for all exits. Never ALO for stop losses."
  }
}
```

---

## Notification Policy

**ONLY alert:** Position OPENED or CLOSED, risk guardian triggered (G2 drawdown, G5 force close, cooldown), critical error.
**NEVER alert:** Scanner found nothing, DSL routine check, SM flip check clean, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

---

## How DIRE WOLF v2 Trades

**Entry:** FIRST_JUMP only, 3+ reasons, prevRank ≥ 20, velocity > 0.03, 4h move < 2%. Most scans produce nothing. When DIRE WOLF enters, the signal is high-conviction and early.

**No rotation:** Slots full = wait. Slots free naturally via structural invalidation (conviction-scaled floors), SM conviction collapse, and DSL trailing stops. No churn.

**Trailing (High Water):** Once past +7% ROE, stop locks 40% of peak. Past +20%, it's 85% of peak forever. A First Jump that runs +100% ROE locks +85%. A monster that runs +300% locks +255%. No ceiling. Standard WOLF's fixed tiers would cap at the highest tier — DIRE WOLF trails infinitely.

**Fee savings:** Every planned entry is a maker order (1.5 bps vs 4.5 bps taker). Exits stay MARKET for instant execution.

**Dynamic slots:** Base 3 entries/day. +$100 profit = 4 entries, +$200 = 5, +$300 = 6. Earn trust through results.

---

## Expected Behavior

| Metric | Standard WOLF | DIRE WOLF v2 (expected) |
|---|---|---|
| Trades/day | 16.7 | 6-8 |
| Fees/day | ~$66 | ~$20-30 |
| Profit factor | 0.73 | >1.2 |
| Rotations/day | 2-4 | 0 |
| HL fee per entry | 4.5 bps | 1.5 bps (maker) |
| Avg winner (standard) | 10-15% ROE | 10-15% ROE (same) |
| Avg winner (runners) | 20-30% ROE (capped) | **40-100%+ ROE (uncapped)** |
| Net daily P&L | -$43 | Target positive |

Two improvements stacked: Sniper Mode halves the fee drag, High Water Mode increases capture on every winner. Same directional accuracy, half the costs, bigger payoffs.
