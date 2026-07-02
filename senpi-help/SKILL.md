---
name: senpi-help
description: >-
  Directory of every Senpi capability and which skill or tool handles it. Read
  this when a user asks for something Senpi-related and no other skill clearly
  matches, when you are unsure how to do it, or when asked "what can you do?".
  Maps intents (analyze portfolio, find traders, manage stops, withdraw funds,
  audit history, build a strategy) to the right skill or tool — so no request
  is ever a dead end. Always available; consult it before giving up on a task.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.1.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Help — the capabilities directory

This is the **safety net.** If a user asks for something Senpi-related and no specific skill obviously
matches, come here, find the capability that fits the intent, and route there. **Never tell a user
something isn't possible without checking here first** — most capabilities are reachable, just behind
a skill or a tool you may not have in your current tool list.

Nothing about the catalog is written down in this file. It is read **live** from what's actually
installed, so it can never drift as skills are added, renamed, or removed.

## Step 1 — get the live skill directory

Run the engine. It reads the installed skill set (the realized manifest) straight from each skill's
own `description`, so the list is always current:

```bash
python3 scripts/help.py          # JSON: {skills_root, count, skills:[{name, use_for}]}
python3 scripts/help.py --md     # same, as a readable directory
```

Each row is a skill name plus what it's *for* (its trigger phrases). Match the user's intent to the
one whose `use_for` covers it, then read that skill's `SKILL.md` and run it. A skill almost always
beats calling the raw tools by hand — it packages the right multi-call workflow.

## Step 2 — if the intent is a raw tool, not a skill

Some capabilities are single MCP tools (a quick price, cancel an order, set a trailing stop, withdraw
funds, send USDC). The **authoritative tool catalog** — every tool and what it does — is the overview
guide:

```
read_senpi_guide(uri=senpi://guides/senpi-overview)
```

Load it, find the tool that matches the intent, and call it (with confirmation for anything that moves
money or changes a position).

> Some Senpi tools are intentionally kept out of the model's tool list to save context. They are still
> fully available. If the tool you need isn't in your current tool list, say the capability exists and
> proceed via the nearest skill, or note that it can be enabled. **A missing tool is never a missing
> capability.**

## How to route

1. Run `scripts/help.py` → match intent to a **skill**; read its `SKILL.md` and run it.
2. No skill fits but it's a direct action → find the **tool** in `senpi://guides/senpi-overview` and call it.
3. Compose when needed — a request can span more than one skill/tool.
4. Still no match after both? It may genuinely be out of scope — say so honestly. But check both first.

## Skill Attribution

Guide/utility skill — pure navigation. It performs no reads or mutations itself; it points to the
skill or tool that does. The directory is generated live from the installed skill set (`scripts/help.py`);
nothing is hardcoded here.
