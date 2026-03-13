# DSL High Water Mode — Implementation Spec for dsl-v5.py

**Version:** DSL v5.3.1 (2026-03-13) — ALL ITEMS IMPLEMENTED. Per-tick recalculation, lockMode support, per-tier breaches, SL auto-sync, highWaterRoe tracking — all shipped.

## Summary

5 skills and trading strategies (GRIZZLY, BISON, GHOST FOX, DIRE WOLF, MAMBA) are shipping with High Water Mode configs, but dsl-v5.py doesn’t support it yet. They’re falling back to legacy fixed tiers or failing silently. This is the single highest-impact DSL change — it unlocks infinite trailing for every skill in the ecosystem.

Full spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

## What Needs to Change

### 1. New field in state file: `lockMode`

Two modes:

| lockMode | Behavior | Current Support |
| --- | --- | --- |
| `fixed_roe` (default) | Tier lockPct is a fixed ROE value. At T3 with lockPct=22, floor = 22% ROE. | ✅ This is how it works today |
| `pct_of_high_water` | Tier lockHwPct is a percentage of the high-water ROE. At T3 with lockHwPct=75 and hwROE=40%, floor = 30% ROE. | ❌ Needs implementation |

If `lockMode` is missing from state, default to `fixed_roe` (backward compatible, nothing breaks).

### 2. New field in tier objects: `lockHwPct`

Current tier format:

```json
{"triggerPct": 30, "lockPct": 22, "retrace": 0.012}
```

High Water tier format:

```json
{"triggerPct": 30, "lockHwPct": 75, "consecutiveBreachesRequired": 2}
```

The engine should check: if `lockHwPct` exists in the tier, use percentage-of-high-water calculation. If only `lockPct` exists, use fixed ROE (current behavior). This means both formats can coexist in the same engine.

### 3. Floor calculation change

**Current (fixed_roe):**

```python
tier_floor_roe = tier["lockPct"]
# Convert to price:
# LONG: entry * (1 + tier_floor_roe / 100 / leverage)
# SHORT: entry * (1 - tier_floor_roe / 100 / leverage)
```

**New (pct_of_high_water):**

```python
if state.get("lockMode") == "pct_of_high_water" and "lockHwPct" in current_tier:
    tier_floor_roe = high_water_roe * current_tier["lockHwPct"] / 100
else:
    tier_floor_roe = current_tier["lockPct"]

# Price conversion is the same as before — just the ROE number changes
# LONG: entry * (1 + tier_floor_roe / 100 / leverage)
# SHORT: entry * (1 - tier_floor_roe / 100 / leverage)
```

That’s it. One `if` statement. The rest of the floor-to-price math, the breach detection, the close logic — all unchanged.

### 4. The infinite trail behavior

In fixed_roe mode, the floor is static once a tier is reached. At T3 with lockPct=22, the floor is always at 22% ROE regardless of where price goes after.

In pct_of_high_water mode, the floor moves EVERY TICK because it’s a percentage of the high-water mark, which updates every tick:

```python
# This already happens in dsl-v5.py:
if current_roe > state["highWaterRoe"]:
    state["highWaterRoe"] = current_roe

# The new part — floor recalculates from updated high water:
if state.get("lockMode") == "pct_of_high_water" and "lockHwPct" in current_tier:
    tier_floor_roe = state["highWaterRoe"] * current_tier["lockHwPct"] / 100
```

This means at Tier 5 (lockHwPct=85):
- hwROE = 30% → floor = 25.5% ROE
- hwROE = 50% → floor = 42.5% ROE
- hwROE = 100% → floor = 85% ROE
- hwROE = 500% → floor = 425% ROE

The floor trails the peak automatically. No new tiers needed. No ceiling.

### 5. Per-tier breach counts

Current: `phase2.consecutiveBreachesRequired` is a single value for all of Phase 2.

High Water configs put `consecutiveBreachesRequired` on each tier:

```json
{"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
{"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
```

Lower tiers are patient (3 breaches — let wicks resolve). Higher tiers are instant (1 breach — protect the profit).

Implementation: when checking breaches, read from the current tier first, fall back to the phase-level default:

```python
max_breaches = current_tier.get("consecutiveBreachesRequired",
                                 phase2_config.get("consecutiveBreachesRequired", 1))
```

### 6. SL sync to Hyperliquid

The `edit_position` SL sync must recalculate every tick in High Water mode because the floor moves. In fixed_roe mode, the SL only needs updating on tier changes. In pct_of_high_water mode, the SL needs updating whenever the high-water mark advances.

Add a check:

```python
# Only sync SL if floor actually changed (avoid unnecessary API calls)
new_floor_price = calculate_floor_price(state, current_tier)
if abs(new_floor_price - state.get("lastSyncedFloorPrice", 0)) > 0.01:
    sync_sl_to_hyperliquid(wallet, coin, new_floor_price)
    state["lastSyncedFloorPrice"] = new_floor_price
```

This already partially exists in dsl-v5.py — just ensure it runs on every tick when lockMode is pct_of_high_water, not only on tier changes.

## Complete Code Change (Pseudocode)

In the main evaluation loop, replace the floor calculation:

```python
# BEFORE (current):
tier_floor_roe = current_tier["lockPct"]

# AFTER:
if state.get("lockMode") == "pct_of_high_water" and "lockHwPct" in current_tier:
    tier_floor_roe = state["highWaterRoe"] * current_tier["lockHwPct"] / 100
else:
    tier_floor_roe = current_tier.get("lockPct", 0)
```

And for breach counting:

```python
# BEFORE (current):
max_breaches = phase2_config["consecutiveBreachesRequired"]

# AFTER:
max_breaches = current_tier.get("consecutiveBreachesRequired",
                                 phase2_config.get("consecutiveBreachesRequired", 1))
```

And for SL sync frequency:

```python
# BEFORE: sync on tier change only
# AFTER: also sync when high water advances in pct_of_high_water mode

should_sync = tier_changed
if state.get("lockMode") == "pct_of_high_water" and high_water_updated:
    should_sync = True
```

## Backward Compatibility

- If `lockMode` is missing from state → defaults to `fixed_roe` → existing behavior unchanged
- If tier has `lockPct` but no `lockHwPct` → uses `lockPct` → existing behavior unchanged
- If tier has both `lockPct` and `lockHwPct` → uses `lockHwPct` when `lockMode` is `pct_of_high_water`, `lockPct` otherwise
- If `consecutiveBreachesRequired` is missing from tier → falls back to phase-level value → existing behavior unchanged

**Zero breaking changes. All existing state files continue to work exactly as before.**

## Validation

Add to `dsl-cli.py validate`:
- If `lockMode` is `pct_of_high_water`, every tier must have `lockHwPct` (not `lockPct`)
- `lockHwPct` values must be 0-100
- `lockHwPct` values must increase with each tier (last tier should be 80-90 for tight trailing)
- `consecutiveBreachesRequired` per tier must be 1-5

## Testing Checklist

Implemented in `scripts/test_all_methods.py` as `TestDslHighWaterChecklist`. Run: `python3 scripts/test_all_methods.py`

- [x]  State file with no `lockMode` → behaves exactly as current (fixed_roe) — `test_checklist_1_state_no_lock_mode_behaves_fixed_roe`
- [x]  State file with `lockMode: "fixed_roe"` → same as current — `test_checklist_2_state_lock_mode_fixed_roe_same_as_current`
- [x]  State file with `lockMode: "pct_of_high_water"` + `lockHwPct` tiers → floor is percentage of hwROE — `test_checklist_3_pct_of_high_water_floor_is_percentage_of_hw_roe`
- [x]  High water advances from 20% to 50% ROE at Tier 5 (85%) → floor moves from 17% to 42.5% — `test_checklist_4_high_water_20_to_50_roe_tier5_85_floor_17_to_42_5`
- [x]  High water advances → SL synced to Hyperliquid at new floor — `test_checklist_5_high_water_advances_sl_would_sync` (asserts need_sync when floor changes)
- [x]  High water flat (no new peak) → SL not re-synced (saves API calls) — `test_checklist_6_high_water_flat_sl_not_resynced`
- [x]  Per-tier breach count: Tier 1 at 3 breaches, Tier 5 at 1 breach — `test_checklist_7_per_tier_breach_count_tier1_3_tier5_1`
- [x]  Tier with only `lockPct` (no `lockHwPct`) → uses lockPct regardless of lockMode — `test_checklist_8_tier_only_lock_pct_uses_lock_pct_regardless_of_lock_mode`
- [x]  LONG direction: floor calculation correct — `test_checklist_9_long_floor_calculation_correct`
- [x]  SHORT direction: floor calculation correct — `test_checklist_10_short_floor_calculation_correct`
- [x]  Mixed state files in same strategy dir (some fixed, some HW) → each uses its own lockMode — `test_checklist_11_mixed_state_files_each_uses_own_lock_mode`

## Priority

This is blocking 5 agents (GRIZZLY, BISON, GHOST FOX, DIRE WOLF, MAMBA) from running their intended DSL configuration. They’re currently on legacy fallback tiers which work but don’t provide the infinite trailing behavior they were designed for.

The actual code change is small — one `if` statement for floor calculation, one for breach count, one for SL sync frequency. The design, config format, and state schema are all defined and documented. The agents are already shipping configs with the correct fields.