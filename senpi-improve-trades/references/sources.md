# Sources and engine runs — where each number comes from

The rules are in `SKILL.md` ("Sources — onchain vs runtime", "Run it in steps", "How to run"): never mix the
two sources, telemetry only from the current book, an empty discovery result is never "no trades", and the
analysis always comes from `review.py`. This file explains the mechanics behind them. Read it before
explaining where a trade, an exit reason or a fee $ came from. The last two sections copy the Sources section
and the fee $ paragraph of `SKILL.md` word for word, so a re-read on a follow-up finds them whole (on 5.7 a
follow-up keeps only the first ~15.9k chars of `SKILL.md`). `SKILL.md` is the rule; a test keeps the copies
the same.

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

## Copy: SKILL.md "Sources — onchain vs runtime", word for word

Two sources, two jobs. Never mix them, and never reconstruct one from the other.

- **Onchain trade facts → `discovery`.** The trade **list itself** + every onchain fact: asset, direction,
  entry/exit price, realized PnL, fees, timing, leverage, size. Discovery **owns** these — they are never
  re-derived from anything else.
- **Runtime / agent events → `telemetry` (the on-disk event log).** The facts discovery *can't* see because
  they left no onchain trace: each trade's **exit reason** (`dsl.closed` / `position.closed` close_reason +
  tier + roe), the **blocked/rejected signals** you never took (`signal.outcome`), and the **leak / exit-
  quality** reads (failed orders, protection gaps, risk halts, maker-vs-taker fills). Telemetry **enriches**
  the discovery trades — it fills `exit_reason` and produces the standalone streams; it **never** becomes the
  trade list and **never** re-derives a price or PnL.

**Telemetry is read ONLY from the current book.** A
closed strategy's exit reasons therefore come from the ratchet record or honest `UNKNOWN` — and the **durable
central event log** (keyed by strategy address, survives the close) is the recovery path for them, not the
ephemeral local ring.

**When telemetry is unavailable** (an older runtime build without the event RPC, or a closed strategy) the
engine **fails open to discovery**: the trades are still listed, and `exit_reason.terminal` falls back to the
ratchet record or honest `UNKNOWN`. Say **"exit mechanism not recorded on this build"** (or "…this strategy is
closed — its live event ring is gone; the durable log is the recovery path") — **never** report it as a bug.
`meta.telemetry_source` (`available` / `partial` / `unavailable`) and `meta.exit_reason_source_counts` tell you
exactly how much enrichment landed; surface that honestly.

**A ratchet-sourced exit never names who closed the trade.** On the fallback path the record is a DSL *ladder*
status, and `CLOSED_OFF_LADDER` means only that the ladder ended for a reason that was not its own stop — the
runtime closing on signal invalidation or a time cut, the agent calling `close_position`, and the user closing
on the venue are **indistinguishable** in it. Say **"not closed by its stop"**; never "you closed this",
"closed by hand" or "manual". And never print the raw status (`MANUALLY_CLOSED`, `CLOSED_EXTERNALLY`,
`EXCHANGE_SL_HIT`): those are wire values, not words for a reader.

**Closed strategies are recovered ON-CHAIN — the engine handles the trap for you.** So a closed strategy's trades and realized total come back **real** — an empty discovery result is never "no trades." You do **not** hand-read `strategy_get_pnl_and_account_value_history` for this. If `trades[]` is still empty after the on-chain fallback, the book genuinely never traded (guardrail 9). How the recovery works: `references/sources.md`.

## Copy: SKILL.md "Quick actions" — the fee $ paragraph, word for word

**Fee $:** `execution_quality` reports the maker-vs-taker **rate** today, not fee dollars. The
authoritative fee $ per order is a ledger join (`references/sources.md`). That per-order
join is the future upgrade; it's intentionally **not** called per-trade (rate-limit risk), so until it's
wired, quote the maker ratio as a fee-tier signal, never a fee dollar total. (`pnl_summary.fees` and the
trade rows do carry fee $.)
