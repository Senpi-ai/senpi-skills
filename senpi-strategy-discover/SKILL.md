---
name: senpi-strategy-discover
description: >-
  Find and recommend a Senpi trading strategy to install. Use when the user asks
  "what should I trade?", "recommend a strategy", "help me pick a strategy", or
  wants to browse the strategy catalog by archetype, asset, budget, or risk.
  Hands off to senpi-strategy-ops to install the chosen one. NOT for building a
  strategy (senpi-strategy-author) or installing a named one directly.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Strategy Discover — find & recommend

The agent's job here: understand what the user wants, recommend **2–3** fitting strategies from the
registry, then hand the chosen one to **senpi-strategy-ops** for `install_strategy`.

## Flow

1. **Pull context** (optional but better): `account_get_portfolio` (budget/holdings),
   `market_get_funding_regime` (current regime).
2. **Read the registry** — the generated index of installable strategy packages:
   ```
   curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/strategy-v2/catalog.json
   ```
   Each entry: `id`, `name`, `emoji`, `tagline`, `group` (archetype), `risk_level`, `min_budget`,
   `version`. (Group by `group`, humanize the slug, sort by `sort_order`.)
3. **Filter to 2–3** by archetype / asset focus / risk appetite / budget. See
   `references/strategy-discovery.md` for the goal→archetype mapping and budget guidance.
4. **Present** each as: name + emoji, one-line tagline, archetype, suggested budget. Lead with your
   top pick and why it fits.
5. **Hand off**: when the user picks one, route to **senpi-strategy-ops**:
   `install_strategy(id=<chosen>, budget=<usd>, wallet="new")`. If nothing fits, offer to **build a
   new one** via **senpi-strategy-author**.

## Rules

- A strategy is a deployable **package**, not a skill — "install" means provisioning a wallet +
  runtime + scanner daemon (ops owns that), not loading agent knowledge.
- `min_budget` (~$100) is the platform floor and a comfortable suggestion, **not a hard gate** —
  position size scales with budget. Never dead-end a willing user at or above the floor.
- Do **not** invent quality tiers ("top picks", "best") — the registry differentiates by archetype
  and thesis only.
