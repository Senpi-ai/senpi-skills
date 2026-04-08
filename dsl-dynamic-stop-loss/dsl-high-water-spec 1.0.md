# DSL High Water Mode — Conviction-Scaled Trailing Stop

As of 11 March, 2026 DSL High Water Mode is the highest-performing DSL configuration in the Senpi ecosystem. Battle-tested across 50+ trades. Portable — any skill can apply this as a DSL profile override.

## Why This Works

Most DSL configs use fixed ROE tiers: "at +10% ROE, lock +5% ROE." This creates a ceiling — the locked amount is static regardless of how high the trade runs. DSL High Water Mode uses **percentage-of-high-water** locks instead. The stop loss is always 85% of wherever the peak is, and it trails infinitely.

A trade that hits +50% ROE has its stop at +42.5%. A trade that hits +200% ROE has its stop at +170%. There is no ceiling. The geometry stays constant — the trade always keeps 85% of its best moment.

---

## Phase 1: The Incubation Zone

Phase 1 absorbs entry volatility. The goal: don't get chopped out by market maker wicks before the momentum breakout arrives.

### Conviction-Scaled Absolute Floors

| Entry Score | Max ROE Loss | Rationale |
|---|---|---|
| 6-7 (low conviction) | -20% ROE | Tight leash — marginal signal, cut fast if wrong |
| 8-9 (medium conviction) | -25% ROE | More room — multi-signal alignment, trust it |
| 10+ (high conviction) | Unrestricted | Maximum breathing room — only time limits apply |

### Time Overrides

| Rule | Trigger | Action |
|---|---|---|
| Hard timeout | Position hasn't hit +7% ROE within 30 min | Close at market |
| Weak peak cut | Peak ROE < 3% and declining at 15 min mark | Close at market |
| Dead weight | **REMOVED** | Early wicks need time to resolve |

Phase 1 ends the moment the position reaches +7% ROE. The absolute floor is discarded. Phase 2 takes over.

---

## Phase 2: The Trailing Locks

Phase 2 uses **percentage-of-high-water** locking. The stop loss is always a percentage of the highest ROE the trade has ever reached. As the trade climbs, the stop climbs with it. It never comes back down.

### Tier Structure

| Tier | Trigger | Lock | Breaches to Close | Behavior |
|---|---|---|---|---|
| 1 | +7% ROE | 40% of high water | 3 consecutive | Patient — lets wicks resolve |
| 2 | +12% ROE | 55% of high water | 2 consecutive | Tightening |
| 3 | +15% ROE | 75% of high water | 2 consecutive | Aggressive protection |
| 4 | +20% ROE+ | 85% of high water | 1 (instant) | Maximum lock — any breach = exit |

### How the Infinite Trail Works

The key insight: **Tier 4 doesn't have a ceiling.** Once the trade is in Tier 4, the lock is always 85% of the high-water mark, and the high-water mark updates every time price makes a new peak.

Example at Tier 4:

| High Water ROE | Stop Loss (85%) | If Price Retraces To | Result |
|---|---|---|---|
| +20% | +17.0% | +16.9% | Exit — locked +16.9% |
| +50% | +42.5% | +42.4% | Exit — locked +42.4% |
| +100% | +85.0% | +84.9% | Exit — locked +84.9% |
| +200% | +170.0% | +169.9% | Exit — locked +169.9% |

The stop loss trails the trade to the moon. It maintains exact 85% profit-lock geometry until the asset reverses and trips the wire. No ceiling. No fixed target. Pure trailing.

### Breach Counting

Tiers 1-3 use consecutive breach counting to prevent micro-wick exits:

- Price drops below lock level → breach count +1
- Price recovers above lock level → breach count resets to 0
- Breach count hits threshold → close at market

Tier 4 is instant — one tick below the 85% line and it's a market sell. At this profit level, protecting the gain is more important than avoiding a wick.

---

## Config (JSON — drop into any skill's DSL profile)

```json
{
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "convictionTiers": [
      {"minScore": 6,  "absoluteFloorRoe": -20, "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0},
      {"minScore": 8,  "absoluteFloorRoe": -25, "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0},
      {"minScore": 10, "absoluteFloorRoe": 0,   "hardTimeoutMin": 30, "weakPeakCutMin": 15, "deadWeightCutMin": 0}
    ]
  },
  "phase2TriggerRoe": 7,
  "phase2": {
    "enabled": true,
    "lockMode": "pct_of_high_water",
    "tiers": [
      {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
      {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
      {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
      {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
    ]
  },
  "execution": {
    "phase1SlOrderType": "MARKET",
    "phase2SlOrderType": "MARKET",
    "breachCloseOrderType": "MARKET"
  }
}
```

### Key Config Fields

| Field | Value | Notes |
|---|---|---|
| `lockMode` | `pct_of_high_water` | This is what makes High Water Mode different. Standard DSL uses `fixed_roe` (lock a static ROE value). High Water Mode uses `pct_of_high_water` (lock a percentage of peak ROE). |
| `lockHwPct` | 40 / 55 / 75 / 85 | Percentage of high-water mark to lock at each tier. Tier 4 at 85% means the trade always keeps 85% of its best ROE. |
| `phase2TriggerRoe` | 7 | ROE threshold to graduate from Phase 1 to Phase 2. Lower than standard (10) — start trailing earlier on momentum entries. |
| `convictionTiers` | by score | Phase 1 floor scales with entry quality. High-conviction signals get unrestricted room. |
| `deadWeightCutMin` | 0 (all tiers) | Removed everywhere. Early wicks need time to resolve. |

---

## Critical: DSL State File Must Include Tiers

**The tiers array must be written into every DSL state file at creation time.** The profile config (`dsl-profile.json`) tells the CLI what tiers to use, but the actual DSL runner (`dsl-v5.py`) reads tiers from the per-position state file, not the profile. If the state file is created without the `tiers` array, the engine falls back to flat 1.5% retrace from high water — the entire High Water Mode is silently disabled.

### What must be in every state file

When `dsl-cli.py add-dsl` creates a position state file, or when an agent creates one manually, it **must** include:

```json
{
  "asset": "BTC",
  "direction": "LONG",
  "entryPrice": 85000.0,
  "leverage": 10,
  "phase": 1,
  "score": 9,
  "highWaterPrice": 85000.0,
  "highWaterRoe": 0,
  "currentTierIndex": -1,
  "consecutiveBreaches": 0,
  "phase1": {
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloorRoe": -25,
    "hardTimeoutMin": 30,
    "weakPeakCutMin": 15,
    "deadWeightCutMin": 0
  },
  "phase2TriggerRoe": 7,
  "lockMode": "pct_of_high_water",
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ],
  "createdAt": "2026-03-11T00:00:00.000Z",
  "active": true
}
```

### Common failure mode

If an agent creates positions via direct MCP calls (`create_position`) instead of through `dsl-cli.py`, the state file may be hand-built without the tiers array. **The agent must always include the full `tiers`, `lockMode`, and `phase2TriggerRoe` fields.** Without them, High Water Mode is dead — the position runs on fallback flat retrace and the infinite trailing never activates.

### How to fix existing positions missing tiers

For any active state file missing the `tiers` array, inject it:

```python
import json, glob

TIERS = [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
]

for path in glob.glob("/data/workspace/dsl/*/*.json"):
    if "_archived" in path or "strategy-" in path:
        continue
    with open(path) as f:
        state = json.load(f)
    if "tiers" not in state or not state["tiers"]:
        state["tiers"] = TIERS
        state["lockMode"] = "pct_of_high_water"
        state["phase2TriggerRoe"] = 7
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        print(f"Fixed: {path}")
```

Run this once to backfill any positions created before High Water Mode was configured. New positions created via `dsl-cli.py` with the correct profile will include tiers automatically.

---

## How To Apply to Other Skills

### Option 1: Replace your skill's dsl-profile.json
Copy the JSON config above into your skill's `dsl-profile.json`. The DSL v5.3.1 engine reads it on every tick.

### Option 2: Pass as override via dsl-cli.py
```bash
python3 dsl-cli.py update-dsl <strategy-id> \
  --configuration @dsl-high-water-profile.json
```

### Option 3: Trading strategy config override
Add the DSL block to your trading strategy's override file (same pattern as DIRE WOLF, FERAL FOX):
```json
{
  "basedOn": "your-skill",
  "name": "Your Skill + High Water DSL",
  "dsl": { ... the config above ... }
}
```

### Compatibility Note
The `lockMode: "pct_of_high_water"` field requires DSL v5.3.1+. If your DSL engine is older, it falls back to `fixed_roe` behavior (standard tier locking). Check your DSL version before deploying.

---

## Comparison: Standard DSL vs High Water Mode

| Aspect | Standard DSL (fixed ROE) | High Water Mode (pct of high water) |
|---|---|---|
| Tier 1 lock at +10% ROE | Lock +5% ROE (fixed) | Lock +4% ROE (40% of 10) |
| Tier at +50% ROE | Lock +40% ROE (fixed) | Lock +42.5% ROE (85% of 50) |
| Tier at +100% ROE | Lock +80% ROE (fixed) | Lock +85% ROE (85% of 100) |
| Tier at +200% ROE | Lock +160% ROE (fixed) | Lock +170% ROE (85% of 200) |
| Ceiling | Yes — highest tier's lock is the max | **No ceiling** — trails infinitely |
| Best for | Momentum trades (minutes to hours) | Conviction trades + explosive runners |

At moderate profit levels (+20-50% ROE), both systems perform similarly. The divergence happens on big runners — at +100% ROE, High Water Mode locks 5% more than standard. At +200%, it locks 10% more. The gap compounds as the trade runs further.

---

## When To Use This vs Standard DSL

| Scenario | Use Standard DSL | Use High Water Mode |
|---|---|---|
| Scalping (minutes, small ROE) | ✓ | — |
| Momentum trades (1-4 hours) | Either | ✓ |
| Conviction holds (4+ hours) | — | ✓ |
| Explosive runners (First Jumps, cascades) | — | ✓ |
| Risk-averse (want fixed known exits) | ✓ | — |
| Profit-maximizing (want uncapped upside) | — | ✓ |
