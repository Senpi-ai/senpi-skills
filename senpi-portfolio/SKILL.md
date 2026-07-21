---
name: senpi-portfolio
description: >-
  Routing front door for the user's portfolio, strategies, positions, holdings, balances, DSL
  protection, PnL and trade history. Use this skill FIRST for ANY portfolio / strategies / positions
  / holdings / balances / "are my positions protected? / do they have a stop-loss?" / "what tier is
  my position in?" / "what happened to my closed [asset]" question, BEFORE any raw
  strategy_get_clearinghouse_state / account_get_portfolio / strategy_list / ratchet_stop_list MCP
  call. It does NOT do its own state math — it routes: cross-strategy portfolio state and
  per-position protection come from `composer status` (the box engine, relayed verbatim); account
  cash/balances/deposits from senpi-account-status; realized PnL / trade quality from
  senpi-improve-trades. Strategy-lifecycle actions (deploy, iterate, close, copy-trade) route to
  senpi-strategy-composer. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Portfolio — routing to the machine render

This skill carries **prose and routing only**. It has no engine, no state interpretation, no MCP
client of its own. State interpretation lives in **one place** — the composer's box engine, surfaced
by `composer status`. Your job is to route to it and **relay its render verbatim.** Never re-derive
portfolio, protection, or position state in prose.

## Flow

1. **Portfolio / strategy-state question** — "analyze my portfolio", "how are my strategies doing",
   "show my positions", "are my positions protected / do they have a stop-loss", "what tier is my
   position in", "what happened to my closed [asset]". Run the **bare** portfolio view:

   ```
   senpi_strategy  args: "composer status"          # (or: openclaw senpi composer status)
   ```

   This is the single cross-strategy PORTFOLIO producer over the box engine: every strategy
   (canonical + legacy layout) with lifecycle stage, wallet, runtime state, and per-position
   protection quoted from the engine (armed tier, floor, exchange-stop id), plus any stranded /
   unmanaged runtimes with recovery text. **Relay the render verbatim.** Add `--json` if you need
   structured output.

2. **Per-strategy detail** — a question about ONE named strategy. Run:

   ```
   senpi_strategy  args: "composer status <strategy>"
   ```

   The per-strategy diagnosis front door (lifecycle chain + live scanners + protection + risk
   gates). **Relay verbatim** — never paraphrase or infer. `--json` available.

3. **Account-level cash / balances / deposits / idle** — how much money, where it sits, deposit or
   withdraw → **senpi-account-status** / backend MCP reads. Reads are always legitimate.

4. **Realized PnL / trade quality / why-it-closed / where-am-I-leaking** → **senpi-improve-trades**.

5. **Change a strategy** (deploy, fund, iterate, update, close, copy-trade setup) →
   **senpi-strategy-composer**. This skill reports state; the composer changes it.

## Never do

- **Never interpret a raw backend read into a lifecycle or protection claim.** Composer strategies
  look like bare `CUSTOM` strategies on the backend — `strategy_list` / `strategy_get_clearinghouse_state`
  / `ratchet_stop_list` do NOT carry lifecycle stage or the config DSL, so a hand-rolled read makes a
  running, protected strategy look unmanaged or every sub-Tier-1 position look "unprotected." Only
  `composer status` reconciles this. Route there.
- **Never re-derive protection / position / lifecycle state in prose.** Quote the machine render. If
  the render says a section is `unavailable (<reason>)`, relay exactly that — never fill the gap with
  a guess, and never upgrade "unavailable" to a reassuring all-clear.
- **Never re-implement state math in a script.** This skill owns no engine. (A separately-versioned
  reimplementation of DSL/state interpretation shipped three field-proven bugs and was retired — that
  is why this skill is narration only.)
- **Never assert "it's live / protected / deployed" without quoting the surface that proves it.**
