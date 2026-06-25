# Always-loaded router — drop this into each agent's AGENTS.md

This is the **compact, always-in-context** version of the `senpi-help` directory. Put it in the
agent's `AGENTS.md` so the intent→skill map is present every turn — the agent never has to *decide to
look it up*. (`senpi-help` remains the full directory, loaded on demand for edge cases.) Cost: ~150
tokens/turn, against the ~24K saved by collapsing tools — a trivial trade that removes the "did the
agent consult help?" risk.

```markdown
## Senpi capabilities — routing (check before saying "I can't")

Match the request, then use the skill or tool. If unsure, read the `senpi-help` skill (full
directory). **Never tell a user a Senpi request is impossible without checking first.**

Reads → run the skill (it carries the data tools internally):
- portfolio / balances / positions / idle-vs-deployed → **senpi-portfolio**
- market read / what's moving / cross-asset / "the tape" → **senpi-market-pulse**
- smart money / whales vs the crowd / divergence → **senpi-smart-money**
- find or vet traders to copy → **senpi-trader-research** (default = FIND; `--trader <addr>` to vet one)
- points / tier / fees / Arena / referrals / wins → **senpi-account-status**
- what happened / history / why it failed → **senpi-audit** (`--strategy <id>` for one strategy)

Act now → first-class tools:
- wallet / quick balance / quick price / list strategies → user_get_me / account_get_portfolio /
  market_get_prices / strategy_list — **"current / my strategies" = ACTIVE only: call
  `strategy_list(status: ["ACTIVE"])`; never present CLOSED/PAUSED as current. Treat active strategies
  as known state, not a fresh discovery.**
- open / close / resize a position → create_position / close_position / edit_position
- create / top-up / close a strategy → strategy_create_custom_strategy / strategy_top_up / strategy_close
- withdraw / move funds / set a stop / cancel an order → the matching tool (may need enabling)

Pick / build / deploy a strategy → senpi-strategy-discover / -author / -ops
Anything else, or unsure which → read **senpi-help**.
```
