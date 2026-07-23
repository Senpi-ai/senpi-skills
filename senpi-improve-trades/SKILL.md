---
name: senpi-improve-trades
description: >-
  Routing front door for retrospective trade review of the user's Senpi trading. Use this skill FIRST
  for ANY "review my trades" / realized PnL / trade-quality / "did I sell too early or late" / "why did
  my [asset] trade close" / "how are my exits firing / am I getting shaken out" / "where am I leaking" /
  "why is [strategy] losing" question, BEFORE any raw discovery_get_trader_history /
  execution_get_closed_position_details / market_get_prices MCP call. It does NOT do its own trade math
  — it routes: closed-trade quality, exit-reason attribution, premature-exit and leak findings, and the
  realized-PnL aggregate come from `composer review` (the box engine, relayed verbatim). Live
  open-position / unrealized / protection state comes from senpi-portfolio (`composer status`); market
  context / movers from senpi-market-pulse; whale comparison from senpi-smart-money. Acting on a fix
  (iterate, tune a DSL, close, copy-trade) routes to senpi-strategy-composer: this skill diagnoses, the
  composer changes the strategy. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "2.1.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Improve My Trades — routing to the machine render

This skill carries **prose and routing only**. It has no engine, no trade-quality math, no MCP client
of its own. Trade-review interpretation lives in **one place** — the composer's box engine, surfaced by
`composer review`. Your job is to route to it and **relay its render verbatim.** Never re-derive trade
quality, exit reasons, leaks, or PnL in prose.

## Flow

1. **Trade-review question** — "review my trades", "did I sell too early / how are my exits firing / am
   I getting shaken out", "why did my [asset] trade close", "where am I leaking", "what's my realized
   PnL / trade quality", "why is [strategy] losing". Run the **bare** review (sweeps every strategy):

   ```
   senpi_strategy  args: "composer review"          # (or: openclaw senpi composer review)
   ```

   The single cross-strategy trade-review producer over the box engine: per-strategy closed-trade
   summary (count / win-rate / realized PnL from backend trade history), exit-reason distribution +
   premature-exit cohort + leak findings from the runtime event log, recommendations, a `spec.thesis`
   mandate line per strategy, and a cross-strategy realized-PnL aggregate. Sources are NAMED in the
   render. **Relay the render verbatim.** Add `--json` if you need structured output.

2. **One named strategy** — a review scoped to a single strategy. Run:

   ```
   senpi_strategy  args: "composer review <strategy>"
   ```

   Same render, one strategy. **Relay verbatim** — never paraphrase or infer.

   **Closed / torn-down strategy, OR the render says the event ring is `unavailable` / `UNDETERMINED`:**
   the live ring is gone from the rim, but `runtime delete` (a `composer close` / `composer update`)
   ARCHIVED it on-box. Read the archived ring — the exit trail SURVIVES teardown:

   ```
   senpi_strategy  args: "composer review <wallet|runtime> --archived"
   ```

   Resolves the NEWEST archived event ring for that wallet or runtime id and renders the same review,
   banner-labelled ARCHIVED. If THAT yields nothing (an empty / absent archived ring, which it says
   honestly), the server-side `ratchet_stop_events(strategyId, wallet)` still retains the DSL event
   trail post-close — check it next. **Never conclude a closed strategy's exits are "unknowable" before
   BOTH the archived ring (`--archived`) AND `ratchet_stop_events` have been tried.**

3. **Live open-position / unrealized / protection state** — "what am I holding now", "are my winners
   running", "what tier / is it protected" → **senpi-portfolio** (`composer status`). Review covers
   *closed* trades only; unrealized/open state is portfolio's surface.

4. **Market context** — "what moved this window / what did I miss vs the market" → **senpi-market-pulse**
   (movers). "Compare me to the whales" → **senpi-smart-money**.

5. **Change a strategy** (iterate, tune a DSL tier / hard stop / entry gate, close, copy-trade setup) →
   **senpi-strategy-composer**. This skill diagnoses; the composer changes the strategy. Never auto-act:
   present the render's recommendations, offer to hand a concrete change to the composer, and stop.

6. **Walk me through / explain my [asset] trade** — the native per-asset lifecycle
   (`position.opened → dsl → close+reason`, threaded by position id):

   ```
   openclaw senpi explain <ASSET> --runtime <id> --json
   ```

   A closed / torn-down strategy's LIVE ring is gone, so `explain` returns nothing — that's expected,
   not a bug. To recover its exits, route to `composer review <wallet|runtime> --archived` (reads the
   archived on-box ring), then `ratchet_stop_events` — do NOT call the exits "unknowable" first.

## Never do

- **Never re-derive trade quality, exit reasons, leaks, or PnL in prose.** Quote the machine render.
  Raw `discovery_get_trader_history` / `execution_get_closed_position_details` / `market_get_prices`
  return un-attributed dumps that invite the exact failure modes this skill exists to prevent —
  hindsight-graded exits ("you sold too early / left $X on the table"), fabricated forward numbers
  (no "+$X/week"), blaming the user for what an autonomous strategy did, and re-derived aggregates.
  Only `composer review` attributes exits and computes the aggregate honestly. Route there.
- **Never re-implement analysis in a script.** This skill owns no engine. (A separately-versioned
  reimplementation shipped two field-proven defects — a zero-trades "telemetry unavailable" mislabel
  and a per-strategy CLI cold-boot timeout — and was retired. That is why this skill is narration only.)
- **Never upgrade an unavailable input to an all-clear.** If the render says an input is
  `unavailable`/`UNDETERMINED` (older build, live event ring gone because the strategy is closed,
  unreadable telemetry), relay that reason exactly — UNDETERMINED ≠ "no leaks / no gaps / all clear."
  For a CLOSED strategy specifically, the ring isn't lost: read the ARCHIVED ring with `composer
  review <wallet|runtime> --archived`, then `ratchet_stop_events`, before saying the exits can't be known.
- **Zero closed trades is a complete, correct result.** The render says "no closed trades yet — nothing
  to review"; relay it and stop. A fresh autonomous strategy is waiting for its first signal — do NOT
  pivot to setup/config nagging ("fund a position", "verify the DSL", generic slippage/leverage advice).
- **Never assert a trade's quality, exit mechanism, or PnL without quoting the surface that proves it.**
