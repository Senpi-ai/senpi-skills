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

## Best practices

- **Single source of truth:** State is migrated by the same script that runs on schedule; no second migration script to keep in sync.
- **Idempotent:** Running the script multiple times is safe; it only syncs when `slOrderId` is missing or the effective floor has changed.
- **Agent visibility:** Use `sl_initial_sync: true` in the output to optionally notify the user that a position was just moved to the Hyperliquid SL flow (see [output-schema.md](output-schema.md) Agent Response Logic).
