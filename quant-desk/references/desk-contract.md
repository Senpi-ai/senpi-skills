# quant-desk — what the desk prints, and where its numbers come from

Moved from SKILL.md in 2.0.0: the skill keeps the rules, this is the reference. The runtime's
`openclaw senpi quant` renders these sections; `--section <name>` picks one.

## What the desk is (the output contract, in render order)

1. **Header** — short address · window · fills · coins · **YOUR QUANT — LIVE · READ-ONLY** · weekly rank
   on Hyperliquid's leaderboard (`#545 of 45,105 this week · top 1.2% · #1,020 on the month · #3,300 all-time`) · Senpi's labels when present (`RELIABLE ·
   AGGRESSIVE · ACTIVE`) · **archetype** (`Aggressive long-only trend rider`) · **verdict** (one sentence:
   strength — weakness. imperative) · flag chips (`NO STOPS (1/3)`, `NEAR LIQUIDATION 3.7%`, `HIGH MARGIN
   122%`, `IN DRAWDOWN`, `LIQUIDATED ×1`, `CHASING`, `PAYING FUNDING`).
2. **Quant score /100** and the **six dimensions** with one line each: timing/edge, risk management,
   cost efficiency, sizing/conviction, consistency, market fit. Weights and formulas:
   `references/methodology.md`.
2b. **What you've been doing** — the strategy read: receipts (what share of trades are which class and
   side; whether longs and shorts were held at once and whether those legs actually diverge; how much of
   the P&L is just BTC; how concentrated the outcome is; sides that never paid; buys strength or weakness;
   TWAP use), a where-the-trades-went table by class × side, and the critique.
2c. **The market you're trading in — right now** — today's label (risk-on / risk-off / mixed) from the
   whole venue by class, Senpi's funding regime, where the top traders' gains sit (Hyperfeed) and whether
   you are with or against them, momentum events, and **how you trade the tape**: your own record by the
   regime of the day you entered, with today's label against your best tape.
3. **Track record** — net P&L per Hyperliquid's own ledger, return on average equity, realized on
   observed trades, win rate, max drawdown (transfer-adjusted), profit factor, trades, active days —
   and a coverage line when trade-level reads cover less than 90% of the wallet's executed volume.
4. **Where your P&L went** — gross → fees → funding → net, cost share vs the whale median.
5. **Top 3 things your agents found** — each: agent · ~$ / window · title · evidence · counterfactual · fix.
6. **Live positions — protection audit** — account value, margin used, withdrawable, net uPnL; per
   position: side, leverage, notional, uPnL, ROE, funding/day, distance to liquidation, **stop cover**
   (share of the size a resting stop covers), status (`AT RISK` / `UNPROTECTED` / `PARTLY COVERED` /
   `PROTECTED`) and what your quant would do.
7. **Performance** — per-coin table, long/short split, hold time winners vs losers, execution
   (taker share, fee rates, liquidations), size-vs-outcome bands.
8. **Leaks** — ranked by $ impact, each counterfactual; then the rules **tested and rejected**.
9. **You vs smart money** — two cohorts (Senpi: the proven cohort — top traders by all-time realized
   P&L with ≥ $1M — and the hot 30-day cohort; public fallback: the leaderboard's large live books). Per
   open position: your side, the cohort's side and headcount, the read (`WITH`, `WITH — BUT LATE (+6h)`,
   `AGAINST SMART MONEY`, `COHORT SPLIT`, `NO COHORT VIEW`); book-level agreement; their book by class vs
   yours; what they hold that you don't; what you hold that none of them do; your entry lag vs theirs.
10. **Market fit** — regime headline, funding across your coins, your stance and its daily funding
    cost, BTC trend; per position: trend, funding, open interest, fit.
11. **Where your edge actually is** — best setups (coin × side, hold bucket, entries before vs after the
    move) with wins/n and profit factor; the catalog families it maps to.
11b. **Live matches** — coins where the cohorts lean, the tape agrees, funding is not punitive and the
    setup fits how this trader wins, ranked and explained; "already moved today — a chase" is a demerit.
12. **What your quant would do next** — protect first · fix the biggest leak · keep the agents on.
12b. **Your quant is ready to go deeper** — three to five follow-ups from the bank of ten.

## Reading the sources (what to say when asked "where does this come from")

- **Public, any wallet:** fills and TWAP slices (`userFillsByTime`, `userTwapSliceFillsByTime`), funding
  payments, the wallet's own fee schedule and daily volume, the equity and P&L series, transfers, the
  live book and resting orders, hourly candles, Hyperliquid's leaderboard. No auth.
- **Coverage:** Hyperliquid keeps only the most recent TWAP slices, so a TWAP-heavy wallet's older
  executions are not returned. The desk measures the gap from the position jumps between consecutive
  fills (`startPosition` is the position before each fill) and prints the share of executed volume it
  could see; ledger figures (net P&L, equity, funding) are complete regardless.
- **With a Senpi token:** closed positions come from Senpi discovery (the complete stream, with
  leverage per trade); the proven cohort from Senpi's ALL_TIME realized-PnL ranking (≥ $1M realized, top
  100) and the hot cohort from the MONTHLY PnL ranking with open positions, both with position ages;
  Senpi's funding regime; and the Hyperfeed attention layer (where the top traders' gains sit, momentum
  events). Without one, the cohort is the largest profitable accounts on the public leaderboard, the read
  carries no entry timing, and the attention layer is absent.
