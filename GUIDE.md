# Senpi Skill Development Guide

Best practices for building production-quality Senpi skills. Extracted from battle-tested patterns in WOLF v6, Emerging Movers, Opportunity Scanner, and other skills.

This guide is for **agents and developers** creating new skills.

---

## 1. Skill Structure

Every skill follows the [Agent Skills](https://agentskills.io) standard.

### Required Layout

```
{skill-name}/
├── SKILL.md                    # Master instructions (required)
├── README.md                   # Quick reference & changelog (optional)
├── scripts/
│   ├── {main}.py               # Core logic
│   ├── {skill}_config.py       # Shared config loader (if multi-script)
│   └── {skill}-setup.py        # Setup wizard (if onboarding needed)
└── references/
    ├── state-schema.md          # JSON state file structure
    ├── cron-templates.md        # Ready-to-use cron mandates
    └── {domain-specific}.md     # Scoring rules, learnings, etc.
```

### SKILL.md Frontmatter

```yaml
---
name: {skill-name}
description: >-
  What the skill does, how it works, when to use it.
  Include key features and requirements.
license: Apache-2.0
compatibility: >-
  Python version, dependencies (mcporter, cron),
  exchange/platform specifics
metadata:
  author: {author}
  version: "{version}"
  platform: senpi
  exchange: hyperliquid
---
```

### SKILL.md Content Sections

1. **One-liner concept** — what it does in one sentence
2. **Architecture** — how components connect, state flow
3. **Quick Start** — 3-5 steps to get running
4. **Detailed rules** — the domain logic agents must follow (crons reference this)
5. **API dependencies** — which MCP tools are used
6. **State schema** — or link to `references/state-schema.md`
7. **Cron setup** — or link to `references/cron-templates.md`
8. **Known limitations / troubleshooting**

---

## 2. Token Optimization

Token burn is the single biggest operational cost. These patterns reduce it dramatically.

### 2.1 Model Tiering

Classify each cron job by the reasoning it requires:

| Tier | Use When | Model Examples |
|------|----------|---------------|
| **Tier 1** (fast/cheap) | Binary/threshold checks, status parsing, health validation | claude-haiku-4-5, gpt-4o-mini, gemini-flash |
| **Tier 2** (capable) | Judgment calls, signal routing, multi-factor scoring | anthropic/claude-sonnet-4-20250514, anthropic/claude-opus-4-20250514, gpt-4o, gemini-pro |

Document the tier for each cron in your `cron-templates.md`. Most crons should be Tier 1.

### 2.2 Heartbeat Early Exit

If a cron run finds nothing actionable, output `HEARTBEAT_OK` immediately. Do not enumerate what was checked.

```
# BAD — wastes tokens on narrative
"Checked 50 markets. No signals above threshold. BTC is flat. Funding rates normal. Nothing to do."

# GOOD — immediate exit
"HEARTBEAT_OK"
```

### 2.3 Minimal Default Output

Scripts should return only the fields the LLM needs for its decision. Diagnostic detail should be opt-in.

```python
# Default: minimal
output = {
    "asset": "HYPE", "direction": "LONG",
    "score": 185, "risks": ["high_funding"]
}

# Verbose (opt-in via env var):
if os.environ.get("MY_SKILL_VERBOSE") == "1":
    output["debug"] = { "all_pillar_scores": {...}, "candle_data": {...} }
```

**Specific techniques:**
- Remove `indent=2` from `json.dumps()` — whitespace costs tokens on large outputs
- Drop informational fields that don't affect the decision
- Below-threshold items: output only `asset` + `score`, not the full analysis
- Remove duplicate data structures (e.g., a separate `closed` array when `status=="closed"` already exists in `results`)

### 2.4 Prompt Compression

Move detailed rules into `SKILL.md` (loaded once into agent context). Cron templates should reference them with one-liners.

```
# BAD — 26-line cron mandate repeating all entry rules
"Check if rank jumped 10+, verify not in top 10, check hourly trend, verify leverage >= 7x,
check slot availability, check rotation cooldown, verify no existing position..."

# GOOD — 4-line cron mandate referencing SKILL.md
"Run `python3 scripts/emerging-movers.py`, parse JSON.
On signals: apply entry rules from SKILL.md, route to best-fit strategy.
Alert Telegram. Else HEARTBEAT_OK."
```

### 2.5 Processing Order Directives

Give crons explicit numbered steps to prevent the LLM from re-reading files or looping:

```
PROCESSING ORDER:
1. Read config ONCE. Map available slots.
2. Build complete action plan: [(asset, direction, margin), ...]
3. Execute entries sequentially. No re-reads.
4. Send ONE consolidated Telegram after all entries.
```

This prevents: config re-reads per action, multiple Telegram messages, context growth from repeated tool calls.

### 2.6 Context Isolation

- Read config files ONCE per cron run
- Build a complete action plan before executing any tool calls
- Send ONE consolidated notification per run, not one per signal
- Skip redundant checks when data is fresh (e.g., < 3 min old)

---

## 3. Using Senpi MCP (mcporter)

### 3.1 Always Use MCP — Never Direct API Calls

All external data must go through Senpi MCP via `mcporter`. Never `curl` third-party APIs directly.

```python
# BAD — direct API call
r = subprocess.run(["curl", "-s", "-X", "POST", "https://api.hyperliquid.xyz/info",
    "-d", json.dumps({"type": "allMids"})], capture_output=True, text=True)

# GOOD — MCP call
r = subprocess.run(["mcporter", "call", "senpi.market_get_prices"],
    capture_output=True, text=True, timeout=15)
```

**Why MCP over direct APIs:**
- MCP handles auth, rate limiting, and caching
- Consistent error format across all tools
- Single abstraction layer — if the upstream API changes, only MCP needs updating
- No secrets (API keys, endpoints) in your skill code

### 3.2 Unified MCP Call Helper

Create a single helper function for all MCP calls in your skill:

```python
def call_mcp(tool, **kwargs):
    """Call a Senpi MCP tool with retry."""
    cmd = ["mcporter", "call", f"senpi.{tool}"]
    for k, v in kwargs.items():
        if isinstance(v, (list, dict, bool)):
            cmd.append(f"{k}={json.dumps(v)}")
        else:
            cmd.append(f"{k}={v}")

    last_error = None
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            data = json.loads(r.stdout)
            if isinstance(data, dict) and data.get("success") is False:
                raise ValueError(data.get("error", "unknown"))
            return data
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(3)
    raise last_error
```

### 3.3 Batch MCP Calls

Prefer single batched calls over multiple separate calls:

```python
# BAD — 3 separate API calls
candles_4h = call_mcp("market_get_candles", asset="BTC", timeframe="4h")
candles_1h = call_mcp("market_get_candles", asset="BTC", timeframe="1h")
candles_15m = call_mcp("market_get_candles", asset="BTC", timeframe="15m")

# GOOD — 1 call returning all intervals
data = call_mcp("market_get_asset_data",
    asset="BTC",
    candle_intervals=["4h", "1h", "15m"],
    include_order_book=False,
    include_funding=False)
```

### 3.4 Subprocess Safety

- **No `shell=True`** — always use list-based args: `subprocess.run(["mcporter", "call", ...])`.
- **No temp files** — read stdout directly: `capture_output=True, text=True`.
- **No `2>/dev/null`** — capture stderr for debugging: it's in `r.stderr`.
- **Always set `timeout`** — prevent hung processes: `timeout=15` (or `timeout=30` for heavy calls).

---

## 4. Storage & State Management

### 4.1 Atomic Writes

All state file mutations must be atomic. Never use bare `open("w") + json.dump()`.

```python
import os, json

def atomic_write(path, data):
    """Write JSON atomically — crash-safe."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # atomic on POSIX
```

**Why:** If a cron is killed mid-write (timeout, OOM, signal), a partial `.json` file will crash every subsequent cron run. `os.replace()` is atomic — the file is either the old version or the new version, never corrupt.

### 4.2 Re-Read Before Write (Race Condition Guard)

When multiple crons can modify the same state file, re-read immediately before writing to detect external changes:

```python
# Before writing, check if another cron modified the file
if not should_close:
    try:
        with open(state_file) as f:
            current = json.load(f)
        if not current.get("active", True):
            return  # Another cron already closed this — don't resurrect
    except (json.JSONDecodeError, IOError):
        pass
atomic_write(state_file, state)
```

**Real bug this prevents:** DSL cron reads state → SM flip cron closes position (sets `active: false`) → DSL cron writes state back → position is resurrected.

### 4.3 State Directory Layout

Scope state files to avoid collisions:

```
{workspace}/
├── {skill}-config.json              # Skill-level config (single source of truth)
├── state/
│   ├── {instance-key}/              # Per-instance state (e.g., per-strategy, per-wallet)
│   │   ├── {entity}-{ASSET}.json    # Per-entity state files
│   │   └── monitor-last.json        # Last run state
│   └── {instance-key-2}/
│       └── ...
├── history/
│   ├── {shared-signal}.json         # Market-wide signals (shared across instances)
│   └── scan-history.json            # Cross-run momentum tracking
└── memory/
    └── {skill}-YYYY-MM-DD.md        # Daily logs/reports
```

**Key principle:** Signals are market-wide (shared). Position/instance state is scoped.

### 4.4 State File Schema

Every state file should include:

```json
{
  "version": 2,
  "active": true,
  "instanceKey": "strategy-abc123",
  "createdAt": "2026-02-20T15:22:00.000Z",
  "updatedAt": "2026-02-26T12:00:00.000Z"
}
```

- `version` — for migration logic when the schema changes
- `active` — deactivate monitoring without deleting the file
- `instanceKey` — back-reference to the owning instance
- `createdAt` / `updatedAt` — audit trail

---

## 5. Configuration Management

### 5.1 Single Source of Truth

Create one shared config module that all scripts import. No script should read config files independently.

```python
# {skill}_config.py — THE config loader

import os, json

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))
CONFIG_FILE = os.path.join(WORKSPACE, "{skill}-config.json")

def load_config():
    """Load skill config with defaults."""
    defaults = {
        "version": 1,
        "maxRetries": 3,
        "alertThreshold": 50,
        # ... all defaults here
    }
    try:
        with open(CONFIG_FILE) as f:
            user_config = json.load(f)
        return deep_merge(defaults, user_config)
    except FileNotFoundError:
        return defaults
```

### 5.2 Deep Merge for User Overrides

Never use `dict.update()` on nested configs — it's a shallow merge that silently loses nested keys.

```python
def deep_merge(base, override):
    """Recursively merge override into base. Preserves nested defaults."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

**Real bug this prevents:** User overrides one nested key → `dict.update()` replaces the entire parent dict → all other nested defaults vanish → `KeyError` crash.

### 5.3 Backward-Compatible Defaults

All config values must have sensible defaults so existing setups don't break when new fields are added:

```python
max_minutes = config.get("dsl", {}).get("phase1MaxMinutes", 90)
weak_peak_threshold = config.get("dsl", {}).get("weakPeakThreshold", 3.0)
```

### 5.4 Legacy Auto-Migration

When changing config format, auto-migrate on first load:

```python
def load_config():
    # Try new format first
    if os.path.exists(NEW_CONFIG):
        return json.load(open(NEW_CONFIG))

    # Auto-migrate old format
    if os.path.exists(OLD_CONFIG):
        old = json.load(open(OLD_CONFIG))
        new = migrate_v1_to_v2(old)
        atomic_write(NEW_CONFIG, new)
        return new

    return defaults
```

### 5.5 Percentage Convention

All thresholds and percentages must use **whole numbers** (5 = 5%), never decimals (0.05). Document this explicitly.

```python
# GOOD — percentage convention
retrace_pct = config.get("retraceFromHW", 5)  # 5 = 5%
retrace_decimal = retrace_pct / 100            # convert internally

# Document in state-schema.md:
# `retraceFromHW` is a percentage — use `5` for 5%.
# The code divides by 100 internally. Do NOT use `0.05`.
```

**Why:** Mixed conventions (some fields as 5, others as 0.05) cause subtle bugs that are hard to catch. Pick one convention and enforce it everywhere.

---

## 6. Retry & Resilience

### 6.1 Standard Retry Pattern

All external calls (MCP, subprocess, file I/O to remote paths) should use this pattern:

```python
def call_with_retry(fn, max_attempts=3, delay=3):
    """Retry with fixed delay. Returns result or raises last error."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                time.sleep(delay)
    raise last_error
```

Apply consistently: **3 attempts, 3-second delays** across all scripts.

### 6.2 Structured Error Output

On total failure, output **valid JSON with error info** — never a raw traceback. This lets the calling LLM parse the response and emit `HEARTBEAT_OK` instead of crashing.

```python
# BAD — raw traceback crashes the cron
raise Exception("API call failed")

# GOOD — structured error, caller can continue
try:
    data = fetch_data()
except Exception as e:
    print(json.dumps({
        "success": False,
        "error": f"fetch_failed: {str(e)}",
        "actionable": False  # tells the LLM: nothing to do
    }))
    sys.exit(1)
```

### 6.3 Graceful Degradation

When a non-critical component fails, continue with reduced functionality instead of aborting:

```python
# Monitoring script: if one data source fails, still check others
alerts = []
try:
    margin_data = call_mcp("account_get_portfolio", wallet=wallet)
    check_margin_buffer(margin_data, alerts)
except Exception as e:
    alerts.append({"level": "WARNING", "msg": f"margin_check_failed: {e}"})

try:
    positions = call_mcp("execution_get_positions", wallet=wallet)
    check_position_health(positions, alerts)
except Exception as e:
    alerts.append({"level": "WARNING", "msg": f"position_check_failed: {e}"})

# Still produces useful output even if one source failed
print(json.dumps({"alerts": alerts, "partial": True}))
```

---

## 7. Cron Job Best Practices

### 7.1 Cron Template Format

All crons use OpenClaw's `systemEvent` format:

```json
{
  "name": "{Skill Name} — {Job Name}",
  "schedule": { "kind": "every", "everyMs": 90000 },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "..."
  }
}
```

### 7.2 Mandate Text Pattern

Keep mandates short. Reference SKILL.md for rules.

```
{SKILL} {JOB}: Run `python3 {SCRIPTS}/{script}.py`, parse JSON.
{Conditional logic — 1-2 lines max}.
Apply {SKILL} rules from SKILL.md. Alert Telegram ({TELEGRAM}).
If no signals → HEARTBEAT_OK.
```

### 7.3 One Set of Crons, Scripts Iterate Internally

Don't create separate crons per instance/wallet/strategy. Scripts should load the config and iterate:

```python
from my_skill_config import load_all_instances

for key, cfg in load_all_instances().items():
    process(key, cfg)
```

### 7.4 Use Timeouts

Wrap script execution in `timeout` for heavy jobs:

```
Run `PYTHONUNBUFFERED=1 timeout 180 python3 scripts/heavy-scan.py`
```

### 7.5 Delete Dead Code

When a script is superseded, delete it. Don't keep legacy files "for reference" — they cause confusion about which file is canonical. Git history is the reference.

### 7.6 Document Field Name Gotchas

If a config field name differs from what you'd expect, document it explicitly:

```markdown
## Gotchas
- `stagnation.thresholdHours` is the correct key. Using `staleHours` will be silently ignored.
- `phase2.retraceFromHW` is a percentage — use `5` for 5%.
```

---

## 8. Defensive Coding

### 8.1 Validate Agent-Generated State

Values written by agents (LLMs) may have wrong signs, units, or types. Defend against this:

```python
# Agent may write negative values for fields that should be positive
retrace = abs(state["retraceThreshold"])

# Agent may use percent (5) or decimal (0.05) — handle both
retrace_decimal = retrace / 100 if retrace > 1 else retrace
```

### 8.2 Field-Aware Unit Conversion

Different API fields may return the same concept in different units. Check which field is present:

```python
# API v1 returns decimal (0.15), API v2 returns percentage (15)
raw = data.get("pct_of_total")
if raw is not None:
    pct = float(raw) * 100      # decimal -> percent
else:
    pct = float(data.get("percentage", 0))  # already percent
```

### 8.3 Safe Initialization

Initialize tracking values to neutral, not current:

```python
# BAD — if position starts at -2%, peak stays at -2% forever
peak_roe = state.get("peakROE", current_roe)

# GOOD — peak starts at 0, will be updated when ROE goes positive
peak_roe = state.get("peakROE", 0)
```

### 8.4 Config-Driven Magic Numbers

Never hardcode tunable constants. Read from config with defaults:

```python
# BAD
if minutes_elapsed > 90:  # hardcoded

# GOOD
max_minutes = config.get("phase1MaxMinutes", 90)
if minutes_elapsed > max_minutes:
```

---

## 9. Script Output Contract

Every script must output a single JSON object to stdout. This is the contract between the script and the cron mandate.

### 9.1 Success Case

```json
{
  "success": true,
  "signals": [...],
  "actions": [...],
  "summary": "2 entries, 1 close"
}
```

### 9.2 Nothing Actionable

```json
{
  "success": true,
  "heartbeat": "HEARTBEAT_OK"
}
```

### 9.3 Error Case

```json
{
  "success": false,
  "error": "market_data_fetch_failed: timeout after 3 attempts",
  "actionable": false
}
```

### 9.4 Verbose Mode

Gate diagnostic output behind an environment variable:

```python
VERBOSE = os.environ.get("{SKILL}_VERBOSE") == "1"

output = build_minimal_output(results)
if VERBOSE:
    output["debug"] = build_debug_output(results)

print(json.dumps(output))
```

---

## 10. Notification Consolidation

Send ONE notification per cron run, not one per signal/action.

```python
# BAD — 5 signals = 5 Telegram messages = spam
for signal in signals:
    send_telegram(f"Signal: {signal['asset']}")

# GOOD — 1 consolidated message
if signals:
    summary = ", ".join(f"{s['asset']} {s['direction']}" for s in signals)
    # Output for LLM to send: "Entered 3 positions: HYPE LONG, SOL SHORT, ETH LONG"
```

The script outputs the consolidated summary; the agent LLM sends the single Telegram.

---

## 11. Reuse Existing Data

Before making a new API call, check if the data is already available from a previous call in the same run.

```python
# BAD — separate API call just for volume trend
volume_trend = call_mcp("market_get_volume_trend", asset=asset)

# GOOD — compute from candles already fetched
def compute_volume_trend(candles_1h):
    mid = len(candles_1h) // 2
    prior_avg = sum(c["volume"] for c in candles_1h[:mid]) / max(mid, 1)
    recent_avg = sum(c["volume"] for c in candles_1h[mid:]) / max(len(candles_1h) - mid, 1)
    return recent_avg / prior_avg if prior_avg > 0 else 1.0
```

---

## 12. Safety & Monitoring

### 12.1 Widen Alert Thresholds

Set warning thresholds with generous buffer. It's better to alert early than to miss a critical event.

```python
# Conservative thresholds
MARGIN_WARNING = 50    # warn at 50%, not 30%
MARGIN_CRITICAL = 30   # critical at 30%, not 15%
```

### 12.2 Cross-Metric Alerting

Compare related metrics to catch dangerous states:

```python
# If liquidation is closer than the stop loss, the stop loss is useless
if liq_distance_pct < stop_loss_distance_pct:
    alerts.append({
        "level": "CRITICAL",
        "msg": f"Liquidation ({liq_distance_pct:.1f}%) closer than stop loss ({stop_loss_distance_pct:.1f}%)"
    })
```

### 12.3 Unused Variable Convention

Use `_` prefix for intentionally unused variables:

```python
for key, _ in load_all_strategies():   # don't need cfg here
    ...

def handler(_meta, _config, data):      # interface requires these params
    return process(data)
```

---

## Quick Reference Checklist

Before shipping a new skill, verify:

- [ ] `SKILL.md` has frontmatter, architecture, quick start, rules, API deps, cron setup
- [ ] Directory layout: `scripts/`, `references/`, `SKILL.md`
- [ ] All MCP calls go through `call_mcp()` helper — no direct `curl`
- [ ] All state writes use `atomic_write()` — no bare `open("w")`
- [ ] All external calls have 3-attempt retry with 3s delay
- [ ] Error output is structured JSON, not tracebacks
- [ ] Cron mandates are short, reference SKILL.md for detailed rules
- [ ] Model tier (Tier 1 / Tier 2) documented per cron
- [ ] `HEARTBEAT_OK` early exit when nothing actionable
- [ ] Default output is minimal; verbose behind env var
- [ ] Config has deep merge, backward-compatible defaults
- [ ] State files have `version`, `active`, `createdAt` fields
- [ ] Percentage convention documented (5 = 5%, not 0.05)
- [ ] One consolidated notification per cron run
- [ ] No hardcoded magic numbers — all from config with defaults
- [ ] No `shell=True`, no temp files, no `2>/dev/null`
- [ ] Dead/legacy code deleted (git history is the reference)
