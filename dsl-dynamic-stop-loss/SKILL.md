---
name: dsl-dynamic-stop-loss
description: >-
  Manages automated dynamic/trailing stop losses (DSL only) for leveraged perpetual positions on
  Hyperliquid. Default mode: High Water (pct_of_high_water) — the trailing floor is a percentage
  of the peak ROE, recalculated every tick, no ceiling. Also supports fixed ROE tiers for legacy
  positions. Monitors price via cron, ratchets profit floors through configurable tiers, syncs
  the stop loss to Hyperliquid via edit_position, and auto-closes positions on breach via mcporter.
  Supports LONG and SHORT, strategy-scoped state isolation, and automatic cleanup.
license: MIT
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and cron. Hyperliquid perp positions
  only (main dex and xyz dex).
metadata:
  author: jason-goldberg
  version: "5.3.1"
  platform: senpi
  exchange: hyperliquid
  
---

# Dynamic Stop Loss (DSL) v5.3.1

**Scope — DSL only.** This skill handles **only** dynamic/trailing stop loss (DSL), not normal (static) stop loss. If the user says "stop loss" without clearly meaning DSL or static, **ask** (e.g. "Do you want a trailing stop that moves up with profit, or a fixed price stop loss?").

**User-facing language.** Use plain terms ("trailing stop", "profit protection"). Do **not** mention state paths, cron IDs, script names, or `DSL_*` env unless the user asks for technical details.

## Default Mode: High Water

**All new positions should use High Water Mode** (`lockMode: "pct_of_high_water"`). This is the recommended default for every Senpi skill. The trailing floor is a percentage of the peak ROE, recalculated on every tick, with no ceiling. A trade at +50% ROE with 85% lock has its floor at +42.5%. A trade at +200% ROE has its floor at +170%. The geometry is constant — the trade always keeps 85% of its best moment.

See [dsl-high-water-spec 1.0.md](dsl-high-water-spec%201.0.md) for the full spec and [dsl-high-water-adoption-guide.md](dsl-high-water-adoption-guide.md) for per-skill tier configurations.

**When creating DSL state files, ALWAYS include:**
```json
{
  "lockMode": "pct_of_high_water",
  "tiers": [
    {"triggerPct": 7,  "lockHwPct": 40, "consecutiveBreachesRequired": 3},
    {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
    {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1}
  ]
}
```
Use the per-skill tiers from `dsl-high-water-adoption-guide.md` — different skills have different tier widths. The above is the standard momentum default (FOX, HAWK, DIRE WOLF).

**Legacy mode** (`lockMode: "fixed_roe"` or omitted) still works for existing positions. `lockPct` tiers lock a fixed fraction of the entry→HW price range. No per-tick recalculation — floor only updates on tier changes.

---

Trailing stop for Hyperliquid perps (main + xyz). **Default: High Water Mode** — floor trails the peak ROE as a percentage, recalculated every tick, no ceiling. Cron runs `dsl-v5.py` every 3–5 min; the script syncs the current floor to Hyperliquid via Senpi `edit_position` so HL can execute the SL even if the cron process goes down. Phase 1: wide retrace with absolute floor cap. Phase 2: tiered High Water locks that ratchet up with profit and never come back down. See [references/tier-examples.md](references/tier-examples.md) and [references/state-schema.md](references/state-schema.md).

**Files:** `scripts/dsl-v5.py` (monitor/close, ndjson output), `scripts/dsl-cleanup.py` (strategy dir cleanup), `scripts/dsl-cli.py` (lifecycle — use for all setup). Config: `dsl-profile.json` (this skill’s default). State: `{DSL_STATE_DIR}/{strategyId}/{asset}.json`; strategy config: [references/strategy-schema.md](references/strategy-schema.md). Cleanup: [references/cleanup.md](references/cleanup.md). Output schema: [references/output-schema.md](references/output-schema.md).

## Architecture

- **Scheduler:** OpenClaw cron (one per strategy, every 3–5 min). No per-position cron — the script discovers positions from MCP clearinghouse and state files.
- **Cron runner:** `dsl-v5.py` — accepts **`--strategy-id <uuid>`** and **`--state-dir <path>`** (CLI args take precedence; env vars `DSL_STRATEGY_ID`, `DSL_STATE_DIR` are fallbacks). Prefer CLI args to avoid agent mistyping UUID in env. Checks strategy active (MCP `strategy_get`); reconciles state files with clearinghouse (archives orphans); for each active position: fetch price, update high water and tiers, sync SL to Hyperliquid via `edit_position`, detect breach, on breach call `close_position` and archive state. Prints one JSON line per position (ndjson).
- **Lifecycle:** `dsl-cli.py` creates/updates strategy config and position state files only (it does not place the SL order). The cron runner syncs the floor to Hyperliquid via `edit_position` and sets `slOrderId` in state. When adding DSL for a strategy with no cron, CLI outputs `cron_needed`, `cron_job_id`, `cron_env`, `cron_schedule`. Agent creates or removes the OpenClaw cron using that ID.
- **Cleanup:** On strategy inactive or no positions left, agent runs `dsl-cleanup.py` to remove the strategy directory. SL sync and close use Senpi (mcporter); scheduling is OpenClaw only.

```
┌─────────────────────────────────────────────────────────────────┐
│ OpenClaw cron (per strategy)  →  dsl-v5.py                        │
│   MCP: strategy_get, clearinghouse, prices, edit_position,       │
│        close_position, execution_get_order_status                │
│   Output: ndjson (one line per position or strategy-level)       │
├─────────────────────────────────────────────────────────────────┤
│ Agent: create/remove cron from CLI output; run dsl-cleanup;       │
│        alert on closed / strategy_inactive / pending_close       │
└─────────────────────────────────────────────────────────────────┘
```

## CLI commands

All lifecycle operations use `scripts/dsl-cli.py`. Global option: `--state-dir` (default: `$DSL_STATE_DIR` or `/data/workspace/dsl`). Config must be a file path with `@` prefix (e.g. `@dsl-profile.json`) or inline JSON.

| Command | Usage | Notes |
|---------|--------|--------|
| **add-dsl** | `add-dsl <strategy-id> [asset dex] --skill <skill-name> --configuration @<path>` | Creates strategy config and position state files. Omit asset/dex for all positions. Optional `--entry-price`. Output: `cron_needed`, `cron_job_id`, `cron_env`, `cron_schedule` when cron must be created. |
| **update-dsl** | `update-dsl <strategy-id> [asset dex] --configuration <json-or-@path>` | Strategy-wide or per-position config patch (e.g. `'{"phase1":{"retraceThreshold":0.05}}'` or `@override.json`). |
| **pause-dsl** | `pause-dsl <strategy-id> [asset dex]` | Pause monitoring; cron keeps running, state preserved. |
| **resume-dsl** | `resume-dsl <strategy-id> [asset dex]` | Resume monitoring. |
| **delete-dsl** | `delete-dsl <strategy-id> [asset dex]` | Archive state and tear down. Output: `cron_to_remove` when agent must remove OpenClaw cron. |
| **status-dsl** | `status-dsl <strategy-id> [asset dex]` | Report current status. |
| **count-dsl** | `count-dsl <strategy-id>` | Aggregate counts. |
| **validate** | `validate --configuration @<path>` | Validate a DSL config file (e.g. `@dsl-profile.json`). |

Examples (this skill): `--skill dsl-dynamic-stop-loss --configuration @<path-to-this-skill>/dsl-profile.json`. Other skills: same CLI with `--skill <their-skill>` and `--configuration @<their-dsl-profile.json>`. Full details: [references/cli-usage.md](references/cli-usage.md).

## This skill (direct DSL setup)

When the user asks for trailing/dynamic stop loss, use **`scripts/dsl-cli.py`** for all lifecycle operations (do not edit state files by hand unless CLI is unavailable).

- **Add DSL:** `python3 scripts/dsl-cli.py add-dsl <strategy-id> [asset dex] --skill dsl-dynamic-stop-loss --configuration @<path-to-this-skill>/dsl-profile.json`
- If output has `cron_needed: true`, create the **OpenClaw** cron with the output `cron_job_id`, `cron_env`, and `cron_schedule` (schedule is derived from `cronIntervalMinutes`, default 3; one cron per strategy).
- **Update / Pause / Resume / Delete / Status / Count:** use the commands in the table above (same CLI, same `--skill` and `--configuration` as for add).

**Agent:** On `closed=true` → alert user. On `strategy_inactive` → remove cron, run `dsl-cleanup.py`. On `pending_close=true` → alert (script retries). On `delete-dsl` output `cron_to_remove` → remove that cron. On **update-dsl** output `cron_schedule_changed: true` → remove the existing cron (using `cron_to_remove.cron_job_id`) and create a new one with the same `cron_job_id` and the new `cron_schedule` (interval is configurable via `cronIntervalMinutes`; default 3 min).

## Other skills: setting up DSL

**Full integration guide:** [references/integration-guide.md](references/integration-guide.md) — step-by-step for any skill (paths, add/delete-dsl, cron, cleanup, checklist).

Any skill (e.g. `wolf-strategy`, `dsl-tight`) can add DSL for its strategies by calling the same CLI with its own profile and skill name.

1. **Locate** `dsl-cli.py` (e.g. `dsl-dynamic-stop-loss/scripts/dsl-cli.py`) and your skill’s **dsl-profile.json** (in your skill directory). Resolve paths at runtime.
2. **Add DSL:**  
   `python3 <path>/dsl-cli.py add-dsl <strategy-id> [asset dex] --skill <your-skill-name> --configuration @<path-to-your-dsl-profile.json>`  
   Use `@` before the config path so the CLI reads the file (without `@` it treats the value as inline JSON).
3. **Cron:** If the CLI output includes `cron_needed: true`, it also includes `cron_job_id`, `cron_env`, `cron_schedule`. Create the **OpenClaw** cron with that ID and env/schedule (one cron per strategy).
4. **Lifecycle:** For update, pause, resume, delete, status, count use the same CLI and the commands in the **CLI commands** table above (same `--skill` and `--configuration` as for add). See [references/cli-usage.md](references/cli-usage.md) for examples.
5. **Agent duties:** Same as this skill — on `closed=true` alert user; on `strategy_inactive` remove cron and run `dsl-cleanup.py`; on `cron_to_remove` remove that OpenClaw cron.

**Example (other skill, single position):**

```bash
python3 /path/to/dsl-dynamic-stop-loss/scripts/dsl-cli.py add-dsl <strategy-id> ETH main \
  --skill wolf-strategy \
  --configuration @/path/to/wolf-strategy/dsl-profile.json
```

Full command reference and configuration merge rules: [references/cli-usage.md](references/cli-usage.md).

## References

| Topic | Reference |
|-------|-----------|
| **Integrating another skill with DSL** | [references/integration-guide.md](references/integration-guide.md) |
| CLI commands and inter-skill usage | [references/cli-usage.md](references/cli-usage.md) |
| State and strategy schema | [references/state-schema.md](references/state-schema.md), [references/strategy-schema.md](references/strategy-schema.md) |
| Output (ndjson) and agent actions | [references/output-schema.md](references/output-schema.md) |
| Cleanup (strategy dir, cron removal) | [references/cleanup.md](references/cleanup.md) |
| Tier math, LONG/SHORT | [references/tier-examples.md](references/tier-examples.md) |
| Config tuning | [references/customization.md](references/customization.md) |
| Cron-only → HL SL migration | [references/migration.md](references/migration.md) |
| **ROE-based → High Water migration** (recommended for existing state) | [references/migration.md](references/migration.md#migrating-from-roe-based-fixed_roe-to-high-water) |

**API:** Strategy/positions/price/close and SL sync via Senpi (mcporter). Do **not** use `strategy_close_strategy` for a single position — use `close_position`.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v5.3.1 | 2026-03-13 | High Water: infinite trail, SL sync when floor moves; spec checklist tests |
| v5.3 | 2026-03-12 | High Water Mode: `lockMode: "pct_of_high_water"`, `lockHwPct` per tier |
| v5.2 | 2026-03-01 | CLI lifecycle manager, multi-skill integration, SL order verification, reconciliation archive types, `cronIntervalMinutes`, config validation |
| v5.0 | — | Two-phase trailing stop, exchange SL sync |
