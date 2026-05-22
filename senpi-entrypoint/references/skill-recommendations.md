# Skill Recommendations

When the user asks "what skills should I install?" or "what should I use for [goal]?",
fetch the current catalog:

```bash
CATALOG=$(curl -s https://raw.githubusercontent.com/Senpi-ai/senpi-skills/refs/heads/main/catalog.json)
```

Then match their goal to the table below.

## Goal → Skill Mapping

> The live fleet changes frequently (new strategy skills ship most weeks). **Always pull the current list from `catalog.json` (above) or the `list_strategies` MCP tool before recommending** — do not rely on a hard-coded list. The mappings below are stable archetype examples, named only with strategies confirmed live in the repo.

| User goal | Recommended skill | Asset focus |
|---|---|---|
| BTC, maximum conviction, multi-timeframe trend | Grizzly (`grizzly`) | BTC |
| SOL alpha hunter, single-asset focus | Kodiak (`kodiak`) | SOL |
| ETH alpha hunter, hybrid hyperfeed + structure | Polar (`polar`) | ETH |
| HYPE, multi-signal confluence sniper | Cheetah (`cheetah`) | HYPE / multi |
| Multi-market active trader (crypto + XYZ) | Scorpion (`scorpion`) | Multi |
| Contrarian — fade crowded trades at exhaustion | Owl (`owl`) | Multi |
| Multi-market scanner, single strongest signal | Hawk (`hawk`) | Multi |
| Build a new strategy from scratch | **`senpi-trading-runtime`** — read [`references/strategy-creation.md`](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/senpi-trading-runtime/references/strategy-creation.md) first (self-contained fast path) | — |

For any goal not covered above, query `list_strategies` for the current live fleet and match on the strategy's own thesis/tagline.

## Budget Guidance

Each strategy declares its own minimum budget in `catalog.json` / `list_strategies`. General guidance:

| Balance | Recommended |
|---|---|
| < $500 | Fund to at least the lowest live strategy's `min_budget` before deploying. |
| $500–$2,000 | Most single-asset and multi-asset strategies are viable. |
| > $2,000 | Any live strategy, or a multi-skill deployment. |

## Presenting a Recommendation

For each recommended skill, include:
- Skill name + one-sentence description
- Minimum budget
- Install command: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill <name> -g -y`

## When Goal Is Unclear

Ask one question: **"Are you looking to follow smart money, trade a specific asset, or have the agent scan everything autonomously?"** — then map their answer to the table above.
