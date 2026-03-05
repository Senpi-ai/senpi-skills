# DSL v5 Cleanup

Two-level cleanup: position close (Level 1) and strategy close (Level 2). State files are **archived** (renamed) on position close; the strategy directory is **deleted** only when the agent runs Level 2 cleanup.

## Level 1: Position Close (archive)

When `dsl-v5.py` reports `closed=true` (breach + successful close or deactivation):

- The script **renames** the state file to `{asset}_archived_{epoch}.json` (e.g. `ETH_archived_1709722800.json`). The file stays in the strategy dir as an archive.
- Other renames: SL already filled on HL → `{asset}_archived_sl_{epoch}.json`; position closed outside DSL → `{asset}_archived_external_{epoch}.json`.

**Agent:** Alert user. (Cron is per strategy; no per-position cron to disable.)

When `dsl-v5.py` reports `status: "strategy_inactive"` (strategy not active):

- The script **removes only active** state files (e.g. `ETH.json`) from the strategy dir. It does **not** remove `*_archived_*` files or the strategy directory itself.
- **Agent:** Remove the cron job for this strategy and run Level 2 cleanup below.

## Level 2: Strategy Cleanup

When the agent runs cleanup after strategy is inactive or all positions are closed:

**Script:** `scripts/dsl-cleanup.py`

`DSL_STATE_DIR` is optional (defaults to `/data/workspace/dsl`); use the env var if set so it matches the value used by the cron.

```bash
DSL_STRATEGY_ID=<strategy-uuid> python3 scripts/dsl-cleanup.py
# Or, if overriding state dir:
# DSL_STATE_DIR=/path/to/dsl DSL_STRATEGY_ID=<strategy-uuid> python3 scripts/dsl-cleanup.py
```

**Behavior:**

1. Scans all `*.json` files in the strategy directory (including `*_archived_*` files).
2. If any file has `active=true` in its JSON → exits with `status: "blocked"` and lists `blocked_by_active` (no deletion). Archived files have `active=false`, so they do not block.
3. If no file has `active=true` (or directory is empty) → **deletes the entire strategy directory**, including all archived state files.

**Output (stdout):**

| Status    | Meaning |
|----------|---------|
| `cleaned` | Strategy directory removed (including any archived files). `positions_deleted` is the count of `.json` files that were in the dir. |
| `blocked` | At least one state file has `active=true`; directory not touched. `blocked_by_active` lists assets. |

Example cleaned:

```json
{
  "status": "cleaned",
  "strategy_id": "strat-abc-123",
  "positions_deleted": 3,
  "blocked_by_active": [],
  "time": "2026-02-27T15:30:00Z"
}
```

Example blocked:

```json
{
  "status": "blocked",
  "strategy_id": "strat-abc-123",
  "blocked_by_active": ["ETH", "BTC"],
  "time": "2026-02-27T15:30:00Z"
}
```

## Agent Responsibilities

| Event | Agent action |
|-------|--------------|
| `closed=true` in dsl-v5.py output | Alert user; script archived state file to `{asset}_archived_{epoch}.json` |
| `status: "strategy_inactive"` in dsl-v5.py output | Remove cron for that strategy; run `dsl-cleanup.py` for that strategy |
| All positions in strategy closed (no active state files left) | Next run may output no_positions or strategy_inactive; then remove cron and run `dsl-cleanup.py` |
| `strategy_close_strategy` called | After strategy is inactive, run `dsl-cleanup.py` |

## File Layout

- **While strategy is active:** Strategy dir contains active state files (`ETH.json`, …) and, after some closes, archived files (`ETH_archived_1709722800.json`, …). Only active files are listed/processed by dsl-v5.
- **After strategy_inactive:** dsl-v5 has removed active state files; dir may still contain `*_archived_*` files.
- **After dsl-cleanup.py runs:** The entire strategy directory is removed (no archived files left).

Paths use `DSL_STATE_DIR` (default `/data/workspace/dsl` when unset):

```
$DSL_STATE_DIR/
  strat-abc-123/
    ETH.json              # active
    BTC_archived_1709722800.json   # archived (after close)
```

Once cleanup runs, `strat-abc-123/` is deleted.
