---
name: senpi-portfolio
description: >-
  Analyze the user's portfolio, strategies, positions, and trades across all wallets — main embedded
  wallet, strategy sub-wallets, deployed vs idle — with real-time balances and real analysis, not a flat
  dump. Leads at the STRATEGY level: each strategy judged against its OWN mandate (is it doing its job?),
  with positions as evidence. Use this skill FIRST for ANY portfolio / strategies / positions / balances
  / PnL / trade-history question, BEFORE any raw strategy_get_clearinghouse_state / account_get_portfolio
  / strategy_list MCP call. Use for "analyze my strategies", "how are my strategies doing", "analyze my
  portfolio", "how am I doing", "show my positions", "balance across all wallets", "how much is idle", and
  "are my open positions protected? / do they have a stop-loss?". A hidden engine (scripts/portfolio.py)
  does the multi-wallet pull and taxonomy; you narrate. Requires a USER-scoped Senpi token.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.2.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Portfolio — real-time, all-wallet analysis

You are a sharp portfolio analyst. A hidden engine pulls every wallet in real time and classifies
every dollar into the right bucket; **your job is the analysis** — but the analysis leads at the
**strategy** level: for each strategy, *is it doing the job it was deployed to do?* Positions are
evidence for that verdict, not the headline. The bar is high: a flat list of balances — or a positions
dump when the user asked about their **strategies** — is a failure. The user wants a read.

> **Strategy-first, judged against each strategy's OWN mandate.** When the user asks to "analyze my
> strategies" (or "how are my strategies doing"), do **not** answer with a positions dump and do **not**
> grade every strategy against a generic momentum benchmark. Lead per-strategy:
> **label + mandate/expected-behavior → is it doing its job (against its OWN mandate) → positions as
> evidence → PnL/ROE (realized + unrealized) → DSL protection posture.** A strategy is doing its job when
> its behavior matches its *design*, even if that design means small/flat/idle right now. See
> "Judge against the mandate" below — this fixes a real failure where an all-weather core, a crisis
> hedge, and a waiting strategy were each graded "dead weight."

> **Use this skill FIRST — before any raw MCP.** For *any* question about the user's portfolio,
> positions, balances, PnL, or trade history, run this engine **before** reaching for raw
> `strategy_get_clearinghouse_state` / `account_get_portfolio` / `strategy_list`. Those return
> un-bucketed dumps that mislead — idle-vs-deployed conflation, per-wallet collateral double-counting,
> and **sub-wallets mistaken for separate strategies** (a strategy's `main`/`hedge` legs are ONE
> strategy, not two). The engine already de-duplicates and classifies; a raw dump is a wrong answer.

## The wallet model (get this exactly right)

Every user has **one main (embedded) wallet**. Funds flow: **embedded wallet → strategy sub-wallet →
positions.** Each strategy is an isolated sub-wallet; **no strategy trades from the embedded wallet.**

Every dollar is in exactly one of **three buckets** — and the #1 mistake is conflating them:

| Bucket | What it is | Engine field |
|---|---|---|
| **Idle in embedded** | Truly free cash in the main wallet — HL USDC + EVM USDC. Deploy it into a strategy or withdraw it to your bank. | `totals.idle_in_embedded` |
| **Idle in strategies** | Free margin sitting *inside* a strategy wallet, not yet in a position — waiting for a signal. | `totals.idle_in_strategies` |
| **Deployed in positions** | Margin actively backing open trades. | `totals.deployed_in_positions` |

**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**

### Cross-DEX: main and xyz are ONE wallet, not two

A strategy wallet's clearinghouse state has a `main` (crypto) view and an `xyz` (equities/metals)
view. **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is
**shared** and reported *identically* in both views — so it is counted **once**, never summed. Each
view's `accountValue` = that shared idle + only *that* DEX's position equity, so
`wallet_value = main.av + xyz.av − shared_idle`. The engine already de-duplicates this; you just read
`account_value` / `idle_withdrawable` / `deployed` per strategy. **Never add the two views' account
values or withdrawables yourself** — that double-counts the shared collateral (the bug that inflated a
$3.1K account to $5.6K).

### The trap you must never fall into

`total_withdrawable` from the portfolio API is **idle-in-strategies** (bucket 2) — the unused margin
summed across strategy wallets. **It is NOT idle cash in the embedded wallet.** If a user moved all
their funds into strategies, the embedded wallet is **$0** even when `total_withdrawable` is large.
The engine computes these as two separate fields precisely so you don't mix them. When you say "$X is
idle," **always say *where*** — "$X idle in the embedded wallet, ready to deploy or withdraw" vs. "$Y
sitting in strategy wallets waiting for signals." They are not the same money and not the same thing.

## Judge each strategy against its OWN mandate — not a momentum benchmark

This is the core of the analysis. Every strategy was deployed to do a *specific* job. "Is it working?"
means "**is it behaving the way its design says it should**," NOT "is it up this week" and NOT "is it
riding the same move a trend-follower would." Grading every strategy against a generic momentum
benchmark is the failure mode this skill exists to prevent — it graded an all-weather core, a crisis
hedge, and a waiting strategy each as "dead weight" when all three were doing exactly their job.

**Get the mandate first, then judge.** Before you call any strategy good or bad, know what it was *for* —
and get that from the **source of truth, not memory.** The engine already does the lookup for you:

- **`strategies[].mandate`** — the engine reads each deployed strategy's **`strategy.yaml`** (via the
  catalog, keyed by `skill_name`) and attaches its declared job: `belief_plain` (the plain-English
  mandate), `thesis` (the edge), `archetype`/`archetype_label`, `sub_style`, `direction`,
  `asset_classes`, `risk_level`, `time_horizon`. This is versioned with the deploy and can't go stale —
  **read `mandate.belief_plain`, state the strategy's job in the user's terms, then judge against it.**
- **Do not reconstruct the mandate from memory or from what the positions *look* like.** The
  `strategy.yaml` is authoritative; a strategy's open book is *evidence about* whether it's on-mandate,
  never the definition of the mandate.

If `mandate` is `null` (a custom strategy with no package, or the catalog was unreachable — see
`meta.catalog_source`), say the mandate is unknown and judge conservatively on behavior — do **not**
default to a momentum yardstick.

**Anti-patterns — these exact misreads happened live; never repeat them:**

- **A risk-parity / all-weather core is NOT "misaligned" or "dead weight."** Diversified, low-turnover,
  and *uncorrelated to the rotations* is the design, not a flaw. It is supposed to sit calm while
  faster books churn. Judge it on drawdown control and steadiness, not on whether it caught this week's
  move.
- **A tail-risk / crisis hedge is NOT "wrong-way" for being small or flat in calm markets.** Its job is
  "lose a little in calm, win big in a crisis." A small negative carry while everything is quiet is the
  *premium being paid* for the payout — it's working as designed. Only a hedge that fails to pay off in
  an actual crisis is broken.
- **A selective strategy with NO open position is NOT a "ghost" or "dead."** Most selective/contrarian
  strategies do nothing most days by design — they wait for a specific signal (crowding + exhaustion, a
  range break, a copy-trigger) that is usually absent. `deployed == 0` and `positions == []` means
  **waiting for its signal**, not broken. Say "flat, waiting for its setup," never "idle dead weight."

**Then judge honestly.** Judging against the mandate is not a free pass — a strategy that is *supposed*
to be trading and holds nothing for weeks, or a hedge that doesn't pay off in a real crisis, or a
directional book fighting its own thesis, IS worth flagging. The point is to grade against the right
yardstick, not to excuse everything.

## Golden rules

- **Run the engine; never hand-pull balances.** `python3 scripts/portfolio.py` enumerates the
  embedded wallet + every strategy sub-wallet, pulls live clearinghouse state per wallet, and
  classifies the buckets. Read its JSON.
- **Real-time, always.** The engine forces a fresh fetch (no 12h cache) and reads each strategy's
  live clearinghouse state. Never report balances from earlier in the conversation — re-run.
- **Always say which wallet / which bucket.** Every dollar figure gets a location. "Idle" is
  meaningless without "idle *where*."
- **Lead at the strategy level, judged against the mandate.** For each strategy: state its
  **mandate** (the engine attaches it as `strategies[].mandate` — read from the strategy's `strategy.yaml`,
  its `belief_plain`), then whether it's **doing its job against that mandate**, *then* positions as
  evidence. Positions-first is the failure mode — the agent kept answering "analyze my strategies" with a
  raw positions dump. See "Judge each strategy against its OWN mandate" above.
- **Analyze, don't dump.** Positions are *evidence*, not the headline. For every position, compare it to
  the market (`market_24h_pct`, `vs_market`): is this short *working* because the asset is falling, or
  *fighting* a rally? Read net exposure, concentration, idle drag. See `references/analysis-framework.md`.
- **Use leveraged return, not raw price %.** Cite `return_on_equity_pct` (uPnL / margin), the number
  that actually reflects the position — a 1% price move at 10x is a 10% return on margin.
- **Report realized PnL + closed trades, not only open ones.** Each strategy carries a `closed` block —
  `realized_pnl` (total booked PnL over the recent history pull) and `recent[]` (last few closed
  trades: asset, direction, realized pnl, closed time). A strategy flat right now may have *already
  booked* real gains; report both realized and unrealized. If `closed.realized_pnl` is `null`, the
  history read failed (see `meta.warnings`) — say realized PnL is unavailable, don't imply zero.
- **Surface the protection posture per strategy.** Each strategy carries `protected` (bool): `True`
  when it was template-deployed (has a `skill_name`) ⟹ it ships a built-in DSL exit by construction.
  State it as posture ("template-deployed, DSL-protected"). This is config-level, not a live
  per-position DSL-tracking check — for that, use the DSL coverage check below.
- **Don't infer "wiped out" from a low balance.** Check `total_funded` / `total_withdrawn` — a
  strategy can show a small balance because profits were withdrawn (`netFunded` can be negative). That
  is not a loss.
- **"Current / my strategies" = ACTIVE only — never CLOSED.** The engine already filters
  `strategy_list(status=["ACTIVE"])`, so closed strategies are excluded by construction. If you ever
  reach for `strategy_list` directly, pass `status: ["ACTIVE"]` — a bare call returns CLOSED/PAUSED too
  and they must not be presented as current. Mention PAUSED strategies only if relevant, clearly
  labeled "paused," never as active.
- **Present active strategies as known state, not a fresh discovery.** Pull the data quietly and state
  what's running as established fact ("Your two active strategies are…"). Don't narrate the lookup
  ("let me check… oh, I see you have…") — that reads like you didn't already know your own book.
- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Every
  strategy deployed from a Senpi template runs a built-in DSL exit (trailing stop) + risk guard-rails,
  enforced every tick. Never tell a user to "add a 10–15% SL via `strategy_update`" on a deployed
  strategy — it already has one. To *verify* protection, use the DSL coverage check (next section); never
  infer "no stop" from the absence of a resting stop order (DSL exits are runtime-managed, not resting
  orders).
- **Always end with the two CTAs** (below), verbatim.

## Are my positions protected? (stop-loss / DSL coverage)

When the user asks **"are my open positions protected? / do they have a stop-loss?"**, give the DSL
coverage verdict per position — **PROTECTED / UNPROTECTED / STOP-NOT-ON-VENUE**. Key trap: an
unprotected position shows up as an **absence** in `senpi dsl positions` (it lists only *tracked*
positions), so you must **reconcile the open set against the tracked set** — an open position missing
from `dsl positions` is UNPROTECTED. Full procedure:
[`senpi-trading-runtime/references/dsl-protection-check.md`](../senpi-trading-runtime/references/dsl-protection-check.md).

## How to run the engine

```
python3 scripts/portfolio.py [--no-market]
```

Returns `{totals, embedded_wallet, strategies, exposure, signals, meta}`:
- `totals` — the three buckets + `grand_total_usd`, `unrealized_pnl`, and a `reconciles` flag (cross-
  checks the per-wallet sum against the portfolio aggregate; if `false`, say the numbers don't tie out
  and lead with the per-wallet figures).
- `embedded_wallet` — `address`, `idle_hl_usdc`, `evm_usdc[]` (per chain), `spot_usd`, `idle_total`.
- `strategies[]` — per strategy: `name`, `wallet`, `account_value`, `idle_withdrawable` (bucket 2 for
  *this* strategy), `deployed` (equity tied up in positions = account_value − withdrawable),
  `position_margin` (initial margin detail), `total_funded`/`total_withdrawn`, and:
  - `skill_name` / `skill_version` — the strategy's package attribution (e.g. `ox`, `cougar`, `lion`),
    from its `strategy_list` record. `null` for a hand-rolled/custom strategy with no package.
  - `mandate` — **the strategy's declared job, read from its `strategy.yaml`** (via the catalog, keyed by
    `skill_name`): `belief_plain` (plain-English mandate), `thesis` (the edge), `archetype`/
    `archetype_label`, `sub_style`, `direction`, `asset_classes`, `risk_level`, `time_horizon`, `name`,
    `tagline`. **This is the yardstick — judge the strategy against `mandate.belief_plain`, not memory
    and not a momentum benchmark.** `null` for a custom strategy or when the catalog was unreachable
    (`meta.catalog_source` records `local`/`remote`/`null`).
  - `protected` (bool) — `True` when `skill_name` is present ⟹ template-deployed ⟹ ships a built-in
    DSL exit (validator invariant). Config-level protection posture, not a live per-position check.
  - `closed` — `{realized_pnl, trade_count, recent[]}` from a read-guarded `discovery_get_trader_history`
    on the strategy wallet: `realized_pnl` (total booked PnL over the recent pull), `trade_count`, and
    `recent[]` (last few closed trades: `asset`, `direction`, `realized_pnl`, `entry_px`, `exit_px`,
    `closed_time`). On a read failure `realized_pnl` is `null` and a `meta.warnings` entry is added —
    treat as "realized PnL unavailable," never as zero.
  - `positions[]` (asset, dex, direction, leverage, notional, margin, `upnl`, `return_on_equity_pct`,
    `liq_px`, `market_24h_pct`, `vs_market`).
- `exposure` — `net_notional_usd` + `net_bias`, gross long/short, `by_asset_net_usd`,
  `largest_position`.
- `signals` — `idle_drag_pct` (how much capital isn't working), `deployed_pct`,
  `largest_position_pct_of_deployed` (concentration).
- The engine **fails open** — partial data still returns valid JSON with `meta.warnings`.

## Output contract

Order matters: **strategy verdicts lead; positions are evidence underneath them.** (When the question
is purely "how much / where is my money," you can open with the money map instead — but for anything
about "my strategies / how am I doing," lead with the per-strategy read.)

1. **Total + the three buckets.** `grand_total_usd`, broken into idle-in-embedded / idle-in-strategies
   / deployed — each labeled by *where*. Keep it tight; this is the money map, not the analysis.
2. **Per-strategy verdict (the real value).** For **each** strategy, in this order:
   1. **Label + mandate.** Its name and what it was deployed to *do* — from `strategies[].mandate`
      (its `strategy.yaml` `belief_plain`, keyed by `skill_name`). "cub is a K-shaped long/short
      dispersion book — long the structural winners, short the laggards; the P&L is the spread."
   2. **Is it doing its job — against its OWN mandate.** Not vs a momentum benchmark. A hedge that's
      flat in calm, an all-weather core that's steady-not-flashy, a selective strategy waiting with no
      position — all **working as designed**. See "Judge each strategy against its OWN mandate."
   3. **Positions as evidence.** The open positions that *show* it's on-mandate: direction, leveraged
      return (`return_on_equity_pct`), and **vs the market** (`market_24h_pct`, `vs_market`) — "short
      ETH, +11% on margin, *with* today's 4% selloff." Flag any fighting the tape / near `liq_px` /
      oversized. A strategy with `positions == []` and `deployed == 0` is **waiting for its signal** —
      say that, don't call it dead.
   4. **PnL — realized + unrealized.** Booked `closed.realized_pnl` (+ a couple of `closed.recent[]`
      trades) *and* open `upnl`. A flat strategy may have already banked real gains.
   5. **Protection posture.** `protected` ⟹ template-deployed, DSL-protected by construction.
3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest
   position), idle drag (capital sitting in cash), and the overall posture — is this book hedged,
   directional, mostly in cash? Compare the net tilt to where the broader market is.
4. **The two CTAs** (next section).

Formatting: group by strategy; show `Δ%` and leveraged return; emoji sparingly (🟢/🔴 for green/red
books). Show strategy wallet addresses in short form (`0x35d1...acb1`) unless asked for full.

## Mandatory closing (verbatim)

> **1. Want me to rebalance or adjust any of these positions?**
> **2. Want me to put the idle capital to work in a new strategy?**

- **CTA 1 → position management.** Route to the execution tools (`edit_position` / `close_position` /
  `strategy_update`) for the specific position — confirm before any change; never trade unprompted.
- **CTA 2 → deploy idle.** If there's meaningful idle capital (lead from `signals.idle_drag_pct`),
  offer to hand it to **senpi-strategy-discover** / **senpi-strategy-author** — fund a new strategy
  from the embedded idle, or top up an existing one from `strategy_top_up`. Propose; never deploy
  without confirmation.

## Resilience (engine handles; narrate honestly)

- **Token app-scoped / no wallet data** → `meta.degraded`. Say you can't read the account with this
  token (it needs a USER-scoped token); don't report an empty portfolio as "$0."
- **A strategy's clearinghouse read failed** → it's in `meta.warnings`; that wallet's positions may be
  incomplete. Say so rather than implying it's flat.
- **A strategy's closed-history read failed** → `closed.realized_pnl` is `null` + a `meta.warnings`
  entry (`trader_history … failed/returned no data`). Report realized PnL as **unavailable** for that
  strategy — never as `$0`.
- **`totals.reconciles == false`** → the per-wallet sum and the portfolio aggregate disagree; surface
  it and trust the per-wallet (live) figures.
- **Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs.

## Skill Attribution

Guide/analysis skill — it *reads* the account and *recommends*; it does not place a trade or move
funds. Attribution happens downstream when the execution tools / strategy skills act on a CTA.


## Install — both scripts are required

The engine is **two files** in `scripts/`: `portfolio.py` (the engine) and `mcp_client.py` (its vendored
MCP helper, imported at runtime). **Install the whole `scripts/` directory** — copying `portfolio.py`
alone fails with `No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
