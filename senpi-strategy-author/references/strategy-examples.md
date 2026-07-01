# Strategy Runtime Examples (Runtime 3.0)

Worked `runtime.yaml` examples with different DSL tuning profiles. Each one is a real Runtime 3.0
instance file: a `position_tracker` scanner (feeds the DSL exit engine) + an `external_scanner` that
points at the instance's `scanners/scan.py`, wired to a rule-mode `OPEN_POSITION` action and a DSL exit.

**Package layout every example assumes** (one dir per instance):

```
<id>/
  strategy.yaml                     # deploy manifest (id, version, requires.runtime: ">=3.0.0", instances[])
  <instance>/                       # e.g. main, swing, scalp
    runtime.yaml                    # the file shown below
    scanners/
      scan.py                       # exports scan(inputs, ctx) -> list[dict] (read-only, single-pass)
      scoring.py                    # pure thesis math (no I/O/MCP); sibling import, NO __init__.py
```

The tuning lives in the `external_scanner`'s `inputs:` (thresholds/universe/leverage) and the `exit:`
`dsl_preset` (protection profile). The runtime spawns and supervises `scan()` on `interval_seconds` —
**there is no daemon to launch**; deploy via `senpi-strategy-ops` `deploy.py`. Copy an example, change
`inputs`, and re-tune the `dsl_preset` to taste. For the gold end-to-end single-asset package, read
`strategies/kodiak/` (`strategy.yaml` + `main/runtime.yaml` + `main/scanners/scan.py` + `scoring.py`).

---

## Table of Contents

- [Balanced (default)](#balanced-default)
- [Conservative (wide stops, long timeouts)](#conservative)
- [Aggressive (tight stops, fast cuts)](#aggressive)
- [Profit-focused (many tiers, generous time)](#profit-focused)

---

## Balanced (default)

Balanced protection with moderate time cuts. A single-slot signal-driven instance.

```yaml
name: example-balanced
version: 3.0.0
group: example
description: >
  Balanced Runtime 3.0 instance: a supervised scan.py emits candidate signals,
  the runtime sizes + executes them, and a two-phase DSL trails the exit.

strategy:
  wallet: "${EXAMPLE_WALLET}"       # bound by deploy.py from the manifest wallet_env
  slots: 2
  margin_pct: 15
  trading_risk: conservative
  enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval_seconds: 10            # built-in scanner (integer seconds, floored at 7)
  - name: example_signals
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300           # per-thesis cadence (NOT the 10s supervisor loop)
    timeout_seconds: 180
    default_signal_validity_seconds: 600
    state_history_max_count: 100
    inputs:
      minScore: 4
      marginPct: 15
    signal_data_schema:
      score: { type: number }
      direction: { type: string }
      reasons: { type: array, required: false }

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]
  - name: example_entry
    action_type: OPEN_POSITION
    decision_mode: rule             # the scan already applied every filter
    scanners: [example_signals]
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options:
        ensure_execution_as_taker: true
        execution_timeout_seconds: 15
    context:
      - type: signal
        scanner: example_signals

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

> `interval_in_minutes` on the DSL time cuts is the preset's own unit (**minutes**, unchanged). The DSL
> poll cadence (`exit.interval_seconds`) and the scanner cadence (`external_scanner.interval_seconds`)
> are in seconds.

---

## Conservative

Wider stops, longer time windows. Gives positions more room to breathe. Good for swing trades or
volatile assets. Only the sizing + `dsl_preset` differ from the balanced example — the scanner/action
wiring is identical, so just the changed sections are shown.

```yaml
name: example-conservative
version: 3.0.0
group: example
description: Wide stops, long time windows for swing trading.

strategy:
  wallet: "${EXAMPLE_WALLET}"
  slots: 3
  margin_pct: 25            # % of account per slot — 3 × 25% ≈ 75% committed; scales with any budget
  trading_risk: conservative
  enabled: true

# scanners + actions: identical to the balanced example (position_tracker + external_scanner + rule entry)

exit:
  engine: dsl
  interval_seconds: 60
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 30
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 720
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 240
      min_value: 3
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 120
    phase1:
      enabled: true
      max_loss_pct: 6.0
      retrace_threshold: 12
      consecutive_breaches_required: 3
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 10, lock_hw_pct: 35 }
        - { trigger_pct: 20, lock_hw_pct: 50 }
        - { trigger_pct: 30, lock_hw_pct: 65 }
        - { trigger_pct: 50, lock_hw_pct: 80 }
```

**Key differences from balanced:**
- `max_loss_pct: 6.0` — wider initial loss tolerance
- `retrace_threshold: 12` — more room for pullbacks from high-water
- `consecutive_breaches_required: 3` — requires sustained breach, not just one tick
- `exit.interval_seconds: 60` — less frequent DSL checks
- Higher tier triggers with lower lock percentages — lets profits run longer
- Longer time cuts (12h hard timeout, 4h weak peak, 2h dead weight)

---

## Aggressive

Tight stops, fast time cuts. Cuts losers quickly and locks in profits early. Good for scalping or
high-frequency setups. Changed sections only.

```yaml
name: example-aggressive
version: 3.0.0
group: example
description: Tight stops, fast cuts for active trading.

strategy:
  wallet: "${EXAMPLE_WALLET}"
  slots: 4
  margin_pct: 10            # 4 × 10% = 40% committed; scales with any budget
  trading_risk: aggressive
  enabled: true

# scanners: position_tracker interval_seconds: 10; external_scanner interval_seconds: 120 (faster thesis cadence)
# actions:  identical rule-mode wiring

exit:
  engine: dsl
  interval_seconds: 15
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 10
  dsl_preset:
    hard_timeout:
      enabled: true
      interval_in_minutes: 120
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 45
      min_value: 3
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 20
    phase1:
      enabled: true
      max_loss_pct: 2.5
      retrace_threshold: 5
      consecutive_breaches_required: 1
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 3,  lock_hw_pct: 45 }
        - { trigger_pct: 7,  lock_hw_pct: 60 }
        - { trigger_pct: 12, lock_hw_pct: 75 }
        - { trigger_pct: 18, lock_hw_pct: 90 }
```

**Key differences from balanced:**
- `max_loss_pct: 2.5` — tight loss cap
- `retrace_threshold: 5` — quick exit on pullback
- `exit.interval_seconds: 15` — more frequent price checks
- Lower tier triggers — starts locking profit earlier (3% ROE)
- Higher lock percentages — locks more at each tier (up to 90%)
- Fast time cuts (2h hard timeout, 45m weak peak, 20m dead weight)

---

## Profit-focused

More tiers with gradual locking. Maximizes profit capture on strong runners while keeping reasonable
protection. Changed sections only.

```yaml
name: example-profit
version: 3.0.0
group: example
description: Many tiers for granular profit locking on runners.

strategy:
  wallet: "${EXAMPLE_WALLET}"
  slots: 2
  margin_pct: 20
  trading_risk: moderate
  enabled: true

# scanners + actions: identical to the balanced example

exit:
  engine: dsl
  interval_seconds: 30
  order_type: FEE_OPTIMIZED_LIMIT
  fee_optimized_limit_options:
    ensure_execution_as_taker: true
    execution_timeout_seconds: 20
  dsl_preset:
    weak_peak_cut:
      enabled: true
      interval_in_minutes: 180
      min_value: 5
    dead_weight_cut:
      enabled: true
      interval_in_minutes: 90
    phase1:
      enabled: true
      max_loss_pct: 4.0
      retrace_threshold: 8
      consecutive_breaches_required: 2
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 5,  lock_hw_pct: 30 }
        - { trigger_pct: 10, lock_hw_pct: 45 }
        - { trigger_pct: 15, lock_hw_pct: 55 }
        - { trigger_pct: 20, lock_hw_pct: 65 }
        - { trigger_pct: 30, lock_hw_pct: 75 }
        - { trigger_pct: 50, lock_hw_pct: 85 }
```

**Key differences from balanced:**
- 6 tiers instead of 4 — more granular profit locking steps
- Lower lock percentages at early tiers — gives runners more room
- No `hard_timeout` — lets profitable positions run indefinitely
- `consecutive_breaches_required: 2` — tolerates one-tick noise
- Higher tier triggers extend to 50% ROE for big movers

---

## Single-asset gold reference

For a complete, production single-instance package (SOL alpha hunter, conviction-tiered leverage, DSL-
only exits, no time cuts), read `strategies/kodiak/` end to end: `strategy.yaml`, `main/runtime.yaml`,
`main/scanners/scan.py`, and `main/scanners/scoring.py`. It is the canonical shape to clone from.
