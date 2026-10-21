# Output contract — the full read

Read before a full read ("analyze my portfolio / how am I doing"). Step 1 and the closing are in
SKILL.md.

The sections below headed "a copy of SKILL.md …" are SKILL.md's own words, kept identical by a test.
Inside a copy, "above", "below" and "see …" point to sections of SKILL.md, not of this file.

## Narrate as the steps return

A full portfolio read is several MCP round-trips (embedded wallet + a live clearinghouse pull per strategy
wallet + the live DSL/ratchet reads + the per-asset market fan-out). Run as **ONE** call it can take
minutes, blow the `exec` timeout, and push you to raw MCP — which loses every guardrail. So run the read as
**fast, resumable STEPS** and **narrate each slice the moment it returns** (this mirrors
`senpi-improve-trades` / `senpi-strategy-ops` — short steps over a shared state file, the skill narrates
between). Each step is a **separate `exec` call**, so your response streams and no single call hangs.
Runtime health inside `strategies` is **one** `openclaw senpi status --json` for the whole fleet (a
progress line goes to stderr), so that step's cost does not grow with the number of runtimes.

The steps (SKILL.md "Run it in steps"):

```sh
python3 scripts/portfolio.py money        # 1. the FAST money map: embedded idle + each wallet's value → the three buckets (narrate FIRST)
python3 scripts/portfolio.py strategies   # 2. per-strategy detail: mandate + DSL ladder + protected + closed/realized + strategy_groups[]
python3 scripts/portfolio.py positions    # 3. position-level: per-position market (market_24h_pct/vs_market) + exposure + signals
python3 scripts/portfolio.py all          # one-shot fallback: the full composed dict (same output as before)
```

**For a FULL portfolio read** — "analyze my portfolio / my strategies", "how am I doing" — run the steps
**in order** and narrate between:

1. `portfolio.py money` → **narrate the one wallet list IMMEDIATELY** — `book.wallets` as one table in the
   engine's order, then `book.totals.line`: the book total, the managed subtotal (`grand_total_usd`)
   broken into the three buckets (idle-in-embedded / idle-in-strategies / deployed-in-positions), each
   labeled by *where*, plus `reconciles`, and the read-only subtotal beside it. A strategy row's
   positions / protection / PnL are `not_read_this_step` here. Don't wait for the other steps.
2. `portfolio.py strategies` → narrate the **per-strategy verdict** — lead from `strategy_groups[]` (a
   strategy is ALL its wallets), each judged vs its OWN `mandate`, its `protected` posture + `dsl` ladder,
   realized/unrealized PnL as evidence. Its `book` fills the strategy rows' positions, protection and
   PnL; the order can move with the fresh read — keep the new one.
3. `portfolio.py positions` → narrate the **position-level read** — each position vs the market
   (`market_24h_pct`, `vs_market`, leveraged return), then `exposure` (net bias, concentration) + `signals`
   (idle drag).

## Render order

2. **Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real
   strategy — never one per wallet), in this order:
   1. **Label + mandate.** The group's `label` and what it was deployed to *do* — from the group's
      `mandate` (its `profile.description`, read from the deployed `runtime.yaml`; add catalog facets
      like `belief_plain`/`archetype` when present). Works the same for a user-authored strategy. "cub
      is a K-shaped long/short dispersion book — long the structural winners, short the laggards; the
      P&L is the spread." **If `is_multi_wallet`, name it as ONE strategy across its sleeves** ("cougar
      is a market-neutral long/short pair") — never as two strategies.
   2. **Is it doing its job — against its OWN mandate.** Not vs a momentum benchmark, and **not vs the 4h
      leaderboard.** A hedge flat in calm, an all-weather core steady-not-flashy, a market-neutral book
      counter to the crowd, a selective strategy waiting with no position — all **working as designed**.
      See "Judge each strategy against its OWN mandate" and "Counter to smart money is not a defect." (both in SKILL.md)
   3. **Positions as evidence — across ALL the strategy's instances.** The open positions (from every
      instance in the group) that *show* it's on-mandate: direction, leveraged return
      (`return_on_equity_pct`), and **vs the market** (`market_24h_pct`, `vs_market`) — "short ETH, +11%
      on margin, *with* today's 4% selloff." A group instance in `flat_instances` is the strategy's
      **OTHER sleeve waiting for its signal** (for a multi-wallet strategy) or the whole strategy
      waiting for its setup (for a single-wallet one) — say that, never "dead money." Flag any position
      fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized.
   4. **PnL — realized + unrealized, summed across the strategy.** The group's `totals.realized_pnl`
      and `totals.upnl` (+ a couple of `closed.recent[]` trades from its instances). A flat sleeve may
      have already banked real gains on the other sleeve.
   5. **DSL protection — ladder + live tiers.** State the group's `protected` posture (⟹ **all**
      instances ship a DSL exit), then **how its DSL works** from `group.dsl` / `profile.dsl` (hard stop
      at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier ladder), then **each open
      position's live tier** from `positions[].dsl` — "armed at Tier N, locked L% of peak" or, for a
      sub-Tier-1 position, "protected from entry, ratchet arms at +X% — currently +Y%." **Never say a
      live position has "no DSL" because it lacks a ratchet record** (sub-Tier-1 positions have none by
      design). See "DSL — how it works per strategy, and which position is in which tier." (SKILL.md)
   6. **Any lever is WHOLE-STRATEGY.** If you suggest close / pause / adjust-config / top-up, it applies
      to the **entire strategy (all its wallets)** — never one sleeve. And the lever is the STRATEGY,
      not a hand-picked position the scanner will just re-open. See the HARD rule (SKILL.md, "The job, and the
      hard rules") and "Recommend at the STRATEGY level."
3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest
   position), idle drag (capital sitting in cash), and the overall posture — is this book hedged,
   directional, mostly in cash? Compare the net tilt to where the broader market is.
4. **The two CTAs** (the last section).

## Resilience — a copy of SKILL.md "Resilience"

Read when a step's read failed. SKILL.md holds the rules; this copy is word for word, so a follow-up
that only has the router still reaches them.

- **Token app-scoped / no wallet data** → `meta.degraded`. Say you can't read the account with this
  token (it needs a USER-scoped token); don't report an empty portfolio as "$0."
- **A strategy's clearinghouse read failed** → it's in `meta.warnings`; that wallet's positions may be
  incomplete. Say so rather than implying it's flat.
- **A strategy's closed-history read failed** → `closed.realized_pnl` is `null` + a `meta.warnings`
  entry (`trader_history … failed/returned no data`). Report realized PnL as **unavailable** for that
  strategy — never as `$0`.
- **`totals.reconciles == false`** → the per-wallet sum and the portfolio aggregate disagree; the engine
  also appends a `TOTALS DO NOT RECONCILE` entry to `meta.warnings` quoting both figures and the gap.
  STOP and re-run first (see above); if it persists, surface it and trust the per-wallet (live) figures.
- **Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs (on `meta.no_strategy_path` the saved-wallets closing replaces them).

SKILL.md "The three buckets", word for word:

Every dollar is in exactly one of **three buckets** — and the #1 mistake is conflating them:

| Bucket | What it is | Engine field |
|---|---|---|
| **Idle in embedded** | Truly free cash in the main wallet — HL perps USDC + HL spot USDC + EVM USDC (all three legs). Only the Hyperliquid legs can fund strategies — EVM USDC does not bridge in (deposit via the funding card lands on Hyperliquid). Withdraw any of it to your bank. | `totals.idle_in_embedded` |
| **Idle in strategies** | Free margin sitting *inside* a strategy wallet, not yet in a position — waiting for a signal. | `totals.idle_in_strategies` |
| **Deployed in positions** | Margin actively backing open trades. | `totals.deployed_in_positions` |

**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**

These three buckets are the **managed** money — Senpi's. The wallets the user added sit outside them:
they are `read-only` rows in the one wallet list and the read-only subtotal beside `grand_total_usd`
(see "One wallet list — every wallet first-class").

## A strategy is ALL its wallets — a copy of SKILL.md

Read for a full read and any per-strategy verdict. SKILL.md "A strategy is ALL its wallets", word for word:

**This is the most important rule in this skill.** A single strategy can deploy as **MULTIPLE instances
on SEPARATE wallets** — **ox** = `core`+`ballast` (risk-parity), **cougar** = `long`+`short`
(market-neutral), **cub** = `long`+`short`+`preipo` (multi-sleeve dispersion). `strategy_list` returns
**each instance/wallet as its own row**, so the raw list looks like several separate strategies. **It is
not.** A multi-wallet strategy (long+short, core+ballast, multi-sleeve) is **ONE strategy across N
wallets/instances** — the wallets are the *legs of one design*, not independent bets.

**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.** The engine re-unites the
per-wallet rows into **`strategy_groups[]`** — one entry per real strategy, with `is_multi_wallet`,
`instances[]` (the per-wallet detail), and `totals` summed across every wallet. **Present each group as
one strategy**; never present its wallets/instances as separate strategies. (`strategies[]` is still
there for per-wallet detail and the bucket math — but the *unit of analysis and recommendation* is the
group.) When `meta.has_multi_wallet_strategy` is true, at least one strategy spans multiple wallets —
be especially careful.

## The analyst's bar (moved from SKILL.md)

You are a sharp portfolio analyst. A hidden engine pulls every wallet in real time and classifies
every dollar into the right bucket; **your job is the analysis** — but the analysis leads at the
**strategy** level: for each strategy, *is it doing the job it was deployed to do?* Positions are
evidence for that verdict, not the headline. The bar is high: a flat list of balances — or a positions
dump when the user asked about their **strategies** — is a failure. The user wants a read.

## The two CTAs — a copy of SKILL.md "Mandatory closing"

Read this before answering a yes to either CTA or a digit from the menu (on 10.x a re-read of SKILL.md
returns a stub). SKILL.md is the rule; this copy must read the same (a test pins it). The deep-dive
question comes before the CTAs (SKILL.md, "One wallet list — every wallet first-class").

Then the two CTAs. CTA 1 and CTA 2 are about managed wallets only (`kind: managed`). On
`meta.no_strategy_path` the saved-wallets closing replaces them — see "One wallet list — every wallet
first-class".

> **1. Want me to rebalance or adjust any of these positions?**
> **2. Want me to put the idle capital to work in a new strategy?**

- **CTA 1 → strategy / position management.** For an **autonomous strategy**, route to the
  STRATEGY-level levers (`strategy_pause` / `strategy_update` config / `strategy_close` / `strategy_top_up`)
  and apply them to the **whole strategy (all its wallets)** — never to a single sleeve of a multi-wallet
  strategy, and never hand-close a position the scanner will just re-open. Only use per-position tools
  (`edit_position` / `close_position`) for a genuinely ad-hoc position the user placed by hand on a Senpi
  wallet — never a saved wallet, which is read-only (quote its `access` line). Confirm
  before any change; never trade unprompted.
- **CTA 2 → deploy idle.** If there's meaningful **truly-free** idle capital (lead from
  `signals.idle_drag_pct` and `idle_in_embedded` — NOT a flat sleeve of a live multi-wallet strategy,
  which is committed, and never `book.totals.read_only_usd` or any read-only row, which Senpi cannot
  deploy), name the ready options rather than opening a blank picker. One per line, so it
  can be answered with a digit:

  > **What should the idle go into?**
  >
  > 1. **Penguin** — the Hyperfeed striker, crypto only: one position at up to 10x, 90% margin, on the
  >    strongest live rotation, with a DSL floor that ratchets up to lock gains. High risk, high reward,
  >    with -15% SL.
  > 2. **Pelican** — the same striker across all assets: crypto plus stocks, commodities, indices
  >    and pre-IPO.
  > 3. **Puffin** — the smart-money signal feed, concentrated into one position at a time at high
  >    leverage.
  > 4. **Signals Hunter** — that same feed traded on a clock, spread across several slots.
  > 5. **Athena** — a smart-money hedge fund, forked under your name.
  > 6. Or **something else** — tell me how you want to trade and we'll find or build it.

  Penguin and Pelican are the two most people start with, because a live rotation shows up fast and
  they are built to be forked and modified. Be able to unpack the numbers: **-15% SL is 15% ROE, not a
  15% price move** — at 10x that is a **1.5%** move, so answer in price when asked. On 90% margin it is
  **~13.5% of that wallet per stop-out**, and say **per stop-out** — their risk guard rails are off, so
  stops compound. **Up to 10x**, never a flat 10x: the per-name venue cap clamps many instruments, which
  moves the price behind every number, not the wallet cost. Say **rotations**, never "pumps".
  Routes 1-5 hand to **senpi-strategy-ops**;
  route 6 to **senpi-strategy-discover** (find one) or **senpi-strategy-author** (build one). Topping up
  an existing *whole* strategy via `strategy_top_up` stays available whenever that fits better.
  Propose; never deploy without confirmation.
