# DSL Exit Engine — Configuration Reference

> For the complete strategy YAML specification (all sections), see [yaml-schema.md](yaml-schema.md).

The DSL (Dynamic Stop-Loss) manages exit logic for open perpetual positions. It monitors prices on a fixed interval and closes positions when price breaches a computed floor. Two-phase design: Phase 1 protects from initial loss, Phase 2 locks in profits as they grow.

## Tuning Guidance

| Profile | Description | Typical settings |
|---------|-------------|------------------|
| **Conservative** | Wide stops, long timeouts. For swing-style or low-conviction entries. | max_loss_pct: 5-8, retrace_threshold: 10+, hard_timeout: 360+ min |
| **Moderate** | Balanced protection. Suitable for most momentum strategies. | max_loss_pct: 2.5-4, retrace_threshold: 7, hard_timeout: 120-240 min |
| **Aggressive** | Tight stops, fast cuts. For scalp-style or high-conviction entries. | max_loss_pct: 1.5-2.5, retrace_threshold: 5, hard_timeout: 60-90 min |

Key trade-offs:
- Lower `max_loss_pct` = less risk per trade, but more stop-outs on noise
- Higher `retrace_threshold` = more room to breathe, but gives back more profit on reversals
- More phase2 tiers = smoother profit locking, but each tier is a potential exit trigger
- Lower `consecutive_breaches_required` = faster exit on breach, but more false triggers

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

Configured under the `exit` key in the recipe YAML.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `engine` | string | Yes | — | Must be `"dsl"` to activate the DSL exit engine. |
| `interval_seconds` | integer | No | `30` | How often the price monitor runs (seconds). Range: 5–3600. |
| `order_type` | string | No | `MARKET` | Execution method for DSL exit closes: `MARKET` or `FEE_OPTIMIZED_LIMIT`. |
| `fee_optimized_limit_options` | object | No | — | Only valid when `order_type` is `FEE_OPTIMIZED_LIMIT`. |
| `fee_optimized_limit_options.ensure_execution_as_taker` | boolean | No | — | When `true`, falls back to market order if maker doesn't fill within timeout. |
| `fee_optimized_limit_options.execution_timeout_seconds` | integer | No | `45` | Seconds to wait for maker fill (1–300). |
| `dsl_preset` | object | Yes | — | Single preset config (see below). |

```yaml
exit:
  engine: dsl
  interval_seconds: 30
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 15
  dsl_preset:
    ...
```

**Validation:** Unknown keys under `exit`, preset, phases, tiers, and time-cut objects are rejected at load. Typos fail fast.

---

## Preset configuration

The `dsl_preset` object contains time-based cuts (at preset level), Phase 1, and Phase 2 config.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `hard_timeout` | object | No | Time-based cut: close after N minutes since open. Evaluates in **both phases** as an outer-bound protection. |
| `weak_peak_cut` | object | No | Time-based cut: close if peak ROE stayed weak. Evaluates in both phases, but practically only fires in Phase 1 when `min_value` < first tier `trigger_pct` (entering Phase 2 implies `peakROE ≥ trigger_pct`, so the `peakROE < min_value` guard becomes unsatisfiable). Set `min_value` above the first tier if you want it active in Phase 2. |
| `dead_weight_cut` | object | No | Time-based cut: close after interval with `currentROE ≤ 0` since last tick with `currentROE > 0`. Evaluates in both phases. |
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

Defined at **preset level** (NOT inside `phase1`). All optional. Evaluated every tick after breach logic, **regardless of phase**; first match wins. The only exception is `hard_timeout`, which is skipped on the exact tick a position crosses into Phase 2 so a boundary hit cannot lose to the clock before the tier advance runs.

Time-cut intervals are clamped to at least the DSL cron interval (e.g. `interval_seconds: 30` -> min 0.5 min), so very small values cannot fire every tick.

### hard_timeout

Close when position has been open for at least N minutes. Fires in both Phase 1 and Phase 2 — this is an outer-bound protection against capital being tied up indefinitely, not a Phase 1 patience knob.

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

Stagnation cut: `deadWeightCutStartedAt` is set at open and **reset every tick** while **`currentROE > 0`**. Close when elapsed ≥ `interval_in_minutes` **and** `currentROE ≤ 0` on that tick.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | Yes | Must be `true` to activate. |
| `interval_in_minutes` | number | Yes | Wall-clock minutes since `deadWeightCutStartedAt` (see above). |

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
2. If breached enough times (Phase 1 only) -> close (`dsl_breach`).
3. Else apply time cuts (`hard_timeout`, `dead_weight_cut`, `weak_peak_cut`) — evaluated in both phases; first match wins.
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
| `hard_timeout` | Time-since-open exceeded `hard_timeout.interval_in_minutes` (fires in both phases). |
| `weak_peak_cut` | Peak ROE stayed below `min_value` and current ROE has retreated from peak (fires in both phases; practically Phase 1 only when `min_value` < first tier `trigger_pct`). |
| `dead_weight_cut` | `currentROE ≤ 0` for at least `dead_weight_cut.interval_in_minutes` since the last positive-ROE tick (fires in both phases). |
| `position_increased` | Position size increased (size-change event). |
| `position_decreased` | Position size decreased (size-change event). |
| `dsl_deleted` | DSL state purged. |

---

## DSL events

Emitted on the runtime event bus:

| Event | When | Key Payload |
|-------|------|-------------|
| `dsl.created` | Position opened, initial state + SL written. | address, asset, preset, tiers, floorPrice, direction, entryPrice, dex |
| `dsl.phase_changed` | Phase 1 → Phase 2 transition (first tier reached). | address, asset, phase, tierIndex, timestamp |
| `dsl.tier_advanced` | Price moved into a higher tier (includes profit-lock fields for alerts). | address, asset, dex, tier, lockHwPct, triggerPct, newFloorPrice |
| `dsl.heartbeat` | Periodic open-position status (sparse interval). | address, asset, dex, direction, floorPrice, ROE fields, elapsedMinutes |
| `dsl.sl_updated` | Exchange stop-loss synced. | address, asset, newSLPrice, slOrderId |
| `dsl.closed` | Position closed (DSL paths, exchange SL, hooks). Close alerts may wait for trade history for PnL. | address, asset, dex, reason, closeReason, snapshot fields |
| `dsl.close_pending` | Close in progress (will retry). | address, asset, attempt |
| `dsl.settings_updated` | DSL config changed. | address, asset, updated |
| `dsl.deleted` | DSL state removed (e.g. strategy delete). | address, asset, dex |

**Telegram:** If the recipe defines `notifications` with `telegram_chat_id`, DSL lifecycle events are forwarded as plain-text alerts by default (gateway HTTP uses `OPENCLAW_GATEWAY_TOKEN` from the environment unless you set optional `gateway_token` in YAML). Set `notifications.dsl_lifecycle: false` to turn off only DSL Telegram messages. Set `notifications.dsl_notify_sl_updates: true` to include `dsl.sl_updated` (noisy; off by default). Close lines are deferred briefly when needed so realized PnL can be filled from trade history; increase/decrease/flip rebuilds suppress the `dsl.created` ping.

**Position events DSL listens to:** `on_position_opened`, `on_position_closed`, `on_position_flipped`, `on_position_increased`, `on_position_decreased`.

---

## Full YAML example

```yaml
exit:
  engine: dsl
  interval_seconds: 30
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 15
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
