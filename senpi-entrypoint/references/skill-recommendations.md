# Skill Recommendations

When the user asks "what skills should I install?" or "what should I use for [goal]?",
first run:

```bash
npx skills add https://github.com/Senpi-ai/senpi-skills --list
```

Then match their goal to the table below.

## Goal → Skill Mapping

| User goal | Recommended skill | Min budget |
|---|---|---|
| Protect profits on open positions / trailing stop loss | `dsl-dynamic-stop-loss` | $100 |
| Scan all markets for high-conviction setups | `opportunity-scanner` | $100 |
| Catch smart money moves early, before they hit the leaderboard | `emerging-movers` | $100 |
| Fully autonomous trading — no manual decisions needed | `wolf-strategy` ⭐ (includes DSL, Scanner, Emerging Movers) | $500 |
| Mirror the best-performing traders automatically | `whale-index` | $500 |
| Orchestrate DSL + Scanner + Emerging Movers on one budget | `autonomous-trading` | $500 |
| Nightly trade review and self-improvement loop | `wolf-howl` (requires `wolf-strategy`) | — |

## Presenting a Recommendation

For each recommended skill, include:
- Skill name + one-sentence description
- Minimum budget
- Install command: `npx skills add https://github.com/Senpi-ai/senpi-skills --skill <name> -g -y`

## Budget Guidance

- **Under $500** — steer toward `dsl-dynamic-stop-loss` or `opportunity-scanner` to start.
- **$500+** — `wolf-strategy` is the most complete autonomous option.

## When Goal Is Unclear

Ask one question: **"Are you looking to protect existing positions, find new ones,
or have the agent trade autonomously?"** — then map their answer to the table above.
