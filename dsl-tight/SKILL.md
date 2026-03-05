---
name: dsl-tight
description: >-
  Opinionated trailing stop loss preset for Hyperliquid perps with tighter
  defaults than DSL v5. 4 tiers with per-tier breach counts that tighten as
  profit grows (3→2→2→1), auto-calculated price floors from entry and leverage,
  stagnation take-profit that closes if ROE ≥8% but high-water stalls for 1 hour.
  Same ROE-based engine as DSL v4 — different defaults, fewer knobs.
  Use when you want aggressive profit protection with minimal configuration.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and cron.
  Hyperliquid perp positions only (main and xyz dex). Uses dsl-v5.py from
  the dsl-dynamic-stop-loss skill; install that skill first.
metadata:
  author: jason-goldberg
  version: "2.0"
  platform: senpi
  exchange: hyperliquid
---

# DSL-Tight — Opinionated Stop-Loss Preset (v5)

A tighter, more opinionated variant of DSL for aggressive profit protection. Uses the **same script and architecture as DSL v5** (`dsl-v5.py`) — strategy-scoped cron, MCP clearinghouse, state under `{DSL_STATE_DIR}/{strategyId}/{asset}.json`. All tier triggers are ROE-based (`PnL / margin × 100`); leverage is accounted for automatically.

**Key difference from default DSL v5:** DSL v5 is the configurable engine with maximum flexibility. DSL-Tight is the "just works" preset — fewer knobs, tighter defaults, per-tier breach counts, stagnation exits, and auto-calculated floors.
## Core Concept

All thresholds defined in ROE. The script auto-converts to price levels:
```
price_floor = entry × (1 ± lockPct / 100 / leverage)
```

## How It Works

### Phase 1 — Absolute Floor (Stop-Loss)
- 5% ROE trailing floor
- 3 consecutive breaches required
- Auto-calculated absolute floor from entry/leverage/retrace

### Phase 2 — Tiered Profit Lock
4 tiers that lock an increasing percentage of the high-water move:

| Tier | Trigger ROE | Lock % of HW Move | Breaches to Close |
|------|-------------|--------------------|--------------------|
| 1 | 10% | 50% | 3 |
| 2 | 20% | 65% | 2 |
| 3 | 40% | 75% | 2 |
| 4 | 75% | 85% | 1 |

Per-tier breach counts tighten as profit grows — at Tier 4 (75% ROE), a single breach closes immediately.

### Stagnation Take-Profit
Auto-closes if:
- ROE ≥ 8% AND
- High-water mark hasn't improved for 1 hour

Catches winners that stall — takes the profit rather than waiting for a reversal.

## Breach Mechanics

- Hard decay only (breach count resets to 0 on recovery)
- Per-tier breach requirements (3→2→2→1) replace the global Phase 1/Phase 2 split
- Floor is always: `max(tier_floor, trailing_floor)` for LONG, `min()` for SHORT

## State File Schema

State files live in the **DSL v5 strategy directory**: `{DSL_STATE_DIR}/{strategyId}/{asset}.json` (main dex) or `{DSL_STATE_DIR}/{strategyId}/xyz--SYMBOL.json` (xyz dex). See [dsl-dynamic-stop-loss references/state-schema.md](../dsl-dynamic-stop-loss/references/state-schema.md) for path conventions.

```json
{
  "active": true,
  "asset": "HYPE",
  "direction": "LONG",
  "leverage": 10,
  "entryPrice": 28.87,
  "size": 1890.28,
  "wallet": "0xYourStrategyWalletAddress",
  "strategyId": "uuid-of-strategy",
  "phase": 1,
  "phase1": {
    "retraceThreshold": 0.05,
    "consecutiveBreachesRequired": 3
  },
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 10, "lockPct": 50, "retrace": 0.015},
    {"triggerPct": 20, "lockPct": 65, "retrace": 0.012},
    {"triggerPct": 40, "lockPct": 75, "retrace": 0.010},
    {"triggerPct": 75, "lockPct": 85, "retrace": 0.006}
  ],
  "breachDecay": "hard",
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 28.87,
  "floorPrice": 28.78,
  "currentBreachCount": 0,
  "createdAt": "2026-02-23T10:00:00.000Z"
}
```

Omit `phase1.absoluteFloor` — dsl-v5 fills it from entry and leverage. Set `highWaterPrice` to entry price, `floorPrice` to entry (or the computed absolute floor) for Phase 1 start.

### Field Reference

| Field | Purpose |
|-------|---------|
| `phase1.absoluteFloor` | **Not needed** — auto-calculated from entry, leverage, retrace |
| `tiers[].breachesRequired` | Per-tier breach count (replaces global phase2 setting) |
| `tiers[].retrace` | Per-tier trailing stop tightness |
| `stagnation.enabled` | Enable stagnation take-profit |
| `stagnation.minRoePct` | Minimum ROE to trigger stagnation check |
| `stagnation.maxStaleSec` | Max seconds HW can be stale before auto-close |

## Cron Setup

**Same as DSL v5:** one cron **per strategy** (not per position), every 3–5 minutes. The agent creates it when setting up DSL for a strategy.

**Resolving the script path:** When setting up the cron, **locate the dsl-dynamic-stop-loss skill on disk** (e.g. search the workspace, skills directory, or ClawHub install path for a folder named `dsl-dynamic-stop-loss` containing `scripts/dsl-v5.py`). Use that resolved path in the cron command — do not assume a fixed relative path. Example once resolved:

```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strategy-uuid python3 /path/to/dsl-dynamic-stop-loss/scripts/dsl-v5.py
```

No `DSL_ASSET` — the script discovers positions from MCP clearinghouse and state files in the strategy dir.

**Clock-aligned schedule (OpenClaw):** For runs at fixed 3-minute boundaries (e.g. :00, :03, :06…), create the job with a cron expression: `"schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" }`. See [dsl-dynamic-stop-loss SKILL.md](../dsl-dynamic-stop-loss/SKILL.md) Cron Setup and [references/explained.md](../dsl-dynamic-stop-loss/references/explained.md) §0.

Agent responsibilities (same as DSL v5): on `strategy_inactive` remove cron and run strategy cleanup; on `closed=true` alert user; on `pending_close=true` alert and retry next tick.

## Key Safety Features

- Same as DSL v5: strategy active check, reconcile state vs clearinghouse, delete state on close
- Auto-calculated floors eliminate manual math errors
- Per-tier breach tightening means large profits get maximum protection
- Stagnation TP prevents stalled winners from reversing

## Example Walkthrough

10x LONG entry at $28.87:

1. **Phase 1**: Floor auto-calculated ~$28.73. Price rises, HW tracks.
2. **Tier 1 at 10% ROE** ($29.16): Floor locks at ~$29.01, 3 breaches to close.
3. **Tier 2 at 20% ROE** ($29.45): Floor locks at ~$29.24, now only 2 breaches.
4. **Stagnation**: Price stalls at 12% ROE for 65 min → auto-closed with profit.
5. **Tier 4 at 75% ROE** ($31.04): Floor at ~$30.71. Single breach = instant close.

## Script and Dependencies

Uses **dsl-v5.py** from the **dsl-dynamic-stop-loss** skill. Install that skill first. **When setting up cron or running the script, locate the dsl-dynamic-stop-loss skill on disk** (search workspace/skills/install path for the folder containing `scripts/dsl-v5.py`) and use that path — do not assume a fixed location. State files go in `{DSL_STATE_DIR}/{strategyId}/{asset}.json` (main) or `xyz--SYMBOL.json` (xyz). For cron setup, path conventions, cleanup on strategy inactive, and output schema, follow the [dsl-dynamic-stop-loss](../dsl-dynamic-stop-loss/SKILL.md) skill.
