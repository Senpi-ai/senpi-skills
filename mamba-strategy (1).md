# 🐍 MAMBA — Range-Bound High Water

A trading strategy (config override) based on the VIPER skill. Same scanner, same range detection, same support/resistance entries. Different exit geometry — DSL High Water Mode's infinite trailing replaces fixed ROE tiers.

**Base skill:** [VIPER v2.1](https://github.com/Senpi-ai/senpi-skills/tree/viper-2-0/viper)
**Philosophy:** VIPER's range-bound entries have a high win rate because support/resistance levels are predictable. But fixed DSL tiers cap the upside — when a range breakout turns into a trend, VIPER exits too early. MAMBA keeps VIPER's precise entries and lets High Water Mode ride the breakouts that escape the range.

---

## MANDATORY: DSL High Water Mode

**MAMBA MUST use DSL High Water Mode. This is not optional. Do not substitute standard DSL tiers.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

When creating DSL state files for any MAMBA position, you MUST include:

```json
{
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

**If `tiers` or `lockMode` is missing from the state file, the DSL engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. Always verify the state file contains these fields after creation.**

**DSL default:** Use this strategy's **`mamba-strategy/dsl-profile.json`** as the default when setting up DSL (`dsl-cli.py add-dsl` / `update-dsl` with `--configuration @<path>/mamba-strategy/dsl-profile.json`). Use it unless the user explicitly provides a custom DSL configuration via the agent.

---

## What Changed vs Standard VIPER

| Variable | VIPER v2.1 | MAMBA | Why |
|---|---|---|---|
| **DSL mode** | Fixed ROE tiers (6 tiers) | **High Water Mode (4 HW tiers)** | Range entries that break out become trend trades — infinite trail captures the escape |
| **Phase 2 trigger** | +3% ROE (T1) | **+5% ROE** | Range trades need a bit more confirmation before trailing starts |
| **Tier structure** | 6 fixed tiers (3→25% ROE) | **4 HW tiers (5→20% → infinite)** | Once past +20% ROE, it's 85% of peak forever. Range breakout → trend capture. |
| **Phase 1 floors** | 8% ROE retrace | **Conviction-scaled (-15/-20/unrestricted)** | High-score range setups (BB squeeze + RSI extreme + vol declining) get more room |
| **Margin per trade** | 28% | **28%** | Same — VIPER's capital deployment already proven |
| **Max positions** | 3 | **3** | Same |
| **Stagnation TP** | 5% ROE, 30min stale | **8% ROE, 45min stale** | More patience — a range breakout may consolidate before the next leg |
| **Entry filters** | Unchanged | **Same** | VIPER's BB/RSI/ATR range detection is the edge — don't touch it |

---

## Config Override File

```json
{
  "basedOn": "viper-2.1",
  "version": "1.0",
  "name": "Mamba",
  "description": "Range-bound entries + High Water infinite trailing — catches breakouts that escape the range",

  "entry": {
    "maxBbWidthPct": 4.0,
    "maxAtrPct": 1.5,
    "rsiOversold": 35,
    "rsiOverbought": 65,
    "minScore": 5,
    "marginPct": 0.28,
    "minOiUsd": 5000000
  },

  "dsl": {
    "_spec": "https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md",
    "lockMode": "pct_of_high_water",
    "phase2TriggerRoe": 5,
    "phase1RetraceRoe": 10,
    "phase1HardTimeoutMin": 0,
    "phase1WeakPeakMin": 0,
    "phase1DeadWeightMin": 0,
    "convictionTiers": [
      {"minScore": 5, "absoluteFloorRoe": -15, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 7, "absoluteFloorRoe": -20, "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0},
      {"minScore": 9, "absoluteFloorRoe": 0,   "hardTimeoutMin": 0, "weakPeakCutMin": 0, "deadWeightCutMin": 0}
    ],
    "tiers": [
      {"triggerPct": 5,  "lockHwPct": 30, "consecutiveBreachesRequired": 3},
      {"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 2},
      {"triggerPct": 15, "lockHwPct": 70, "consecutiveBreachesRequired": 2},
      {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
    ],
    "stagnationTp": {
      "enabled": true,
      "roeMin": 8,
      "hwStaleMin": 45
    }
  },

  "risk": {
    "maxEntriesPerDay": 8,
    "maxDailyLossPct": 8,
    "maxDrawdownPct": 18,
    "maxSingleLossPct": 5,
    "maxConsecutiveLosses": 4,
    "cooldownMinutes": 20
  },

  "execution": {
    "entryOrderType": "FEE_OPTIMIZED_LIMIT",
    "entryEnsureTaker": true,
    "exitOrderType": "MARKET",
    "slOrderType": "MARKET",
    "takeProfitOrderType": "FEE_OPTIMIZED_LIMIT",
    "_note": "SL and emergency exits MUST be MARKET. Never ALO for stop losses."
  }
}
```

---

## How MAMBA Captures Range Breakouts

VIPER enters at range boundaries — long at support, short at resistance. Most of the time, price bounces back into the range and VIPER profits on the mean reversion. Standard VIPER exits at fixed tiers (3%, 5%, 8% ROE) and moves on.

But sometimes, the entry at support is the start of a trend — price doesn't just bounce, it breaks through resistance and keeps going. Standard VIPER's fixed tiers cap the gain at +20-25% ROE. MAMBA's High Water Mode lets that breakout run indefinitely.

| Scenario | VIPER (fixed) | MAMBA (High Water) |
|---|---|---|
| Normal range bounce: +8% ROE, retraces | Lock +5% (T3), exit | Lock +4% (50% of 8), exit |
| Range breakout: +30% ROE | Lock +18% (T6 cap) | Lock +25.5% (85% of 30) |
| Trend develops: +80% ROE | Would have exited at T6 cap | Lock +68% (85% of 80) |
| Explosive breakout: +200% ROE | Long gone | Lock +170% (85% of 200) |

On normal range bounces (the majority of trades), both perform similarly. The divergence happens on the 1-in-5 trade where the range entry catches a trend — that's where MAMBA captures 3-10x more than VIPER.

---

## Notification Policy

**ONLY alert:** Position OPENED or CLOSED, risk guardian triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine check, any reasoning.
All crons isolated. `NO_REPLY` for idle cycles.

---

## Deployment

MAMBA runs on the VIPER v2.1 skill. Deploy VIPER first, then apply these overrides.

**Critical:** After every position is opened, verify the DSL state file contains `lockMode: "pct_of_high_water"` and the full `tiers` array. Without them, High Water Mode is silently disabled.

---

## Expected Behavior vs Standard VIPER

| Metric | VIPER v2.1 | MAMBA (expected) |
|---|---|---|
| Trades/day | 4-8 | 4-8 (same entries) |
| Win rate | ~60-65% | ~60-65% (same entries) |
| Avg winner (range bounce) | 5-12% ROE | 5-12% ROE (similar on small moves) |
| Avg winner (breakout) | 15-25% ROE (capped) | **30-80%+ ROE** (uncapped) |
| Avg loser | -8 to -15% ROE | -8 to -15% ROE (similar) |
| Profit factor | ~1.2-1.5 | **~1.4-1.8** (breakout captures compound) |

Same entries, same win rate, same losers. The edge is entirely in the tail — the breakout trades that standard VIPER leaves on the table.
