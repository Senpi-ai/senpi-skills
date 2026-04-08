# Integrating Your Skill with DSL (Dynamic Stop Loss)

This guide explains how to integrate another skill (e.g. a trading strategy) with the **dsl-dynamic-stop-loss** skill so your positions get automated trailing stop-loss protection on Hyperliquid. The **wolf-strategy** skill is the reference implementation; this document generalizes that pattern for any skill.

---

## Prerequisites

- **dsl-dynamic-stop-loss** skill installed (provides `dsl-cli.py`, `dsl-v5.py`, `dsl-cleanup.py`).
- Your skill manages Hyperliquid perp positions (main dex and/or xyz dex) and has a **strategy ID** (UUID) per “strategy” or wallet.
- Senpi MCP (mcporter) configured; OpenClaw cron available for scheduling.

---

## Integration Overview

| Your skill’s responsibility | DSL skill’s responsibility |
|-----------------------------|-----------------------------|
| Resolve path to `dsl-cli.py` (and optionally `dsl-v5.py`) | Provide CLI and cron runner scripts |
| Provide a **dsl-profile.json** (or equivalent config) | Define config schema (phase1, phase2, tiers, etc.) |
| Call **add-dsl** after opening a position | Create strategy config + position state files |
| Call **delete-dsl** after closing a position (or when your logic closes) | Archive state; output `cron_to_remove` when last position gone |
| Create/remove **one OpenClaw cron per strategy** using CLI output | Output `cron_needed`, `cron_job_id`, `cron_schedule`, `cron_to_remove` |
| On `strategy_inactive` → remove cron, run **dsl-cleanup.py** | Define cleanup behavior |
| Never write DSL state files directly | Own all state under `{DSL_STATE_DIR}/{strategyId}/` |

**State layout:** DSL state lives under a single base dir, e.g. `{workspace}/dsl/`. Per strategy: `{DSL_STATE_DIR}/{strategyId}/` (strategyId = UUID). Position files: `{ASSET}.json` (main) or `xyz--SYMBOL.json` (xyz). Your skill’s own state (e.g. trade counters) should live in a **separate** directory (e.g. `state/{strategyKey}/`) so it is not confused with DSL position state.

---

## Step 1: Provide a DSL Configuration Profile

Create a **dsl-profile.json** (or equivalent) in your skill directory that defines default DSL behavior for your strategy. The CLI merges this with strategy-level overrides.

**Minimal example:**

```json
{
  "cronIntervalMinutes": 3,
  "phase1": {
    "enabled": true,
    "retraceThreshold": 0.10,
    "consecutiveBreachesRequired": 3
  },
  "phase2TriggerTier": 0,
  "phase2": {
    "enabled": true,
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2,
    "tiers": [
      { "triggerPct": 5, "lockPct": 50 },
      { "triggerPct": 10, "lockPct": 65 },
      { "triggerPct": 15, "lockPct": 75 },
      { "triggerPct": 20, "lockPct": 85 }
    ]
  }
}
```

Optional Phase 1 time-based cuts (if you want the DSL cron to handle them):

- `phase1.hardTimeout`: close after N minutes in Phase 1.
- `phase1.weakPeakCut`: close if peak ROE &lt; minValue and current &lt; peak after N minutes.
- `phase1.deadWeightCut`: close if ROE was never positive for N minutes.

See [state-schema.md](state-schema.md) and [strategy-schema.md](strategy-schema.md) for full field reference. Config precedence: `--configuration` (inline or `@file`) → strategy `defaultConfig` → built-in defaults. Use `@path` so the CLI reads from file: `--configuration @/path/to/your/dsl-profile.json`.

---

## Step 2: Resolve Paths to DSL Scripts

Your skill must locate **dsl-cli.py** (and, for the cron mandate, **dsl-v5.py**) at runtime. Options:

1. **Environment:** `DSL_CLI_PATH` (and optionally a similar env for the script dir).
2. **Registry/config:** Store `dslCliPath` (and `dslScriptPath`) in your skill’s global config (e.g. `wolf-strategies.json` → `global.dslCliPath`).
3. **Discovery:** Scan known roots (e.g. workspace `skills/`, or parent of your skill dir) for a directory containing `scripts/dsl-cli.py` and use that.

**Convention:** The DSL skill keeps `dsl-cli.py` and `dsl-v5.py` under `scripts/`; discovery can look for `scripts/dsl-cli.py` under sibling/skill directories. If discovery fails, document that the user must set the path in config or env.

Example (conceptual):

```python
def resolve_dsl_cli_path():
    path = os.environ.get("DSL_CLI_PATH")
    if path and os.path.isfile(path):
        return path
    path = your_registry.get("global", {}).get("dslCliPath")
    if path and os.path.isfile(path):
        return path
    path = discover_dsl_cli_path()  # e.g. sibling dirs: dsl-dynamic-stop-loss/scripts/dsl-cli.py
    if path:
        return path
    raise FileNotFoundError("dsl-cli.py not found. Set dslCliPath or DSL_CLI_PATH, or install dsl-dynamic-stop-loss skill.")
```

Use the same directory as `dsl-cli.py` for `dsl-v5.py` (e.g. `os.path.join(os.path.dirname(cli_path), "dsl-v5.py")`).

---

## Step 3: Call add-dsl After Opening a Position

After you open a position (and have wallet, strategyId, asset, dex, and optionally entry/size/leverage), create DSL state via the CLI. **Do not** create or edit DSL JSON files by hand.

**Single position:**

```bash
python3 <path>/dsl-cli.py add-dsl <strategy-id> <asset> <dex> \
  --skill <your-skill-name> \
  --configuration @<path-to-your-dsl-profile.json> \
  --state-dir <DSL_STATE_DIR>
```

- **strategy-id:** UUID of the strategy (must match what you use for MCP and cron).
- **asset:** Symbol as used on HL (e.g. `HYPE`, `ETH`). For xyz use prefix in state; CLI accepts `xyz:SILVER` and normalizes to `xyz--SILVER` in filenames.
- **dex:** `main` or `xyz`.
- **--skill:** Your skill name (e.g. `wolf-strategy`). Stored in strategy config as `createdBySkill`.
- **--configuration:** Use `@path` to point to your dsl-profile.json so the CLI reads the file. You can also pass multiple `--configuration` values; later ones override (field-level). Inline JSON is allowed: `--configuration '{"phase1":{"retraceThreshold":0.05}}'`.
- **--state-dir:** Base directory for DSL state (e.g. `{workspace}/dsl`). Defaults to `$DSL_STATE_DIR` or `/data/workspace/dsl` if omitted.

Optional: **--entry-price** if you need to override (e.g. multi-fill average). Otherwise the CLI can fetch from clearinghouse.

**All current positions for a strategy:**

```bash
python3 <path>/dsl-cli.py add-dsl <strategy-id> \
  --skill <your-skill-name> \
  --configuration @<path-to-your-dsl-profile.json> \
  --state-dir <DSL_STATE_DIR>
```

**Parsing output:** CLI prints JSON to stdout. Important fields:

- **cron_needed:** `true` when this is the first DSL for this strategy → you must create the OpenClaw cron.
- **cron_job_id:** Use this exact ID when creating (and later removing) the cron.
- **cron_schedule**, **cron_env**, **cron_interval_minutes:** Use these to create the cron. Schedule is derived from `cronIntervalMinutes` (default 3).
- **positions_added:** List of positions for which state was created (may be empty if clearinghouse not yet updated; DSL will reconcile on next run).

If `cron_needed` is true, create the OpenClaw cron (see Step 5) and store `cron_job_id` in your strategy config so you can remove it when the strategy has no positions left.

---

## Step 4: Call delete-dsl When Closing a Position

Whenever **your skill** closes a position (or a process under your control closes it), archive DSL state with delete-dsl. **Never** set `active: false` in the state file; the CLI archives by renaming the file.

```bash
python3 <path>/dsl-cli.py delete-dsl <strategy-id> <asset> <dex> --state-dir <DSL_STATE_DIR>
```

**Parse output:** If the CLI returns **cron_to_remove**, this was the last position for that strategy → remove the OpenClaw cron for this strategy and run **dsl-cleanup.py** (see Cleanup below).

Asset/dex rules:

- **Main dex:** asset as symbol (e.g. `ETH`), dex `main`.
- **XYZ dex:** asset with `xyz:` prefix (e.g. `xyz:SILVER`); dex `xyz`. Filename will be `xyz--SILVER.json`.

---

## Step 5: One OpenClaw Cron Per Strategy

DSL runs as **one cron per strategy**, not per position. The cron runs `dsl-v5.py` with that strategy’s ID and state dir.

**When to create the cron:** When the first position gets DSL (i.e. when add-dsl output has `cron_needed: true`). Use the **cron_job_id** from the CLI so you can remove the same job later.

**Cron command:**

```bash
PYTHONUNBUFFERED=1 python3 <path>/dsl-v5.py --strategy-id <strategy-id-uuid> --state-dir <DSL_STATE_DIR>
```

Prefer CLI args; env vars `DSL_STRATEGY_ID` and `DSL_STATE_DIR` are fallbacks.

**Schedule:** Use the schedule from add-dsl output (e.g. every 3 minutes from `cronIntervalMinutes`). If you later change `cronIntervalMinutes` via update-dsl, the CLI will output `cron_schedule_changed: true` and `cron_to_remove`; remove the old cron and create a new one with the new schedule and the same `cron_job_id`.

**Cron mandate (for the agent that runs in the cron):** The agent should:

1. Run the command above and parse **ndjson** (one JSON line per position or strategy-level event).
2. For each line:
   - **closed=true** → Notify user (e.g. Telegram) with asset, direction, reason, PnL; consider freed slot for next entry.
   - **strategy_inactive=true** → Remove this cron, run `dsl-cleanup.py` for this strategy.
   - **pending_close=true** → Notify “DSL close pending retry for {asset}”.
   - **sl_initial_sync=true** → Optional silent (HL SL is active).
3. If no actionable events → HEARTBEAT_OK.

---

## Step 6: Handling dsl-v5.py Output and Cleanup

- **closed=true:** DSL closed the position; state file was archived. Notify user; no need to call delete-dsl again (DSL already did the archive).
- **strategy_inactive:** Strategy is inactive (e.g. MCP says so). Remove the OpenClaw cron for this strategy and run Level 2 cleanup.
- **cron_to_remove** (from delete-dsl): Your code closed the last position; remove the cron and run cleanup.

**Level 2 cleanup (strategy directory):**

```bash
DSL_STRATEGY_ID=<strategy-id> python3 <path>/dsl-cleanup.py
# Or with explicit state dir:
DSL_STATE_DIR=<path> DSL_STRATEGY_ID=<strategy-id> python3 <path>/dsl-cleanup.py
```

Cleanup removes the strategy directory only when no file has `active=true`. See [cleanup.md](cleanup.md).

---

## Step 7: Optional — Dynamic Config (e.g. Per-Strategy Tiers)

If your skill has per-strategy DSL settings (e.g. different tiers or breach counts), you can:

- Build a config dict in code (merge your defaults with dsl-profile.json and strategy overrides), then pass it as inline JSON: `--configuration '{"phase2":{"tiers":[...]}}'`, or
- Use a temp file and `--configuration @/tmp/your-config.json`.

Configuration merge: multiple `--configuration` values are merged (later overrides); `tiers` is replaced as a whole when present. See [cli-usage.md](cli-usage.md).

---

## Step 8: Optional — update-dsl, pause-dsl, resume-dsl

- **update-dsl:** Patch strategy or position config (e.g. change `phase1.retraceThreshold`). If you change `cronIntervalMinutes`, recreate the cron as in Step 5.
- **pause-dsl / resume-dsl:** Pause or resume monitoring; state is preserved; the cron can keep running.

See [cli-usage.md](cli-usage.md) for syntax.

---

## Checklist for Your Skill

- [ ] **dsl-dynamic-stop-loss** skill installed; paths to `dsl-cli.py` and `dsl-v5.py` resolved (env, config, or discovery).
- [ ] **dsl-profile.json** (or equivalent) in your skill with phase1/phase2/tiers; use `@path` in `--configuration`.
- [ ] After **open position** → call **add-dsl** with `--skill <your-skill-name>` and `--state-dir`; if output has `cron_needed`, create OpenClaw cron with given `cron_job_id` and schedule.
- [ ] After **close position** (from your side) → call **delete-dsl**; if output has `cron_to_remove`, remove cron and run **dsl-cleanup.py**.
- [ ] **Never** write or edit DSL state files directly; never set `active: false` in place.
- [ ] Cron mandate: run `dsl-v5.py --strategy-id <uuid> --state-dir <dir>`, parse ndjson, handle `closed` / `strategy_inactive` / `pending_close`; on `strategy_inactive` remove cron and run cleanup.
- [ ] Keep your skill’s state (e.g. trade counters) in a **separate** directory from `DSL_STATE_DIR` so DSL and your logic don’t conflict.

---

## Reference: wolf-strategy Integration

- **Config:** `wolf-strategy/dsl-profile.json`; per-strategy overrides merged in `build_wolf_dsl_config()` in `wolf_config.py`.
- **Paths:** `resolve_dsl_cli_path()` / `_discover_dsl_cli_path()` in `wolf_config.py`; stored in `wolf-strategies.json` as `global.dslCliPath` and `global.dslScriptPath`.
- **Add DSL:** `open-position.py` calls dsl-cli add-dsl after opening; passes `--skill wolf-strategy` and JSON config from `build_wolf_dsl_config()`.
- **Delete DSL:** `open-position.py`, `wolf-monitor.py`, `sm-flip-check.py`, `job-health-check.py` call delete-dsl when closing or healing; they check for `cron_to_remove`.
- **Cron:** One DSL cron per strategy; template in `references/cron-templates.md`; `wolf-setup.py` emits the mandate with filled `dsl-v5.py` path and `DSL_STATE_DIR`.
- **State layout:** DSL under `{workspace}/dsl/{strategyId_UUID}/`; wolf-specific state under `state/{strategyKey}/`.

For full CLI and schema details, see [cli-usage.md](cli-usage.md), [state-schema.md](state-schema.md), and [strategy-schema.md](strategy-schema.md).
