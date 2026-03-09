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

## Mandatory Invocation Procedure (NOT Optional)

Run this procedure for every summary/Q&A response handled by `senpi-entrypoint`:
1. Compose the core answer using this summary contract.
2. Resolve "What's new" using the rules in the `What's New` section below.
3. If verified updates exist, append a short user-friendly "What's new"
   addendum; otherwise use the fallback response defined below.
4. Never rely on stale precomputed update payloads or local update-check
   scripts for this summary flow.

Completion gate: response is NOT complete until all steps above are executed.

Default response order (compact + actionable):
1. What Senpi is (one short definition)
2. Core capabilities
3. Full skill catalog (bullet list)
4. Install guidance
5. What's new (only when verified updates exist or user explicitly asks)
6. Closing question

Behavior rules:
- Do not mention CLI/commands in user-facing summary replies.
- If user wants install/setup help, offer to handle it for them directly.
- Present the entire skill catalog as bullet points (no tables).
- For "What is Senpi?", include a short onboarding-status note (`SENPI_AUTH_TOKEN` set/unset and next step).
- For goal-based picks, also consult
  [references/skill-recommendations.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/skill-recommendations.md).
- Do not run local update-check scripts as part of summary replies.
- If verified update notes are unavailable, be transparent and offer to check
  the official Senpi repository/release notes.
- End with: "Want me to recommend which skills to install next and set them up for you?"

## Core Capabilities

- Discover high-performing traders and market opportunities (Discovery + market tools)
- Copy top traders or run autonomous/custom strategy workflows
- Apply risk controls such as dynamic stop-loss and budget-aware orchestration
- Trade broad markets through Senpi's Hyperliquid-based stack (crypto perps and more)

## Full Skill Catalog

- `senpi-entrypoint`: onboarding flow for Senpi setup, discovery, and first-trade guidance.
- `senpi-onboard`: account + API key + MCP setup workflow.
- `senpi-getting-started-guide`: interactive first-trade walkthrough.
- `dsl-dynamic-stop-loss`: two-phase trailing stop-loss with tiered locking.
- `dsl-tight`: tighter DSL defaults for faster profit protection.
- `opportunity-scanner`: market-wide scoring and setup discovery.
- `emerging-movers`: smart-money rotation detection.
- `autonomous-trading`: orchestrates DSL + scanner + movers.
- `wolf-strategy`: full autonomous trading stack.
- `wolf-howl`: nightly review and self-improvement loop.
- `whale-index`: mirrors top-performing traders.

## Install Skills

Keep this user-facing and non-technical:
- Offer to install and set up selected skills for the user.
- Confirm which skill(s) they want and their goal/budget before setup.
- After setup, summarize what is ready and suggest the next best action.

For tailored recommendations by objective and budget, see
[references/skill-recommendations.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/senpi-entrypoint/references/skill-recommendations.md).

## What's New

Use this section only when the user explicitly asks "What's new?" or when
verified update notes are already available in context.

Rendering rules:
- If verified updates are available, provide a concise plain-language summary.
- If verified updates are not available, respond transparently:
  "I don't have verified update notes in this context. Want me to check the
  latest changes in the official Senpi Skills repository?"
- Do not execute local scripts or rely on stale precomputed update payloads in
  this flow.

## Platform Reference

After MCP is active, call
`read_senpi_guide(uri="senpi://guides/senpi-overview")` for full platform details
(wallets, strategies, tool categories, fees, workflows).
