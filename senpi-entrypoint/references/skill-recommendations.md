# Skill Recommendations

When the user asks "what skills should I install?" or "what should I use for [goal]?", fetch the live strategy catalog from the senpi-agent-tracker MCP (see `references/post-onboarding.md` → "Show me the strategies" for the canonical call) and match their goal to the table below.

Do NOT hardcode strategy names in this file. The active fleet changes; the table below is goal-to-pattern mapping, not goal-to-specific-strategy.

## Goal → Skill Mapping

| User goal | What to install | Notes |
|---|---|---|
| Build a new autonomous strategy from scratch | `senpi-trading-runtime` | The canonical runtime + DSL + Producer SDK. Use this when the user wants to author their own thesis rather than deploy an existing one. See `senpi-trading-runtime/SKILL.md` for the full build flow. |
| Deploy a proven autonomous trading strategy | Top strategy from live leaderboard (sorted by ROE) | Fetch with `get_leaderboard` MCP call and recommend the top result whose `min_budget` ≤ the user's balance. |
| Mirror a specific top trader (copy-trading) | `senpi-getting-started-guide` | Walks the user through `discovery_get_top_traders` + `strategy_create` to mirror one trader. The simplest first-trade path. |
| Run multiple strategies in parallel | Top N strategies from live leaderboard | One strategy per wallet, isolated capital. Slot count by budget; see Budget Guidance below. |

## Budget Guidance

Always fetch the live leaderboard via `get_leaderboard` and filter by each strategy's `min_budget` field. Recommend based on the user's balance:

| Balance | Recommendation |
|---|---|
| < $500 | Recommend funding to at least $500 before deploying any catalog strategy. Most active strategies have `min_budget` ≥ $500. |
| $500 – $2,000 | Any active strategy with `min_budget` ≤ balance. Lead with the current #1 by ROE. |
| $2,000 – $5,000 | Full catalog available. Lead with the current #1 by ROE. Multiple strategies in parallel become viable at this range. |
| > $5,000 | Full catalog + multi-strategy deployment. Consider 2–3 strategies with different theses for diversification. |

## Presenting a Recommendation

For each recommended strategy, include:
- Strategy name + one-line description (from `list_strategies` MCP)
- Minimum budget (from leaderboard `min_budget` field)
- Current ROE (from leaderboard `roe` field)
- Install command: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill <name> -g -y`

For the "Build a new strategy" path:
- Point them at `senpi-trading-runtime`'s SKILL.md as the build playbook
- Mention `senpi-trading-runtime/references/producer-patterns.md` as the catalog of scanner archetypes they can choose from
- Note: building a strategy requires Python familiarity and an LLM-decision-gate concept; not a 5-min path

## When Goal Is Unclear

Ask one question: **"Are you looking to deploy an existing strategy that's already running, mirror a specific trader you've identified, or build your own from scratch?"** — then map their answer to the table above.
