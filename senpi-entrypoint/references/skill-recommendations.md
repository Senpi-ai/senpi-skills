# Skill Recommendations

When the user asks "what skills should I install?" or "what should I use for [goal]?":

1. **Walk the Decision Tree in [`producer-patterns.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/producer-patterns.md#decision-tree--help-a-user-pick-their-first-strategy) — this is the canonical routing source** for which strategy to recommend.
2. **Cross-reference `catalog.json` for install metadata only** (name, tagline, install command, min_budget):

   ```bash
   CATALOG=$(curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/catalog.json)
   ```

   `catalog.json` is install/display metadata — **not** the routing source. When its structure suggests a different recommendation than the Decision Tree, the Decision Tree wins.

3. As a shortcut, the table below maps common goals to specific skills. The Decision Tree is more comprehensive (and handles the harder cases like "just pick something for me" or "bet against the Trump economy"); this table is for the easy direct asks.

## Goal → Skill Mapping (shortcut for common asks)

> The live fleet changes frequently (new strategy skills ship most weeks). **Always pull the current install metadata from `catalog.json` (above) or the `list_strategies` MCP tool before recommending** — do not rely on a hard-coded list. The mappings below are stable archetype examples, named only with strategies confirmed live in the repo. For anything beyond these — and especially for unsure / open-ended users — use the Decision Tree.

| User goal | Recommended skill | Asset focus |
|---|---|---|
**⚠️ Before matching: classify the user's intent.** *Operational* ("buy me HYPE 10x", "copy this trader") goes to MCP tools, not to a skill. *Strategic* ("help me pick", "what should I trade") goes to the template-first picker + build fallback. Full router: [`strategy-intent-routing.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-entrypoint/references/strategy-intent-routing.md).

**Operational intents — no skill, just MCP:**

| User goal | Path |
|---|---|
| Open a specific position ("Buy me HYPE 10x", "go short BTC at 3x") | MCP `strategy_create_custom_strategy` — execute directly, no template |
| Copy a named trader ("mirror 0x...", "copy this OG") | MCP `strategy_create` — copy-trader, no template |

**Strategic intents — template-first, then build fallback:**

| User goal | Recommended skill | Asset focus |
|---|---|---|
| **Help me pick a strategy / what should I trade / recommend a strategy** | **Run the picker flow** (see [`post-onboarding.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-entrypoint/references/post-onboarding.md)): pull balance + regime, **walk the [Decision Tree in `producer-patterns.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/producer-patterns.md#decision-tree--help-a-user-pick-their-first-strategy) (canonical routing source)**, cross-reference `catalog.json` for install metadata, recommend 2–3 fleet templates, install via `install_skill`. **Always offer build-from-scratch as the last option.** | — |
| BTC, maximum conviction, multi-timeframe trend | Grizzly (`grizzly`) | BTC |
| SOL alpha hunter, single-asset focus | Kodiak (`kodiak`) | SOL |
| ETH alpha hunter, hybrid hyperfeed + structure | Polar (`polar`) | ETH |
| HYPE, multi-signal confluence sniper | Cheetah (`cheetah`) | HYPE / multi |
| Multi-market active trader (crypto + XYZ) | Scorpion (`scorpion`) | Multi |
| Contrarian — fade crowded trades at exhaustion | Owl (`owl`) | Multi |
| Multi-market scanner, single strongest signal | Hawk (`hawk`) | Multi |
| Build a new autonomous strategy from scratch (no fleet template fits) | **`senpi-trading-runtime`** — read [`references/strategy-creation.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/strategy-creation.md) first (self-contained fast path) | — |

For any goal not covered above, query `list_strategies` for the current live fleet and match on the strategy's own thesis/tagline.

## Budget Guidance

Each strategy declares a `min_budget` in `catalog.json` / `list_strategies`. **It is a suggested comfortable size, not a hard gate** — position size scales with budget via `margin_pct`, so any balance at or above the ~$100 platform floor can deploy any strategy. Never refuse a willing user over budget; offer to start small instead.

| Balance | Recommended |
|---|---|
| < $100 | Fund to ~$100 first — the platform floor to open any position at all. |
| $100–$500 | Any strategy — start small; positions are smaller but the logic is identical. |
| > $500 | Any strategy at comfortable sizing, or a multi-skill deployment. |

## Presenting a Recommendation

For each recommended skill, include:
- Skill name + one-sentence description
- Minimum budget
- Install command: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill <name> -g -y`

## When Goal Is Unclear

Ask one question: **"Are you looking to follow smart money, trade a specific asset, or have the agent scan everything autonomously?"** — then map their answer to the table above.
