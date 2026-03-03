# About Senpi

Senpi is an agent-first trading platform on Hyperliquid that lets users discover
opportunities, automate strategies, and manage risk from one MCP-connected workflow.

## Summary Response Contract

Use this section only for explicit summary questions such as:
- "What is Senpi?"
- "Summarize Senpi"
- "Summarize skills/capabilities"
- "How do I install skills?"
- "What's new?"

Do not auto-insert this summary during normal onboarding.

Default response order (compact + actionable):
1. What Senpi is (one short definition)
2. Core capabilities
3. Compact skill snapshot
4. Install guidance
5. What's new (only if updates exist)
6. Closing question

Behavior rules:
- Use queued startup `UPDATE_OUTPUT` for "what's new" when available.
- Do not force a live update check in summary responses.
- If no updates are queued, do not mention "what's new" at all.
- Do not tell users to run CLI commands themselves; run install/update/list actions on their behalf.
- End with: "Want me to recommend which skills to install next and set them up for you?"

## Core Capabilities

- Discover high-performing traders and market opportunities (Discovery + market tools)
- Copy top traders or run autonomous/custom strategy workflows
- Apply risk controls such as dynamic stop-loss and budget-aware orchestration
- Trade broad markets through Senpi's Hyperliquid-based stack (crypto perps and more)

## Compact Skill Snapshot

### Protect Positions
- `dsl-dynamic-stop-loss`: two-phase trailing stop-loss with tiered locking
- `dsl-tight`: tighter DSL defaults for faster profit protection

### Find Opportunities
- `opportunity-scanner`: market-wide scoring and setup discovery
- `emerging-movers`: smart-money rotation detection

### Trade Autonomously or Copy
- `wolf-strategy`: full autonomous trading stack
- `autonomous-trading`: orchestrates DSL + scanner + movers
- `whale-index`: mirrors top-performing traders
- `wolf-howl`: nightly review and self-improvement loop (requires `wolf-strategy`)

## Install Skills

When users pick a skill, install it on their behalf with the skills CLI.
Do not ask them to run terminal commands themselves.

Default install action (agent-side):
- Run: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill <skill-name> -g -y`
- Then report what was installed and suggest the immediate next action.

For catalog refreshes, run the live list command agent-side and summarize results
instead of pasting raw output.

## What's New

Use queued updates from the entrypoint startup pending file
(`$SENPI_STATE_DIR/pending-skill-updates.json` when present). Do not force a
live check in summary responses.

If no updates are queued, skip this section in the reply.

## Platform Reference

After MCP is active, call
`read_senpi_guide(uri="senpi://guides/senpi-overview")` for full platform details
(wallets, strategies, tool categories, fees, workflows).
