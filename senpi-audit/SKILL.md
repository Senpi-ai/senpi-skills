---
name: senpi-audit
description: >-
  Answer "what happened?" — review the user's recent Senpi activity, a single
  strategy's change history, or investigate failures. Use for "what did my
  strategy do", "what happened yesterday", "show my activity", "why did that
  fail", "audit my account", "what trades were made", "recent actions". A
  hidden engine (scripts/audit.py) pulls and normalizes the audit trail; you
  summarize it. Requires a USER-scoped Senpi token. Not for live positions
  (senpi-portfolio) or market data (senpi-market-pulse).
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Audit — what happened

A hidden engine pulls the audit trail; **your job is to make it readable** — what the account (or a
strategy) did, and why, with failures surfaced.

## Golden rules

- **Run the engine; never hand-pull.** `python3 scripts/audit.py` (recent), `--strategy <id>` (one
  strategy's history), `--failures` (debugging), `--tool <name>` (one tool). Read its JSON.
- **Lead with the answer to "what happened," then the detail.** Summarize the activity (counts by
  action, the notable mutations), then list entries if asked.
- **Surface failures loudly.** Any `success: false` entry is a flag — lead with it when debugging.
- **Cite the reasoning.** Audit entries carry the agent's `reason` for each action — quote it; that's
  the "why" the user actually wants.
- **Times + tools in plain English.** Translate tool names to user language ("opened a position,"
  "topped up," "closed the strategy"); don't dump raw tool names.

## How to run

```
python3 scripts/audit.py [--strategy <id>] [--failures] [--tool <name>]
```

Returns `{entries, summary, meta}`:
- `entries[]` — `time`, `action_type` (read/create/update/delete), `tool`, `success`, `resource`,
  `reason`.
- `summary` — `total`, `by_action` (counts), `failures[]` (the failed ops).
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

1. **The headline** — what happened in the window, in one line ("3 positions opened, 1 strategy
   closed, no failures").
2. **Notable actions** — the mutations that matter (opens/closes/top-ups/updates), each with its
   `reason`.
3. **Failures** — any `success: false`, with the error, lead here if the user is debugging.
4. **(On `--strategy`)** a clean timeline for that one strategy.

## Mandatory closing (verbatim)

> **1. Want me to dig into any of these actions or failures?**
> **2. Want me to check the current state of your positions or strategies?**

CTA 2 → hand to **senpi-portfolio** (live positions) — the audit log is *history*; the portfolio is
*now*.

## ⚠ Token scope

USER-scoped `SENPI_AUTH_TOKEN` (defaults to the authenticated user). App-scoped → `meta.degraded`.

## Skill Attribution

Guide/analysis skill — read-only; it reviews history, it does not act.
