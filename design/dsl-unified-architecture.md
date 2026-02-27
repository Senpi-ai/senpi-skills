# DSL — Unified Plugin Architecture

**Status:** Design v2 — Industry-Standard Plugin
**Date:** 2026-02-27
**Scope:** `dsl` (renamed from `dsl-dynamic-stop-loss`), `wolf-strategy`, `dsl-tight`

---

## 1. Vision

DSL is a **first-class skill plugin** — self-contained, independently installable, and composable.
It owns all trailing-stop logic. Other skills declare it as a peer dependency and interact via a
stable contract (config schema, state schema, output schema, events). One codebase. One contract.
No duplicated engines.

---

## 2. Industry Patterns Applied

| Pattern | Origin | Applied As |
|---------|--------|-----------|
| SKILL.md as agent contract | Agent SDK — native skill format | `SKILL.md` frontmatter: `name` + `description` trigger |
| Progressive disclosure (3-level) | Agent SDK — official loading architecture | Frontmatter (~100t) → body (<5k t) → `references/` (on demand) |
| Plugin-based bundling + deps | Agent SDK Plugins — plugin manifest | `skill.json peerDependencies` as Senpi-platform extension on top |
| Semantic versioning | semver.org, PyPI `packaging` library | `"version": "5.0.0"` + `require_skill()` semver check |
| Layered config | `pydantic-settings`, 12-Factor App | defaults → file → strategy.json → env vars → CLI args |
| Schema validation | `pydantic`, `jsonschema` Python lib | `schema/config.v1.json`, `state.v3.json`, `strategy.v1.json` |
| Single-writer ownership | `threading.Lock`, Kubernetes Operator | DSL owns `runtime`; producers own `config` |
| Hierarchical resource org | Python `pathlib`, directory namespacing | `strategy.json` (parent) owns `dsl-*.json` (children) |
| Agent lifecycle hooks | Agent SDK hooks (`PreToolUse`, `PostToolUse`, etc.) | install → configure → run → stop |
| Event sourcing | Python `open("a")` append, CQRS | append-only `events/dsl/{strategyKey}.jsonl`; `EventReader` |
| Retention policy | Python `pathlib` sweep, `datetime` | configurable TTL, maxHistory, cleanupOnClose — per strategy |
| Model selection | Agent SDK `model` option | `model.primary` / `model.fallback` in skill.json, overridable per strategy |

---

## 3. Problem Audit (Current State)

### 3.1 Three-Engine Divergence (Critical)

DSL logic lives in 3 separate files. Bug fixes applied to one silently miss the others.

| File | Lines | Version |
|------|-------|---------|
| `dsl-dynamic-stop-loss/scripts/dsl-v4.py` | 243 | v4 |
| `wolf-strategy/scripts/dsl-v4.py` | ~351 | v4.1 |
| `wolf-strategy/scripts/dsl-combined.py` | 458 | v4.1+ |

**Formula bug in base `dsl-v4.py`:**

```python
# WRONG — conflates ROE % with price %
tier_floor = round(entry * (1 + tier["lockPct"] / 100 / state["leverage"]), 4)

# CORRECT (wolf version) — floor = entry + fraction of the entry→highwater range
tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)
```

At entry=$28.87, hw=$32.00, lockPct=50%, leverage=10:
- Wrong: $30.31 (14% of gain locked)
- Correct: $30.44 (50% of actual gain locked)

### 3.2 Features Present in Wolf but Missing from Base

| Feature | Base dsl-v4 | Wolf dsl-v4.1 | dsl-combined |
|---------|-------------|---------------|--------------|
| Stagnation take-profit | ✗ | ✓ | ✓ |
| Auto-fix `absoluteFloor` | ✗ | ✓ | ✓ |
| `hwTimestamp` tracking | ✗ | ✓ | ✓ |
| Phase 1 auto-cut (90 min) | ✗ | ✗ | ✓ |
| `peakROE` tracking | ✗ | ✗ | ✓ |
| Atomic write | ✓ (dsl_common) | ✗ | ✓ |
| `CLOSE_NO_POSITION` recovery | ✓ | ✓ | ✓ |

### 3.3 No Configuration System

No layered config. No JSON Schema contract. Behaviour is controlled only by env variables
(`DSL_STATE_FILE`) and hardcoded defaults. Changing cron frequency, model, or retention requires
editing SKILL.md or Python source.

### 3.4 No Skill Manifest

No `skill.json` anywhere in the repo. Wolf imports DSL via filesystem convention with no version
checking. If DSL is missing or outdated, the failure is a cryptic Python import error.

### 3.5 No State Retention Policies

Closed positions accumulate forever. No TTL, no per-position history limit, no cleanup on close.
Users must manually delete old state files.

### 3.6 Token Cost

`dsl-dynamic-stop-loss/SKILL.md` is ~2,600 tokens. With 3 positions × every 3 min × 8 hours the
agent loads this SKILL.md ~480 times/day. At Sonnet pricing (~$3/MTok): **~$4/day** for heartbeat
checks alone.

---

## 4. Skill Manifest (`skill.json`)

**SDK-native manifest:** The Agent SDK recognises YAML frontmatter inside `SKILL.md`
as the skill's identity (`name`, `description`). The frontmatter is the only manifest the SDK
reads natively.

**Senpi platform extension:** `skill.json` sits alongside `SKILL.md` and carries additional
metadata the SDK frontmatter does not support: semantic version, peer skill requirements, config
schema, and what this skill exposes to other skills (engine path, presets path, etc.). The
Senpi host reads `skill.json` at install time. The agent never reads it — it reads
`SKILL.md`.

Both files are required. They are complementary, not redundant:

| File | Read by | Purpose |
|------|---------|---------|
| `SKILL.md` frontmatter | Agent (SDK) | Skill discovery and invocation trigger |
| `SKILL.md` body | Agent (SDK) | Instructions for how to run the skill |
| `skill.json` | Senpi host / tooling | Version, peer deps, config schema, provides |

### 4.1 `dsl/skill.json`

```json
{
  "name": "dsl",
  "version": "5.0.0",
  "description": "Dynamic stop-loss engine for Hyperliquid perpetual positions",
  "author": "senpi",
  "license": "Apache-2.0",
  "main": "scripts/dsl.py",

  "scripts": {
    "run":     "scripts/dsl.py",
    "init":    "scripts/dsl-init.py",
    "migrate": "scripts/dsl-migrate.py"
  },

  "provides": {
    "engine":       "scripts/dsl_engine.py",
    "presets":      "scripts/dsl_presets.py",
    "config":       "scripts/dsl_config.py",
    "stateSchema":  "schema/state.v3.json",
    "eventSchema":  "schema/event.v1.json",
    "configSchema": "schema/config.v1.json"
  },

  "peerDependencies": {},

  "state": {
    "owns": "state/dsl",
    "namespaced": true,
    "events":  "events/dsl"
  },

  "defaultConfig": {
    "model": {
      "primary":      "<primary-model-id>",
      "fallback":     "<fallback-model-id>",
      "allowOverride": true
    },
    "cron": {
      "intervalSeconds":  180,
      "mode":             "multi",
      "maxConcurrent":    5,
      "pauseOnError":     false,
      "backoffSeconds":   30,
      "maxRetries":       3
    },
    "state": {
      "retentionDays":          30,
      "maxHistoryPerPosition":  0,
      "cleanupOnClose":         false,
      "persistInactive":        true,
      "namespace":              "default"
    },
    "execution": {
      "mode":        "multi",
      "outputLevel": "full",
      "priceSource": "auto"
    }
  }
}
```

### 4.2 `wolf-strategy/skill.json`

```json
{
  "name": "wolf-strategy",
  "version": "3.0.0",
  "description": "Multi-strategy autonomous trading for Hyperliquid",

  "peerDependencies": {
    "dsl": ">=5.0.0"
  },

  "skillConfig": {
    "dsl": {
      "cron":  { "intervalSeconds": 60, "mode": "strategy" },
      "state": { "namespace": "wolf", "cleanupOnClose": false }
    }
  }
}
```

### 4.3 `dsl-tight/skill.json`

```json
{
  "name": "dsl-tight",
  "version": "2.0.0",
  "description": "Opinionated tight preset for dsl — no engine, config only",

  "peerDependencies": {
    "dsl": ">=5.0.0"
  },

  "skillConfig": {
    "dsl": {
      "execution": { "defaultPreset": "tight" }
    }
  }
}
```

---

## 5. Configuration System (Layered)

Configuration is resolved in priority order (lowest → highest):

```
1. skill.json defaultConfig                              — packaged defaults
2. /data/workspace/config/dsl.json                       — user install config
3. state/dsl/{strategyKey}/strategy.json  "config" block — per-strategy overrides  ← NEW
4. Environment variables DSL_*                           — deployment / container overrides
5. CLI arguments --config-*                              — per-run overrides
```

Layer 3 is what makes strategy the primary organizational unit: each strategy carries its own
config overrides (model, cron interval, retention, max positions) that apply only to the positions
within that strategy. Two strategies running simultaneously can use different models and different
cron frequencies without touching the global config file.

### 5.1 User Config File (`/data/workspace/config/dsl.json`)

```json
{
  "model": {
    "primary": "<model-id>"
  },
  "cron": {
    "intervalSeconds": 60
  },
  "state": {
    "retentionDays": 7,
    "cleanupOnClose": true
  }
}
```

### 5.2 Environment Variables

| Variable | Type | Description |
|----------|------|-------------|
| `DSL_MODEL` | string | Model ID — overrides `model.primary` |
| `DSL_CRON_INTERVAL` | int | Cron interval in seconds |
| `DSL_CRON_MODE` | string | `single` \| `strategy` \| `multi` |
| `DSL_OUTPUT_LEVEL` | string | `full` \| `minimal` \| `silent` |
| `DSL_NAMESPACE` | string | Active state namespace |
| `DSL_STATE_FILE` | string | Single state file path (single mode) |
| `DSL_REGISTRY` | string | Registry JSON path (strategy/multi mode) |
| `DSL_STATE_DIR` | string | Glob root for state files |
| `DSL_RETENTION_DAYS` | int | State retention in days |
| `DSL_MAX_HISTORY` | int | Max history snapshots per position |

### 5.3 Config Schema (`schema/config.v1.json`)

Full JSON Schema (types, ranges, descriptions, defaults) for every config key. Loaded and
validated by `dsl_config.py` at skill startup. Invalid config raises a human-readable error
before any cron logic runs.

---

## 6. Plugin / Dependency System

### 6.1 Declaring a Dependency

A consuming skill adds `peerDependencies` to its `skill.json` — the same concept as
`[project.optional-dependencies]` in Python's `pyproject.toml`:

```json
{ "peerDependencies": { "dsl": ">=5.0.0" } }
```

At **install time** the Senpi host:
1. Checks if `dsl` is installed (sibling directory with a `skill.json`).
2. Checks semver compatibility using `packaging.version.Version`.
3. If missing: prompts "wolf-strategy requires dsl ≥5.0.0. Install it? [Y/n]".
4. If version mismatch: prompts to upgrade.

### 6.2 Runtime Import (`dsl_skill.py`)

```python
# wolf-strategy/scripts/wolf_config.py
from dsl_skill import require_skill

dsl = require_skill("dsl", min_version="5.0.0")
# If dsl is missing or outdated → ImportError with actionable message:
# "Peer skill 'dsl' not found. Install with: senpi skills install dsl"
```

```python
# dsl/scripts/dsl_skill.py
import os, sys, importlib, json

def require_skill(name: str, min_version: str = None):
    skills_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    skill_dir   = os.path.join(skills_root, name)
    scripts_dir = os.path.join(skill_dir, "scripts")

    if not os.path.isdir(scripts_dir):
        raise ImportError(
            f"Peer skill '{name}' not found at {scripts_dir}.\n"
            f"Install with: senpi skills install {name}"
        )

    if min_version:
        manifest_path = os.path.join(skill_dir, "skill.json")
        with open(manifest_path) as f:
            version = json.load(f).get("version", "0.0.0")
        if not _semver_gte(version, min_version):
            raise ImportError(
                f"Skill '{name}' v{version} < required v{min_version}.\n"
                f"Upgrade with: senpi skills upgrade {name}"
            )

    sys.path.insert(0, scripts_dir)
    return importlib.import_module("dsl_engine")
```

### 6.3 Plugin Lifecycle Hooks

```
install → validate_config → start_cron → [run* → emit_events*] → stop_cron → uninstall
```

| Hook | When | What Happens |
|------|------|--------------|
| `on_install` | First install | Validate schema, create dirs, run `dsl-migrate.py` |
| `on_configure` | Config file change | Reload + validate config, log changed keys |
| `on_cron_start` | Agent starts cron | Resolve config, discover state files |
| `on_run` | Each cron tick | Fetch prices, evaluate all positions, write state |
| `on_position_event` | Close / tier change | Emit event to event log |
| `on_error` | Any exception | Log, backoff, optionally pause cron |
| `on_stop` | Agent stops skill | Flush events, flush in-flight state writes |
| `on_uninstall` | Skill removed | Archive or delete state per retention policy |

---

## 7. Execution Modes (Cron Modes)

**Strategy is the primary and recommended execution unit.** A position never runs in isolation
in production — it always belongs to a strategy. `single` mode exists for development and
debugging only.

| Mode | Primary Use | Discovery |
|------|-------------|-----------|
| `single` | Dev / debug only | `DSL_STATE_FILE` env var — one state file |
| `strategy` | **Default** — one strategy, all its positions | `DSL_NAMESPACE` → load `strategy.json`, glob positions |
| `multi` | All enabled strategies | `DSL_REGISTRY` → iterate strategies, run each |

The cron entry `dsl.py` resolves mode from config and dispatches accordingly. The engine
(`dsl_engine.py`) is mode-agnostic — it processes one position at a time. `dsl.py` orchestrates
strategy discovery, batch price fetch, parallelism (up to `strategy.config.maxConcurrent`),
strategy-level aggregation, and JSON output.

### 7.1 Cron Entry Point

```bash
# Strategy mode (recommended) — load strategy.json, run all its positions
python scripts/dsl.py --strategy wolf-abc123

# Multi mode — run all enabled strategies from registry
python scripts/dsl.py --mode multi --registry /data/workspace/wolf-strategies.json

# Single mode (dev/debug only)
DSL_STATE_FILE=/data/workspace/state/dsl/default/dsl-HYPE.json python scripts/dsl.py
```

### 7.2 Strategy-First Discovery

When running in `strategy` mode, `dsl.py` follows this resolution order:

```
1. Load  state/dsl/{strategyKey}/strategy.json  (descriptor + config overrides)
2. Apply strategy config on top of global config
3. Glob  state/dsl/{strategyKey}/dsl-*.json     (all position state files)
4. Batch-fetch prices for all assets in one call
5. Run   process_position() for each position
6. Aggregate results into strategy-level output
7. Emit  strategy-level events if any positions closed/upgraded
8. Write strategy runtime state (slot count, last run, aggregate ROE)
```

### 7.3 Error Handling and Backoff

- Per-position consecutive fetch failures → set `fetchFailed=true` in state, skip on next tick, still output FETCH_FAILED.
- Strategy-level: if all positions in a strategy fail, emit `strategy.cron_failed`.
- cron-level: `maxRetries` consecutive full-cron failures → emit `cron.paused` event, pause until next agent cycle.
- Backoff: linear `backoffSeconds`, capped at `maxBackoffSeconds` (default 300s).

---

## 8. State Management

### 8.1 Strategy as the Primary Organizational Unit

A strategy is not just a namespace label. It is a first-class resource with its own descriptor
file, config block, runtime state, and slot budget. Every position belongs to exactly one
strategy. DSL never operates on a "global" pool of positions — it operates on strategies.

```
state/dsl/
  {strategyKey}/
    strategy.json          ← strategy descriptor + config overrides + runtime aggregate
    dsl-{ASSET}.json       ← position state (owned by DSL engine)
    dsl-{ASSET2}.json
    history/               ← position snapshots (optional)
      dsl-{ASSET}-{ts}.json
```

### 8.2 Strategy Descriptor (`strategy.json`)

Written by the producing skill (e.g. wolf-setup.py) when the strategy is first created.
The `config` block is immutable after creation. The `runtime` block is updated by DSL at the
end of each strategy-mode cron run.

```json
{
  "strategyKey":  "wolf-abc123",
  "displayName":  "Wolf Strategy A",
  "schemaVersion": 1,
  "owner": {
    "skill": "wolf-strategy",
    "ref":   "abc123"
  },
  "active":    true,
  "createdAt": "2026-01-01T00:00:00Z",

  "config": {
    "maxPositions":  3,
    "model": {
      "primary":  "<model-id>"
    },
    "cron": {
      "intervalSeconds": 60
    },
    "state": {
      "cleanupOnClose":  false,
      "retentionDays":   14,
      "maxHistoryPerPosition": 50
    },
    "execution": {
      "preset":      "wolf",
      "outputLevel": "minimal"
    }
  },

  "runtime": {
    "activePositions":    2,
    "slotsAvailable":     1,
    "totalUnrealizedROE": 8.4,
    "lastRunAt":          "2026-01-15T12:00:00Z",
    "lastRunStatus":      "HEARTBEAT_OK",
    "consecutiveErrors":  0
  }
}
```

**Config inheritance for this strategy:**
```
skill.json defaultConfig
  → config/dsl.json
    → strategy.json "config" block        ← per-strategy overrides applied here
      → DSL_* env vars
        → CLI args
```

Each strategy is effectively its own isolated DSL instance with its own model, cron frequency,
retention policy, and slot limit — without running a separate process.

### 8.3 State Scope Summary

| Scope | Files |
|-------|-------|
| Single position (dev) | `state/dsl/{strategyKey}/dsl-{ASSET}.json` |
| Strategy | `state/dsl/{strategyKey}/strategy.json` + `dsl-*.json` |
| All strategies | `state/dsl/*/strategy.json` + `dsl-*.json` |

### 8.4 Position State Schema v3 (Canonical)

Split into immutable `config` (written at open, never touched by engine) and mutable `runtime`
(mutated exclusively by DSL engine). `meta` tracks ownership and schema version.

```json
{
  "meta": {
    "schemaVersion":  3,
    "namespace":      "wolf-abc123",
    "owner": {
      "skill": "wolf-strategy",
      "ref":   "strategy-abc123"
    },
    "createdAt":  "2026-01-01T00:00:00Z",
    "updatedAt":  "2026-01-15T12:00:00Z"
  },

  "config": {
    "asset":       "HYPE",
    "direction":   "long",
    "entryPrice":  28.87,
    "size":        100,
    "leverage":    10,
    "wallet":      "0xABC...",
    "dex":         "hyperliquid",
    "strategyKey": "wolf-abc123",
    "phase1": {
      "retracePercent":    3.0,
      "breachesRequired":  3,
      "absoluteFloor":     27.50,
      "autocut": {
        "maxMinutes":       90,
        "weakPeakMinutes":  45,
        "weakPeakROE":      3.0
      }
    },
    "phase2": {
      "retracePercent":    1.5,
      "breachesRequired":  1
    },
    "tiers": [
      { "roePct": 5,  "lockPct": 40, "breachesRequired": 3 },
      { "roePct": 10, "lockPct": 60, "breachesRequired": 2 },
      { "roePct": 20, "lockPct": 75, "breachesRequired": 1 }
    ],
    "stagnation": {
      "minROE":      5.0,
      "staleHours":  4.0
    }
  },

  "runtime": {
    "phase":                    1,
    "active":                   true,
    "pendingClose":             false,
    "highWaterPrice":           32.00,
    "hwTimestamp":              "2026-01-15T10:00:00Z",
    "currentTierIndex":         1,
    "tierFloorPrice":           30.44,
    "floorPrice":               30.44,
    "absoluteFloor":            27.50,
    "currentBreachCount":       0,
    "peakROE":                  10.8,
    "consecutiveFetchFailures": 0
  }
}
```

**Principle:** `config` is written once by the producer (wolf-setup.py, dsl-init.py, etc.).
`runtime` is mutated only by the DSL engine. A partial write cannot corrupt position config.

### 8.5 Slot Management

Each strategy has a `maxPositions` limit in its `strategy.json config` block. DSL enforces it:

- Before a producer creates a new position state file, it checks `strategy.runtime.slotsAvailable`.
- If `slotsAvailable == 0`, the producer should not open a new position. DSL will skip state files
  beyond `maxPositions` and emit a `strategy.slots_exceeded` warning.
- When a position closes, DSL decrements `runtime.activePositions` and increments
  `runtime.slotsAvailable`, then emits `strategy.slot_freed` so the consuming skill can react
  immediately (e.g. look for a new entry).

This replaces the ad-hoc slot tracking wolf currently does by counting strategy state files.

### 8.6 Retention Policies

Retention is configured globally in `config/dsl.json` or overridden per strategy in
`strategy.json config.state`:

```json
{
  "state": {
    "retentionDays":          30,
    "maxHistoryPerPosition":  100,
    "cleanupOnClose":         false,
    "persistInactive":        true
  }
}
```

| Policy | Description |
|--------|-------------|
| `retentionDays` | Prune closed positions older than N days (0 = keep forever) |
| `maxHistoryPerPosition` | Keep at most N history snapshots per position (0 = no snapshots) |
| `cleanupOnClose` | Delete state file immediately when `active` transitions to `false` |
| `persistInactive` | When false, delete inactive states on next run (overrides retentionDays) |

Strategy A can keep positions for 30 days; Strategy B can delete on close — configured in each
`strategy.json`, not by environment variables.

Cleanup runs at skill startup and once per day via a lightweight sweep. DSL only deletes state
files within strategy directories it owns. It never touches `strategy.json` itself — that is
owned by the producing skill.

### 8.7 History Snapshots (Optional)

When `maxHistoryPerPosition > 0`, DSL saves a snapshot before each state write:
```
state/dsl/{strategyKey}/history/dsl-{ASSET}-{timestamp}.json
```
Rotates automatically when count exceeds `maxHistoryPerPosition`. Useful for auditing trades
and debugging tier logic per strategy.

### 8.8 State Migration (`dsl-migrate.py`)

Existing flat state files (v1/v2) are auto-migrated to v3 on first load:
- Flat field `entryPrice` → `config.entryPrice`
- Flat field `highWaterPrice` → `runtime.highWaterPrice`
- `meta` block added with `namespace=default`, `owner.skill=dsl`, `schemaVersion=3`

Run manually: `python scripts/dsl-migrate.py --state-dir /data/workspace/state`

---

## 9. Model Selection

Every skill can specify which AI model the agent should use when interpreting cron output.
DSL is a Python engine (no LLM calls), but the *agent reading SKILL.md* uses a model.

```json
{
  "model": {
    "primary":       "<primary-model-id>",
    "fallback":      "<fallback-model-id>",
    "allowOverride": true
  }
}
```

| Field | Description |
|-------|-------------|
| `primary` | Default model for cron runs |
| `fallback` | Used when primary is unavailable or at capacity |
| `allowOverride` | Allow `DSL_MODEL` env var to override at run time |

A consuming skill overrides in its `skillConfig.dsl.model` block:

```json
{
  "skillConfig": {
    "dsl": {
      "model": { "primary": "<model-id>" }
    }
  }
}
```

---

## 10. Event System

DSL emits typed, append-only events. Consumers read by namespace using a checkpointed reader.
No polling of DSL JSON output for cross-skill communication.

### 10.1 Event Log Location

```
/data/workspace/events/dsl/{namespace}.jsonl
```

One JSON object per line. Never mutated — only appended.

### 10.2 Event Types

Events are namespaced by `strategyKey`. Each strategy gets its own event log file:
`events/dsl/{strategyKey}.jsonl`

**Position-level events:**

| Event | Trigger | Key Payload Fields |
|-------|---------|-------------------|
| `position.opened` | First active processing | asset, entry, leverage, direction, phase |
| `position.tier_upgraded` | tier_idx increases | asset, tier, floor, roe |
| `position.breached` | breach_count++ | asset, breach_count, price, floor |
| `position.closed` | active=false | asset, reason, roe, upnl_pct, phase, tier |
| `position.pending_close` | close API failed | asset, error |
| `position.stagnation_tp` | Stagnation TP fired | asset, roe, stale_hours |
| `position.phase1_autocut` | 90-min or weak-peak cut | asset, reason, elapsed_min |
| `position.fetch_failed` | Price fetch error | asset, consecutive_failures |

**Strategy-level events:**

| Event | Trigger | Key Payload Fields |
|-------|---------|-------------------|
| `strategy.slot_freed` | Position closed, slot now available | strategyKey, asset, slots_available, slots_total |
| `strategy.slots_full` | activePositions == maxPositions | strategyKey, active_positions, max_positions |
| `strategy.slots_exceeded` | State files > maxPositions | strategyKey, found, max_positions |
| `strategy.all_closed` | All positions in strategy closed | strategyKey, position_count, avg_roe |
| `strategy.cron_failed` | All positions in one run errored | strategyKey, error_count |

**System events:**

| Event | Trigger | Key Payload Fields |
|-------|---------|-------------------|
| `cron.paused` | maxRetries hit | reason, next_retry_at |
| `cron.resumed` | Cron resumes after pause | — |
| `config.changed` | Config reloaded | changed_keys |

### 10.3 Event Format

```json
{
  "v":         1,
  "event":     "position.closed",
  "ts":        "2026-01-15T12:00:00Z",
  "source":    "dsl",
  "namespace": "wolf-abc123",
  "payload": {
    "asset":      "HYPE",
    "direction":  "long",
    "reason":     "breach_limit",
    "roe":        -2.1,
    "upnl_pct":   -2.1,
    "phase":      2,
    "tier":       1
  }
}
```

### 10.4 EventReader (for consuming skills)

```python
from dsl_engine import EventReader

reader = EventReader(namespace="wolf-abc123")
for event in reader.read_new():           # reads from last checkpoint
    if event["event"] == "position.closed":
        handle_close(event["payload"])
    elif event["event"] == "position.tier_upgraded":
        log_locked_profit(event["payload"])
reader.save_checkpoint()
```

---

## 11. Canonical Engine (`dsl_engine.py`)

Single importable module. All DSL logic lives here. Every other script — cron entry, wolf,
dsl-tight — imports from this module.

### 11.1 Public API

```python
# ── Position processing ──────────────────────────────────────────────────────
def process_position(state: dict, current_price: float, cfg: Config) -> ProcessResult
def validate_state(state: dict) -> ValidationResult
def migrate_state(state: dict) -> dict                     # flat v1/v2 → v3

# ── Position state I/O ───────────────────────────────────────────────────────
def load_state(path: str) -> dict
def save_state(path: str, state: dict) -> None             # atomic write via .tmp

# ── Strategy descriptor I/O ──────────────────────────────────────────────────
def load_strategy(strategy_key: str, base: str = DATA_DIR) -> dict
def save_strategy(strategy_key: str, descriptor: dict, base: str = DATA_DIR) -> None
def create_strategy(strategy_key: str, owner_skill: str, owner_ref: str,
                    config: dict, base: str = DATA_DIR) -> dict
def list_strategies(base: str = DATA_DIR) -> list[str]     # all strategy keys with strategy.json
def get_strategy_summary(strategy_key: str, base: str = DATA_DIR) -> StrategySummary
    # returns: active_positions, slots_available, total_unrealized_roe, last_run_at

# ── Slot management ──────────────────────────────────────────────────────────
def strategy_has_slot(strategy_key: str, base: str = DATA_DIR) -> bool
def strategy_slot_count(strategy_key: str, base: str = DATA_DIR) -> tuple[int, int]
    # returns: (active_positions, max_positions)

# ── Path helpers (importable by wolf) ────────────────────────────────────────
def dsl_state_path(strategy_key: str, asset: str, base: str = DATA_DIR) -> str
def dsl_state_glob(strategy_key: str, base: str = DATA_DIR) -> list[str]
def strategy_dir(strategy_key: str, base: str = DATA_DIR) -> str
def all_dsl_states(base: str = DATA_DIR) -> dict[str, list[str]]
    # returns: { strategyKey: [path, ...], ... }

# ── Config resolution ────────────────────────────────────────────────────────
def resolve_config(strategy_key: str = None) -> Config
    # applies: defaults → config/dsl.json → strategy.json config → env vars → cli

# ── Events ───────────────────────────────────────────────────────────────────
def emit_event(strategy_key: str, event: str, payload: dict) -> None
class EventReader:
    def __init__(self, strategy_key: str, base: str = DATA_DIR): ...
    def read_new(self) -> list[dict]: ...
    def save_checkpoint(self) -> None: ...
```

### 11.2 Corrected Formulas (Unified)

```python
# High-water tier floor — CORRECT: floor locks N% of the entry→highwater range
tier_floor = round(entry + (hw - entry) * tier["lockPct"] / 100, 4)

# ROE retrace to price
retrace_price = round(hw - (hw - entry) * tier["retrace"] / 100, 4)

# ROE calculation
roe_pct = (price - entry) / entry * leverage * 100  # long
roe_pct = (entry - price) / entry * leverage * 100  # short
```

### 11.3 Features Unified from All Three Engines

| Feature | Source |
|---------|--------|
| Phase 1 / Phase 2 trailing logic | dsl-v4 base |
| Stagnation take-profit | wolf dsl-combined |
| Phase 1 auto-cut (90 min / 45 min weak-peak) | wolf dsl-combined |
| `peakROE` tracking | wolf dsl-combined |
| `hwTimestamp` tracking | wolf dsl-v4.1 |
| `absoluteFloor` auto-fix | wolf dsl-v4.1 |
| Atomic write | dsl_common (base) |
| `CLOSE_NO_POSITION` recovery | all three |
| Correct `lockPct` formula | wolf (fix applied) |
| Batch price fetch (`market_get_prices`) | wolf dsl-combined |

---

## 12. Preset System

All presets in `dsl/scripts/dsl_presets.py`. Unified field names everywhere.

```python
from dsl_presets import generate_state_file, PRESETS

state = generate_state_file(
    preset="wolf",
    asset="HYPE",
    entry=28.87,
    size=100,
    leverage=10,
    direction="long",
    wallet="0xABC...",
    namespace="wolf-abc123",
    owner_skill="wolf-strategy",
    owner_ref="strategy-abc123",
)
```

| Preset | Use Case |
|--------|----------|
| `conservative` | Wide floors, more breaches allowed |
| `moderate` | Balanced (default when no preset specified) |
| `aggressive` | Tight floors, fast lock |
| `tight` | dsl-tight opinionated preset |
| `wolf` | Wolf strategy defaults |

Unified field names across all presets and state files: `breachesRequired`, `minROE`,
`staleHours`, `lockPct`, `retrace`.

---

## 13. Directory Structure

```
senpi-skills/
├── dsl/                               # Renamed from dsl-dynamic-stop-loss
│   ├── skill.json                     # Manifest — name, version, provides, config, peers
│   ├── SKILL.md                       # ~250 tokens — agent cron mandate + decision table
│   ├── scripts/
│   │   ├── dsl_engine.py              # Canonical engine v5 (importable by all skills)
│   │   ├── dsl_common.py              # State I/O, atomic write, MCP price, close (→ merged)
│   │   ├── dsl_presets.py             # All presets + generate_state_file()
│   │   ├── dsl_config.py              # Config loading, env resolution, validation
│   │   ├── dsl_skill.py               # require_skill(), semver check
│   │   ├── dsl.py                     # Thin cron entry (~40 lines)
│   │   ├── dsl-init.py                # Position state file generator CLI
│   │   ├── dsl-strategy-init.py       # Strategy descriptor generator CLI
│   │   └── dsl-migrate.py             # One-shot migration: flat v1/v2 → v3
│   ├── schema/
│   │   ├── config.v1.json             # JSON Schema — config contract
│   │   ├── state.v3.json              # JSON Schema — position state contract
│   │   ├── strategy.v1.json           # JSON Schema — strategy descriptor contract  ← NEW
│   │   └── event.v1.json              # JSON Schema — event contract
│   └── references/
│       ├── config-reference.md        # All config keys, types, defaults, examples
│       ├── state-schema.md            # v3 position state format with field descriptions
│       ├── strategy-schema.md         # strategy.json format, config overrides  ← NEW
│       ├── output-schema.md           # Output JSON per mode + minimal mode
│       └── presets.md                 # Presets, field names, tier examples

/data/workspace/                       # Runtime data (not in repo)
├── config/
│   └── dsl.json                       # Global user config overrides
├── state/
│   └── dsl/
│       └── {strategyKey}/
│           ├── strategy.json          # Strategy descriptor + config overrides + runtime agg
│           ├── dsl-{ASSET}.json       # Position state (owned by DSL engine)
│           └── history/
│               └── dsl-{ASSET}-{ts}.json
└── events/
    └── dsl/
        └── {strategyKey}.jsonl        # Append-only event log per strategy
│
├── dsl-tight/                         # Config-only — no engine scripts
│   ├── skill.json                     # peerDependencies: {dsl: ">=5.0.0"}, preset: "tight"
│   └── SKILL.md                       # ~100 tokens — run dsl-init.py --preset tight
│
├── wolf-strategy/
│   ├── skill.json                     # peerDependencies: {dsl: ">=5.0.0"}
│   ├── SKILL.md
│   └── scripts/
│       ├── wolf_config.py             # Imports dsl_engine path helpers + EventReader
│       ├── wolf-setup.py              # Imports generate_state_file from dsl_presets
│       ├── wolf-cron.py               # Runs dsl in multi mode; reads events
│       └── ...
│
└── design/
    └── dsl-unified-architecture.md    # This document
```

---

## 14. SKILL.md Token Design

### 14.1 Three-Level Loading (SDK Architecture)

The Agent SDK loads skill content in three stages. SKILL.md must be designed around this:

| Level | Content | When Loaded | Token Cost |
|-------|---------|-------------|-----------|
| **1 — Metadata** | YAML frontmatter (`name`, `description`) | Always, at agent startup | ~100 tokens |
| **2 — Instructions** | SKILL.md body (cron command, decision table, quick config) | When skill is triggered | < 5k tokens target |
| **3 — Resources** | Files in `references/`, scripts in `scripts/` | On demand via Bash | Effectively unlimited |

The agent uses Bash to read `references/` files only when needed. Scripts are *executed* via
Bash — only their output, not their source, enters context.

### 14.2 Token Budget (Level 2 — SKILL.md Body)

| Section | Target Tokens |
|---------|--------------|
| Cron command | ~50 |
| Decision table (status → agent action) | ~120 |
| Common config quick-ref | ~80 |
| Reference links | ~20 |
| **Total Level 2** | **~270 tokens** (vs current ~2,600) |

### 14.3 `dsl/SKILL.md` Template

```markdown
---
name: dsl
description: >
  Dynamic stop-loss engine for Hyperliquid leveraged positions.
  Use when running a trailing stop cron, checking a floor price,
  managing an open position, or processing a DSL heartbeat tick.
---

# DSL — Dynamic Stop-Loss Engine

**Cron:** every `${DSL_CRON_INTERVAL:-180}s`  **Model:** `${DSL_MODEL:-<primary-model-id>}`

## Run
```bash
python scripts/dsl.py
```

## Decision Table
| status          | agent action                             |
|-----------------|------------------------------------------|
| HEARTBEAT_OK    | nothing                                  |
| TIER_CHANGED    | log; optionally alert user               |
| CLOSED          | alert user; free strategy slot           |
| PENDING_CLOSE   | alert user; retry next tick              |
| FETCH_FAILED    | warn; continue (auto-retried)            |
| ERROR           | alert user; check logs                   |

## Common Config (`/data/workspace/config/dsl.json`)
- `cron.intervalSeconds` — run frequency (default 180)
- `model.primary` — model for this skill (default from skill.json)
- `state.cleanupOnClose` — delete state on close (default false)
- `state.retentionDays` — prune old closed states (default 30)

Full reference → `references/config-reference.md`
State schema  → `references/state-schema.md`
Presets       → `references/presets.md`
```

The frontmatter `description` is the Level 1 trigger — it must clearly state both *what* the
skill does and *when* to use it. The SDK loads only this ~100-token block at startup.

---

## 15. How Other Skills Use DSL

### 15.1 Wolf as Producer + Consumer

**Producer — strategy creation** (wolf-setup.py, once per strategy):
```python
from dsl_skill import require_skill
dsl = require_skill("dsl", min_version="5.0.0")
from dsl_engine import create_strategy

# Creates state/dsl/wolf-abc123/strategy.json
create_strategy(
    strategy_key="wolf-abc123",
    owner_skill="wolf-strategy",
    owner_ref="abc123",
    config={
        "maxPositions": 3,
        "model": { "primary": "<model-id>" },
        "cron": { "intervalSeconds": 60 },
        "state": { "cleanupOnClose": False, "retentionDays": 14 }
    }
)
```

**Producer — opening a position** (wolf-setup.py, per entry):
```python
from dsl_engine import strategy_has_slot, dsl_state_path
from dsl_presets import generate_state_file

if not strategy_has_slot("wolf-abc123"):
    raise RuntimeError("Strategy wolf-abc123 is at max positions")

state = generate_state_file(
    preset="wolf", asset="HYPE", entry=28.87,
    strategy_key="wolf-abc123", owner_skill="wolf-strategy"
)
# writes to state/dsl/wolf-abc123/dsl-HYPE.json
```

**Consumer** (wolf-cron.py — every cron tick):
```bash
python /path/to/dsl/scripts/dsl.py --strategy wolf-abc123
```
or consume events to react immediately when a slot frees up:
```python
from dsl_engine import EventReader

for event in EventReader(strategy_key="wolf-abc123").read_new():
    if event["event"] == "strategy.slot_freed":
        look_for_new_entry(event["payload"]["strategyKey"])
    elif event["event"] == "position.closed":
        alert_user(event["payload"])
```

### 15.2 dsl-tight as Config-Only Consumer

dsl-tight is not an engine. It is a preset definition + documentation.
```bash
# Create a tight position state file
python /path/to/dsl/scripts/dsl-init.py --preset tight --asset ETH \
  --entry 3000 --size 50 --leverage 5 --direction long --wallet 0xABC...
```
Then run DSL normally. No extra scripts needed in dsl-tight.

---

## 16. Migration Path

### P0 — Fixes Live Formula Bug (do first)
1. Build `dsl_engine.py` — unified engine with correct `lockPct` formula + all wolf features.
2. Replace `dsl-dynamic-stop-loss/scripts/dsl-v4.py` with thin wrapper calling `dsl_engine`.
3. Add `skill.json` to `dsl-dynamic-stop-loss/`, `wolf-strategy/`, `dsl-tight/`.
4. Rename `dsl-dynamic-stop-loss/` → `dsl/`.

### P1 — Core Plugin Architecture
5. `dsl_config.py` — layered config loader (defaults → file → strategy.json → env → CLI).
6. `schema/config.v1.json` — JSON Schema with all config keys.
7. `schema/state.v3.json` — formal position state contract.
8. `schema/strategy.v1.json` — formal strategy descriptor contract.
9. `dsl-migrate.py` — auto-migration flat v1/v2 → v3; synthesise `strategy.json` from existing state dirs.
10. `dsl-strategy-init.py` — strategy descriptor generator (writes `strategy.json`).
11. `dsl-init.py` — position state file generator with presets (checks slot availability).
12. Strategy mode in `dsl.py` — load `strategy.json`, apply config, run all positions, update runtime aggregate.

### P2 — Full Ecosystem
11. Retention policies — cleanup on close, TTL, max history.
12. Event log — emit from engine, `EventReader` in `dsl_engine.py`.
13. Wolf imports from dsl — `wolf_config.py`, `wolf-setup.py`.
14. Shrink `SKILL.md` files to target token budgets.
15. Model config wired through to SKILL.md cron mandate.

### P3 — Polish
16. `schema/event.v1.json` formal contract.
17. `dsl_skill.py` — `require_skill()` + semver check (runtime peer enforcement).
18. `cleanup_inactive_states()` scheduled sweep.
19. Minimal output mode (`DSL_OUTPUT_LEVEL=minimal` for heartbeat-OK ticks).
20. Delete `wolf-strategy/scripts/dsl-v4.py` and `wolf-strategy/scripts/dsl-combined.py`.

**Backward compatibility:** Existing flat state files auto-migrate on first load.
`DSL_STATE_FILE` env var continues to work. Default output format unchanged.

---

## 17. Verification Checklist

- [ ] **Formula:** Same state through old dsl-v4 vs new engine; `tier_floor` 30.31 → 30.44 (intentional); all other fields identical.
- [ ] **Config:** Invalid config (bad type, out-of-range) → clear error message before first run, no cron starts.
- [ ] **Layered config:** `DSL_MODEL` env overrides `strategy.json config` overrides `config/dsl.json` overrides `skill.json` defaultConfig.
- [ ] **Strategy config:** Strategy A with `model.primary=haiku` and Strategy B with `model.primary=opus` resolve different models from same global defaults.
- [ ] **State v3:** `validate_state()` rejects missing `meta`; auto-migrates flat v1 files on load.
- [ ] **Strategy descriptor:** `create_strategy()` writes valid `strategy.json`; `dsl-migrate.py` synthesises one from existing position-only dirs.
- [ ] **Slot management:** `strategy_has_slot()` returns false when `activePositions == maxPositions`; returns true after a position closes.
- [ ] **Strategy isolation:** `dsl_state_glob("wolf-abc123")` returns only that strategy's position files.
- [ ] **Strategy events:** Closing a position emits `strategy.slot_freed` with correct `slots_available`.
- [ ] **Retention per strategy:** Strategy A `cleanupOnClose=true` deletes its positions on close; Strategy B `retentionDays=30` keeps them.
- [ ] **History:** `maxHistoryPerPosition=10` keeps exactly 10 snapshots per position, rotates oldest.
- [ ] **Events:** `position.closed` appended to `events/dsl/{strategyKey}.jsonl`; `EventReader.read_new()` returns it once, then empty.
- [ ] **Peer dependency:** Remove `dsl/` dir; `import wolf_config` → `ImportError` with install message.
- [ ] **Model:** `DSL_MODEL=<model-id>` overrides `primary` in skill.json.
- [ ] **Strategy mode:** `dsl.py --strategy wolf-abc123` produces same per-position results as single mode on each file; also updates `strategy.runtime`.
- [ ] **Presets:** `generate_state_file(preset="wolf", ...)` passes `validate_state()`.

---

## 18. Priority / Effort Summary

| Priority | Item | Impact | Effort |
|----------|------|--------|--------|
| P0 | Unified engine + formula fix | Fixes live profit-lock bug | 2 d |
| P0 | `skill.json` manifests | Dependency resolution | 0.5 d |
| P0 | Rename `dsl-dynamic-stop-loss` → `dsl` | Infrastructure role | 0.5 d |
| P1 | Layered config + config schema | Configurability | 1 d |
| P1 | State v3 + migration | Config/runtime split, ownership | 1 d |
| P1 | `strategy.json` + strategy mode | Strategy as primary unit, slot mgmt | 1 d |
| P1 | Strategy-level config inheritance | Per-strategy model/cron/retention | 0.5 d |
| P1 | `dsl-strategy-init.py` | Creates strategy descriptor | 0.5 d |
| P1 | `dsl-init.py` + presets | Replaces manual JSON + wolf-setup | 0.5 d |
| P1 | SKILL.md token reduction | ~90% fewer tokens per agent load | 0.5 d |
| P2 | Retention policies | Storage hygiene | 0.5 d |
| P2 | Event log + EventReader | Decouples wolf from output parsing | 1.5 d |
| P2 | Wolf imports from dsl | Single source of truth for wolf | 1 d |
| P2 | Model config in skill.json | Model selection per skill | 0.5 d |
| P3 | JSON Schema contracts | Validation at load | 1 d |
| P3 | `require_skill()` semver check | Runtime peer enforcement | 0.5 d |
| P3 | Cleanup sweep | Automated state hygiene | 0.5 d |
| P3 | Minimal output mode | ~91% token reduction on quiet crons | 0.5 d |

**Total ~12.5 days.** P0 alone (formula fix + manifests + rename) is one sprint and delivers
the highest-impact fix immediately.
