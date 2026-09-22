# quant-desk — methodology

> **This document describes the engine as of quant-desk 1.24.0.** Nine formulas in it were stale
> between 1.9.0 and 1.14.0 while SKILL.md sent the agent here for them, so an agent asked "how is my
> cost score computed?" answered with the pre-1.9.0 rule, confidently. If you change a formula in
> `scripts/`, change it here in the same commit — `test_methodology_matches_the_engine` fails if the
> versions drift.


Every number on the desk is a function of public onchain data (or Senpi discovery when a token is
present). This file is the formula sheet. Nothing here is a prediction; every dollar figure on a leak is a
counterfactual of a process rule applied to the trades that happened.

## Sources

| Layer | Endpoint | Notes |
|---|---|---|
| Fills | `userFillsByTime` (2000/page) + `userTwapSliceFillsByTime` (2000/page) | merged on `tid`. Hyperliquid keeps only the most recent TWAP slices, so older TWAP executions are absent — measured as **coverage** below. |
| Funding | `userFunding` (500/page) | complete; signed (`usdc` < 0 = paid) |
| Fee schedule + daily volume | `userFees` | `userCrossRate` taker, `userAddRate` maker; `dailyUserVlm` is the wallet's own volume per day (~15 days) |
| Equity + P&L series | `portfolio` | `accountValueHistory`, `pnlHistory` per window; the longest window covering the analysis window is used |
| Transfers | `userNonFundingLedgerUpdates` | sends (`user` = sender, `destination` = receiver), deposits, withdrawals |
| Live book | `clearinghouseState`, `frontendOpenOrders` | positions with leverage, liquidation price, cumulative funding; resting trigger orders |
| Market | `metaAndAssetCtxs`, `candleSnapshot` 1h | funding, open interest, mark; 91 days of hourly candles per coin touched |
| Rank | `stats-data.hyperliquid.xyz/Mainnet/leaderboard` | PnL rank among every account listed — week, month and all-time, so one window cannot pass for the trader |
| Senpi (optional) | `discovery_get_trader_history`, `discovery_get_top_traders`, `discovery_get_trader_state` | complete closed positions with leverage; the ≥ $1M-realized cohort with position ages |

## Round trips

A fill's `startPosition` is the signed position before it; after = before ± sz. An episode opens when the
position leaves 0 and closes when it returns to 0; a flip closes one and opens the next at the same fill.
* `truncated` — first observed fill starts from a non-zero position (opened before the fetch window).
* `unobserved_qty` / `close_observed=False` — a fill's `startPosition` disagreed with the tracked position:
  fills between were not returned. The tracker resyncs to the fill's own `startPosition`; when the resync
  crosses zero the episode is closed at the last observed fill.
* `complete` — none of the above. Only complete episodes feed hold-time, entry-timing and best-setup
  statistics; P&L, fee and volume totals sum every observed fill.

**Coverage** = observed fill volume ÷ (observed + the volume implied by `startPosition` jumps between
consecutive fills). Self-contained. On the wallets checked, maker volume reconciled to the dollar against
the wallet's own `dailyUserVlm` while taker volume fell short by exactly the TWAP slices older than the
retained window; that daily-volume ratio is kept only as a diagnostic (`volume_ratio`) because it is not a
reliable denominator on every wallet. Below 90% the desk says so and labels ledger figures as the complete
ones.

## Track record

* Win rate = winners ÷ closed episodes (`realizedPnl` > 0, gross of fees — Hyperliquid's convention).
* Profit factor = Σ winners ÷ |Σ losers| (gross).
* Payoff ratio = average winner ÷ average loser.
* Net (observed) = gross realized − fees + funding. **Net P&L (ledger)** = the `pnlHistory` delta over the
  window — Hyperliquid's own figure, fees, funding and unrealized included; complete regardless of coverage.
* Return on average equity = ledger net ÷ mean transfer-adjusted equity over the window.
* Max drawdown: on the transfer-adjusted equity curve (account value + cumulative net outflows), so a
  withdrawal never reads as a loss; % of the adjusted peak. `IN DRAWDOWN` when the last point sits > 5%
  under the peak.
* Hold times: medians over complete episodes, stated only with ≥ 5 complete winners **and** ≥ 5 complete
  losers.
* Cost ratio = (fees − funding) ÷ gross realized, when gross > 0.
* Fee recoverable = fees **actually paid** (summed from fills) × the taker share of those fees ×
  the spread between crossing and resting. Both the apportionment and the spread are computed per
  dex — main and HIP-3 (`xyz:`) volume bill on different schedules, and a book can be almost
  entirely on one of them. Rebates (negative fees) are earned, never recoverable.
* Sizing: coefficient of variation and max ÷ median of peak notional over episodes whose open was observed.

## Protection audit

A stop for a long is a resting sell trigger below the mark; for a short a buy trigger above it. Stop cover
= stop-covered size ÷ position size. `AT RISK` = liquidation < 5% away with cover < 90%; `UNPROTECTED` =
cover 0; `PARTLY COVERED` = 0 < cover < 90%. Funding per day = −hourly rate × notional × 24 (sign by side).

## Timing (complete episodes with candles)

* Move before entry = signed change over the 24h before the open (in the trade's direction). **Chased** =
  ≥ +3%. The chased vs calm split reports profit factors for each.
* MFE / MAE = best / worst excursion from the entry VWAP over the hold, from hourly highs and lows.
  Give-back = (MFE − realized%) ÷ MFE for winners.
* Counterfactuals are priced on the **exposure that existed**: every fill's signed size, average
  entry and booked P&L are carried on the episode, so "close at hour h" means the P&L already booked
  by h plus mark-to-market on the size actually open then. It used to be `return × peak size ×
  entry VWAP` — the peak size assumed held from entry to the exit, which on a scaled position is a
  different trade and overstated by up to 20× on a shape nothing flagged. senpi's indexed rows are
  aggregate positions with one size and one entry price, so for those the two forms are identical:
  * time-cut on losers at 12h / 24h / 48h — exit at the first candle past the cut if still losing;
  * trailing lock — once the peak reaches +3% (or +5%), exit when price gives back 50% (or 70%) of it.
  Every setting with ≥ `MIN_PATTERN_TRADES` (5) engaged trades enters its family; the one quoted is the
  family's **lower median**, and the family is dropped if that median hands back more than
  1 − `NOISE_SHARE` (85%) of what it saves. Selection runs over every adequately-sampled setting, never
  over a set the noise bar has already filtered by score — otherwise a family with one survivor quotes
  that survivor and the grid search comes back as a finding. Rules that fail the bar are printed as
  **tested and rejected**.
* Funding leak = funding paid on payments landing more than 24h after the episode holding that coin
  opened — the part hold time alone would have avoided. Capping a hold at 24h also **closes** it, so the
  lever carries the P&L of the exits it forces (charged, never credited — that gain belongs to the
  time-cut). With no 24h-cut sample there is nothing to charge, so the fix is left unpriced and only the
  measured funding cost is stated.
* Liquidations are stated, not priced: where a stop would have gone is a guess, and a guess must not
  top a ranking of measured fixes.
* Sizing leak = over every trade sized > 1.5× the median winner, realized × (1 − median ÷ size) —
  winners charged alongside losers, or the cap only ever shrinks the losses.
* Chase leak = realized on entries that had already run ≥ 3%, at the same sample and noise bar as
  every other pattern lever.

## Six dimensions (0–100) and the quant score

| Dimension | Weight | Start | Deductions / credits |
|---|---:|---:|---|
| Risk management | 0.25 | 85 | −min(30, (hold ratio − 1) × 15); −25 × naked share; −15 if any position < 5% from liquidation; −10 per liquidation (max 30); −min(20, (margin used − 60%) × 50); −min(20, max DD × 60) |
| Consistency | 0.20 | 50 | + (min(PF, 3) − 1) × 20 + (win rate − 50%) × 40, then shrunk toward 50 by n ÷ (n + 10) |
| Timing / edge | 0.15 | 70 | −40 × chased share; −15 if chased PF < 1 < calm PF (n ≥ 3); −20 × median give-back; −min(20, 2 × entry lag h) or +10 when ahead of the cohort; 60 flat when < 5 complete trades |
| Cost efficiency | 0.15 | 100 | −min(70, cost ratio × 150); −10 if taker share > 60%; gross ≤ 0 → 90 − min(60, taker share × 50) |
| Market fit | 0.15 | 65 | +10 per position with the trend (max 25); −15 per position against (max 45); −min(20, annualized funding cost ÷ equity × 50); 60 flat with no positions |
| Sizing / conviction | 0.10 | 85 | −min(30, size CV × 20); −15 if median loser > 1.3× median winner; −min(20, (gross exposure ÷ equity − 5) × 3); −10 if one position > 60% of a multi-position book |

Quant score = Σ weight × dimension, rounded.

## Archetype, flags, verdict

* Archetype = adjective (Aggressive: exposure > 5× equity, margin used > 60% or any position ≥ 10×;
  Careful: < 2× and < 30%; else Balanced) + optional "long-only"/"short-only" (≥ 90% one side, ≥ 5 trades)
  + noun (momentum chaser ≥ 50% chased; scalper: median hold < 2h and ≥ 3 trades/day; dip buyer / fader:
  median pre-entry move < −2%; position trader > 5d; swing trader > 24h; trend rider: buys strength and
  keeps > 55% of the peak; else opportunist).
* Flags: `NO STOPS (n/m)` / `PARTIAL STOPS`, `NEAR LIQUIDATION x%`, `HIGH MARGIN` (≥ 60%), `IN DRAWDOWN`,
  `LIQUIDATED ×n`, `CHASING`, `PAYING FUNDING $/DAY` (> 10% of equity a year), and Senpi's consistency /
  risk / activity labels when present (CHOPPY, STREAKY, SNIPER, DEGEN).
* Verdict = strength (profit factor ≥ 1.5 on ≥ 10 trades → "Real edge"; payoff ≥ 2 → "You let winners
  run"; win rate ≥ 55%; net > 0; else "No edge shows up") — weakness (the lowest dimension, or a position
  < 5% from liquidation) . imperative.

## Smart money

Cohort bias per coin = net ÷ gross signed notional over cohort members holding it ([−1, +1]); a read needs
≥ 3 members; |bias| < 0.2 is `COHORT SPLIT`. `WITH — BUT LATE (+h)` when your entry sits more than 4h after
the cohort's median entry on your side (Senpi source only — the public source carries no entry times).
Whale median table: `benchmark.json`, medians computed by `scripts/benchmark.py` with this same engine over
the Senpi discovery cohort (members with ≥ 10 trades); rendered only when the benchmark holds ≥ 5 such
members. A public-leaderboard run is diagnostic only — whales are TWAP-heavy and their public round trips
come back nearly empty.

## Market fit

The 20-day mean needs 20 days: under 480 hourly candles the trend reads `UNKNOWN` rather than calling a
direction off whatever tape a new listing has. Trend from hourly candles: UP when the 7-day change > +5%
and the close > +2% above its 20-day mean; DOWN
symmetric; else RANGING. Funding in bp per 8h = hourly rate × 8 × 10⁴. Fit: long ↔ UP, short ↔ DOWN =
WITH; opposite = AGAINST; RANGING = NEUTRAL. Regime headline from the median funding across the coins held
(≥ 10 bp/8h HIGH POSITIVE, ≥ 3 POSITIVE, ≤ −3 NEGATIVE, ≤ −10 DEEPLY NEGATIVE) and BTC's trend.

## Where the edge is

Groups of complete episodes (≥ 3): coin × side, side × hold bucket (< 4h, 4–24h, 1–3d, > 3d), entries
before vs after a ≥ 3% move. Best = profit factor ≥ 1.5 and positive realized, by realized; worst = profit
factor < 1. Catalog families: buys strength and keeps the peak → trend_following, gives it back →
breakout_momentum; buys weakness → contrarian_fade; one coin ≥ 50% of volume → single_market.

---

# v2 — the strategy read, the market context, two cohorts, live matches, follow-ups

## Asset classes (`taxonomy.py`)

Crypto tiers come from live open interest on the main dex: **majors** = top 3 by OI, **large caps** =
the next 12, **alts** = the rest. **Memecoins** = every 1000×-denominated `k…` name plus a short documented
list (a taxonomy, never a trading whitelist). xyz assets use senpi-market-pulse's own groups verbatim
(equities, indices, commodities, FX/macro).

## The strategy read (`strategy_read.py`)

* **Where the trades went** — trades, wins, realized and profit factor by class × side.
* **Simultaneity** — the share of the window with at least one long AND one short open, and the class
  pairs most often held together.
* **Leg correlation** — correlation of hourly log returns between the equal-weight basket of coins the
  trader went long and the basket they went short (≥ 100 common hours). Above +0.6: "the hedge is mostly a
  fee"; below +0.3: genuinely different bets.
* **P&L beta to BTC** — correlation and slope of P&L changes (as a share of equity) against BTC returns
  over the same intervals of the ledger's P&L series. Reported as "a 1% BTC move swings your equity by
  about X%".
* **Outcome concentration** — the three best trades ÷ total realized (> 100% means the rest nets
  negative).
* **Dead sides** — class × side with ≥ 3 trades and no winner.
* **Style** — buys strength (≥ 50% of entries after a ≥ 3% move) or weakness (median pre-entry move
  < −2%); pyramids (≥ 1 add per trade); works orders (> 20% of fills are TWAP slices).
* **Critique rules** — hedged with correlated legs; directional and mostly against the trend; P&L is BTC;
  edge in three trades; a dead side; losers outliving winners; against the proven cohort. Each is one
  paragraph; the agent relays and asks "is that deliberate?".

## The market context (`market.py`)

* **Breadth** — 24h change per asset (mark vs previous day) from the live contexts of both dexes, grouped
  by class; the day is `risk_off` when ≥ 3 groups are down ≥ 0.5% and at least twice as many groups are down
  as up, `risk_on` symmetric, else `mixed` (senpi-market-pulse's rule).
* **Daily regimes** — one label per UTC day over the window from daily candles of a basket (BTC, ETH and
  the ten largest by OI): `risk_on` when BTC closed > +1% or ≥ 65% of the basket closed up; `risk_off`
  symmetric; else `mixed`.
* **How you trade the tape** — the trader's trades split by the regime of the day they were opened
  (and by side): trades, win rate, realized, profit factor; today's label against the best cell.
* **Senpi layers** — `market_get_funding_regime` (LONG_CROWDED / SHORT_CROWDED / NEUTRAL, extreme count),
  `leaderboard_get_markets` (dominant direction per token, share of the top traders' 4h gains, headcount;
  overlap with the trader's book as WITH / AGAINST), `leaderboard_get_momentum_events` (coin × direction
  counts in the last 4h; with / against the trader's positions).

## Two cohorts (`smart_money.py`)

* **Proven** — `discovery_get_top_traders(ALL_TIME, sort PROFIT_AND_LOSS_REALIZED)`, members with ≥ $1M
  realized, top 100. **Hot** — `MONTHLY, sort PROFIT_AND_LOSS, open_position_filter`, top 100. Books via
  `discovery_get_trader_state` with position ages. Public fallback: the leaderboard's large profitable
  accounts with a live book (no ages).
* Per coin: bias = net ÷ gross signed notional, headcount by side; reads as in v1 (`WITH — BUT LATE (+h)`
  when the trader's entry sits more than 4h after the cohort's median entry on that side).
* **Book-level agreement** — over the classes the trader holds, weight × sign agreement × min(1, |cohort
  bias| ÷ 0.2), in [−1, +1]. **Tilt** — cohort net ÷ gross and long/short headcount per class, against the
  trader's own. **They hold, you don't** — coins with ≥ 3 members and |bias| ≥ 0.2 the trader is not in,
  by headcount. **You alone** — coins the trader holds that no cohort member does.

## Live matches (`opportunities.py`)

Candidates: coins a cohort leans on (≥ 5 members: proven +2, hot +1), coins the cohort is with the
trader on (+1), the top traders' gain markets (≥ 5 traders, +1), momentum events (+0.5), and the trader's
own winning coin × side (+3). Adjustments: fits the trader's best class × side pattern (≥ 4 trades, profit
factor ≥ 1.5, +2); trend with (+2) / against (−2.5); funding the side would pay above 10 bp/8h (−1) or
collect below −3 (+0.5); already moved ≥ 5% today in that direction (−1, "a chase"). Top six with a
positive score, each with its reasons. Process only.

## Follow-ups (`followups.py`, `deep.py`)

A bank of ten, scored for relevance (naked or near-liquidation positions → `protect` first; against the
cohort → `smart`; a funding bill → `funding`; a losers leak → `replay`; regime cells present → `regime`;
≥ 30 trades → `compare`; a best setup → `rules`/`scout`). Three to five are offered; each maps to
`--deep <mode>`:
* `protect` — hard stop = 1.5 × the average **daily** range (each day's high-to-low over the last two
  weeks of hourly candles, not the average of a day's hourly ranges), pulled in toward the mark when
  liquidation is nearer than that so the stop still triggers first, keeping a 40% buffer above the
  liquidation price; the trailing lock arms two ranges in the money and trails at half the peak gain; dollars at
  risk before (margin at risk to liquidation) vs after (distance to the stop × size).
* `replay` — the worst 7-day window by realized, its trades, and the time-cut / trailing-lock grid on
  exactly those trades.
* `funding` — funding per day at today's rates × 30 per position, total as a share of equity.
* `compare` — last 30 days vs the 60 before on trades, win rate, profit factor, realized, fees, size,
  holds, taker share.
* `rules` — entries (best setups), entry timing (chased vs calm profit factors), holding, sizing, risk,
  catalog families, and the discover/author handoff.
* `regime`, `smart`, `scout`, `strategy`, `watch` — the corresponding sections in full.

## Scoring rules as of 1.24.0 — read these, not any older formula above

These nine changed between 1.9.0 and 1.15.0 while this file still described the pre-1.9.0 engine.

| Rule | Current |
|---|---|
| Cost efficiency | `100 − min(70, costs/|ledger_net| × 150)`. Base is the money actually lost, not gross — measuring cost against a loss measures the loss. A maker **rebate** is earned, never counted as a cost. |
| Drawdown penalty | `min(75, dd_pct × 75)`. The old `min(20, ×60)` saturated at 33%, scoring a wipeout the same as a third. |
| Funding penalty | `min(45, yr × 50)`. Saturated at 40%/yr. |
| Abstention | All six dimensions return `None` rather than score when they have nothing to measure — below `MIN_PATTERN_TRADES` closed trades, or with no open book for market fit. |
| Headline | Re-normalised over the dimensions that measured. Below `MIN_DIMENSIONS` (3) the desk declines to score at all. |
| Which lever is quoted | **Median within a family, max across families.** Taking the max over 3 lock × 3 cut settings was a grid search reported as a finding. |
| Leak values | Charged — each fix nets the trades it costs. `leaks()` and `recoverable()` read one `levers()` table, so the list and the headline cannot disagree. |
| Liquidations | Stated, not priced. "A stop halfway would have kept half" was a guess. |
| Series selection | The portfolio window that **spans** the most of the analysis window, never the one with the most points. |
| Every lever, one bar | Sample (`MIN_PATTERN_TRADES`) and noise (`NOISE_SHARE`) apply to every pattern lever, chase included, and selection runs before the noise bar rather than after it. |
| Stop ladder | 1.5 × the average **daily** high-to-low, built by blocking hourly candles into rolling 24h windows. It was the mean HOURLY range under a column called "24h range" — a stop ~4.7× too tight on BTC. |
| Fees, per dex | Taker **and** maker volume are priced on the dex they traded on, and the quoted bp is the blended rate the wallet actually faces. |
| Beta | Each step's P&L over the transfer-adjusted equity **at that step**, not over today's account value. |
| Counterfactual scale | Priced on the exposure held at the moment of the counterfactual exit, from the episode's own fill-by-fill size path — never on the peak size assumed held throughout. |
| Where the grid reads | Totals come from senpi's indexed history where it is available; the counterfactual grid always reads the FILLS-derived episodes, which carry a real size path. A discovery row's `size` accumulates over the position's life — one row on one wallet is 13,651 fills over 20 days — so it is not an exposure to price an exit against. |
| Quoted fee rates | Measured from the fills (taker fees ÷ taker volume), not from `userFees` — the xyz schedule comes back identical to main, so the quoted bp would not reproduce the quoted dollars. |
| Beta denominator | Floored at a tenth of the window's median equity; steps below it are dropped rather than divided by a collapsed tail. |
| One bar, one table | `leaks()` and `recoverable()` read the same `levers()` table and nothing else. A family the lever bar declines is stated **unpriced**, with the measured pattern that survives it; there is no second path that reports a number the bar rejected. |
