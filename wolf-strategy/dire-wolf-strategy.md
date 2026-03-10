# 🐺 DIRE WOLF — Sniper Mode

A trading strategy (config override) based on the WOLF v6.1.1 skill. Same scanner, same scripts, same DSL — different rules of engagement.

**Base skill:** WOLF v6.1.1
**Philosophy:** Standard WOLF trades 16.7 times/day and picks direction correctly but fees could turn it into a net loser. DIRE WOLF fixes this: fewer trades, zero rotation, tighter entry filters, wider stops, maker orders. The directional accuracy is already there — DIRE WOLF lets it breathe.

---

## What Changes vs Standard WOLF

| Variable | Standard WOLF | DIRE WOLF | Why |
|---|---|---|---|
| **Signal type** | FIRST_JUMP + IMMEDIATE_MOVER | **FIRST_JUMP only** | IMMEDIATE_MOVER generates most marginal entries. FJ is the money signal. |
| **Min reasons** | 2+ | **3+** | Below 3 reasons = noise |
| **Previous rank** | No gate | **Must be ≥ 20 before jump** | Don't chase assets already in the top 20 |
| **4h price move** | No gate | **Skip if \|priceChg4h\| > 2%** | If it already moved 2%+, the easy money is gone |
| **Velocity** | vel > 0 | **contribVelocity > 0.03** | Minimum velocity gate filters out noise |
| **Top-10 block** | No gate | **Skip if currentRank ≤ 10 AND prevRank ≤ 20** | Don't chase peaked assets. Allow monster jumps (prevRank ≥ 20 passes). |
| **Rotation** | Enabled (cooldown 45min) | **DISABLED** | Every rotation = 2 trades = 5-7% ROE in fees. At 0.73 profit factor, rotation amplifies losses. |
| **Max entries/day** | 8 | **6** | Quality over quantity |
| **Phase 1 floor** | 0.10/leverage (~100% ROE) | **0.12/leverage (~120% ROE)** | Wider floor + G5 backstop. Big winners average 274 min — they need room. |
| **Phase 1 dead weight** | 30min | **DISABLED** | Consistent with all other skills. Hard timeout handles real duds. |
| **Entry order type** | MARKET | **FEE_OPTIMIZED_LIMIT** | Saves 3 bps per entry. At 6 trades/day = ~$18/day saved. |
| **G2 Drawdown halt** | Not implemented | **30% from peak → CLOSED** | Prevents slow multi-day bleed without circuit breaker |
| **G5 Per-position cap** | Not implemented | **Loss > 5% of account → force close** | DSL is the mechanical stop. G5 is the risk limit. |
| **Daily loss limit** | 15% | **10%** | Tighter daily cap |
| **Cooldown** | 60 min | **60 min** | Unchanged |
| **Max consecutive losses** | 3 | **3** | Unchanged |

---

## Config Override File

```json
{
  "basedOn": "wolf",
  "version": "1.0",
  "name": "Dire Wolf",
  "description": "Sniper mode — fewer trades, zero rotation, maker orders, wider stops, tighter entry filters",

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
    "maxRotationsPerDay": 0,
    "_reenableCriteria": "Only re-enable when: profit factor > 1.0, trade frequency < 8/day, cap at 1/day FIRST_JUMP only"
  },

  "dsl": {
    "phase1RetraceRoe": 12,
    "phase1HardTimeoutMin": 90,
    "phase1WeakPeakMin": 45,
    "phase1DeadWeightMin": 0,
    "tiers": [
      {"triggerPct": 5,   "lockPct": 2},
      {"triggerPct": 10,  "lockPct": 5},
      {"triggerPct": 15,  "lockPct": 10},
      {"triggerPct": 20,  "lockPct": 15},
      {"triggerPct": 30,  "lockPct": 24},
      {"triggerPct": 50,  "lockPct": 42},
      {"triggerPct": 75,  "lockPct": 60},
      {"triggerPct": 100, "lockPct": 85},
      {"triggerPct": 150, "lockPct": 120}
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
    "_note": "Maker orders for planned entries (saves 3 bps). MARKET for all exits, SL, SM flip cuts, and risk guardian force closes. Never ALO for stop losses."
  }
}
```

---

## Notification Policy

Same as all Senpi skills:

**ONLY alert the user when:**
- Position OPENED or CLOSED
- Risk guardian triggered (gate closed, G2 drawdown halt, G5 force close, cooldown)
- Critical error (MCP auth expired, 3+ consecutive failures)

**NEVER alert for:**
- Scanner ran and found nothing
- DSL checked positions and nothing changed
- Health check passed
- SM flip check found no flips
- Any reasoning, thinking, or narration

All crons run on **isolated sessions**. Use `NO_REPLY` for idle cycles.

---

## How Dire Wolf Trades Differently

**Entry:** FIRST_JUMP only, 3+ reasons, prevRank ≥ 20, velocity > 0.03, 4h move < 2%. Most scans produce nothing. When DIRE WOLF enters, the signal is high-conviction and early — catching the move before it peaks.

**No rotation:** Slots full = wait. Slots free naturally via DSL Phase 1 cuts (90min max), SM conviction collapse, and trailing stops. With 3-6 entries/day, slots turn over every 2-4 hours. No need to churn.

**Wider stops:** Phase 1 floor at 0.12/leverage (~120% ROE at 10x). But G5 caps any single position at 5% of account value — that's the real backstop. The DSL floor gives room; G5 prevents catastrophe.

**Fee savings:** Every planned entry is a maker order (1.5 bps vs 4.5 bps taker). Exits stay MARKET for instant execution. Hybrid approach saves ~33% on exchange fees.

**Dynamic slots:** Base 3 entries/day. Profitable days unlock more: +$100 = 4 entries, +$200 = 5, +$300 = 6. Earning trust through results.

---

## Expected Behavior vs Standard WOLF

| Metric | Standard WOLF | DIRE WOLF (expected) |
|---|---|---|
| Trades/day | 16.7 | 6-8 |
| Fees/day | ~$66 | ~$20-30 |
| Profit factor | 0.73 | >1.0 |
| Rotations/day | 2-4 | 0 |
| HL fee per entry | 4.5 bps | 1.5 bps (maker) |
| Net daily P&L | -$43 | Target positive |
| Win rate | ~55% | ~55-60% (same scanner, better filter) |
| Avg hold time | Short (rotation churn) | 2-4 hours (natural exits) |

The single biggest driver: reducing trade frequency. Same directional accuracy, less than half the fees.

---

## When To Use

| Condition | Standard WOLF | DIRE WOLF |
|---|---|---|
| High-volume market with many FJs | Either | ✓ (more signals to filter) |
| Low-volume / choppy market | ✓ (IMMEDIATE picks up scraps) | ✓ (sits out, avoids chop) |
| Small budget (< $2K) | — | ✓ (fewer fees, more patient) |
| Large budget (> $5K) | Either | ✓ (fee savings compound) |
| Fee drag is the main problem | — | ✓ (designed for this) |
| Want maximum trade frequency | ✓ | — |
