---
name: senpi-strategy-discover
description: >-
  Find and recommend a Senpi trading strategy (a.k.a. "predator") to install. Use
  when the user asks "what should I trade?", "recommend a strategy", "help me pick
  a strategy/predator", "which predators are there?", "browse the strategy
  catalog", or wants a strategy but has NOT named a specific one ("install a
  trading strategy", "set me up with a predator" — pick first, then hand off).
  Filters the catalog by archetype, asset, budget, or risk and hands the chosen
  one to senpi-strategy-ops to install. NOT for installing a NAMED strategy
  directly (that's senpi-strategy-ops) or building one (senpi-strategy-author).
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
5. **Hand off to senpi-strategy-ops** with the chosen `id`. Ops then: gets the wallet (create a new one
   via MCP `strategy_create_custom_strategy` — confirm budget ≥ $100 — or reuse an existing one), then
   runs `senpi-helpers install <id> --wallet <addr> --decision-model <model>`. (Ops does NOT create
   wallets and is not called as `install_strategy(...)`.) If nothing fits, offer to **build a new one**
   via **senpi-strategy-author**.

## Rules

- A strategy is a deployable **package**, not a skill — "install" means provisioning a wallet +
  runtime + scanner daemon (ops owns that), not loading agent knowledge.
- `min_budget` (~$100) is the platform floor and a comfortable suggestion, **not a hard gate** —
  position size scales with budget. Never dead-end a willing user at or above the floor.
- Do **not** invent quality tiers ("top picks", "best") — the registry differentiates by archetype
  and thesis only.
