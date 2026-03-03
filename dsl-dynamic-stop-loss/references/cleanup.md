# DSL v5 Cleanup

Two-level cleanup: position close (Level 1) and strategy close (Level 2). No archiving — closed state and strategy directories are **deleted**.

## Level 1: Position Close

When `dsl-v5.py` reports `closed=true` (breach + successful close or deactivation):

- The script **deletes** the state file for that position.
- No `_closed/` archive; the file is removed.

**Agent:** Alert user. (Cron is per strategy; no per-position cron to disable.)

When `dsl-v5.py` reports `status: "strategy_inactive"` (strategy not active or clearinghouse failed, or no state files):

- The script has **deleted** all state files in the strategy dir (when inactive) or reports no state files.
- **Agent:** Remove the cron job for this strategy and run Level 2 cleanup below.

## Level 2: Strategy Cleanup

When an entire strategy is shut down (all positions closed, or agent signals strategy close):

**Script:** `scripts/dsl-cleanup.py`

`DSL_STATE_DIR` is optional (defaults to `/data/workspace/dsl`); use the env var if set so it matches the value used by the cron.

```bash
DSL_STRATEGY_ID=<strategy-uuid> python3 scripts/dsl-cleanup.py
# Or, if overriding state dir:
# DSL_STATE_DIR=/path/to/dsl DSL_STRATEGY_ID=<strategy-uuid> python3 scripts/dsl-cleanup.py
```

**Behavior:**

1. Scans all `*.json` files in the strategy directory.
2. If any file has `active=true` → exits with `status: "blocked"` and lists `blocked_by_active` (no deletion).
3. If all positions are closed (or directory is empty) → **deletes** the entire strategy directory.

**Output (stdout):**

| Status    | Meaning |
|----------|---------|
| `cleaned` | Strategy directory removed. `positions_deleted` is the number of state files that were in it. |
| `blocked` | At least one position still `active`; directory not touched. `blocked_by_active` lists assets. |

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
| `closed=true` in dsl-v5.py output | Alert user; script already deleted state file |
| `status: "strategy_inactive"` in dsl-v5.py output | Remove cron for that strategy; run `dsl-cleanup.py` for that strategy |
| All positions in strategy closed (no state files left) | Next run will output strategy_inactive; then remove cron and run `dsl-cleanup.py` |
| `strategy_close_strategy` called | After strategy is inactive, run `dsl-cleanup.py` |

## File Layout After Cleanup

Only active strategy dirs and active position files remain. No `_closed/`.
Paths use `DSL_STATE_DIR` (default `/data/workspace/dsl` when unset):

```
$DSL_STATE_DIR/
  strat-abc-123/
    ETH.json
```

Closed positions and fully closed strategies are deleted.
