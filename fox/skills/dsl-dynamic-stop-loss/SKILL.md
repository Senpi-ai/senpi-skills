---
name: dsl-dynamic-stop-loss
description: >-
  Manages automated **dynamic/trailing** stop losses (DSL only) for leveraged perpetual positions on
  Hyperliquid. Monitors price via cron, ratchets profit floors through configurable tiers, and auto-closes positions on breach via mcporter — no agent intervention for the critical path. Supports LONG and SHORT, strategy-scoped state isolation, and automatic cleanup on position or strategy close. ROE-based (return on margin)
  tier triggers that automatically account for leverage.
  Use only when the user wants a **trailing/dynamic** stop loss (DSL). Do not use for normal/static stop loss. If the user says "stop loss" without specifying DSL vs normal, ask which they mean before proceeding.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and cron.
  Hyperliquid perp positions only (main dex and xyz dex).
metadata:
  author: jason-goldberg
  version: "5.1"
  platform: senpi
  exchange: hyperliquid
---

# Dynamic Stop Loss (DSL) v5

**Scope — DSL only.** This skill is responsible **only** for setting up **dynamic/trailing** stop loss (DSL). It does **not** handle normal (static) stop loss. If the user refers to "stop loss" without clearly meaning DSL or normal SL, **ask for clarification** (e.g. "Do you want a trailing stop that moves up with profit, or a fixed price stop loss?") before acting.

**Communication with users.** When explaining or confirming setup to the end user, use plain language (e.g. "trailing stop", "dynamic stop", "profit protection"). Do **not** reveal implementation details such as storage locations, script names, file paths, or internal file names unless the user explicitly asks for technical or implementation details.

**User-facing response guidelines.** When you confirm setup or report status to the user, write like this — and **avoid** the technical style below.

- **Good (do):** "Trailing stop protection is on for LAYER, PAXG, xyz:SILVER, xyz:CL, and xyz:NATGAS. Each position is checked every few minutes and will close automatically if the stop is hit. I'll notify you if any position closes or if there’s a problem. Cleanup after close is handled automatically."
- **Good (do):** "LAYER’s price couldn’t be fetched this run (temporary). The monitor will retry on the next run. I’ll alert you if it keeps failing."
- **Bad (don’t):** Mention state file paths, `~/.openclaw/workspace/dsl/…`, cron IDs, "dsl-v5.py", `DSL_STATE_DIR`, `DSL_STRATEGY_ID`, `DSL_ASSET`, or "per-position crons" — unless the user explicitly asks how it works under the hood.
- **Bad (don’t):** Ask "if we should add cleanup automation" — cleanup (disable on close, strategy cleanup when all closed) is already part of the flow; don’t offer it as an optional add-on.
- **Bad (don’t):** Raw error text like "price_fetch_failed: no price for LAYER (dex=main)". Rephrase in plain language: e.g. "Couldn’t get a price for LAYER this time; will retry shortly."

---

Automated trailing stop loss for leveraged perp positions on Hyperliquid (main and xyz dex). Monitors price via cron, ratchets profit floors upward through configurable tiers, and **syncs the stop loss to Hyperliquid** via Senpi `edit_position` so that **Hyperliquid executes the SL** when price hits — reducing loss exposure versus a 3-minute cron-only close. Breach detection, tier upgrades, and retraction logic still run in the cron; cancellation and setup of the SL happen via Senpi API; the new SL order ID is stored in state and status can be checked via `strategy_get_open_orders`. On breach the script may still call `close_position` as a backup. v5 adds strategy-scoped state paths and archive-on-close (state file renamed to `{asset}_archived_{epoch}.json`).

## Self-Contained Design

```
Script handles:              Agent handles:
✅ Strategy active check (MCP strategy_get)   📢 Telegram alerts
✅ Reconcile state vs positions (rename orphan state files to *_archived_external_*)   🧹 On strategy_inactive: remove cron, run cleanup
✅ Price monitoring           📊 Portfolio reporting
✅ High water + tier upgrades  🔄 Retry awareness (pendingClose alerts)
✅ Sync SL via edit_position (Senpi MCP); HL executes SL when price hits  ⏰ One cron per strategy when user sets up DSL
✅ Breach + close_position backup (mcporter)  📋 sl_synced / sl_order_id in output
✅ State archive on close (rename to *_archived_{epoch}.json)
✅ Error handling (fetch failures)
```

The script syncs the current effective floor to Hyperliquid as a native stop-loss order via Senpi `edit_position`. When price hits that level, Hyperliquid closes the position without waiting for the next cron tick. If the agent is slow or the SL did not fire yet, the script still attempts `close_position` on breach as a backup.

## How It Works

### Phase 1: "Let It Breathe" (uPnL < first tier)
- **Wide retrace**: 3% ROE from high water (converted to price via ÷ leverage so 3% = 3% ROE, not 3% price)
- **Patient**: requires 3 consecutive breach checks below floor
- **Absolute floor**: hard price floor to cap max loss
- **Goal**: Don't get shaken out before the trade develops

### Phase 2: "Lock the Bag" (uPnL ≥ first tier)
- **Tight retrace**: 1.5% ROE from high water (or per-tier retrace), leverage-adjusted
- **Quick exit**: 1 consecutive breach to close (default; configurable via `phase2.consecutiveBreachesRequired`)
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

**Retrace is ROE-based.** The configured retrace (e.g. 0.03 = 3%) is interpreted as **return on equity**, not raw price. The script converts to price via `retrace / leverage`, so at 10x leverage 3% ROE = 0.3% price move. This way "3%" means 3% ROE at any leverage, not 3% price (which would be 30% ROE at 10x).

See [references/tier-examples.md](references/tier-examples.md) for LONG and SHORT worked examples with exact price calculations.

### Direction Matters

> ⚠️ **CRITICAL — Getting direction backwards causes immediate false breaches or no protection at all.** The script handles this automatically via the `direction` field, but double-check when initializing state files manually.

| | LONG | SHORT |
|---|---|---|
| **Tier floor** | `entry + (hw − entry) × lockPct / 100` | `entry − (entry − hw) × lockPct / 100` |
| **Absolute floor** | Below entry (e.g., entry × 0.97) | Above entry (e.g., entry × 1.03) |
| **High water** | Highest price seen | Lowest price seen |
| **Trailing floor** | `hw × (1 - retrace/leverage)` | `hw × (1 + retrace/leverage)` |
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

**Cron is per strategy, not per position.** One cron per strategy; the script uses MCP clearinghouse as the source of truth for active positions and reconciles state files to it.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Cron: every 3-5 min (per strategy) — env: DSL_STATE_DIR, DSL_STRATEGY_ID  │
├──────────────────────────────────────────────────────────────────────────┤
│ scripts/dsl-v5.py (each run)                                              │
│ 1. MCP: strategy_get(strategy_id -> uuid) → strategy status from Senpi (not clearinghouse). │
│    If status not ACTIVE/PAUSED → remove active state files; print strategy_inactive.      │
│ 2. For each state file with slOrderId: MCP execution_get_order_status; if filled,         │
│    rename to {asset}_archived_sl_{epoch}.json (SL already hit on HL).                    │
│ 3. MCP: strategy_get_clearinghouse_state(wallet) → active position coins (main + xyz).   │
│ 4. Reconcile: rename state files whose asset is not in active positions to              │
│    {asset}_archived_external_{epoch}.json (position closed outside DSL).                │
│ 5. For each active position that has a state file:                                       │
│    • Fetch price via MCP (market_get_prices / allMids)                                    │
│    • Update high water, tier upgrades (ROE-based), effective floor                        │
│    • Sync SL: Phase 1 → edit_position(floor, MARKET); Phase 2 → edit_position(floor, LIMIT) │
│    • Detect breaches (Phase 2 default: 1 breach to close)                                  │
│    • ON BREACH: close_position via mcporter (backup); rename state to {asset}_archived_{epoch}.json  │
│    • Print one JSON line per position (ndjson)                                             │
├──────────────────────────────────────────────────────────────────────────┤
│ Agent reads output (one line per position, or one strategy-level line):  │
│ • strategy_inactive → remove cron for this strategy; run cleanup if needed│
│ • closed=true → alert user (script archived state file to {asset}_archived_{epoch}.json)   │
│ • pending_close=true → alert, will retry                                  │
│ • tier_changed=true → notify user                                         │
│ • status=error → log, check failures                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `scripts/dsl-v5.py` | Strategy-scoped DSL: MCP clearinghouse + reconcile state, then per-position monitor/close, outputs ndjson |
| `scripts/dsl-cleanup.py` | Strategy-level cleanup — deletes entire strategy dir (including archived state files) when no active positions remain |
| State file (JSON) | Per-position config + runtime state; path: `{DSL_STATE_DIR}/{strategyId}/{asset}.json` |
| [references/migration.md](references/migration.md) | Upgrading from cron-only DSL to Hyperliquid SL flow |

Use `DSL_STATE_DIR` + `DSL_STRATEGY_ID` only for cron (no per-position env). See [references/state-schema.md](references/state-schema.md) for path conventions. Cleanup: [references/cleanup.md](references/cleanup.md). Upgrading from cron-only DSL: [references/migration.md](references/migration.md).

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
    "consecutiveBreachesRequired": 1
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

The script prints **one JSON line per position** (ndjson), or **one strategy-level line** when the strategy is inactive or has no state files. See [references/output-schema.md](references/output-schema.md) for the complete schema.

Key fields for agent decision-making:

| Field / status | Agent action |
|----------------|--------------|
| `status: "strategy_inactive"` | Remove cron for this strategy; run strategy cleanup |
| `closed: true` | Alert user (script archived state file to `{asset}_archived_{epoch}.json`) |
| `pending_close: true` | Alert — close failed, retrying next tick |
| `tier_changed: true` | Notify user with tier details |
| `status: "error"` | Log; alert if `consecutive_failures >= 3` |
| `breached: true` | Alert "⚠️ BREACH X/X" |
| `distance_to_next_tier_pct < 2` | Optionally notify approaching next tier |
| `sl_initial_sync: true` | Optional: notify user that trailing stop is now synced to Hyperliquid for this position (e.g. after upgrade) |

## Cron Setup

**One cron per strategy** (every 3–5 min). **The agent must create this cron automatically when setting up DSL for a strategy** — do not leave cron setup to the user. Do **not** create a separate cron per position.

```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 python3 scripts/dsl-v5.py
```

No `DSL_ASSET` — the script discovers positions from MCP clearinghouse and state files in the strategy dir.

**Clock-aligned schedule (OpenClaw):** If the cron platform uses an internal anchor for interval-based schedules (e.g. `everyMs`), runs can occur at irregular wall-clock times. For **regular 3-minute boundaries** (e.g. :00, :03, :06…), create the job with a **cron expression** instead of a raw interval. Example: `"schedule": { "kind": "cron", "expr": "*/3 * * * *", "tz": "UTC" }` (use the user's timezone if preferred). This avoids the need to remove and re-add the cron at a boundary.

## How to Set Up a New Position

**Agent must complete all steps; cron is one per strategy, created automatically.**

1. Open position via Senpi API (`create_position`) if not already open.
2. **Create state directory and file** (see "State directory and file creation" below) — pay close attention to path and filename. Ensure the state file’s `wallet` and `strategyId` match the strategy (script uses wallet to call clearinghouse).
3. **Cron:** One cron per strategy. If this is the first position for this strategy, create the cron (every 3–5 min) with `DSL_STATE_DIR` and `DSL_STRATEGY_ID` only. If the strategy already has a cron, do not add another; the same cron run will pick up the new position via clearinghouse and its state file.
4. DSL handles monitoring and close from there.

### State directory and file creation

- **Base directory:** Use `DSL_STATE_DIR` (e.g. `/data/workspace/dsl`). Ensure it exists; create it if missing.
- **Strategy directory:** `{DSL_STATE_DIR}/{strategyId}` — create this directory if it does not exist. One directory per strategy.
- **State filename:**
  - Main dex: `{asset}.json` (e.g. `ETH` → `ETH.json`, `HYPE` → `HYPE.json`).
  - xyz dex: replace colon with double-dash — `xyz:SILVER` → `xyz--SILVER.json`, `xyz:AAPL` → `xyz--AAPL.json`.
- **Full path:** `{DSL_STATE_DIR}/{strategyId}/{filename}.json`. The script finds state files by listing the strategy dir and matching assets to clearinghouse positions.
- **State file contents:** Include all required fields from the schema, including **`wallet`** (used for clearinghouse and close). **Double-check `direction`** (LONG/SHORT). **Calculate `absoluteFloor`** correctly for the direction (see Absolute Floor Calculation below). Set `highWaterPrice` to entry price, `currentBreachCount` to 0, `currentTierIndex` to -1, `tierFloorPrice` to null, `floorPrice` to the absolute floor.

### When a Position Closes

1. ✅ **Hyperliquid** may close the position when price hits the synced SL (no cron delay). Each run the script calls `execution_get_order_status` for state files with `slOrderId`; if the SL order is **filled**, the state file is renamed to `{asset}_archived_sl_{epoch}.json` and not processed further.
2. ✅ If the position was closed outside DSL (e.g. manually), the next run finds the asset missing from clearinghouse and renames the state file to `{asset}_archived_external_{epoch}.json`.
3. ✅ On breach the script may close via `senpi:close_position` (backup; coin with `xyz:` prefix as-is; with retry). On successful close the state file is renamed to `{asset}_archived_{epoch}.json` (archived, not deleted). Archive filenames use epoch (Unix time) and underscores.
4. 🤖 **Agent:** On `closed=true` in script output — alert user. No need to disable a “position cron” (cron is per strategy and keeps running).
5. 🤖 **Agent:** On `status: "strategy_inactive"` — remove the cron for that strategy and run strategy cleanup (`dsl-cleanup.py`). The script only removes **active** state files; the agent must run cleanup to remove the strategy directory (and any archived files in it) — see [references/cleanup.md](references/cleanup.md).
6. 🤖 **Agent:** When the strategy has no active state files left, remove cron and run cleanup so the strategy directory is removed.

If close fails, script sets `pendingClose: true` and retries on the next cron tick.

## Customization

See [references/customization.md](references/customization.md) for conservative/moderate/aggressive presets and per-tier retrace tuning guidelines.

## API Dependencies

- **Strategy active**: `senpi:strategy_get` via mcporter (by `strategy_id`); status must be ACTIVE or PAUSED for DSL to run
- **Positions**: `senpi:strategy_get_clearinghouse_state` via mcporter (by strategy wallet from strategy_get)
- **Price**: `senpi:market_get_prices` or `senpi:allMids` via mcporter (main + xyz dex)
- **Sync stop loss**: `senpi:edit_position` via mcporter (`strategyWalletAddress`, `coin`, `stopLoss: { price, orderType }`). Phase 1 (initial/absolute floor) uses `orderType: "MARKET"` for fast exit; Phase 2 (tiered) uses `orderType: "LIMIT"`. Cancels previous SL and sets new SL on Hyperliquid.
- **SL order status**: `senpi:execution_get_order_status` (user/wallet, orderId) to detect if SL was already filled before reconcile; `senpi:strategy_get_open_orders` to resolve or verify SL order ID after edit.
- **Close position**: `senpi:close_position` via mcporter (backup on breach; pass `coin` with `xyz:` prefix for xyz assets)

> ⚠️ **Do NOT use `strategy_close_strategy`** to close individual positions. That closes the **entire strategy** (irreversible). Use `close_position`.

## Setup Checklist (agent responsibilities)

1. Ensure required scripts and mcporter (Senpi auth) are available.
2. **State:** Create base dir if needed; create strategy dir `{DSL_STATE_DIR}/{strategyId}`; create state file per position with correct filename (main: `{asset}.json`, xyz: `xyz--SYMBOL.json`). See [references/state-schema.md](references/state-schema.md). Each state file must include `wallet` (strategy wallet).
3. **Cron:** One cron per strategy (every 3–5 min), env `DSL_STATE_DIR` and `DSL_STRATEGY_ID` only — user must not set up cron manually.
4. **Alerts:** Read script output (ndjson); on `closed=true` alert user; on `strategy_inactive` remove cron for that strategy and run cleanup.
5. **Cleanup:** On `strategy_inactive` or when strategy has no positions left, run strategy cleanup so the strategy directory is removed — see [references/cleanup.md](references/cleanup.md).
6. If `pending_close=true`, script auto-retries on next tick; alert user.
