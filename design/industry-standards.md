# Industry Standards Reference

**Date:** 2026-02-27
**Context:** Standards applied in the DSL unified plugin architecture
**Stack:** Agent SDK · Python scripts · SKILL.md contracts

This document explains each industry pattern used, where it originates, what problem it solves,
and how it is applied in this codebase. References are grounded in agent platform standards first,
with broader software patterns where the platform has no native concept.

---

## 1. SKILL.md as the Agent Skill Contract

**Origin:** Agent SDK / agent platform — the native and authoritative format for
defining agent skills. Every skill is a directory containing a `SKILL.md` file with YAML
frontmatter and Markdown body. This is how the agent platform discovers, loads, and invokes
skills.

**Official SDK structure:**
```
skills/dsl/
└── SKILL.md          ← required; frontmatter + instructions
└── scripts/          ← optional; Python scripts the agent runs via Bash
└── references/       ← optional; detail files loaded on demand
```

**SKILL.md frontmatter (the agent-level manifest):**
```yaml
---
name: dsl
description: >
  Dynamic stop-loss engine for Hyperliquid perpetual positions.
  Use when managing an open leveraged position, running a trailing stop,
  checking a floor price, or processing a DSL cron tick.
---
```

The `description` field is what the agent reads at startup to know *when* to invoke the skill.
The SDK loads only the frontmatter on startup (~100 tokens per skill). The full SKILL.md body
is loaded only when the skill is triggered.

**Problem it solves:** Without a `description`, the agent cannot decide whether to invoke this
skill for a given user request. Without a `name`, the skill cannot be referenced unambiguously
across the platform.

**Official frontmatter field rules:**
- `name`: max 64 chars, lowercase letters/numbers/hyphens only, no reserved words
- `description`: max 1024 chars, no XML tags; must describe both *what* and *when*

**Applied as:** Every skill in this codebase gets a `SKILL.md` with carefully written
frontmatter. The description is the primary trigger. All implementation detail lives in the
SKILL.md body or `references/` — never in the frontmatter.

**Note on `skill.json`:** Senpi adds a platform-level `skill.json` alongside `SKILL.md` to
carry metadata that the SDK frontmatter does not support: semantic version, peer skill
declarations, config schema, and what the skill exposes to other skills. This is a
Senpi-platform extension — not part of the base agent SDK.

---

## 2. Progressive Disclosure (Three-Level Loading)

**Origin:** Agent SDK documentation — the official architecture for how skill content
enters the agent's context window. Described in detail in the Agent Skills overview.

**Problem it solves:** If a skill's full documentation loaded on every agent turn, skills with
comprehensive reference material would consume thousands of tokens even when not in use. At
3 active positions × 480 ticks/day, loading a 2,600-token SKILL.md on every tick costs ~$4/day
just for heartbeat-OK checks.

**The three levels (from the SDK docs):**

| Level | Content | When Loaded | Token Cost |
|-------|---------|-------------|-----------|
| **1 — Metadata** | YAML frontmatter (`name`, `description`) | Always, at agent startup | ~100 tokens per skill |
| **2 — Instructions** | SKILL.md body (workflow, decision table, commands) | When the skill is triggered by a matching request | < 5k tokens |
| **3 — Resources** | Bundled files in `references/`, scripts in `scripts/` | On demand — only when the agent reads them via Bash | Effectively unlimited |

The agent uses Bash to read files at Level 3 — the file contents never enter the context
window until explicitly read. Scripts are *executed* via Bash; only their output (not their
source code) consumes tokens.

**Applied as:**

```
dsl/
├── SKILL.md           ← Level 1 (frontmatter) + Level 2 (instructions, ~250 tokens)
└── references/
    ├── config-reference.md     ← Level 3 — loaded only when agent needs config detail
    ├── state-schema.md         ← Level 3 — loaded only when agent needs schema
    ├── output-schema.md        ← Level 3 — loaded only when agent is debugging output
    └── presets.md              ← Level 3 — loaded only when agent is setting up a position
└── scripts/
    ├── dsl_engine.py           ← Level 3 — executed via Bash; source never in context
    └── dsl.py                  ← Level 3 — executed via Bash; source never in context
```

**Key rule:** SKILL.md body is the interface. It must contain only what the agent needs to make
a decision on every tick. Everything else goes into `references/` to be loaded on demand.

---

## 3. Plugin-Based Skill Bundling (Inter-Skill Dependencies)

**Origin:** Agent SDK Plugins — the official mechanism for packaging multiple skills,
commands, agents, hooks, and MCP servers into a single installable unit. A plugin is a
directory with a plugin manifest (e.g. `.plugin/plugin.json`).

**Official plugin structure:**
```
dsl-plugin/
├── .plugin/
│   └── plugin.json          ← required plugin manifest
├── skills/
│   └── dsl/
│       └── SKILL.md         ← bundled skill
├── commands/                ← optional slash commands
├── agents/                  ← optional subagent definitions
├── hooks/                   ← optional event handlers
└── .mcp.json               ← optional MCP server definitions
```

**`plugin.json` manifest:**
```json
{
  "name":        "dsl-plugin",
  "version":     "5.0.0",
  "description": "Dynamic stop-loss engine — includes dsl skill and supporting scripts"
}
```

**Problem it solves:** At the agent SDK level, skills are standalone filesystem artifacts —
there is no native peer-dependency mechanism between individual skills. If wolf-strategy
requires the DSL skill to be installed, the SDK cannot enforce this automatically on a
per-skill basis. The Plugin system solves this: a wolf plugin bundles *both* wolf's own
capabilities *and* declares that DSL must be co-installed.

**Applied as:** The Senpi platform adds a `skill.json` alongside `SKILL.md` with a
`peerDependencies` field as a platform-level convention. At install time, the Senpi host
reads `skill.json` and ensures all peer skills are available. At agent runtime, the
wolf SKILL.md instructs the agent to verify DSL is present:
```markdown
**Requires:** `dsl` skill installed at `skills/dsl/SKILL.md`
```
This makes the dependency visible to both the platform and the agent.

**SDK loading:** Plugins are loaded via the Agent SDK `plugins` option:
```python
from agent_sdk import query, AgentOptions

options = AgentOptions(
    plugins=[{"type": "local", "path": "./senpi-plugins/dsl"}],
    setting_sources=["user", "project"],
    allowed_tools=["Skill", "Bash", "Read"],
)
```

**Key rule:** At the agent SDK level, inter-skill dependencies are a Plugin concern. The Plugin
manifest (`plugin.json`) is the right place to declare them. Per-skill `peerDependencies` in
`skill.json` is a Senpi platform extension on top of this.

---

## 4. Semantic Versioning (semver)

**Origin:** semver.org — adopted by PyPI, pip, Cargo, RubyGems, and every modern package
manager. Python's `packaging` library implements the comparison operators (`>=`, `~=`, `!=`)
used in version constraints.

**Problem it solves:** Version strings like "v4.1+" are meaningless to tooling. You cannot
programmatically decide whether `v4.1+` satisfies `>=4.0.0`.

**Standard rules:**
```
MAJOR.MINOR.PATCH

MAJOR — breaking change (state schema, removed functions, renamed config keys)
MINOR — new feature, backwards compatible (new config key with a default)
PATCH — bug fix, backwards compatible (formula fix, error message improvement)
```

**Python check:**
```python
from packaging.version import Version
Version("5.1.0") >= Version("5.0.0")   # True
Version("4.9.0") >= Version("5.0.0")   # False
```

**Applied as:** DSL starts at `5.0.0` because the engine rewrite (state schema v3, new config
system) is a breaking change from v4. Wolf's `skill.json` declares `"dsl": ">=5.0.0"`. The
Senpi host uses `packaging.version` to enforce this at install time.

**Key rule:** Increment MAJOR when you change the state schema, rename config keys, or remove
public functions. Never break callers with a MINOR or PATCH bump.

---

## 5. Layered Configuration (12-Factor App — Factor III)

**Origin:** The Twelve-Factor App (12factor.net) — Factor III: "Store config in the
environment." Agent platforms use environment variables extensively for configuration.
Python's `pydantic-settings` library formalises this pattern for application-level config.

**Problem it solves:** Hardcoded defaults require source edits to change behaviour. A single
config file cannot serve different strategies with different needs without file swaps.

**Five-layer resolution, lowest → highest:**
```
1. skill.json defaultConfig          — packaged safe defaults
2. /data/workspace/config/dsl.json   — user install preferences
3. strategy.json config block        — per-strategy overrides  ← agent-level concept
4. DSL_* environment variables       — deployment overrides
5. CLI arguments                     — per-run overrides
```

**Key rules:**
- Every key has a safe default so the skill runs out of the box.
- Config is validated by `pydantic` before any cron logic runs.
- Log which source won for each key at startup (`logging.DEBUG`).

---

## 6. Schema Validation (Pydantic)

**Origin:** Python's `pydantic` library — the de facto standard for runtime data validation
in Python. Used by FastAPI, LangChain, CrewAI, and most modern Python agent frameworks for
validating config, request bodies, and internal data models.

**Problem it solves:** A state file with `"leverage": "10"` (string instead of number) silently
produces wrong math. A config with `"retentionDays": -1` causes undefined behaviour. Bugs surface
in the middle of live trading, not at startup.

```python
from pydantic import BaseModel, Field, field_validator

class TierConfig(BaseModel):
    roePct:           float = Field(gt=0)
    lockPct:          float = Field(ge=0, le=100)
    breachesRequired: int   = Field(ge=1)

class PositionConfig(BaseModel):
    asset:      str
    entryPrice: float = Field(gt=0)
    leverage:   int   = Field(ge=1, le=100)
    direction:  str   = Field(pattern="^(long|short)$")
    tiers:      list[TierConfig]

    @field_validator("tiers")
    @classmethod
    def tiers_must_be_ascending(cls, v):
        if [t.roePct for t in v] != sorted(t.roePct for t in v):
            raise ValueError("Tier roePct must be ascending")
        return v
```

**Applied as:** `dsl_engine.py` uses pydantic models for config and state validation.
JSON Schema files in `schema/` (validated with the `jsonschema` Python library) serve as
language-agnostic contracts readable by any tool.

**Key rule:** Validate at load time. Fail with a human-readable error. Never silently fall
back to defaults for invalid values.

---

## 7. Resource Ownership (Single-Writer Pattern)

**Origin:** Single-writer principle — one component is the authoritative writer for a given
resource. Applied in Kubernetes (CRD / Operator pattern), Python's `threading.Lock` /
`multiprocessing.Lock`, and database row-level ownership.

**Problem it solves:** Wolf and DSL both wrote to the same position state files, sometimes
with stale data and different field sets. There was no authoritative owner.

**Applied as:**

| Resource | Owner | Who may NOT write |
|----------|-------|------------------|
| `config` block in state file | Producing skill (wolf-setup.py) | DSL engine |
| `runtime` block in state file | DSL engine | wolf, other skills |
| `strategy.json config` | Producing skill | DSL engine |
| `strategy.json runtime` | DSL engine | wolf |
| Event log `*.jsonl` | DSL engine (append only) | nobody — immutable |

**Key rule:** One component owns a resource type. Others create instances; only the owner mutates them.

---

## 8. Hierarchical Resource Organization

**Origin:** File system directory hierarchy, Python's `pathlib`, multi-tenant SaaS
architecture. A strategy is a parent resource (directory) that owns position state files
(children). The parent carries config, slot budget, and aggregate runtime state.

**Problem it solves:** A flat pool of position files has no way to express ownership, enforce
slot limits per strategy, or apply different config to different groups of positions.

```
state/dsl/
  wolf-abc123/               ← parent (strategy)
    strategy.json
    dsl-HYPE.json            ← child (position)
    dsl-ETH.json
  dsl-tight-xyz/             ← separate parent, fully isolated
    strategy.json
    dsl-SOL.json
```

```python
from pathlib import Path

def strategy_dir(strategy_key: str) -> Path:
    return Path(DATA_DIR) / "state" / "dsl" / strategy_key

def position_files(strategy_key: str) -> list[Path]:
    return list(strategy_dir(strategy_key).glob("dsl-*.json"))
```

**Key rule:** The parent resource owns the config and slot budget. Children inherit; they do
not define their own top-level config.

---

## 9. Namespace Isolation

**Origin:** Python package namespaces, Unix filesystem permissions, multi-tenant SaaS.
Each strategy is an isolated directory. Cleanup, event logs, and discovery are all scoped
to a single strategy key.

**Applied as:**
- Every strategy lives under `state/dsl/{strategyKey}/` — its own directory.
- `position_files("wolf-abc123")` never returns dsl-tight's positions.
- Event log per strategy: `events/dsl/{strategyKey}.jsonl`.
- EventReader checkpoint per strategy: `events/dsl/{strategyKey}.checkpoint`.
- DSL cleanup respects `meta.owner.skill` — never deletes across strategy boundaries.

---

## 10. Config / Runtime Split (Immutable vs Mutable State)

**Origin:** Python `dataclasses(frozen=True)` for config vs mutable runtime, Kubernetes
`spec` (desired state, set by user) vs `status` (observed state, set by controller).

**Problem it solves:** When config and runtime live in the same flat dict, a partial write
during a crash can corrupt position config (entry price, tiers, wallet address).

```python
from dataclasses import dataclass

@dataclass(frozen=True)    # config: immutable after creation
class PositionConfig:
    asset:       str
    entry_price: float
    leverage:    int

@dataclass                 # runtime: mutated by DSL engine on every tick
class PositionRuntime:
    phase:              int   = 1
    active:             bool  = True
    high_water_price:   float = 0.0
    peak_roe:           float = 0.0
```

**Applied as:** `config` block written once by the producer. `runtime` mutated exclusively
by `dsl_engine.py`. Same split in `strategy.json`.

**Key rule:** Producers write `config`. The engine writes `runtime`. Nothing writes both.

---

## 11. Agent Lifecycle Hooks

**Origin:** Agent SDK hooks — `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`,
`SessionEnd`, `UserPromptSubmit`. These are the native agent lifecycle events. Python's
`pluggy` library (used by pytest) provides the same pattern at the Python level.

**SDK hook example:**
```python
from agent_sdk import query, AgentOptions, HookMatcher
import json
from pathlib import Path

async def on_state_write(input_data, tool_use_id, context):
    """Audit every state file write."""
    path = input_data.get("tool_input", {}).get("file_path", "")
    if "state/dsl" in path:
        Path("audit.log").open("a").write(
            f"{utcnow()} WRITE {path}\n"
        )
    return {}

options = AgentOptions(
    hooks={
        "PostToolUse": [
            HookMatcher(matcher="Write|Edit", hooks=[on_state_write])
        ]
    }
)
```

**Standard skill lifecycle:**
```
install → configure → start_cron → [run* → emit_events*] → stop_cron → uninstall
```

**Applied as:**

| Hook | What it does |
|------|--------------|
| `on_install` | Validate config schema, create dirs, run `dsl-migrate.py` |
| `on_configure` | Reload + validate config, log changed keys |
| `on_cron_start` | Resolve config, discover strategy/position state files |
| `on_run` | Fetch prices, evaluate positions, write state, update strategy runtime |
| `on_position_event` | Emit to event log |
| `on_error` | Log with Python `logging`, backoff, optionally pause |
| `on_stop` | Flush in-flight writes via `atexit` handler |

---

## 12. Event Sourcing / Append-Only Event Log

**Origin:** Event Sourcing (Martin Fowler), Python's `open("a")` append mode, Apache Kafka.
Core idea: state is derived from an immutable sequence of events, not a mutable record.

**Problem it solves:** Wolf currently parses DSL's full JSON stdout. If DSL renames a field,
wolf silently breaks. If wolf misses a cron tick, the event is lost entirely.

```python
def emit_event(strategy_key: str, event: str, payload: dict) -> None:
    log = Path(DATA_DIR) / "events" / "dsl" / f"{strategy_key}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {"v": 1, "event": event, "ts": utcnow(), "payload": payload}
    with log.open("a") as f:
        f.write(json.dumps(record) + "\n")   # "a" = append, never overwrite

class EventReader:
    def read_new(self) -> list[dict]:
        offset = int(self._ckpt.read_text()) if self._ckpt.exists() else 0
        events = []
        with self._log.open() as f:
            f.seek(offset)
            for line in f:
                events.append(json.loads(line))
            self._next = f.tell()
        return events
```

**Applied as:** DSL appends to `events/dsl/{strategyKey}.jsonl`. Wolf reads via `EventReader`.
If wolf misses 3 ticks it catches up on the next read.

**Key rule:** Events are facts. Never delete or mutate — only append.

---

## 13. Atomic Writes (POSIX `os.replace`)

**Origin:** POSIX `rename()` syscall — used by SQLite WAL, PostgreSQL, and systemd. In Python:
`os.replace()` or `pathlib.Path.replace()`.

**Problem it solves:** `json.dump(state, open(path, "w"))` has a crash window where the file
is partially written, leaving a corrupt unparseable state file.

```python
def save_state(path: str, state: dict) -> None:
    p   = Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, p)    # atomic on POSIX — never a partial file
```

**Applied as:** `save_state()` and `save_strategy()` in `dsl_engine.py`. Already in
`dsl_common.py`; unified into the engine.

---

## 14. Retention Policies

**Origin:** Python `pathlib` + `datetime` sweeps, `logging.handlers.TimedRotatingFileHandler`
for the same concept applied to log files, InfluxDB retention policies.

**Applied as:** `retentionDays`, `maxHistoryPerPosition`, `cleanupOnClose`, `persistInactive`
in config — overridable per strategy. Cleanup sweep runs at startup and once per 24 hours.

```python
def cleanup_closed_positions(strategy_key: str, retention_days: int) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    for path in position_files(strategy_key):
        state = load_state(path)
        if not state["runtime"]["active"]:
            updated = datetime.fromisoformat(state["meta"]["updatedAt"])
            if updated < cutoff:
                path.unlink()
```

---

## 15. Single Source of Truth

**Origin:** DRY — Don't Repeat Yourself (Hunt & Thomas, *The Pragmatic Programmer*, 1999).
In Python: a single importable module is the canonical implementation; all others `import` it.

**Problem it solves:** Three copies of DSL logic diverged silently. A formula fix applied to
wolf's copy never reached the base skill. Users of the base skill had incorrect
profit-lock calculations for months.

```python
# Every consumer does this — never copies the logic
from dsl_engine import process_position, generate_state_file, EventReader
```

**Applied as:** One `dsl_engine.py`. `dsl_presets.py` for presets. `dsl_config.py` for config
loading. All legacy copies deleted after migration.

**Key rule:** If the same logic exists in two `.py` files, one of them is wrong.

---

## 16. Fail Fast with Actionable Errors

**Origin:** "Fail Fast" principle (Jim Shore, 2004). In Python: raise at the earliest point
where the contract is violated, with enough context to fix it.

**Problem it solves:** A missing peer skill surfaces as a cryptic `ModuleNotFoundError` deep
inside a cron run. An invalid config silently falls back to a default the user never intended.

```python
def require_skill(name: str, min_version: str = None) -> None:
    if importlib.util.find_spec(name) is None:
        raise ImportError(
            f"Required skill '{name}' is not installed.\n"
            f"Fix: senpi skills install {name}"
        )
```

**Applied as:** `require_skill()`, `validate_state()` (pydantic), `dsl_config.py` validation
before any cron logic. Bad config → `sys.exit(1)`. No silent fallback.

---

## 17. Structured Output Schema

**Origin:** Python `pydantic` typed returns, the general principle that function output should
have a documented type. Agent SDKs typically use typed message objects in the stream.

**Problem it solves:** DSL outputs an ad-hoc ~30-field JSON dict. If a field is renamed, the
agent's decision table silently breaks.

```python
from pydantic import BaseModel
from typing import Literal

class DSLResult(BaseModel):
    status: Literal["HEARTBEAT_OK","TIER_CHANGED","CLOSED",
                    "PENDING_CLOSE","FETCH_FAILED","ERROR"]
    asset:        str
    price:        float
    roe:          float
    tier:         int
    floor:        float
    closed:       bool       = False
    tier_changed: bool       = False
    reason:       str | None = None
    ts:           str
```

**Applied as:** `dsl_engine.py` returns typed `ProcessResult` objects. Output schema documented
in `references/output-schema.md`. `DSL_OUTPUT_LEVEL=minimal` emits only the 6 fields the agent
needs for the decision table, saving ~91% of output tokens on quiet ticks.

---

## 18. Schema Versioning and Auto-Migration

**Origin:** Python migration frameworks: Alembic (SQLAlchemy), Django `migrations`, Peewee
migrations. All follow the same pattern: a version field + numbered migration functions.

```python
MIGRATIONS = {
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
}

def load_state(path: str) -> dict:
    state   = json.loads(Path(path).read_text())
    version = state.get("meta", {}).get("schemaVersion", 1)
    for v in range(version, CURRENT_SCHEMA_VERSION):
        state = MIGRATIONS[v](state)
    return state
```

**Applied as:** `meta.schemaVersion: 3` in every state file. `dsl_engine.load_state()`
auto-migrates v1/v2 → v3 on load. `dsl-migrate.py` does the same in batch.

---

## 19. Runtime Skill Discovery via `importlib`

**Origin:** Python's `importlib` module — the standard library mechanism for dynamic imports,
used by pytest's plugin discovery, Flask's blueprint loading, and Django's `INSTALLED_APPS`.

**Problem it solves:** `wolf_config.py` currently uses raw `sys.path.insert(0, "../../dsl/scripts")`
— a hardcoded relative path with no version check and a cryptic error on failure.

```python
import sys, importlib, json
from pathlib import Path

def require_skill(name: str, min_version: str = None):
    skills_root = Path(__file__).parent.parent.parent  # senpi-skills/
    scripts_dir = skills_root / name / "scripts"

    if not scripts_dir.is_dir():
        raise ImportError(
            f"Peer skill '{name}' not found at {scripts_dir}\n"
            f"Install: senpi skills install {name}"
        )
    if min_version:
        manifest  = json.loads((skills_root / name / "skill.json").read_text())
        installed = manifest.get("version", "0.0.0")
        if Version(installed) < Version(min_version):
            raise ImportError(
                f"Skill '{name}' v{installed} < required v{min_version}\n"
                f"Upgrade: senpi skills upgrade {name}"
            )

    sys.path.insert(0, str(scripts_dir))
    return importlib.import_module("dsl_engine")
```

**Applied as:** `dsl_skill.py` exposes `require_skill()`. Wolf calls it once at module load.
All path logic and version checking live in one place.

---

## Summary Table

| # | Standard | Origin | Applied As |
|---|----------|--------|-----------|
| 1 | SKILL.md as agent skill contract | Agent SDK — native skill format | `SKILL.md` frontmatter: `name` + `description` |
| 2 | Progressive disclosure (3-level loading) | Agent SDK — official loading architecture | Frontmatter → SKILL.md body → `references/` |
| 3 | Plugin-based skill bundling + deps | Agent SDK Plugins — plugin manifest | `skill.json` peerDependencies as Senpi-platform extension |
| 4 | Semantic versioning | semver.org, PyPI, `packaging` library | `"version": "5.0.0"` + `require_skill()` check |
| 5 | Layered config | 12-Factor App, `pydantic-settings` | defaults → file → strategy.json → env → CLI |
| 6 | Schema validation | `pydantic`, `jsonschema` Python library | config, state, strategy, event validation |
| 7 | Single-writer / resource ownership | `threading.Lock`, Kubernetes Operator | DSL owns `runtime`; producers own `config` |
| 8 | Hierarchical resource org | Python `pathlib`, directory hierarchy | `strategy.json` (parent) → `dsl-*.json` (children) |
| 9 | Namespace isolation | Python package namespaces, multi-tenant SaaS | `state/dsl/{strategyKey}/` per strategy |
| 10 | Config/runtime split | `dataclasses(frozen=True)`, K8s spec/status | Immutable `config` + mutable `runtime` |
| 11 | Agent lifecycle hooks | Agent SDK hooks (`PreToolUse`, `PostToolUse`, etc.) | install → configure → run → stop |
| 12 | Event sourcing | Python `open("a")` append mode, CQRS | Append-only `.jsonl` + `EventReader` checkpoint |
| 13 | Atomic writes | Python `os.replace()` / POSIX `rename()` | Write to `.tmp`, then `os.replace()` |
| 14 | Retention policies | Python `pathlib` sweep, `TimedRotatingFileHandler` | `retentionDays`, `maxHistory`, `cleanupOnClose` |
| 15 | Single source of truth | DRY, Python `import` | One `dsl_engine.py`; all others import from it |
| 16 | Fail fast + actionable errors | "Fail Fast" principle, Python `raise` | `require_skill()`, `validate_state()`, config check |
| 17 | Structured output schema | `pydantic` model output, typed returns | `DSLResult` model; `DSL_OUTPUT_LEVEL=minimal` |
| 18 | Schema versioning + migration | Alembic, Django migrations | `schemaVersion` field + `MIGRATIONS` dict |
| 19 | Runtime skill discovery | Python `importlib`, `sys.path` | `require_skill()` in `dsl_skill.py` |
