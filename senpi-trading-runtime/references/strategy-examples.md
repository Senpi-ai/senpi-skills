# Strategy YAML examples

Ready-to-use YAML examples for the position tracker with different DSL tuning profiles.

---

## Table of Contents

- [Default position tracker](#default-position-tracker)
- [Conservative (wide stops, long timeouts)](#conservative)
- [Aggressive (tight stops, fast cuts)](#aggressive)
- [Profit-focused (many tiers, generous time)](#profit-focused)

---

## Default position tracker

Reference configuration matching the dsl-showcase. Balanced protection with moderate time cuts.

```yaml
name: position-tracker
version: 1.0.0
description: >
  On-chain position tracker with DSL trailing stop-loss.
  Monitors wallet positions on Hyperliquid and applies
  automated exit management.

strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 500
    slots: 2
    margin_per_slot: 200
    trading_risk: conservative
    enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

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

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

---

## Conservative

Wider stops, longer time windows. Gives positions more room to breathe. Good for swing trades or volatile assets.

```yaml
name: conservative-tracker
version: 1.0.0
description: Wide stops, long time windows for swing trading.

strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 1000
    slots: 3
    margin_per_slot: 300
    trading_risk: conservative
    enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 60
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

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

**Key differences from default:**
- `max_loss_pct: 6.0` — wider initial loss tolerance
- `retrace_threshold: 12` — more room for pullbacks from high-water
- `consecutive_breaches_required: 3` — requires sustained breach, not just one tick
- `interval_seconds: 60` — less frequent checks
- Higher tier triggers with lower lock percentages — lets profits run longer
- Longer time cuts (12h hard timeout, 4h weak peak, 2h dead weight)

---

## Aggressive

Tight stops, fast time cuts. Cuts losers quickly and locks in profits early. Good for scalping or high-frequency setups.

```yaml
name: aggressive-tracker
version: 1.0.0
description: Tight stops, fast cuts for active trading.

strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 500
    slots: 4
    margin_per_slot: 100
    trading_risk: aggressive
    enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 5s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 15
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

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

**Key differences from default:**
- `max_loss_pct: 2.5` — tight loss cap
- `retrace_threshold: 5` — quick exit on pullback
- `interval_seconds: 15` — more frequent price checks
- Lower tier triggers — starts locking profit earlier (3% ROE)
- Higher lock percentages — locks more at each tier (up to 90%)
- Fast time cuts (2h hard timeout, 45m weak peak, 20m dead weight)

---

## Profit-focused

More tiers with gradual locking. Designed to maximize profit capture on strong runners while maintaining reasonable protection.

```yaml
name: profit-tracker
version: 1.0.0
description: Many tiers for granular profit locking on runners.

strategies:
  main:
    wallet: "${WALLET_ADDRESS}"
    budget: 800
    slots: 2
    margin_per_slot: 350
    trading_risk: moderate
    enabled: true

scanners:
  - name: position_tracker
    type: position_tracker
    interval: 10s

actions:
  - name: position_tracker_action
    action_type: POSITION_TRACKER
    decision_mode: rule
    scanners: [position_tracker]

exit:
  engine: dsl
  interval_seconds: 30
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

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

**Key differences from default:**
- 6 tiers instead of 4 — more granular profit locking steps
- Lower lock percentages at early tiers — gives runners more room
- No hard_timeout — lets profitable positions run indefinitely
- `consecutive_breaches_required: 2` — tolerates one-tick noise
- Higher tier triggers extend to 50% ROE for big movers
