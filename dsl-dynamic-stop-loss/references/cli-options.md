# DSL v5.1 — CLI options

Reference for all options used when constructing and running the DSL script. Script: `python3 scripts/dsl-v5.py` (or path to `dsl-v5.py`).

## Subcommands

| Subcommand | Purpose |
|------------|---------|
| *(none)* | Monitor mode (cron): read state, fetch prices, update tiers, sync SL, close on breach |
| `add-dsl <preset>` | Create state for a new position (entry/size/wallet from clearinghouse) |
| `update-dsl` | Merge config into existing position(s) |
| `pause-dsl` | Set `active: false` for position(s) |
| `resume-dsl` | Set `active: true` for position(s) |
| `delete-dsl` | Remove state file(s) |
| `status-dsl` | Print state JSON (read-only) |

## Global flags (all subcommands except monitor)

| Flag | Default | Description |
|------|---------|-------------|
| `--state-dir` | `$DSL_STATE_DIR` or `/data/workspace/dsl` | State directory root |
| `--strategy-id` | `$DSL_STRATEGY_ID` | Strategy ID (required if env not set) |

## add-dsl

```text
python3 scripts/dsl-v5.py add-dsl <preset> --strategy-id ID --asset ASSET --direction LONG|SHORT --leverage N --margin N [--dex DEX] [--config '{}']
```

| Flag | Required | Description |
|------|----------|-------------|
| `preset` | Yes (positional) | Preset name (e.g. `default`, `dsl-tight`) |
| `--asset` | Yes | Ticker: main → `ETH`; xyz → `xyz:SILVER` or `SILVER` with `--dex xyz` |
| `--dex` | No | DEX when asset has no prefix: `''`/`main` = main, `xyz` = xyz. Inferred from `xyz:` prefix if omitted. |
| `--direction` | Yes | `LONG` or `SHORT` |
| `--leverage` | Yes | Positive number |
| `--margin` | Yes | Position margin (collateral) in quote units; used for ROE. Must be > 0. |
| `--config` | No | JSON object to override defaults. See [The `--config` JSON](#the---config-json) below. |

Entry, size, and wallet are resolved from clearinghouse; position must already exist.

## update-dsl

```text
python3 scripts/dsl-v5.py update-dsl --config '<json>' [--asset ASSET] [--dex DEX]
```

| Flag | Required | Description |
|------|----------|-------------|
| `--config` | Yes | JSON to merge into state. Same keys and merge rules as add-dsl. |
| `--asset` | No | Limit to one position (e.g. `ETH`, `xyz:SILVER`, or `SILVER` with `--dex xyz`). Omit = all positions in strategy. |
| `--dex` | No | DEX when asset has no prefix. |

## pause-dsl / resume-dsl

```text
python3 scripts/dsl-v5.py pause-dsl  [--asset ASSET] [--dex DEX]
python3 scripts/dsl-v5.py resume-dsl [--asset ASSET] [--dex DEX]
```

Omit `--asset` to pause/resume all positions in the strategy. Use `--asset` and optionally `--dex` for one position.

## delete-dsl

```text
python3 scripts/dsl-v5.py delete-dsl [--asset ASSET] [--dex DEX]
```

Omit `--asset` to delete all state files for the strategy. Use `--asset` and optionally `--dex` for one position.

## status-dsl

```text
python3 scripts/dsl-v5.py status-dsl [--asset ASSET] [--dex DEX]
```

Prints state JSON (pretty if single `--asset`, ndjson if all). Use `--asset` and optionally `--dex` to target one position.

## Asset and DEX

- **Main dex:** `--asset ETH` (no `--dex` or `--dex main`/`''`). State file: `ETH.json`.
- **XYZ via prefix:** `--asset xyz:SILVER`. State file: `xyz--SILVER.json`. DEX inferred.
- **XYZ via flag:** `--asset SILVER --dex xyz`. Canonical asset becomes `xyz:SILVER`; state file `xyz--SILVER.json`.

Same `--asset` / `--dex` rules apply for update-dsl, pause-dsl, resume-dsl, delete-dsl, status-dsl when targeting one position.

---

## The `--config` JSON

Used by **add-dsl** (optional) and **update-dsl** (required). Single JSON object. Only the keys below are accepted; others are ignored. Include only keys you want to override; omitted keys keep preset/default (add-dsl) or existing state (update-dsl).

### Allowed top-level keys

| Key | Type | Description |
|-----|------|-------------|
| `phase1` | object | Phase 1 overrides. Sub-keys: `retraceThreshold`, `consecutiveBreachesRequired`, `absoluteFloor`. Deep-merged. |
| `phase2` | object | Phase 2 overrides. Sub-keys: `retraceThreshold`, `consecutiveBreachesRequired`. Deep-merged. |
| `phase2TriggerTier` | int | Tier index (0-based) where Phase 2 starts. `>= 0`, `< len(tiers)`. |
| `tiers` | array | Tier list. **Full replacement** (not merged). Each tier: `triggerPct`, `lockPct`; optional `retrace`, `breachesRequired`. |
| `breachDecay` | string | `"hard"` or `"soft"`. |
| `closeRetries` | int | Close attempts before `pendingClose`. 1–20. |
| `closeRetryDelaySec` | number | Seconds between retries. 0–60. |
| `maxFetchFailures` | int | Consecutive price failures before deactivate. 1–100. |

### phase1 object (when using `phase1` in --config)

| Key | Type | Notes |
|-----|------|-------|
| `retraceThreshold` | number | ROE fraction (e.g. 0.03 = 3%). > 0, ≤ 0.5. |
| `consecutiveBreachesRequired` | int | 1–20. |
| `absoluteFloor` | number | Optional at add-dsl (auto from entry + retrace/leverage). LONG: < entry; SHORT: > entry. |

### phase2 object (when using `phase2` in --config)

| Key | Type | Notes |
|-----|------|-------|
| `retraceThreshold` | number | ROE fraction. > 0, ≤ 0.5. |
| `consecutiveBreachesRequired` | int | 1–20. |

### Tier object (each element of `tiers`)

| Key | Required | Notes |
|-----|----------|-------|
| `triggerPct` | Yes | ROE % for tier (e.g. 10 = 10% ROE). ≥ 0. Order ascending. |
| `lockPct` | Yes | % of entry→HW range to lock. 0–100. |
| `retrace` | No | Per-tier retrace (ROE fraction). |
| `breachesRequired` | No | Breaches to close at this tier. 1–20. |

### Merge behavior

- **add-dsl:** Start from preset/core defaults. For objects (`phase1`, `phase2`): **deep-merge**. For scalars: replace. For `tiers`: **replace** entire array.
- **update-dsl:** Same rules applied to **existing state**. Runtime fields (highWaterPrice, currentTierIndex, etc.) are never changed by `--config`.

### Example: add-dsl with config

```bash
python3 scripts/dsl-v5.py add-dsl dsl-tight --strategy-id strat-1 --asset HYPE --direction LONG --leverage 10 --margin 500 \
  --config '{
    "phase1": {"retraceThreshold": 0.05, "consecutiveBreachesRequired": 3},
    "phase2": {"retraceThreshold": 0.02, "consecutiveBreachesRequired": 2},
    "tiers": [
      {"triggerPct": 10, "lockPct": 50, "retrace": 0.015, "breachesRequired": 3},
      {"triggerPct": 20, "lockPct": 65, "retrace": 0.012, "breachesRequired": 2},
      {"triggerPct": 40, "lockPct": 75, "retrace": 0.010, "breachesRequired": 2},
      {"triggerPct": 75, "lockPct": 85, "retrace": 0.006, "breachesRequired": 1}
    ]
  }'
```

### Example: update-dsl (partial)

```bash
python3 scripts/dsl-v5.py update-dsl --strategy-id strat-1 --asset HYPE \
  --config '{"phase2": {"retraceThreshold": 0.01}}'
```

For more examples (minimal add-dsl, xyz, pause, status, delete, cron), see [examples.md](examples.md). For state file schema, see [state-schema.md](state-schema.md).
