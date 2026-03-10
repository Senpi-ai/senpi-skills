# DSL Customization

This guide covers presets, Phase 1 time-based cuts, and tuning guidelines for dynamic stop-loss configs. Use it when building skill-specific `dsl-profile.json` or patching config via `update-dsl`.

**Contents**

1. [Presets](#1-presets) — Conservative, moderate, aggressive
2. [Cron interval (general)](#2-cron-interval-general) — how often the DSL runs
3. [Phase 1 time-based auto-cut](#3-phase-1-time-based-auto-cut) — hardTimeout, weakPeakCut, deadWeightCut
4. [Phase 1 retrace tuning](#4-phase-1-retrace-tuning) — `retraceThreshold` by style
5. [Phase 2 per-tier retrace](#5-phase-2-per-tier-retrace) — per-tier `retrace` values
6. [Operations](#6-operations) — cross-check and staggering

---

## 1. Presets

Choose a base style and merge into your `defaultConfig` or `--configuration`. Adjust retrace and breach counts to taste.

| Preset | Style | Phase 1 retrace | Phase 2 retrace | Phase 2 breaches |
|--------|--------|------------------|------------------|-------------------|
| **Conservative** | Swing trades | 5% | 2.5% | 3 |
| **Moderate** | Day trades | 3% | 1.5% | 2 |
| **Aggressive** | Scalps | 2% | 0.8% | 1 |

### Conservative (swing trades)

```json
"phase1": {"retraceThreshold": 0.05, "consecutiveBreachesRequired": 4},
"phase2": {"retraceThreshold": 0.025, "consecutiveBreachesRequired": 3},
"tiers": [{"triggerPct": 15, "lockPct": 8}, {"triggerPct": 30, "lockPct": 20, "retrace": 0.02}, ...],
"breachDecay": "soft"
```

### Moderate (day trades)

```json
"phase1": {"retraceThreshold": 0.03, "consecutiveBreachesRequired": 3},
"phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 2},
"tiers": [{"triggerPct": 10, "lockPct": 5}, {"triggerPct": 20, "lockPct": 14}, {"triggerPct": 30, "lockPct": 22, "retrace": 0.012}, ...],
"breachDecay": "hard"
```

### Aggressive (scalps)

```json
"phase1": {"retraceThreshold": 0.02, "consecutiveBreachesRequired": 2},
"phase2": {"retraceThreshold": 0.008, "consecutiveBreachesRequired": 1},
"tiers": [{"triggerPct": 5, "lockPct": 2}, {"triggerPct": 10, "lockPct": 6, "retrace": 0.006}, ...],
"breachDecay": "hard"
```

> **Note:** Phase 2 with `consecutiveBreachesRequired: 1` means a single check below the floor triggers immediate close. Effective for locking gains; use with care in noisy markets.

---

## 2. Cron interval (general)

**Top-level** setting: how often the DSL cron runs for the strategy. Not part of `phase1`.

| Field | Default | Description |
|-------|---------|-------------|
| `cronIntervalMinutes` | 3 | Run interval in minutes. When changed, the agent must **remove the old OpenClaw cron and create a new one** (CLI outputs `cron_schedule_changed`, `cron_to_remove`, `cron_needed`). Phase 1 time-based cut `intervalInMinutes` values must be ≥ this. |

---

## 3. Phase 1 time-based auto-cut

Optional rules that close Phase 1 positions by **elapsed time** and **ROE** when the skill supplies the corresponding objects. **If a block is not present, that cut is disabled** (no default object is written). Each cut’s `intervalInMinutes` must be ≥ the strategy [cron interval](#2-cron-interval-general).

### Phase 1 cut objects

| Object | Purpose | When it closes |
|--------|---------|----------------|
| **hardTimeout** | Hard timeout in Phase 1 | Elapsed time from `createdAt` ≥ `intervalInMinutes`. |
| **weakPeakCut** | Early exit on weak peak | After `intervalInMinutes`, if peak ROE &lt; `minValue` (ROE %) and current ROE &lt; peak ROE. |
| **deadWeightCut** | Exit when never positive | Elapsed ≥ `intervalInMinutes` and ROE was **never positive** (high water never better than entry). |

Each object has:

- `enabled` (boolean) — turn the rule on or off.
- `intervalInMinutes` (number) — must be ≥ the strategy cron interval when enabled and &gt; 0.
- **weakPeakCut only:** `minValue` (number) — ROE % below which peak is considered “weak”; typical 3.

### Example (skill profile)

```json
"cronIntervalMinutes": 3,
"phase1": {
  "enabled": true,
  "retraceThreshold": 0.03,
  "consecutiveBreachesRequired": 3,
  "hardTimeout": { "enabled": true, "intervalInMinutes": 90 },
  "weakPeakCut": { "enabled": true, "intervalInMinutes": 45, "minValue": 3 },
  "deadWeightCut": { "enabled": true, "intervalInMinutes": 30 }
}
```

The cron runner uses `createdAt` each run to compute elapsed time; when it meets the configured duration(s), it evaluates and closes if the condition is met. The current **phase** is written to the state file every run. Legacy flat fields (`phase1MaxMinutes`, `weakPeakCutMinutes`, `weakPeakThreshold`, `deadWeightCutMin`) are migrated once into these objects when present.

---

## 4. Phase 1 retrace tuning

`phase1.retraceThreshold` is the ROE fraction (e.g. 0.03 = 3%) that defines how far price can retrace from high water before a breach is counted.

| Threshold (ROE) | Style | Notes |
|------------------|--------|--------|
| 1.5–2% | Tight scalps | Quick exits; may get stopped on noise. |
| 2–3% | Balanced | Good default for most trades. |
| 3–4% | Wide | High-conviction plays; rides through volatility. |
| 4–5% | Very wide | Swing trades; accepts deeper pullbacks. |

---

## 5. Phase 2 per-tier retrace

Phase 2 tiers can override retrace per tier via the `retrace` field (ROE fraction). Use the global `phase2.retraceThreshold` when a tier omits `retrace`.

| Tier range (ROE) | Suggested retrace | Rationale |
|------------------|--------------------|------------|
| Tier 1–2 (10–20% ROE) | Use global default | Small profit; let it breathe. |
| Tier 3 (30% ROE) | 1.0–1.2% | Meaningful profit; start tightening. |
| Tier 4–5 (50–75% ROE) | 0.8–1.0% | Large profit; protect aggressively. |
| Tier 6 (100% ROE) | 0.5–0.6% | Doubled margin; lock tight. |

---

## 6. Operations

### Cross-check

Periodically verify DSL state matches actual on-chain positions via `strategy_get_clearinghouse_state`. Fix any mismatches immediately.

### Staggering multiple positions

If several positions share the same cron, stagger checks to spread load (e.g. different minute offsets per position when using a single strategy cron):

| Position | Interval | Offset |
|----------|----------|--------|
| A | 3 min | :00 |
| B | 3 min | :01 |
| C | 3 min | :02 |
