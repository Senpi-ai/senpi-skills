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

Token burn is the single biggest operational cost.

### 2.1 Model Tiering
Classify each cron job: Primary (complex judgment), Mid (structured tasks), Budget (threshold checks).

### 2.2 Heartbeat Early Exit
If nothing actionable, output `HEARTBEAT_OK` immediately.

### 2.3 Minimal Default Output
Scripts return only fields the LLM needs. Verbose behind env var.

### 2.4 Prompt Compression
Cron templates reference SKILL.md, don't repeat rules.

### 2.5 Processing Order Directives
Explicit numbered steps prevent re-reads and loops.

### 2.6 Context Isolation
Read config ONCE, build plan, execute, ONE notification. Default to isolated sessions.

---

## 3. MCP Usage

- ALL calls through centralized `mcporter_call()` helper
- Envelope stripped in ONE place
- Temp file output, shlex.quote, timeouts
- Two variants: `mcporter_call` (raises) and `mcporter_call_safe` (returns None)
- Batch calls, reduce redundant calls, empty response guard

---

## 4. State Management

- Atomic writes via `atomic_write()` — never bare `open("w")`
- Re-read before write (race condition guard)
- State files have version, active, instanceKey, createdAt, updatedAt

---

## 5. Config Management

- Single source of truth: `{skill}_config.py`
- Deep merge for user overrides
- Backward-compatible defaults
- Percentage convention: whole numbers (5 = 5%), always /100

---

## 6. Retry & Resilience

- 3 attempts, 3s delay standard
- Structured error JSON (never raw tracebacks)
- Self-healing scripts with `action` field
- Graceful degradation

---

## 7. Cron Best Practices

- Main session (systemEvent, `text` key) vs Isolated (agentTurn, `message` key)
- Explicit if/then rules for Budget/Mid mandates
- One set of crons, scripts iterate internally
- Timeout wrapper for heavy jobs
- Slot guard pattern
- Setup script with parameterized model IDs

---

## 8. Defensive Coding

- Validate agent-generated state
- Config-driven magic numbers
- Backward-compatible key fallbacks
- API field name `or` fallbacks
- Path hygiene: strip prefixes before file paths

---

## 9. Script Output Contract

- Single JSON to stdout
- Success/heartbeat/error cases
- Verbose mode behind env var
- Action-only output filtering

---

## 10. Notifications

- ONE consolidated notification per cron run

---

## 12. Safety

- Wide alert thresholds
- Cross-metric alerting

---

## Quick Reference Checklist

- [ ] SKILL.md has frontmatter, architecture, quick start, rules, API deps, cron setup
- [ ] Directory layout: scripts/, references/, SKILL.md
- [ ] All MCP calls through centralized mcporter_call()
- [ ] Response envelope stripped in ONE place
- [ ] All state writes use atomic_write()
- [ ] All external calls have 3-attempt retry with 3s delay
- [ ] Structured error JSON output
- [ ] Cron mandates are short, reference SKILL.md
- [ ] Model tier + session type documented per cron
- [ ] HEARTBEAT_OK early exit
- [ ] Minimal default output; verbose behind env var
- [ ] Config: deep merge, backward-compatible defaults
- [ ] State files have version, active, createdAt
- [ ] Percentage convention: whole numbers, /100
- [ ] ONE notification per cron run
- [ ] No hardcoded magic numbers
- [ ] Slot guard pattern for resource limits
- [ ] shlex.quote on subprocess args; tempfile.mkstemp
- [ ] Prefixed identifiers stripped before file paths
- [ ] Script output contains only actionable items
- [ ] Placeholder scope hygiene ({WORKSPACE} vs {SCRIPTS} vs {SKILL})
