# DSL High Water Mode — Adoption Guide v2.0

**Target:** Every Senpi skill uses DSL High Water Mode as the default DSL configuration.
**Spec:** https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md
**Engine:** DSL v5.3.1 — fully supports `lockMode: "pct_of_high_water"` with per-tick floor recalculation.

---

## What Changed in DSL v5.3.1

Update implements everything from the High Water spec:

1. **Per-tick floor recalculation.** The tier floor now updates on EVERY tick using the latest high-water mark — not just when entering a new tier. A trade at +18% ROE within Tier 2 (lockHwPct 40) has its floor at 40% of +18%, not 40% of the +10% it was at when entering Tier 2.

2. **`lockMode: "pct_of_high_water"` support.** Set this in the state file and use `lockHwPct` in tiers. The engine calculates the floor as a percentage of highWaterRoe.

3. **`lockPct` also works in HW mode.** If a tier uses `lockPct` (not `lockHwPct`) with `lockMode: "pct_of_high_water"`, the engine recalculates using the entry→HW price range. Both field names work.

4. **Per-tier `consecutiveBreachesRequired`.** Each tier can override the phase-level default. Also accepts `breachesRequired` as an alias.

5. **SL auto-syncs on HW advance.** The Hyperliquid stop-loss order updates whenever the effective floor changes — which now happens every tick in HW mode.

6. **`highWaterRoe` tracked.** Updated on every tick alongside `highWaterPrice`. Used for the ROE-based HW floor calculation.

**Legacy tiers continue to work.** Positions using `lockPct` with `lockMode: "fixed_roe"` (or omitted) behave exactly as before. Zero breaking changes.

---

## How to Apply (All Skills)

### Step 1: Add to your skill's DSL config

```json
{
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": <see per-skill table>,
  "floorBase": <see per-skill table>,
  "tiers": <see per-skill High Water tiers>
}
```

### Step 2: Use High Water tiers in DSL state files

When creating DSL state files via `dsl-cli.py add-dsl`, include `lockMode: "pct_of_high_water"` and use `lockHwPct` in the tiers array. The engine will trail the floor as a percentage of high-water ROE on every tick.

### Step 3: Legacy fallback (optional, for existing positions)

Existing positions on legacy tiers (`lockPct` with `lockMode: "fixed_roe"`) continue working unchanged. New positions should use High Water tiers.

---

## Per-Skill Configuration

### 🦊 FOX v1.6 — Explosive Breakout Sniper
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.015,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🦊 Feral Fox v3.0 — Production-Tuned Momentum
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "floorBase": 0.05,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 10, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 15, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
    {"triggerPct": 25, "lockHwPct": 95, "consecutiveBreachesRequired": 1}
  ]
}
```
_Tightest Phase 2 in the zoo. Wide Phase 1 (-50% ROE) + 95% lock at T4._

---

### 👻 Ghost Fox v2.0 — Ultra-Selective Infinite Trailing
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
    {"triggerPct": 40, "lockHwPct": 90, "consecutiveBreachesRequired": 1}
  ]
}
```
_5 tiers (unique). Extra T5 at +40% ROE locks 90% for big runners._

---

### 🐺 DIRE WOLF — Sniper Mode
```json
"dsl": {
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

---

### 🐻 GRIZZLY v2.0 — BTC Alpha Hunter
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "floorBase": 0.035,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 20, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_BTC-specific wide tiers. 15-20x leverage means BTC pullbacks of 5-10% ROE are normal._

---

### 🐆 CHEETAH v2.0 — HYPE Alpha Hunter
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "floorBase": 0.06,
  "tiers": [
    {"triggerPct": 10,  "lockHwPct": 10, "consecutiveBreachesRequired": 3},
    {"triggerPct": 20,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 35,  "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50,  "lockHwPct": 65, "consecutiveBreachesRequired": 1},
    {"triggerPct": 75,  "lockHwPct": 80, "consecutiveBreachesRequired": 1},
    {"triggerPct": 100, "lockHwPct": 88, "consecutiveBreachesRequired": 1}
  ]
}
```
_Widest tiers in the zoo. HYPE wicks 5-10% ROE routinely during trends. 6 tiers with 6% notional floor._

---

### 🦅 HAWK v1.2 — Multi-Asset Best Signal
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.02,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🐍 VIPER v2.1 — Range-Bound Sniper
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "floorBase": 0.015,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Lower Phase 2 trigger (+5%) — range profits are smaller, protect early._

---

### 🐍 MAMBA — Range Breakout Predator
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🐍 ANACONDA — Deep Liquidity Range Hunter
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 5,
  "floorBase": 0.015,
  "tiers": [
    {"triggerPct": 5,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
    {"triggerPct": 10, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 20, "lockHwPct": 88, "consecutiveBreachesRequired": 1}
  ]
}
```
_Tighter geometry than VIPER/MAMBA — 88% lock at T4._

---

### 🐍 COBRA v2.0 — Triple Convergence
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.02,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🦬 BISON — Conviction Trend Holder
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "floorBase": 0.03,
  "tiers": [
    {"triggerPct": 10,  "lockHwPct": 0,  "consecutiveBreachesRequired": 3},
    {"triggerPct": 20,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 30,  "lockHwPct": 40, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50,  "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 75,  "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 100, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Widest momentum tiers. T1 locks 0% (pure trailing) because conviction holds need room to develop._

---

### 🦉 OWL v5 — Contrarian Crowding-Unwind
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "floorBase": 0.04,
  "tiers": [
    {"triggerPct": 10, "lockHwPct": 20, "consecutiveBreachesRequired": 3},
    {"triggerPct": 20, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 35, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 75, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Contrarian entries retrace hard before the unwind. Widest early tiers + 4% notional floor._

---

### 🐊 CROC — Funding Rate Arbitrage
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "floorBase": 0.02,
  "tiers": [
    {"triggerPct": 8,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 65, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🐊 GATOR — Patient Funding Arbitrage
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 10,
  "floorBase": 0.035,
  "tiers": [
    {"triggerPct": 10, "lockHwPct": 20, "consecutiveBreachesRequired": 3},
    {"triggerPct": 20, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 35, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 50, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 75, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Funding arb is slow. Widest patience + 3.5% notional floor. Structural thesis exits (funding flip) are primary exit — DSL is the safety net._

---

### 🦈 SHARK — Liquidation Cascade Hunter
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.018,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🦂 SCORPION v1.1 — Whale Wallet Tracker
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.02,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 35, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
    {"triggerPct": 30, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🦗 MANTIS — High-Conviction Whale Mirror
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "floorBase": 0.03,
  "tiers": [
    {"triggerPct": 8,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 65, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40, "lockHwPct": 80, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Whale timing is uncertain — wide early tiers (25% at T1)._

---

### 🦏 RHINO — Momentum Pyramider
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 8,
  "floorBase": 0.025,
  "tiers": [
    {"triggerPct": 8,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 60, "consecutiveBreachesRequired": 2},
    {"triggerPct": 40, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_DSL applies to the full pyramided position, not per-add. Scout entry needs room (25% at T1)._

---

### 🐺 HYENA — OI Momentum Pyramider
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "floorBase": 0.025,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 25, "consecutiveBreachesRequired": 3},
    {"triggerPct": 15, "lockHwPct": 45, "consecutiveBreachesRequired": 2},
    {"triggerPct": 25, "lockHwPct": 65, "consecutiveBreachesRequired": 1},
    {"triggerPct": 40, "lockHwPct": 80, "consecutiveBreachesRequired": 1},
    {"triggerPct": 60, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```

---

### 🐺 JACKAL v2.0 — First Jump Pyramider
```json
"dsl": {
  "lockMode": "pct_of_high_water",
  "phase2TriggerRoe": 7,
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 1},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
_Same tiers as FOX — the pyramid mechanic is the differentiator, not the DSL._

---

## Tier Design Philosophy

| Skill Type | Phase 2 Trigger | T1 Lock | 85% Trail At | Floor Base | Why |
|---|---|---|---|---|---|
| **Momentum** (FOX, Dire Wolf, Hawk) | +7% | 40% HW | +20% ROE | 1.5-2% | Fast entries, quick trailing |
| **Momentum aggressive** (Feral v3) | +5% | 50% HW | +15% ROE (95% at +25%) | 5% | Widest Phase 1, tightest Phase 2 |
| **Ultra-selective** (Ghost Fox v2) | +5% | 40% HW | +20% (90% at +40%) | — | 5 tiers, 90% lock on runners |
| **Convergence** (COBRA, SHARK) | +7% | 30-35% HW | +30% ROE | 1.8-2% | Multiple signals, moderate patience |
| **Range** (VIPER, MAMBA, ANACONDA) | +5% | 30-35% HW | +20% ROE | 1.5% | Smaller moves, protect early |
| **Funding arb** (CROC, GATOR) | +8-10% | 20-25% HW | +40-75% ROE | 2-3.5% | Slow but structural. Patience. |
| **Conviction hold** (BISON) | +10% | 0% HW | +100% ROE | 3% | Multi-day holds, maximum room |
| **Single-asset BTC** (GRIZZLY) | +5% | 20% HW | +30% ROE | 3.5% | BTC pullbacks normal at 15x |
| **Single-asset HYPE** (CHEETAH) | +10% | 10% HW | +100% ROE | 6% | HYPE wicks 5-10% routinely. 6 tiers. |
| **Contrarian** (OWL) | +10% | 20% HW | +75% ROE | 4% | Contrarian entries retrace hard before working |
| **Whale tracking** (SCORPION, MANTIS) | +7-8% | 25-35% HW | +30-60% ROE | 2-3% | Whale timing uncertain |
| **Pyramider** (RHINO, HYENA, JACKAL) | +7-8% | 25-40% HW | +60% ROE | 2.5% | Scout entries need room |

---

## Agent Instructions

Add this to every skill's SKILL.md or cron mandate:

```
DSL CONFIGURATION:
Engine: DSL v5.3.1+
Mode: High Water (pct_of_high_water)
Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files, ALWAYS include:
  "lockMode": "pct_of_high_water"
  Use the tiers array with lockHwPct fields.
  The floor trails the high-water mark on EVERY tick — no ceiling.

Legacy tiers (lockPct with lockMode omitted) still work for existing positions.
New positions MUST use High Water tiers.
```
