---
name: dsl
description: >
  Dynamic stop-loss engine for Hyperliquid leveraged positions.
  Use when running a trailing stop cron, checking a floor price,
  managing an open position, or processing a DSL heartbeat tick.
license: Apache-2.0
metadata:
  author: senpi
  version: "5.0.0"
  platform: senpi
  exchange: hyperliquid
---

# DSL — Dynamic Stop-Loss Engine v5

Automated trailing stop loss for leveraged perp positions on Hyperliquid. Monitors price via cron, ratchets profit floors upward through configurable tiers, and **auto-closes positions on breach** — no agent intervention required for the critical path.

## Unified DSL — Single Executor

This skill is the **single place** where DSL logic runs. Other skills depend on it:

- **wolf-strategy**, **dsl-tight**, etc. are **producers**: they create DSL state files when opening positions (canonical schema defined here).
- They are **consumers**: they run this skill (single or batch), parse JSON output, and handle alerts/slots.

No duplicated DSL engines; one codebase for tier/breach/close. See [design/dsl-unified-architecture.md](../design/dsl-unified-architecture.md) for the full design, migration plan, and optimization (batch mode, registry discovery, events, presets, token reduction).

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

The gap between trigger and lock (e.g., 10% trigger → 5% lock) gives breathing room so a minor pullback after hitting a tier doesn't immediately close. **Ratchets never go down** — once you hit Tier 2, Tier 1's floor is permanently superseded.

See [references/tier-examples.md](references/tier-examples.md) for LONG and SHORT worked examples with exact price calculations.

### Direction Matters

> ⚠️ **CRITICAL — Getting direction backwards causes immediate false breaches or no protection at all.** The script handles this automatically via the `direction` field, but double-check when initializing state files manually.

| | LONG | SHORT |
|---|---|---|
| **Tier floor** | `entry × (1 + lockPct / 100 / leverage)` | `entry × (1 - lockPct / 100 / leverage)` |
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
│ scripts/dsl-v4.py                        │
│ • Reads state from JSON file             │
│ • Fetches price from allMids API         │
│ • Direction-aware (LONG + SHORT)         │
│ • Updates high water mark                │
│ • Checks tier upgrades (ROE-based)       │
│ • Per-tier retrace override              │
│ • Calculates effective floor             │
│ • Detects breaches (with decay modes)    │
│ • ON BREACH: closes via mcporter w/retry │
│ • pendingClose if close fails            │
│ • Outputs enriched JSON status           │
├──────────────────────────────────────────┤
│ Agent reads JSON output:                 │
│ • closed=true → alert user, disable cron │
│ • pending_close=true → alert, will retry │
│ • tier_changed=true → notify user        │
│ • status=error → log, check failures     │
│ • Otherwise → silent                     │
└──────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `scripts/dsl_common.py` | Shared module — state I/O, MCP price fetch, close_position |
| `scripts/dsl-v4.py` | Entry point — loads state, uses dsl_common for I/O and MCP, runs tier/breach logic, outputs JSON |
| State file (JSON) | Per-position config + runtime state |

**Multiple positions:** Set `DSL_STATE_FILE=/path/to/state.json` to run separate instances per position. Each gets its own state file and cron job.

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
DSL_STATE_FILE=/data/workspace/dsl-state-BTC.json python3 scripts/dsl-v4.py
```

Stagger multiple positions by offsetting start times (:00, :01, :02).

## How to Set Up a New Position

1. Open position via Senpi API (`create_position`)
2. Create a state file with position details (see schema above)
   - **Double-check `direction`** — controls all LONG/SHORT math
   - **Calculate `absoluteFloor` correctly** for the direction
3. Create a cron job (every 3-5 min)
4. DSL handles everything from there

### When a Position Closes

1. ✅ Script closes position via `mcporter call senpi close_position` (with retry)
2. ✅ Script sets `active: false` (or `pendingClose: true` if close fails)
3. 🤖 Agent disables the cron (reads `closed=true`)
4. 🤖 Agent sends alert to user

If close fails, script sets `pendingClose: true` and retries next cron tick.

## Customization

See [references/customization.md](references/customization.md) for conservative/moderate/aggressive presets and per-tier retrace tuning guidelines.

## API Dependencies

- **Price**: Hyperliquid `allMids` API (direct HTTP, no auth)
- **Close position**: Senpi `close_position` via mcporter

> ⚠️ **Do NOT use `strategy_close_strategy`** to close individual positions. That closes the **entire strategy** (irreversible). Use `close_position`.

## Setup Checklist

1. Extract `scripts/dsl-v4.py` and `chmod +x`
2. Ensure `mcporter` is configured with Senpi auth
3. Create state file(s) per position
4. Set up cron: `DSL_STATE_FILE=/path/to/state.json python3 scripts/dsl-v4.py`
5. Agent reads output for alerts and cron cleanup
6. If `pending_close=true`, script auto-retries on next tick
