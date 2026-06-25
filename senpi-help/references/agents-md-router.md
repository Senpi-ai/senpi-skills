# Always-loaded router — drop this into each agent's AGENTS.md

This is the **compact, always-in-context** version of the `senpi-help` directory. Put it in the
agent's `AGENTS.md` so the intent→skill map is present every turn — the agent never has to *decide to
look it up*. (`senpi-help` remains the full directory, loaded on demand for edge cases.) Cost: ~150
tokens/turn — cheap insurance that the agent routes to the right skill (or tool) first.

**No tools are denied or removed.** Routing is about *order of preference*, not access: prefer a skill
because it's cleaner and does the multi-step work for you, but every tool stays reachable. If this
runtime has **Tool Search** enabled (`tools.toolSearch` — `tool_search` / `tool_describe` /
`tool_call`, or `tool_search_code`), you discover and call any catalog tool on demand; if it isn't,
the tool is already in your list. Either way nothing is hidden. (How we reduce per-turn context —
enabling Tool Search vs. denying tools — is a separate decision; Tool Search is the lower-risk lever
because it keeps every tool reachable.)

```markdown
## Senpi capabilities — routing (check before saying "I can't")

**Find the capability in this order — then stop, don't cycle:**
1. **Senpi skill first.** Scan the skills index for one that matches the request; if it fits, read its
   SKILL.md and use it.
2. **Then `senpi-help`.** No clear skill? Read the `senpi-help` skill — its directory maps the intent to
   the right skill or tool.
3. **Then Tool Search (or the tool directly).** Still nothing? If Tool Search is enabled, use it —
   `tool_search` to find the tool, `tool_describe` to load its schema, `tool_call` to run it (or the
   `tool_search_code` bridge). If it isn't, the matching tool is already in your list — find its name
   in `senpi-help` and call it. Nothing is hidden either way.
4. **Cap it at ~5 attempts — do NOT keep cycling.** Don't grind for 10–20 minutes retrying the same
   thing. If you're still stuck after ~5 tries, **stop**: tell the user what you tried and suggest
   another path (an external source, a different framing, or one clarifying question). A clear "here's
   what I tried, here's another way" beats grinding silently.

**Never tell a user a Senpi request is impossible without going through steps 1–3 first.**

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
- withdraw / move funds / set a stop / cancel an order → the matching tool (already in your list)

Pick / build / deploy a strategy → senpi-strategy-discover / -author / -ops
Anything else, or unsure which → read **senpi-help**.
```
