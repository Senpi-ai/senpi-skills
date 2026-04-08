# DSL Exit Engine — Full Configuration Reference

The DSL (Dynamic Stop-Loss) manages exit logic for open perpetual positions. It monitors prices on a fixed interval and closes positions when price breaches a computed floor. Two-phase design: Phase 1 protects from initial loss, Phase 2 locks in profits as they grow.

---

## Table of Contents

- [Exit block](#exit-block)
- [Preset configuration](#preset-configuration)
- [Phase 1 configuration](#phase-1-configuration)
- [Time-based cuts](#time-based-cuts)
- [Phase 2 configuration](#phase-2-configuration)
- [Tier definition](#tier-definition)
- [How phases and tiers combine](#how-phases-and-tiers-combine)
- [Exchange stop-loss vs DSL floor](#exchange-stop-loss-vs-dsl-floor)
- [Retrace convention](#retrace-convention)
- [Consecutive breaches](#consecutive-breaches)
- [Close reasons](#close-reasons)
- [DSL events](#dsl-events)
- [Full YAML example](#full-yaml-example)

---

## Exit block

Configured under the `exit` key in the runtime YAML.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `engine` | string | Yes | — | Must be `"dsl"` to activate the DSL exit engine. |
| `interval_seconds` | integer | No | `30` | How often the price monitor runs (seconds). Range: 5–3600. |
| `dsl_preset` | object | Yes | — | Single preset config (see below). |

```yaml
exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
    ...
```

**Validation:** Unknown keys under `exit`, preset, phases, tiers, and time-cut objects are rejected at load. Typos fail fast.

---

## Preset configuration

The `dsl_preset` object contains time-based cuts (at preset level), Phase 1, and Phase 2 config.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `hard_timeout` | object | No | Time-based cut: close after N minutes in Phase 1. |
| `weak_peak_cut` | object | No | Time-based cut: close if peak ROE stayed weak. |
| `dead_weight_cut` | object | No | Time-based cut: close if position never went positive. |
| `phase1` | object | Yes | Phase 1 config (see below). |
| `phase2` | object | Yes | Phase 2 config with tiers (see below). |

Time-based cuts are defined at the preset level (siblings of `phase1` and `phase2`).

---

## Phase 1 configuration

Active from entry until the first tier is reached.

**Floor = max(absolute_floor, trailing_floor)**
- Absolute floor from `max_loss_pct`: entry x (1 - max_loss_pct/100/leverage) for LONG
- Trailing floor from high-water using `retrace_threshold` (ROE %)

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enabled` | boolean | No | `true` | `false` = skip Phase 1 rules; start with Phase 2 behavior. |
| `max_loss_pct` | number | Yes | — | Max loss % from entry. Range: (0, 100]. Sets the absolute floor. |
| `retrace_threshold` | number | Yes* | — | ROE % retrace from high-water mark. Must be > 0. *Required when phase1 enabled. |
| `consecutive_breaches_required` | integer | Yes* | — | Consecutive ticks below floor before exit (>= 1). *Required when phase1 enabled. |

```yaml
phase1:
  enabled: true
  max_loss_pct: 4.0
  retrace_threshold: 7
  consecutive_breaches_required: 1
```

---

## Time-based cuts

Defined at **preset level** (NOT inside `phase1`). All optional. Evaluated after breach logic in Phase 1 only; first match wins.

Time-cut intervals are clamped to at least the DSL cron interval (e.g. `interval_seconds: 30` -> min 0.5 min), so very small values cannot fire every tick.

### hard_timeout

Close when position has been open for at least N minutes in Phase 1.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | Yes | Must be `true` to activate. |
| `interval_in_minutes` | number | Yes | Close when elapsed minutes >= this value. |

Close reason: `hard_timeout`

### weak_peak_cut

Close when, after the interval, the peak ROE stayed below a threshold and current ROE has declined from that peak.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | Yes | Must be `true` to activate. |
| `interval_in_minutes` | number | Yes | Evaluate only after this many minutes. |
| `min_value` | number | Yes | ROE % threshold. Close only if peakROE < min_value AND currentROE < peakROE. |

Close reason: `weak_peak_cut`

### dead_weight_cut

Close when position never went favorable vs entry after the interval.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | Yes | Must be `true` to activate. |
| `interval_in_minutes` | number | Yes | Evaluate only after this many minutes. |

Condition: LONG -> highWaterPrice <= entryPrice; SHORT -> highWaterPrice >= entryPrice.
Close reason: `dead_weight_cut`

```yaml
hard_timeout:
  enabled: true
  interval_in_minutes: 360
weak_peak_cut:
  enabled: true
  interval_in_minutes: 120
  min_value: 5
dead_weight_cut:
  enabled: true
  interval_in_minutes: 60
```

---

## Phase 2 configuration

Phase 2 is exchange-SL driven. It starts when the first tier is reached (always tier 0). Phase 2 only has `enabled` and `tiers`.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enabled` | boolean | No | `true` | `false` = never transition to phase 2; tiers still apply in phase 1. |
| `tiers` | array | Yes | — | Ordered list of tier objects (see below). |

**Constraint:** `phase1.enabled` and `phase2.enabled` cannot both be false.

```yaml
phase2:
  enabled: true
  tiers:
    - { trigger_pct: 7,  lock_hw_pct: 40 }
    - { trigger_pct: 12, lock_hw_pct: 55 }
    - { trigger_pct: 15, lock_hw_pct: 75 }
    - { trigger_pct: 20, lock_hw_pct: 85 }
```

---

## Tier definition

Each tier is a profit milestone. Tiers must be sorted ascending by `trigger_pct`. Each tier has exactly two fields.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `trigger_pct` | number | Yes | ROE % from entry that activates this tier. Must be > 0; strictly increasing across tiers. |
| `lock_hw_pct` | number | Yes | Lock floor at this % of current high-water ROE (0-100). Floor trails every tick as high water advances. |

Example: `{ trigger_pct: 7, lock_hw_pct: 40 }` means: when ROE reaches 7% from entry, lock a floor at 40% of the high-water ROE. If high-water ROE is 10%, the floor locks at 4% ROE equivalent price.

---

## How phases and tiers combine

**While a tier is active:**
1. **Tier floor** = `lock_hw_pct`% of `highWaterRoe`, converted to price. Ratchets (never loosens).
2. **Effective floor** = stricter of tier floor and any phase-level trailing floor.

**Phase transition:** Phase becomes 2 when the first tier is reached (tier index 0) and `phase2.enabled` is not false. Tiers can be active in Phase 1 before the transition.

**On each tick:**
1. Update high water -> recompute floors.
2. If breached enough times -> close (`dsl_breach`).
3. Else apply time cuts if still in Phase 1.
4. Else detect tier from current ROE; on new higher tier, update tier floor and possibly transition to Phase 2.
5. If tier active, recompute tier floor every tick so `lock_hw_pct` trails high-water ROE.

---

## Exchange stop-loss vs DSL floor

- **Phase 1:** Exchange SL stays at the `max_loss_pct` absolute floor. Tighter exits (retrace, tier-augmented floors) are enforced by `closePosition` after consecutive breaches — the exchange stop is NOT moved to the tighter level.
- **Phase 2:** Exchange SL tracks the full effective `floorPrice` and updates as it moves.

DSL floor and exchange stop can intentionally diverge in Phase 1.

---

## Retrace convention

`retrace_threshold` (Phase 1 only) is **ROE %**. The engine converts to price fraction by dividing by leverage:
- At 10x leverage: 7% ROE retrace = 0.7% price below high-water
- LONG trailing floor: highWaterPrice x (1 - retrace/100/leverage)
- SHORT trailing floor: highWaterPrice x (1 + retrace/100/leverage)

---

## Consecutive breaches

Breaches are **tick-based, not time-based**. Each monitor tick (every `interval_seconds`), if price violates the floor, the breach counter increments. If price recovers, the counter resets.

Example: `interval_seconds: 30` and `consecutive_breaches_required: 1` means a single tick with price below floor triggers a close. With value `3`, three consecutive ticks (~60+ seconds) must all breach.

---

## Close reasons

| Reason | When |
|--------|------|
| `manual_close` | User or action closed the position. |
| `closed_externally` | Position closed outside the runtime (e.g. exchange UI). |
| `exchange_sl_hit` | Exchange stop-loss order filled. |
| `dsl_breach` | Floor breached for required consecutive ticks. |
| `flipped` | Position flipped (same asset, reverse direction). |
| `close_position_failed` | Close failed after max retries. |
| `hard_timeout` | Phase 1 hard_timeout time cut. |
| `weak_peak_cut` | Phase 1 weak_peak_cut triggered. |
| `dead_weight_cut` | Phase 1 dead_weight_cut triggered. |
| `position_increased` | Position size increased (size-change event). |
| `position_decreased` | Position size decreased (size-change event). |
| `dsl_deleted` | DSL state purged. |

---

## DSL events

Emitted on the runtime event bus:

| Event | When | Key Payload |
|-------|------|-------------|
| `dsl.created` | Position opened, initial state + SL written. | address, asset, preset, tiers, floorPrice |
| `dsl.phase_changed` | Phase 1 -> Phase 2 transition. | address, asset, phase, tierIndex |
| `dsl.tier_advanced` | Price moved into a higher tier. | address, asset, tier, lockHwPct, triggerPct, newFloorPrice |
| `dsl.sl_updated` | Exchange stop-loss synced. | address, asset, newSLPrice, slOrderId |
| `dsl.closed` | Position closed by DSL. | address, asset, reason, closeReason |
| `dsl.close_pending` | Close in progress (will retry). | address, asset, attempt |
| `dsl.settings_updated` | DSL config changed. | address, asset, updated |
| `dsl.deleted` | DSL state removed. | address, asset |

**Position events DSL listens to:** `on_position_opened`, `on_position_closed`, `on_position_flipped`, `position.increased`, `position.decreased`.

---

## Full YAML example

```yaml
exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 360
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 120
      min_value: 5
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 60
    phase1:
      enabled: true
      max_loss_pct: 4.0
      retrace_threshold: 7
      consecutive_breaches_required: 1
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 7,  lock_hw_pct: 40 }
        - { trigger_pct: 12, lock_hw_pct: 55 }
        - { trigger_pct: 15, lock_hw_pct: 75 }
        - { trigger_pct: 20, lock_hw_pct: 85 }
```
