# Upgrading / Migration (Hyperliquid SL flow)

See the main skill: [SKILL.md](../SKILL.md).

This document describes how to move from an earlier DSL version (cron-only breach detection and `close_position` on breach) to the current flow where the stop loss is synced to Hyperliquid via Senpi `edit_position` and Hyperliquid executes the SL when price hits.

## Summary

- **No separate migration script or manual state edits.** The main script ([scripts/dsl-v5.py](../scripts/dsl-v5.py)) performs migration on the first run after the update by syncing each position’s SL to Hyperliquid and backfilling state.
- **Crons and state paths are unchanged.** Same env vars, same schedule, same state file paths.

## Crons

No change. The same per-strategy cron (same schedule, same env `DSL_STATE_DIR`, `DSL_STRATEGY_ID`) keeps running the updated script. Do not remove or recreate crons unless you are changing schedule or strategy.

## State files

No manual migration. Existing state files do not have `slOrderId`, `lastSyncedFloorPrice`, or `slOrderIdUpdatedAt`. On the **first run** after the update:

1. For each position the script sees that `slOrderId` is missing.
2. It calls `edit_position` to place the current effective floor as a stop loss on Hyperliquid.
3. It writes `slOrderId`, `lastSyncedFloorPrice`, and `slOrderIdUpdatedAt` into the state file.

From that run onward, the position is protected by Hyperliquid’s native SL and the script only updates the SL when the floor changes. The script output may include `sl_initial_sync: true` for positions that were migrated this run (see [output-schema.md](output-schema.md)).

## Migrate immediately (optional)

To sync SL to Hyperliquid without waiting for the next cron tick, run the same command the cron uses **once per strategy**, e.g.:

```bash
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=<strategy-uuid> python3 scripts/dsl-v5.py
```

That run will perform the initial sync for all positions of that strategy and backfill state. The agent may see `sl_initial_sync: true` in the output for those positions.

## Migrating from ROE-based (fixed_roe) to High Water

As of this version, **the default when no dsl-profile is supplied is High Water Mode** (`lockMode: "pct_of_high_water"`). If you have existing strategies or position state files that still use **fixed ROE tiers** (tiers with `lockPct` and no `lockMode`, or `lockMode: "fixed_roe"`), we recommend migrating them to High Water so the floor trails the peak ROE with no ceiling.

### Why migrate

- **High Water** trails the stop as a percentage of the highest ROE reached (e.g. 85% of peak). The floor moves up every tick with new highs; there is no cap.
- **Fixed ROE** locks a static ROE value per tier (e.g. “at 50% ROE lock 40% ROE”). The floor does not trail beyond that.

### How to migrate

Use **`update-dsl`** with a High Water configuration. This updates both the strategy’s `defaultConfig` and **all active position state files** for that strategy (tiers, `lockMode`, `phase2TriggerRoe`). Runtime state (high water price, current tier, breach count, etc.) is preserved.

**Option A — Use this skill’s default High Water profile (recommended):**

```bash
python3 scripts/dsl-cli.py update-dsl <strategy-id> \
  --state-dir /data/workspace/dsl \
  --configuration @/path/to/dsl-dynamic-stop-loss/dsl-profile.json
```

**Option B — Use your skill’s High Water dsl-profile:**

```bash
python3 scripts/dsl-cli.py update-dsl <strategy-id> \
  --state-dir /data/workspace/dsl \
  --configuration @/path/to/your-skill/dsl-profile.json
```

**Option C — Inline High Water config:**

```bash
python3 scripts/dsl-cli.py update-dsl <strategy-id> \
  --state-dir /data/workspace/dsl \
  --configuration '{"lockMode":"pct_of_high_water","phase2TriggerRoe":7,"tiers":[{"triggerPct":7,"lockHwPct":40,"consecutiveBreachesRequired":3},{"triggerPct":12,"lockHwPct":55,"consecutiveBreachesRequired":2},{"triggerPct":15,"lockHwPct":75,"consecutiveBreachesRequired":2},{"triggerPct":20,"lockHwPct":85,"consecutiveBreachesRequired":1}]}'
```

After running `update-dsl`:

- The next cron run (or a manual run of `dsl-v5.py`) will use the new tiers and sync the SL to Hyperliquid based on the High Water floor.
- No need to remove or recreate crons unless you change `cronIntervalMinutes`.

See [dsl-high-water-adoption-guide.md](../dsl-high-water-adoption-guide.md) for per-skill High Water presets and [dsl-high-water-spec 1.0.md](../dsl-high-water-spec%201.0.md) for the spec.

### If you don't migrate

**Old state files keep working.** The cron script ([dsl-v5.py](../scripts/dsl-v5.py)) treats state that has no `lockMode` (or `lockMode: "fixed_roe"`) as ROE-based: it uses each tier’s `lockPct` as a fraction of the entry→high-water range and does not trail the floor beyond that. So:

- Existing position state files with `lockPct` tiers and no `lockMode` (or `lockMode: "fixed_roe"`) continue to run with the same fixed-ROE behavior as before.
- New strategies and new positions created without a supplied profile get High Water by default; existing state is not changed unless you run `update-dsl` with a High Water config.

Migration is **recommended** so existing positions get the benefits of High Water (floor trails peak with no ceiling), but it is **optional** — you can migrate when convenient.

## Best practices

- **Single source of truth:** State is migrated by the same script that runs on schedule; no second migration script to keep in sync.
- **Idempotent:** Running the script multiple times is safe; it only syncs when `slOrderId` is missing or the effective floor has changed.
- **Agent visibility:** Use `sl_initial_sync: true` in the output to optionally notify the user that a position was just moved to the Hyperliquid SL flow (see [output-schema.md](output-schema.md) Agent Response Logic).
