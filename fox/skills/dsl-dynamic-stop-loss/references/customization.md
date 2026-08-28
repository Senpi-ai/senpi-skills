# Customization

Presets for different trading styles and tuning guidelines for per-tier retrace.

## Presets

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

> **Note:** Phase 2 with `consecutiveBreachesRequired: 1` means any single check below the floor triggers immediate close. Tight but effective for locking gains on volatile assets.

## Phase 1 Retrace Threshold Tuning

| Threshold | Style | Notes |
|---|---|---|
| 1.5-2% | Tight scalps | Quick exits, may get stopped on noise |
| 2-3% | Balanced | Good default for most trades |
| 3-4% | Wide | For high-conviction plays, rides through volatility |
| 4-5% | Very wide | Swing trades, accepts deeper pullbacks |

## Per-Tier Retrace Tuning (v4)

| Tier range | Suggested retrace | Rationale |
|---|---|---|
| Tier 1-2 (10-20% ROE) | Use global default | Profit is small — let it breathe |
| Tier 3 (30% ROE) | 1.0-1.2% | Meaningful profit — start tightening |
| Tier 4-5 (50-75% ROE) | 0.8-1.0% | Large profit — protect aggressively |
| Tier 6 (100% ROE) | 0.5-0.6% | Doubled your money — lock it tight |

## Cross-Check

Periodically verify DSL state matches actual on-chain positions via `strategy_get_clearinghouse_state`. Fix any mismatches immediately.

## Staggering Multiple Positions

| Position | Interval | Offset |
|---|---|---|
| Position A | 3 min | :00 |
| Position B | 3 min | :01 |
| Position C | 3 min | :02 |
