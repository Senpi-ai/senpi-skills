---
name: dsl-dynamic-stop-loss
description: >-
  Manages automated trailing stop losses for leveraged perpetual positions on
  Hyperliquid. Monitors price via cron, ratchets profit floors through configurable tiers, and auto-closes positions on breach via mcporter — no agent intervention for the critical path. Supports LONG and SHORT, strategy-scoped state isolation, and automatic cleanup on position or strategy close. ROE-based (return on margin)
  tier triggers that automatically account for leverage.
  Use when protecting an open Hyperliquid perp position, setting up trailing stops, managing profit tiers, or automating position exits on breach.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and cron.
  Hyperliquid perp positions only (main dex and xyz dex).
metadata:
  author: jason-goldberg
  version: "5.0"
  platform: senpi
  exchange: hyperliquid
---

# Dynamic Stop Loss (DSL) v5

Automated trailing stop loss for leveraged perp positions on Hyperliquid (main and xyz dex). Monitors price via cron, ratchets profit floors upward through configurable tiers, and **auto-closes positions on breach** — no agent intervention required for the critical path. v5 adds strategy-scoped state paths and delete-on-close cleanup.

## Self-Contained Design

```
Script handles:              Agent handles:
✅ Price monitoring           📢 Telegram alerts
✅ High water tracking        🧹 Cron cleanup (disable after close)
✅ Tier upgrades              📊 Portfolio reporting
✅ Breach detection           🔄 Retry awareness (pendingClose alerts)
✅ Position closing (via mcporter, with retry)
✅ State deactivation
✅ Error handling (fetch failures)
```

The script closes positions directly via `mcporter`. If the agent is slow, busy, or restarting, the position still gets closed on the next cron tick.

## How It Works

### Phase 1: "Let It Breathe" (uPnL < first tier)
- **Wide retrace**: 3% from high water mark
- **Patient**: requires 3 consecutive breach checks below floor
- **Absolute floor**: hard price floor to cap max loss
- **Goal**: Don't get shaken out before the trade develops

### Phase 2: "Lock the Bag" (uPnL ≥ first tier)
- **Tight retrace**: 1.5% from high water mark (or per-tier retrace)
- **Quick exit**: 1–2 consecutive breaches to close
- **Tier floors**: ratchet up as profit grows — never go back down
- **Effective floor**: best of tier floor and trailing floor

### ROE-Based Tier Ratcheting

All tier triggers use ROE (Return on Equity): `PnL / margin × 100`. This means a `triggerPct: 10` fires at 10% return on margin, not 10% price move. Leverage is accounted for automatically.

Tiers are defined as `{triggerPct, lockPct}` pairs. Each tier can optionally specify its own `retrace` value to tighten stops as profit grows:

```json
"tiers": [
  {"triggerPct": 10, "lockPct": 5},
  {"triggerPct": 20, "lockPct": 14},
  {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
  {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
  {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
  {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
]
```

The tier floor locks a **fraction of the move from entry to high water** (lockPct % of that range). The gap between trigger and lock gives breathing room so a minor pullback after hitting a tier doesn't immediately close. **Ratchets never go down** — once you hit Tier 2, Tier 1's floor is permanently superseded.

See [references/tier-examples.md](references/tier-examples.md) for LONG and SHORT worked examples with exact price calculations.

### Direction Matters

> ⚠️ **CRITICAL — Getting direction backwards causes immediate false breaches or no protection at all.** The script handles this automatically via the `direction` field, but double-check when initializing state files manually.

| | LONG | SHORT |
|---|---|---|
| **Tier floor** | `entry + (hw − entry) × lockPct / 100` | `entry − (entry − hw) × lockPct / 100` |
| **Absolute floor** | Below entry (e.g., entry × 0.97) | Above entry (e.g., entry × 1.03) |
| **High water** | Highest price seen | Lowest price seen |
| **Trailing floor** | `hw × (1 - retrace)` | `hw × (1 + retrace)` |
| **Breach** | `price ≤ floor` | `price ≥ floor` |
| **uPnL** | `(price - entry) × size` | `(entry - price) × size` |

### Breach Decay

When price recovers above the floor:
- `"hard"` (default): breach count resets to 0
- `"soft"`: breach count decays by 1 per check

Soft mode is useful for volatile assets where price rapidly oscillates around the floor.

### Floor Resolution

At each check, the effective floor is the **best** of:
1. **Tier floor** — locked profit level (Phase 2 only)
2. **Trailing floor** — from high water mark and retrace %
3. **Absolute floor** — hard minimum (Phase 1 only)

For LONGs, "best" = maximum. For SHORTs, "best" = minimum.

## Architecture

```
┌──────────────────────────────────────────┐
│ Cron: every 3-5 min (per position)       │
├──────────────────────────────────────────┤
│ scripts/dsl-v5.py                        │
│ • Reads state (v5: strategy dir + asset) │
│ • Fetches price via MCP (main + xyz)     │
│ • Direction-aware (LONG + SHORT)         │
│ • Updates high water mark                │
│ • Checks tier upgrades (ROE-based)       │
│ • Per-tier retrace override              │
│ • Calculates effective floor             │
│ • Detects breaches (with decay modes)    │
│ • ON BREACH: closes via mcporter w/retry │
│ • Deletes state file on close (no archive)   │
│ • pendingClose if close fails                 │
│ • Outputs enriched JSON status                │
├──────────────────────────────────────────┤
│ Agent reads JSON output:                 │
│ • closed=true → alert user, disable cron (script already deleted state file) │
│ • pending_close=true → alert, will retry │
│ • tier_changed=true → notify user        │
│ • status=error → log, check failures     │
│ • Otherwise → silent                     │
└──────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `scripts/dsl-v5.py` | Core DSL engine — monitors, closes, deletes state on close, outputs JSON |
| `scripts/dsl-cleanup.py` | Strategy-level cleanup — deletes strategy dir when all positions closed |
| State file (JSON) | Per-position config + runtime state; path: `{DSL_STATE_DIR}/{strategyId}/{asset}.json` |

Use `DSL_STATE_DIR` + `DSL_STRATEGY_ID` + `DSL_ASSET` per position. See [references/state-schema.md](references/state-schema.md) for path conventions. Cleanup: [references/cleanup.md](references/cleanup.md).

## State File Schema

See [references/state-schema.md](references/state-schema.md) for the complete schema with all fields documented.

Minimal required fields to create a new state file:

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
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 28.00
  },
  "phase2TriggerTier": 1,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 22, "retrace": 0.012},
    {"triggerPct": 50, "lockPct": 40, "retrace": 0.010},
    {"triggerPct": 75, "lockPct": 60, "retrace": 0.008},
    {"triggerPct": 100, "lockPct": 80, "retrace": 0.006}
  ],
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 28.87,
  "floorPrice": 28.00,
  "currentBreachCount": 0,
  "createdAt": "2026-02-20T15:22:00.000Z"
}
```

**`wallet` is required** — the script uses it to call `close_position` on breach.

### Absolute Floor Calculation

- **LONG:** `entry × (1 - maxLoss% / leverage)` — e.g., 10x with 3% → `28.87 × (1 - 0.03/10)` = $28.78
- **SHORT:** `entry × (1 + maxLoss% / leverage)` — e.g., 7x with 3% → `1955 × (1 + 0.03/7)` = $1,963.38

## Output JSON

The script prints a single JSON line per run. See [references/output-schema.md](references/output-schema.md) for the complete schema.

Key fields for agent decision-making:

| Field | Agent action |
|-------|-------------|
| `closed: true` | Alert user, disable cron |
| `pending_close: true` | Alert — close failed, retrying next tick |
| `tier_changed: true` | Notify user with tier details |
| `status: "error"` | Log; alert if `consecutive_failures >= 3` |
| `breached: true` | Alert "⚠️ BREACH X/X" |
| `distance_to_next_tier_pct < 2` | Optionally notify approaching next tier |

## Cron Setup

Per-position cron (every 3-5 min):

```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 DSL_ASSET=ETH python3 scripts/dsl-v5.py
```

For xyz dex: `DSL_ASSET=xyz:SILVER` (state file: `xyz--SILVER.json`). Stagger multiple positions by offsetting start times (:00, :01, :02).

## How to Set Up a New Position

1. Open position via Senpi API (`create_position`)
2. Create a state file with position details (see schema above)
   - **Double-check `direction`** — controls all LONG/SHORT math
   - **Calculate `absoluteFloor` correctly** for the direction
3. Create a cron job (every 3-5 min)
4. DSL handles everything from there

### When a Position Closes

1. ✅ Script closes position via `senpi:close_position` (coin with `xyz:` prefix as-is; with retry)
2. ✅ Script deletes the state file (no archive)
3. 🤖 Agent disables the cron (reads `closed=true`)
4. 🤖 Agent sends alert to user

If close fails, script sets `pendingClose: true` and retries next cron tick. When all crons for a strategy are disabled, run `dsl-cleanup.py` for strategy-level cleanup — see [references/cleanup.md](references/cleanup.md).

## Customization

See [references/customization.md](references/customization.md) for conservative/moderate/aggressive presets and per-tier retrace tuning guidelines.

## API Dependencies

- **Price**: `senpi:market_get_prices` or `senpi:allMids` via mcporter (main + xyz dex)
- **Close position**: `senpi:close_position` via mcporter (pass `coin` with `xyz:` prefix for xyz assets)

> ⚠️ **Do NOT use `strategy_close_strategy`** to close individual positions. That closes the **entire strategy** (irreversible). Use `close_position`.

## Setup Checklist

1. Extract `scripts/dsl-v5.py` and `scripts/dsl-cleanup.py`; `chmod +x`
2. Ensure `mcporter` is configured with Senpi auth
3. Create state file(s) per position under `{DSL_STATE_DIR}/{strategyId}/{asset}.json` (see [references/state-schema.md](references/state-schema.md))
4. Set up cron per position with `DSL_STATE_DIR`, `DSL_STRATEGY_ID`, `DSL_ASSET`
5. Agent reads output for alerts and cron cleanup; on `closed=true`, disable cron (script already deleted state file)
6. When all positions in a strategy are closed, run `dsl-cleanup.py` — see [references/cleanup.md](references/cleanup.md)
7. If `pending_close=true`, script auto-retries on next tick
