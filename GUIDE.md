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

# GOOD — MCP call via centralized helper (see Section 3.2)
data = mcporter_call("market_get_prices")
prices = data.get("prices", {})
```

**Why MCP over direct APIs:**
- MCP handles auth, rate limiting, and caching
- Consistent error format across all tools
- Single abstraction layer — if the upstream API changes, only MCP needs updating
- No secrets (API keys, endpoints) in your skill code

### 3.2 Unified MCP Call Helper

Create **one** centralized helper in your shared config module (`{skill}_config.py`). All scripts import and use this — no script should invoke `mcporter` directly.

```python
import json, os, subprocess, time, tempfile, shlex

def mcporter_call(tool, retries=3, timeout=30, **kwargs):
    """Call a Senpi MCP tool via mcporter. Returns the `data` portion of the response.

    Args:
        tool: Tool name (e.g. "market_get_prices", "close_position").
        retries: Number of attempts before giving up.
        timeout: Subprocess timeout in seconds.
        **kwargs: Tool arguments as key=value pairs.

    Returns:
        The `data` dict from the MCP response envelope (envelope already stripped).

    Raises:
        RuntimeError: If all retries fail or the tool returns success=false.
    """
    args = []
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, (list, dict, bool)):
            args.append(f"{k}={json.dumps(v)}")
        else:
            args.append(f"{k}={v}")

    mcporter_bin = os.environ.get("MCPORTER_CMD", "mcporter")
    cmd_str = " ".join(
        [shlex.quote(mcporter_bin), "call", shlex.quote(f"senpi.{tool}")]
        + [shlex.quote(a) for a in args]
    )
    last_error = None

    for attempt in range(retries):
        fd, tmp = None, None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            subprocess.run(
                f"{cmd_str} > {tmp} 2>/dev/null",
                shell=True, timeout=timeout,
            )
            with open(tmp) as f:
                d = json.load(f)
            if d.get("success"):
                return d.get("data", {})       # ← strip envelope here
            last_error = d.get("error", d)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
            last_error = str(e)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        if attempt < retries - 1:
            time.sleep(3)

    raise RuntimeError(f"mcporter {tool} failed after {retries} attempts: {last_error}")


def mcporter_call_safe(tool, retries=3, timeout=30, **kwargs):
    """Like mcporter_call but returns None instead of raising on failure."""
    try:
        return mcporter_call(tool, retries=retries, timeout=timeout, **kwargs)
    except RuntimeError:
        return None
```

**Key design decisions:**
- **Temp file, not `capture_output`** — mcporter output can exceed pipe buffer limits on large responses. Writing to a temp file avoids silent truncation.
- **`shlex.quote` on all args** — prevents shell injection even with `shell=True`.
- **Envelope stripped in one place** — callers never see `{success, data}`. They get the inner `data` dict directly.
- **Two variants** — `mcporter_call` raises on failure (for critical paths), `mcporter_call_safe` returns `None` (for best-effort fetches).
- **`tempfile.mkstemp` not `mktemp`** — `mktemp` is deprecated (race condition). `mkstemp` atomically creates the file.

### 3.3 Response Envelope Parsing

MCP responses are wrapped in a `{success, data}` envelope. The centralized helper strips it (see 3.2), so callers work directly with the inner data. But the inner data itself has tool-specific nesting that callers must handle.

```python
# The raw MCP response looks like:
# {"success": true, "data": {"prices": {"BTC": "65000", "ETH": "3500", ...}}}
#
# After mcporter_call strips the envelope, you get:
# {"prices": {"BTC": "65000", "ETH": "3500", ...}}
#
# Callers extract the specific field they need:
prices = data.get("prices", {})
```

**Common extraction patterns (after envelope is stripped):**

```python
# market_get_prices → nested under "prices"
data = mcporter_call("market_get_prices")
prices = data.get("prices", {})

# market_list_instruments → nested under "instruments"
data = mcporter_call("market_list_instruments")
instruments = data.get("instruments", [])

# leaderboard_get_markets → double-nested
data = mcporter_call("leaderboard_get_markets")
markets = data.get("markets", {}).get("markets", [])

# leaderboard_get_top → double-nested differently
data = mcporter_call("leaderboard_get_top", limit=100)
traders = data.get("leaderboard", {}).get("data", [])

# clearinghouse state → separate sections
data = mcporter_call("strategy_get_clearinghouse_state", strategy_wallet=wallet)
crypto = data.get("main", {})
xyz = data.get("xyz", {})
```

**The #1 bug pattern:** Forgetting to strip the envelope (doing `data["prices"]` on the raw response → `KeyError`) or double-stripping (adding `.get("data", raw)` when the helper already stripped it → missing data). Pick ONE place to strip the envelope and never strip it again.

### 3.4 Batch MCP Calls

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

### 3.5 Reduce Redundant API Calls

If a single API call returns data for multiple contexts, use it instead of making separate calls:

```python
# BAD — two separate calls for crypto and equities
crypto = mcporter_call("get_clearinghouse_state", wallet=w)
xyz = mcporter_call("get_clearinghouse_state", wallet=w, dex="xyz")

# GOOD — one call returns both sections
data = mcporter_call("get_clearinghouse_state", wallet=w)
crypto = data.get("main", {})
xyz = data.get("xyz", {})
```

When processing similar data from multiple sections, extract a shared helper:

```python
def _extract_positions(section_data):
    """Extract non-zero positions from a clearinghouse section."""
    positions = {}
    for p in section_data.get("assetPositions", []):
        pos = p.get("position", {})
        coin = pos.get("coin")
        szi = float(pos.get("szi", 0))
        if not coin or szi == 0:
            continue
        positions[coin] = {
            "direction": "SHORT" if szi < 0 else "LONG",
            "size": abs(szi),
            # ... other fields
        }
    return positions

# One helper, two calls — no duplicated parsing
crypto_positions = _extract_positions(data.get("main", {}))
xyz_positions = _extract_positions(data.get("xyz", {}))
```

### 3.6 Subprocess Safety

- **`shlex.quote()` all arguments** — when using `shell=True` for output redirection, quote every variable part of the command to prevent injection.
- **`tempfile.mkstemp` for temp files** — atomically creates a unique file. Always clean up in a `finally` block.
- **Always set `timeout`** — prevent hung processes: `timeout=15` for lightweight calls, `timeout=30` for data-heavy calls, `timeout=60` for batch operations.
- **Capture errors, don't silence them** — surface error info so callers can decide how to handle it:

```python
# BAD — error silently returns empty, caller can't distinguish "no data" from "call failed"
except Exception:
    return {}

# GOOD — error info returned for caller to handle
except Exception as e:
    return {}, {}, f"fetch failed: {e}"
```

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

### 6.3 Self-Healing Scripts

When a script can detect AND fix an issue, do it in the script — don't rely on the LLM to interpret the problem and run manual fixes. Include an `action` field so the cron mandate knows what already happened:

```python
# Script detects orphaned state and auto-fixes
if orphan_detected and not had_fetch_error:
    raw["active"] = False
    raw["closeReason"] = "externally_closed_detected_by_healthcheck"
    atomic_write(dsl_file, raw)
    issues.append({
        "type": "ORPHAN_DSL",
        "action": "auto_deactivated",       # ← script already fixed it
        "message": f"{asset} DSL was active but no position found -- auto-deactivated"
    })
elif orphan_detected and had_fetch_error:
    issues.append({
        "type": "ORPHAN_DSL",
        "action": "skipped_fetch_error",    # ← script couldn't safely fix
        "message": f"{asset} DSL appears orphaned but skipping due to fetch error"
    })
```

**Why:** Tier 1 models are unreliable at multi-step remediation. Moving fix logic into the script makes it deterministic. The cron mandate just routes the `action` field to "alert" or "ignore."

### 6.4 Graceful Degradation

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

### 7.2 Mandate Text Pattern — Explicit Rules for Lower-Tier Models

Cron mandates run on the cheapest model possible (Tier 1). These models **cannot make judgment calls** — they need deterministic, per-case rules. Never say "apply rules from SKILL.md" in a Tier 1 mandate.

```
# BAD — vague, requires judgment (Tier 1 model will hallucinate)
"Parse JSON. Apply WOLF SM rules from SKILL.md for judgment calls.
If hasFlipSignal=false → HEARTBEAT_OK."

# GOOD — explicit per-case branching (Tier 1 model follows steps)
"Parse JSON. For each alert in `alerts`:
if `alertLevel == "FLIP_NOW"` → close that position, alert Telegram ({TELEGRAM}).
Ignore alerts with `alertLevel` of WATCH or FLIP_WARNING (no action needed).
If `hasFlipSignal == false` or no FLIP_NOW alerts → HEARTBEAT_OK."
```

```
# BAD — "fix immediately" is vague
"CRITICAL issues → fix immediately, alert Telegram."

# GOOD — explicit per-type instructions
"Fix issues by type:
- auto_created → DSL was missing, script created it. Alert Telegram.
- auto_deactivated → Orphan DSL deactivated. No alert needed.
- alert_only → Script could not auto-fix. Alert Telegram.
If no issues → HEARTBEAT_OK."
```

**Rule of thumb:** If the mandate says "judgment" or "apply rules," it's too vague for Tier 1. Rewrite with explicit `if/then` per output field.

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

Values written by agents (LLMs) may have wrong signs or types. Defend against this:

```python
# Agent may write negative values for fields that should be positive
retrace = abs(state["retraceThreshold"])

# Enforce percentage convention — always whole numbers, always /100
retrace_decimal = retrace / 100
```

### 8.2 Prefer Extending Functions Over Creating New Ones

When a new context needs the same data (e.g., a second DEX), add a parameter to the existing function instead of creating a new one:

```python
# BAD — separate function per context (code duplication)
def fetch_crypto_prices(): ...
def fetch_xyz_prices(): ...

# GOOD — one function with optional parameter
def fetch_all_mids(dex=None):
    kwargs = {"dex": dex} if dex else {}
    data = mcporter_call("market_get_prices", **kwargs)
    return data.get("prices", {})

# Usage
crypto_prices = fetch_all_mids()
xyz_prices = fetch_all_mids(dex="xyz")
```

### 8.3 Safe Initialization

Choose defaults carefully — they depend on the field's semantics:

```python
# Tracking fields (peak, high water) — default to current value
# BAD — peak starts at 0, never reflects the actual entry state
peak_roe = state.get("peakROE", 0)

# GOOD — peak starts at current ROE, tracks upward from actual entry
peak_roe = state.get("peakROE", current_roe)

# Counter fields (breaches, failures) — default to zero
breach_count = state.get("currentBreachCount", 0)
fetch_failures = state.get("consecutiveFetchFailures", 0)
```

**Rule:** Tracking values (peaks, high water marks) default to the current observed value. Counters default to zero.

### 8.4 Config-Driven Magic Numbers

Never hardcode tunable constants. Read from config with defaults:

```python
# BAD
if minutes_elapsed > 90:  # hardcoded

# GOOD
max_minutes = config.get("phase1MaxMinutes", 90)
if minutes_elapsed > max_minutes:
```

### 8.5 Backward-Compatible Key Fallbacks

When config keys get renamed, chain `.get()` calls so old configs still work:

```python
# Field was renamed from "staleHours" to "thresholdHours"
stale_hours = cfg.get("thresholdHours", cfg.get("staleHours", 1.0))
```

This is cheaper than writing a migration and avoids breaking existing deployments.

### 8.6 API Field Name Conventions

External APIs may return the same field under different naming conventions or different field names across versions/endpoints. Handle with `or` fallbacks:

```python
# snake_case vs camelCase for same field
lev = inst.get("max_leverage") or inst.get("maxLeverage")

# Different field names across API versions for the same concept
asset = m.get("token") or m.get("asset") or m.get("coin", "")
traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
```

When the same value has different **units** across fields, check which field is present:

```python
# pct_of_top_traders_gain is decimal (0.15), pnlContribution is already percent (15)
raw_pnl = data.get("pct_of_top_traders_gain")
if raw_pnl is not None:
    pnl_pct = float(raw_pnl) * 100      # decimal → percent
else:
    pnl_pct = float(data.get("pnlContribution", 0))  # already percent
```

### 8.7 Percentage Convention — Always Divide by 100

Never write "smart" detection logic that guesses whether a value is a percentage or a decimal:

```python
# BAD — ambiguous, breaks when value is exactly 1
retrace_decimal = retrace / 100 if retrace > 1 else retrace

# GOOD — enforce "all percentages are whole numbers" and always divide
retrace_decimal = retrace / 100
```

Enforce the convention at the config layer (see Section 5.5) and trust it in the code. If the value is wrong, fix the config — don't add branching logic.

### 8.8 Path Hygiene with Prefixed Identifiers

When identifiers carry scope prefixes (e.g., `xyz:BTC`), strip them before using as file paths:

```python
# Coin might be "xyz:AAPL" — can't use colon in filenames
clean_coin = coin.replace("xyz:", "")
path = os.path.join(state_dir, f"dsl-{clean_coin}.json")
```

Always strip prefixes at the boundary where you create file paths, not inside business logic.

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
- [ ] All MCP calls go through centralized `mcporter_call()` — no direct subprocess invocations
- [ ] Response envelope (`{success, data}`) stripped in ONE place (the helper), never re-stripped
- [ ] Tool-specific nested data extraction documented per tool used
- [ ] All state writes use `atomic_write()` — no bare `open("w")`
- [ ] All external calls have 3-attempt retry with 3s delay
- [ ] Errors surfaced (not silently swallowed) — callers can distinguish "no data" from "call failed"
- [ ] Error output is structured JSON, not tracebacks
- [ ] Cron mandates are short, reference SKILL.md for detailed rules
- [ ] Model tier (Tier 1 / Tier 2) documented per cron
- [ ] `HEARTBEAT_OK` early exit when nothing actionable
- [ ] Default output is minimal; verbose behind env var
- [ ] Config has deep merge, backward-compatible defaults
- [ ] Renamed config keys have chained `.get()` fallbacks for backward compatibility
- [ ] State files have `version`, `active`, `createdAt` fields
- [ ] Percentage convention: whole numbers only (5 = 5%), always `/ 100` — no guessing logic
- [ ] One consolidated notification per cron run
- [ ] No hardcoded magic numbers — all from config with defaults
- [ ] Redundant API calls eliminated — use single calls that return multiple sections
- [ ] Shared helpers for duplicated parsing logic (DRY)
- [ ] `shlex.quote()` on all subprocess args; `tempfile.mkstemp` for temp files with `finally` cleanup
- [ ] Prefixed identifiers (e.g., `xyz:BTC`) stripped before use in file paths
- [ ] Dead/legacy code deleted (git history is the reference)
