---
name: senpi-entrypoint
description: >
  Onboard AI agents into the Senpi trading platform on Hyperliquid. Walks
  through account creation, API key setup, MCP server configuration, skill
  discovery, and first-trade guidance. Use when user says "set up Senpi",
  "get started with Senpi", "connect to Senpi", "install Senpi",
  "onboard to Senpi", or when an agent needs to start trading on Hyperliquid
  via Senpi.
---

# Senpi Agent Entrypoint

Onboard an AI agent into the Senpi trading platform on Hyperliquid via
modular skills: account setup, skill discovery, and trading strategies.

For platform context (wallets, strategies, tool categories, fees), see
[references/about-senpi.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/staging/senpi-entrypoint/references/about-senpi.md).


If any `npx` command fails, consult
[references/error-handling.md](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/staging/senpi-entrypoint/references/error-handling.md) for recovery
steps.

## Environment Variable Defaults

| Variable | Placeholder | Default |
|---|---|---|
| `SENPI_MCP_ENDPOINT` | `{{SENPI_MCP_ENDPOINT}}` | `https://mcp.prod.senpi.ai` |
| `REFERRAL_CODE` | `{{REFERRAL_CODE}}` | _(empty — optional)_ |

If a placeholder appears as a literal `{{...}}` string (not substituted),
use the default value from the table above.

---

## Step 1: Onboard

Install the onboarding skill:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-onboard -g -y
```

Load and follow the `senpi-onboard` skill instructions. Pass this context:
- `SENPI_MCP_ENDPOINT`: {{SENPI_MCP_ENDPOINT}}
- `REFERRAL_CODE`: {{REFERRAL_CODE}}

**Gate — verify before proceeding:**
1. Senpi account exists
2. API key is generated
3. MCP server is configured and responds to a tool call

---

## Step 2: Discover

List available trading skills:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --list
```

Present the catalog to the user. If the command fails, point the user to the
official [Senpi Skills repository](https://github.com/Senpi-ai/senpi-skills)
to browse available skills.

**Gate — verify before proceeding:** User has seen the skill catalog.

---

## Step 3: Guide (Optional)

Ask the user:

> "Would you like to learn how to trade on Senpi? I can walk you through
> your first trade with an interactive guide."

If the user agrees, install the getting-started guide:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill senpi-getting-started-guide -g -y
```

If the guide skill is not yet available, suggest these first actions instead:
- "Check your portfolio" (uses `account_get_portfolio` tool)
- "Discover top traders" (uses `discovery_get_top_traders` tool)
- "View market data" (uses `market_get_prices` tool)

---

## Step 4: Expand (User-Driven)

Install additional trading skills on demand based on user interest:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill <skill-name> -g -y
```

Example:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --skill wolf-strategy -g -y
```

Onboarding is complete. The agent is now equipped with Senpi's trading
toolkit and can install more skills as needed.

