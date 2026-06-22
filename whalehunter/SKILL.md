---
name: whalehunter-strategy
description: >-
  WHALEHUNTERHEDGE v2.0 — a long/short book that positions WITH the smartest money on
  Hyperliquid and AGAINST the crowd. It segments traders into cohorts by LIFETIME
  REALIZED gains (smart money = >$1M, crowd = $10k–$100k), aggregates each cohort's NET
  positioning per asset (a bias in [-1,+1]), tracks whether the smart cohort is ADDING
  daily, and strikes when the smart cohort is heavily net-directional on an asset AND
  growing the position while the crowd leans the other way — a smart-money-vs-crowd
  DIVERGENCE. It also surfaces that divergence as a human-readable insight. Two
  INDEPENDENT sleeves on SEPARATE wallets (long / short) so the book can hold conflicting
  positions on the same asset at once. Rides on a WIDE DSL. Funding split default 50/50.
  The v1.x per-whale conviction copier is retained behind a flag (OFF by default). NOT a
  blind copy-trader; runtime owns the LLM gate, DSL, risk.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "2.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐋 WHALEHUNTERHEDGE v2.0 — Smart-Money-vs-Crowd Divergence (long/short)

Position **with the smartest money, against the crowd.** WhaleHunterHedge measures what
the cohort with the largest **lifetime realized gains** is *actually doing* — net long or
net short, per asset, and whether they're **adding** — and fires when the smart money
diverges hard from the crowd. The signal it's built to catch:

> *This week the most profitable wallets on Hyperliquid went heavily short while the crowd
> piled into longs — and the winners added to that short every single day.* That's the
> setup: **smart money net-short + adding, crowd net-long → short it with them.**

| Sleeve | What it positions | Direction | Wallet |
|---|---|---|---|
| **long** | assets the smart cohort is net-long + adding | LONG only | one |
| **short** | assets the smart cohort is net-short + adding | SHORT only | one |

> **Two wallets, fully independent** — so the book can hold **conflicting positions on the
> same asset** (smart money net-long ETH while net-short HYPE → long sleeve holds ETH,
> short sleeve holds HYPE, no netting). Funding default **50/50** — no built-in directional bias.

## Why cohort divergence is the edge

A single whale's trade is noise. The **weight of the entire >$1M-realized cohort** is signal —
it's the closest thing Hyperliquid has to a "commitment of the smartest traders." When that
cohort is lopsided on an asset **and growing the position day over day**, while the crowd
(the $10k–$100k wallets that are net buyers of every rally) is on the other side, you have a
**positioning divergence** — the most durable read in the market. You're not chasing one bet;
you're standing where the people who've actually made money are standing, and fading the
people who haven't. Because it's a *positioning* signal (not a momentum one), a **wide DSL**
fits: you hold while the smart cohort holds.

## The engine — four steps, all from Senpi Discover

1. **Cohorts by lifetime realized gains.** The ALL-TIME realized-PnL ranking, **paged by
   offset**, bucketed by `$`: **smart = ≥ `smartMinRealizedUsd` ($1M)**,
   **crowd = `crowdMinRealizedUsd`..`crowdMaxRealizedUsd` ($10k–$100k)**. Paging matters:
   the ranking is realized-descending, so the smart cohort sits at the top but the crowd
   lives *thousands of ranks deeper* — a single top-N pull catches only a weak tail of the
   crowd (the inverted "crowd ≪ smart" symptom). The walk pages down (`cohortMaxPages`)
   until the crowd is representatively sampled or the ranking drops below the crowd floor,
   capping each cohort (`cohortSampleCap`) to bound per-tick load. Membership cached daily.
2. **Net positioning per cohort, per asset.** `discovery_get_trader_state` across each cohort
   → sum signed notional per coin → **bias = net / gross in [-1,+1]** (+1 = all long,
   -1 = all short), plus member counts.
3. **The "adding daily" trend.** A daily **cohort ledger** snapshots the smart cohort's net
   per coin; today's net minus the earliest snapshot in the window = **growth**. The smart
   cohort must be *growing* its position in the signal direction (`requireGrowing`).
4. **The divergence strike.** For this sleeve's direction: the smart cohort is net-directional
   past `biasThreshold` (default 0.50) **and** growing, scored higher when the bias is strong
   (≥0.7), when it's adding, and when the **crowd diverges** (net-opposite by ≥`crowdDivergenceMin`).
   Score ≥ `cohortMinScore` emits.

> **~1-day warmup:** the growth gate needs ≥2 daily ledger snapshots, so a fresh deploy
> emits no divergence strikes on day 1 — it's building the baseline. This is by design.

## Mirror + ride + surface

- **Size to YOUR budget, score-scaled** — `margin = equity × marginPct`, scaled up +25% per
  point above the score floor, capped at `maxMarginPct`. No hardcoded `$`.
- **Leverage capped** — conviction shows in *size*, not leverage (clamp to `maxLeverage` then
  venue max).
- **Surfaces the insight** — every tick the producer emits a human-readable `insight` line per
  top divergence (`"HYPE: smart -0.85 vs crowd +0.65, Δ-$2.1M → SHORT"`), so the agent can
  *report what the smart money is doing*, not just trade it.
- **Wide DSL** — wide disaster stop, `weak_peak` OFF, time-cuts OFF, Phase 2 does nothing until
  a big run. Ride the divergence. Planned invalidation exit: **close when the smart cohort
  flips or unwinds** (the mirror of the entry).

## Deploy — two wallets

```
WHALEHUNTER_LEG=long   WHALEHUNTER_LONG_WALLET=<wallet A>
WHALEHUNTER_LEG=short  WHALEHUNTER_SHORT_WALLET=<wallet B>
```
Fund **50/50** for a balanced smart-money long/short book. **Requires a USER-scoped
`SENPI_AUTH_TOKEN`** — the `discovery_*` tools need a valid user id (no user scope → the
smart cohort comes back empty and the producer reports "cohort too small").

## Fleet-standard rules (enforced)

- **Max leverage 5x** (clamp + venue). Score-scaled margin (no hardcoded $); per-position cap
  `maxMarginPct`; up to `maxSlots` (6) per sleeve.
- **Drawdown halt 25%**, baseline-seed guard, per-asset cooldown. `daily_loss_limit_pct`
  disabled on multi-wallet funds (perpDay base reads ~$0 → $0 limit; DSL + drawdown_halt protect).
- **Mandatory DSL**; entries + exits `FEE_OPTIMIZED_LIMIT` with taker fallback (a confirmed
  divergence must fill).
- **Sizes off `max(main, xyz)` account value** — never the sum (cross-margin).
- **Signature-adaptive daemon launch**; per-(leg,wallet) lock; daily-cached cohorts + daily ledger.

## Hard rule — user-conversation sessions are READ-ONLY

A Claude session conversing with a user MUST NOT call `create_position`, `close_position`,
`edit_position`, `ratchet_stop_*`, `cancel_order`, or any `strategy_close*` tool against
WhaleHunter's wallets. Entries are emitted only by the producer daemons; exits are owned only
by the runtime DSL.

## Versions

- **v2.0 (current)** — **the cohort-divergence engine.** Pivots from per-whale copying to
  measuring the NET positioning of the >$1M-realized cohort vs the $10k–$100k crowd, per asset,
  with an "adding daily" growth gate and a crowd-divergence booster — and surfacing it as a
  readable insight. Positions WITH the smart money against the crowd. The v1.x per-whale
  conviction copier is retained behind `enableIndividualCopy` (OFF by default).
- **v1.2** — activity tune + near-miss observability for the per-whale copier (convictionPct
  0.25→0.18, poolSize 30→50) after it sat 0 trades in 3 days (patient whales rarely open a
  ≥25%-of-book new position — the rarity that v2.0's cohort approach sidesteps).
- **v1.1** — tiered sizing across four consistency×style tiers (`tagWeights`).
- **v1.0** — patient-whale conviction copier (ELITE+PATIENT, conviction gate, wide-DSL ride).
