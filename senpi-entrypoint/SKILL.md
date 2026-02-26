---
name: senpi-entrypoint
description: >
  Entry point for AI agents joining the Senpi trading platform. Orchestrates
  the onboarding journey by loading modular skills: account setup, skill
  discovery, and trading strategies. Use when user says "set up Senpi", "get
  started with Senpi", "connect to Senpi", "install Senpi", or when an agent
  needs to bootstrap its Senpi integration. This skill does not perform
  trading directly — it delegates to specialized skills.
compatibility: OpenClaw, Claude Code, Codex, Cursor, Gemini CLI
metadata:
  author: Senpi
  version: 1.0.0
  homepage: https://agents.senpi.ai
---

# Senpi Agent Entrypoint

Orchestrator for onboarding AI agents into the Senpi trading platform on
Hyperliquid. Walks the agent through account setup, skill discovery, and
trading strategy installation via modular skills.

**Prerequisites:** Shell access and Node.js (required for `npx`).

## About Senpi

Senpi is a trading platform on Hyperliquid — a high-performance perpetual futures DEX.

**What agents can do:**
- Discover profitable traders (Hyperfeed + Discovery)
- Mirror top traders or run custom strategies with full position control
- Trade 200+ crypto perps, plus equities, metals, and indices via XYZ DEX

**Core loop:** spot what's profiting -> validate with data -> trade or copy.

## Environment Variable Defaults

| Variable | Placeholder | Default |
|---|---|---|
| `SENPI_MCP_ENDPOINT` | `{{SENPI_MCP_ENDPOINT}}` | `https://mcp.prod.senpi.ai` |
| `REFERRAL_CODE` | `{{REFERRAL_CODE}}` | _(empty — optional)_ |

If a placeholder appears as a literal `{{...}}` string (not substituted),
use the default value from the table above.

---

## Step 1: Onboard

Install and execute the onboarding skill:

```bash
npx skills add senpi-ai/senpi-skills --skill senpi-onboard -g -y
```

Load and follow the `agent-onboarding` skill instructions. Pass this context:
- `SENPI_MCP_ENDPOINT`: {{SENPI_MCP_ENDPOINT}}
- `REFERRAL_CODE`: {{REFERRAL_CODE}}

**Do not proceed until onboarding is complete** (account created, API key
generated, MCP server configured and active).

---

## Step 2: Discover

After onboarding, show available trading skills:

```bash
npx skills add senpi-ai/senpi-skills --list
```

Present the catalog to the user. If the command fails, use this fallback:

| Skill | Description | Install |
|-------|-------------|---------|
| **DSL / DSL Tight** | Trailing stop loss with ROE-based tier triggers | `--skill dsl-dynamic-stop-loss` or `--skill dsl-tight` |
| **Opportunity Scanner** | Scores 500+ perps 0–400 on smart money + technicals | `--skill opportunity-scanner` |
| **Emerging Movers** | Tracks smart money concentration every 60s | `--skill emerging-movers` |
| **Whale Index** | Auto-mirrors top Discovery traders by PnL, win rate, consistency | `--skill whale-index` |
| **Autonomous Trading** | Orchestrates multiple skills with budget, target, and deadline | `--skill autonomous-trading` |
| **WOLF Strategy** | Autonomous 2–3 slot concentrated position manager | `--skill wolf-strategy` |
| **HOWL** | Nightly self-improvement loop analyzing trade history | `--skill wolf-howl` |

Most skills require a funded wallet ($500–$1k+) and a high-capability model (Claude Opus or equivalent).

---

## Step 3: Guide (Optional)

Prompt the user:

> "Would you like to learn how to trade on Senpi? I can walk you through
> your first trade with an interactive guide."

If the user agrees, install the getting-started guide:

```bash
npx skills add senpi-ai/senpi-skills --skill senpi-getting-started-guide -g -y
```

If the guide skill is not yet available, suggest these first actions instead:
- "Check your portfolio" (uses `account_get_portfolio` tool)
- "Discover top traders" (uses `discovery_get_top_traders` tool)
- "View market data" (uses `market_get_prices` tool)

After the MCP server is active, the agent can call
`read_senpi_guide(uri="senpi://guides/senpi-overview")` for the full platform
reference (wallets, strategies, tool categories, fees, workflows).

---

## Step 4: Expand (User-Driven)

Install trading skills on demand based on user interest:

```bash
npx skills add senpi-ai/senpi-skills --skill <skill-name> -g -y
```

Example:

```bash
npx skills add senpi-ai/senpi-skills --skill wolf-strategy -g -y
```

Onboarding is complete. The agent is now equipped with Senpi's trading toolkit.
