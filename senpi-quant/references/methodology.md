# senpi-quant — methodology

Every number on the desk is a function of public on-chain data (or Senpi discovery when a token is
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
| Rank | `stats-data.hyperliquid.xyz/Mainnet/leaderboard` | weekly PnL rank among every account listed |
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
* Fee recoverable = taker volume × (taker rate − maker rate) from the wallet's own schedule.
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
* Counterfactuals use peak size × price move (adds and partials ignored — stated as approximate):
  * time-cut on losers at 12h / 24h / 48h — exit at the first candle past the cut if still losing;
  * trailing lock — once the peak reaches +3% (or +5%), exit when price gives back 50% (or 70%) of it.
  A rule becomes a leak only when its total is positive in **at least two of three** settings; the figure
  shown is the median setting. Rules that fail the bar are printed as **tested and rejected**.
* Funding leak = funding paid on payments landing more than 24h after the episode holding that coin
  opened — the part hold time alone would have avoided.
* Liquidation leak ≈ half the liquidation loss (a stop halfway to liquidation).
* Sizing leak = for losers sized > 1.5× the median winner, the loss × (1 − median ÷ size).

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
Whale median table: `benchmark.json`, medians over the public cohort computed by `scripts/benchmark.py`
with this same engine (members with ≥ 10 trades).

## Market fit

Trend from hourly candles: UP when the 7-day change > +5% and the close > +2% above its 20-day mean; DOWN
symmetric; else RANGING. Funding in bp per 8h = hourly rate × 8 × 10⁴. Fit: long ↔ UP, short ↔ DOWN =
WITH; opposite = AGAINST; RANGING = NEUTRAL. Regime headline from the median funding across the coins held
(≥ 10 bp/8h HIGH POSITIVE, ≥ 3 POSITIVE, ≤ −3 NEGATIVE, ≤ −10 DEEPLY NEGATIVE) and BTC's trend.

## Where the edge is

Groups of complete episodes (≥ 3): coin × side, side × hold bucket (< 4h, 4–24h, 1–3d, > 3d), entries
before vs after a ≥ 3% move. Best = profit factor ≥ 1.5 and positive realized, by realized; worst = profit
factor < 1. Catalog families: buys strength and keeps the peak → trend_following, gives it back →
breakout_momentum; buys weakness → contrarian_fade; one coin ≥ 50% of volume → single_market.
