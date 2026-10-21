# Sources and engine runs — where each number comes from

The rules are in `SKILL.md` ("Sources — onchain vs runtime", "Run it in steps", "How to run"): never mix the
two sources, telemetry only from the current book, an empty discovery result is never "no trades", and the
analysis always comes from `review.py`. This file explains the mechanics behind them. Read it before
explaining where a trade, an exit reason or a fee $ came from.

## Discovery — the trade list

`discovery_get_trader_history` is the trade lister; `market_get_asset_data`
supplies the current price for the "if I'd held to now" counterfactual.

## Telemetry — only from the current book

**Telemetry is read ONLY from the current book.** The event-log ring lives on-disk with a running runtime, so
the engine shells `openclaw senpi events` **only for ACTIVE/PAUSED strategies** — a *closed* strategy's ring is
torn down with its runtime. (Probing a closed one isn't a hang — the gateway returns an immediate `NOT_FOUND` —
but every `openclaw senpi events` spawns a *second* CLI process, so fanning dozens of those process pairs
across the worker pool starved the CPU and pushed even *live* reads past the timeout: that's what once made a
whole review report "telemetry unavailable / every exit UNKNOWN." Not probing closed strategies is the fix.) A
closed strategy's exit reasons come from the ratchet record or honest `UNKNOWN`.

The event shapes: `references/event-log.md`.

## Closed strategies — recovered on-chain

**Closed strategies are recovered ON-CHAIN — the engine handles the trap for you.** `discovery_get_trader_history` returns **empty** once a strategy is closed/torn down — Senpi clears its own index, but the trades are **NOT gone** (Hyperliquid keys fills by wallet **address**, so they survive the close). The engine detects an empty discovery result and **falls back to on-chain HL fills**, rebuilding the real round-trips (`meta.closed_trade_source == "onchain_fills"`, wallets in `meta.onchain_recovered_wallets`); `realized_pnl` then comes from HL's own `closedPnl`.

## Fees in $ — what ships, and the pending ledger join

**Fees in $ now ship on both paths** — discovery rows carry `totalFees` (its `totalHyperliquidFees` +
`totalBuilderFees`; discovery splits those two, unlike HL's raw `fee`, which is already inclusive), on-chain
rows carry both legs of the round trip, and `pnl_summary` carries `fees` / `realized_net`. What remains below
is *reconciliation against the ledger*, not availability.

The one pending upgrade — authoritative fee $. The authoritative fee **$**
lives in the ledger — `order.filled` / `position.closed` carry `senpi.order.id`, which joins to
`execution_get_closed_position_details({closedOrderId})` → realized PnL + **fees** + funding.

## Engine runs — steps, flags, the sample

- `--window` / `--last` / `--no-market` / `--fixture` / `--full` apply to every step. Same fail-open contract
  as `all`: each step returns valid JSON with `meta.warnings` on partial data and never crashes on a
  missing/corrupt state file (it recomputes).
- `all` (the default with no step) composes every slice into one dict — the same output the engine always
  produced.
- **`trades[]` is a curated OUTLIER SAMPLE.** To keep the model-context payload small
  (the raw ledger on a big multi-strategy account is tens of thousands of tokens and blows the delivery
  timeout), the engine emits only the top handful of trades by biggest realized PnL + biggest hold-to-now
  delta — the ones a coach actually cites.
- **Scope the ask.** When the user names a trade count — "improve my last 10", "review my last 20 trades" —
  **pass `--last N`**: it's a GLOBAL bound (the N most-recent closed trades across the whole book), so the review
  stays about what they asked instead of the entire 7-day book. (The telemetry step still event-fetches every
  *current* strategy regardless of `--last` — those events also feed `missed_signals`/leaks — but a **closed**
  strategy is never probed, which is what removes the heavy fan-out on a large, churned book.)

## Why the analysis must come from `review.py`

The engine is the entire point — it computes the timing table, the exit reasons, the mandate reads,
and the current-vs-closed split *so the guardrails hold*. Free-styling on raw `strategy_list` /
`discovery_get_trader_history` / `ratchet_stop_list` reproduces the exact failure modes this skill exists to
prevent — "everything's unprotected," "consolidate the wallets," "dead weight," fabricated numbers.
