# Strategy Intent Routing — operational vs. strategic

**When a user mentions a "strategy" or trading action, FIRST classify the intent.** Two paths, very different code, very different docs. Picking the wrong one is the failure mode. The word "strategy" is overloaded in this product — same word, opposite paths — so the routing has to be deliberate.

## The two intents (and the disambiguation rule)

| The user says... | They mean... | Path |
|---|---|---|
| "Buy me 10x HYPE long" · "Open a short on BTC" · "Go long ETH at 3x" · "Open a strategy with 50% BTC long / 50% ETH long" — **any specific position, asset + direction + size** | **Operational** — execute a position right now | **MCP `strategy_create_custom_strategy`** (multi-asset positions the runtime then manages). Purely functional — no recommendation, no template, no producer code. |
| "Copy this trader" · "Mirror 0x..." · "Follow this user's trades" — **explicit copy-trading intent on a named target** | **Operational — copy** | **MCP `strategy_create`** (mirror an existing trader) |
| "Help me pick a strategy" · "What should I trade?" · "Build a trading strategy" · "Recommend a strategy" · "I want to start trading but don't know how" · "Help me decide" — **open-ended strategy intent, no specific position named** | **Strategic** — pick a fleet template, or author a new one | **Template-first picker flow** (below). **Never** default to `strategy_create_custom_strategy` here. |

**If the intent is ambiguous, ASK the user before acting:**

> "Do you want to (a) open a specific position you already have in mind, (b) copy a specific trader, or (c) get help choosing or building a trading strategy from our catalog?"

That single question costs nothing and prevents the entire failure mode this doc exists to address.

## Path 1 — Operational (MCP, direct execution)

The user knows what they want; the agent just executes.

- **Specific position(s):** call `strategy_create_custom_strategy` with the assets + directions + leverage they specified.
- **Copy a trader:** call `strategy_create` with the trader address they named.

**No template recommendation, no fleet pitch, no producer code, no `strategy-creation.md`.** The user already picked. Don't overthink it.

## Path 2 — Strategic (template-first picker → build fallback)

When the user wants help **deciding** — not executing a known position — the default flow is **always recommend a template from our fleet first**, then offer to build a new one if no template fits. The fleet templates are validated, deployable, and exist precisely so users don't start from zero.

### Picker flow (default)

1. **Pull user context** in one parallel read-only batch:
   - `account_get_portfolio` → available balance
   - `market_get_funding_regime` → current regime (sanity-check the thesis)
   - From the conversation: any expressed preference (asset bias, risk appetite, horizon)

2. **Walk the Decision Tree in [`producer-patterns.md`](../../senpi-trading-runtime/references/producer-patterns.md#decision-tree--help-a-user-pick-their-first-strategy) — this is the canonical routing source.** It encodes the conversational flow (Layer 0 default paths, Layer 1 belief routing, Layer 2 sub-branches) end-to-end. Use Layer 0A's *"Express Lane"* for "just pick something for me," Layer 2G for thesis funds, Layer 2H for hedge funds, etc. The tree is what tells you *which* strategy to recommend.

3. **Cross-reference `catalog.json` for install metadata only** (name, tagline, emoji, install command, min_budget, predators_url):
   ```bash
   CATALOG=$(curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/catalog.json)
   ```
   `catalog.json` is **not** the routing source — when its `_instructions` or field structure suggests a different recommendation than the Decision Tree, the Decision Tree wins. Use the catalog for the install half: which `install_skill skill_name=<x>` to run, what min_budget to flag, what tagline to display in the recommendation message.

4. **Filter to 2–3 templates that fit:**
   - Walk the Decision Tree from Layer 1 down to a leaf — that's your candidate set
   - If the user named an asset, asset focus aligns (BTC → Beaver/Grizzly; HYPE → Hummingbird/Wolverine/Stag; etc.)
   - Current regime is consistent with the strategy's thesis (don't recommend a funding-fade in a flat funding regime)
   - `min_budget` is a **soft sizing hint, not a filter** — never drop a strategy for being above the user's balance; positions scale via `margin_pct`, so flag "you're under the suggested size, positions will be smaller — start small or fund more" and let the user decide

5. **Present each template** with: name + tagline, archetype, asset focus, min budget, the one-line install command (`install_skill skill_name=<name>` via MCP). Two or three options — not the whole catalog. Pick the best fits.

6. **End with the build-from-scratch offer, every time:**

   > "Or, if none of these fit your thesis, we can build a new autonomous strategy from scratch — that path is in [`strategy-creation.md`](../../senpi-trading-runtime/references/strategy-creation.md). Which do you want?"

7. **User picks a template → install via `install_skill`.** Done.

8. **User picks "build new" → route to [`strategy-creation.md`](../../senpi-trading-runtime/references/strategy-creation.md).** That doc is self-contained — read it once, build the bundle, hand off to the operator to deploy.

## What NEVER to do

- **Never default to `strategy_create_custom_strategy` for an ambiguous "what should I trade?" or "help me pick" query.** That tool is for specific positions the user named. Using it as the default for strategic queries surfaces a manual-position basket where the user wanted *strategy recommendations* — the exact failure mode this doc exists to prevent.
- **Never skip the template recommendation.** Always surface fleet templates first when the intent is strategic. Building from scratch is the fallback, not the default.
- **Never improvise positions** ("I think you should short HYPE/ETH/BTC") in response to a strategic query. That's the operational tool's job and the user didn't ask for a manual position basket.
- **Never read `strategy-creation.md` for an operational request.** It's the authoring path for new autonomous agents — wrong layer entirely if the user just wants to open a position.

## Quick reference

| Intent | First step | Tool / doc |
|---|---|---|
| Open a specific position | Execute it | MCP `strategy_create_custom_strategy` |
| Copy a named trader | Execute it | MCP `strategy_create` |
| Help me pick a strategy | Walk the Decision Tree, install with catalog metadata | Decision Tree in `producer-patterns.md` (routing) + `catalog.json` (install metadata) → `install_skill` |
| Build a new strategy from scratch | Read the build doc | [`strategy-creation.md`](../../senpi-trading-runtime/references/strategy-creation.md) |
| Ambiguous | Ask the user to disambiguate | (this doc's three-option question) |
