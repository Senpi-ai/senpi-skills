---
name: whalehunter-strategy
description: >-
  WHALEHUNTERHEDGE v1.0 — a long/short copy book that follows the SINGLE
  highest-conviction trades of CONSISTENT + PATIENT Hyperliquid winners. It
  watches traders tagged ELITE (consistency) AND PATIENT (activity) on Senpi
  Discover — winners who rarely trade — and strikes only when one opens a NEW
  position that is a large share of their OWN balance (their highest-conviction
  read). Two INDEPENDENT sleeves on SEPARATE wallets (long / short), so the book
  can hold conflicting positions on the same asset at once. Mirrors the strike,
  sized to your budget (conviction- and consensus-scaled, leverage-capped), and
  rides it on a WIDE DSL. Funding split default 50/50 (no directional bias). NOT a
  blind copy-trader; runtime owns the LLM gate (pass-through), the wide DSL, risk.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐋 WHALEHUNTERHEDGE v1.0 — Patient-Whale Conviction Copy (long/short)

Follow the **one big bet** of the patient winners. WhaleHunterHedge shadows traders
tagged **ELITE** (consistency) and **PATIENT** (activity) on Senpi Discover, and
strikes only when one of them makes a **high-conviction move** — a new position that's
a large share of their own balance — then rides it wide.

| Sleeve | What it mirrors | Direction | Wallet |
|---|---|---|---|
| **long** | whales' high-conviction LONG strikes | LONG only | one |
| **short** | whales' high-conviction SHORT strikes | SHORT only | one |

> **Two wallets, fully independent** — so the book can hold **conflicting positions on
> the same asset** (one whale high-conviction long ETH + a *different* whale
> high-conviction short ETH → long sleeve holds ETH-long, short sleeve holds
> ETH-short, no netting). Funding default **50/50** — balanced, no directional bias.

## Why patient + consistent is the edge

A trader who is both **ELITE** (consistently profitable — no losing 7-day-or-longer
segments) and **PATIENT** (trades infrequently, holds long) has **almost no routine
trades to copy**. So when one finally commits a big slice of their own book to a new
position, that's not noise — it's their single highest-conviction read. **The rarity
is the alpha.** You're not copying their churn (they have none); you're copying their
one big swing — and because they're patient, they hold it, which is exactly why a
*wide* DSL fits: you ride as long as the conviction lasts.

## Three gates before a strike (all from Senpi Discover)

1. **Consistent** — `consistency == ELITE` (`discovery_get_top_traders`).
2. **Patient** — `activity == PATIENT` (same call). *(Both tags are config-driven;
   widen to include `TACTICAL` if the strict pool is too thin to ever fire.)*
3. **High-conviction size** — a **new** position (diffed vs baseline) whose
   `marginUsed / accountValue` (capital at risk as a share of *their* balance) clears
   `convictionPct` (default 25%), recently opened (`durationInSeconds < maxEntryAgeSec`).

The pool refreshes daily (cross-checkable to ALL_TIME for durability); a **baseline-seed
guard** prevents firing on pre-existing positions at startup.

## Mirror + ride

- **Size to YOUR budget, conviction-scaled** — `margin = equity × marginPct`, scaled up
  by how big *their* bet was and by **pool consensus** (more elite whales agreeing on
  the same coin+direction = bigger size; agreement is consensus, **not** a second
  position — within-tick dedup). Capped at `maxMarginPct` of equity.
- **Leverage capped** — conviction shows in *size*, not inherited leverage (clamp to
  `maxLeverage` then venue max; never copy a whale's 25×).
- **Wide DSL** — wide disaster stop, `weak_peak` OFF, time-cuts OFF, Phase 2 does
  nothing until a big run. Ride the conviction. **v1.1 will add "follow them out"** —
  close when the source whale closes (a producer-emitted invalidation exit).

## Deploy — two wallets

```
WHALEHUNTER_LEG=long   WHALEHUNTER_LONG_WALLET=<wallet A>
WHALEHUNTER_LEG=short  WHALEHUNTER_SHORT_WALLET=<wallet B>
```
Fund **50/50** for a balanced whale-conviction long/short book. **Requires a
USER-scoped `SENPI_AUTH_TOKEN`** — the `discovery_*` tools need a valid user id.

## Fleet-standard rules (enforced)

- **Max leverage 5x** (clamp + venue). Conviction-scaled margin (no hardcoded $);
  per-position cap `maxMarginPct`; up to `maxSlots` (6) per sleeve.
- **Drawdown halt 25%**, daily loss 15%, baseline-seed guard, per-asset cooldown.
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback
  (copying a conviction strike must fill).
- **Sizes off `max(main, xyz)` account value** — never the sum (cross-margin).
- **Signature-adaptive daemon launch**; per-(leg,wallet) lock; daily-cached pool.

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`,
`close_position`, `edit_position`, `ratchet_stop_*`, `cancel_order`, or any
`strategy_close*` tool against WhaleHunter's wallets. Entries are emitted only by the
producer daemons; exits are owned only by the runtime DSL.

## v1.0 — initial build

The first **patient-whale conviction copier**. Gates on the three Discover signals
(ELITE + PATIENT + conviction-as-%-of-their-balance), mirrors the rare big strike
long/short on separate wallets, rides wide. v1.1 plans the "follow them out" exit
(close when the source whale closes) and an ALL_TIME ∩ MONTHLY durability cross-check.
