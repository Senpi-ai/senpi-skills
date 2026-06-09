# Strategy Intent Routing — classify, then route

When a user mentions a "strategy" / "predator" or a trading action, **FIRST classify the intent**, then
route. The word "strategy" is overloaded — same word, different paths — so routing must be deliberate.
A strategy (a.k.a. a **predator**) is a deployable **package**, not a skill; installing one means
provisioning a wallet + runtime + scanner daemon (owned by `senpi-strategy-ops`), not loading agent
knowledge.

## The intents (and the disambiguation rule)

| The user says… | Intent | Route to |
|---|---|---|
| "Buy me 10x HYPE long" · "short BTC" · "open 50% BTC / 50% ETH long" — **a specific position** (asset + direction + size) | **Operational** — execute now | MCP `strategy_create_custom_strategy` |
| "Copy this trader" · "mirror 0x…" — **explicit copy-trading** on a named target | **Operational** — copy | MCP `strategy_create` |
| "Install polar" · "deploy the spider strategy" · "run kodiak" · "set up the polar predator" · "install the polar predators strategy" — **a NAMED strategy/predator to run** | **Deploy a packaged strategy** | **`senpi-strategy-ops`** (resolves the id → gets a wallet → `senpi-helpers install <id>`) |
| "What should I trade?" · "recommend a strategy" · "help me pick a predator" · "set me up with a strategy" — **wants a strategy, no specific name** | **Strategic** — choose | **`senpi-strategy-discover`** (recommends 2–3 → hands the chosen id to ops) |
| "Build a strategy from scratch" · "make me a custom scanner" · "tune `<strategy>`'s exits" — **create/edit a strategy** | **Author** | **`senpi-strategy-author`** |
| Ambiguous | Ask first | the question below |

**If ambiguous, ASK before acting:**

> "Do you want to (a) open a specific position, (b) copy a specific trader, (c) install one of our
> ready strategies (predators), or (d) build a new one from scratch?"

That single question costs nothing and prevents the main failure mode.

## Path 1 — Operational (MCP, direct execution)

The user knows the position; the agent just executes.
- **Specific position(s):** `strategy_create_custom_strategy` with the assets + directions + leverage.
- **Copy a trader:** `strategy_create` with the trader address.

No recommendation, no template, no producer code. The user already picked.

## Path 2 — Lifecycle (the `senpi-strategy-*` skills)

For everything strategy-package-related, route to the lifecycle skill — it carries the full flow; this
router only points:
- **Install a named strategy/predator** → load **`senpi-strategy-ops`** (resolve the `id` from the
  registry/catalog → get the wallet → `senpi-helpers install`).
- **Help me choose / "what should I trade?"** → load **`senpi-strategy-discover`** (reads the registry
  catalog, recommends 2–3, hands the chosen `id` to ops).
- **Build or edit a strategy** → load **`senpi-strategy-author`**.

## What NEVER to do

- **Never default to `strategy_create_custom_strategy`** for "what should I trade?" / "help me pick" —
  that's `senpi-strategy-discover`. That MCP tool is only for a specific position the user named.
- **Never use the retired `install_skill` MCP tool to install a strategy.** Strategies are deployable
  packages now, installed via `senpi-strategy-ops` (`senpi-helpers install`), not copied as skills.
- **Never improvise positions** ("I think you should short HYPE") in response to a strategic query —
  the user wanted a strategy, not a manual basket.

## Quick reference

| Intent | Route |
|---|---|
| Open a specific position | MCP `strategy_create_custom_strategy` |
| Copy a named trader | MCP `strategy_create` |
| Install a named strategy / predator | `senpi-strategy-ops` |
| Help me pick a strategy | `senpi-strategy-discover` |
| Build / edit a strategy | `senpi-strategy-author` |
| Ambiguous | Ask (the four-option question above) |
