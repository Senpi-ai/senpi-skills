# Analysis framework — reading a portfolio, not dumping balances

The engine hands you a precise, real-time breakdown. This is how you turn it into analysis. Two
jobs: (1) get the **money map** exactly right, and (2) read the **positions against the market.**

## 1. The money map — three buckets, never conflated

Every dollar is in exactly one place. The single most common failure is mislabeling where idle cash
sits, so anchor on this:

```
grand_total
├── idle_in_embedded      ← truly free. HL USDC + EVM USDC in the MAIN wallet.
│                            Deploy into a strategy, or withdraw to your bank.
├── idle_in_strategies     ← free margin sitting INSIDE strategy wallets, between trades.
│                            Withdrawable back to main, but currently committed to a strategy.
└── deployed_in_positions  ← margin actively backing open trades.
```

**The `total_withdrawable` trap.** The portfolio API's `total_withdrawable` is **idle_in_strategies**
(bucket 2) — the unused margin summed across strategy wallets. It is **not** cash in the embedded
wallet. A user who has moved everything into strategies has a **$0 embedded wallet** even when
`total_withdrawable` reads four figures. The engine splits these into two fields on purpose. When you
report idle capital, **name the wallet**: *"$0 idle in your embedded wallet — it's all deployed to
strategies; $2,300 of that is sitting as free margin inside the strategy wallets waiting for
signals."* Never collapse those into one "idle" number.

**Always re-fetch.** Balances move every second. The engine forces a fresh pull (no 12h cache) and
reads each strategy's live clearinghouse state — so re-run it every time, even if the user asked a
minute ago. Never quote a figure from earlier in the conversation.

## 2. Read each position against the market

A balance is data; *"this short is working because the asset is falling"* is analysis. For every
open position, the engine gives you `return_on_equity_pct`, `market_24h_pct`, and `vs_market`:

- **Leveraged return, always.** Cite `return_on_equity_pct` (uPnL / margin), not the raw price move.
  A 1% price move at 10x is a 10% return on margin — the raw % understates everything.
- **With or against the tape.** A short is *working* when the asset is down today; a long when it's
  up. `vs_market` flags this. The interesting cases are the ones *fighting* the move — a long
  bleeding into a falling market is a position to call out, not bury.
- **Alignment to the thesis.** If the book is a hedge (long sleeve + short sleeve), say which sleeve
  is carrying — *"the short sleeve is up 11% and cushioning the long sleeve's drawdown."* That's the
  read the user actually wants.

## 3. Portfolio-level signals

Zoom out from individual positions to the posture of the whole book:

- **Net exposure** (`exposure.net_notional_usd`, `net_bias`). Is the book net long, net short, or
  market-neutral? Compare that tilt to where the broader market is — a net-long book into a
  semiconductor-led selloff is exposed; a net-short book is positioned for it.
- **By-asset / by-sector** (`by_asset_net_usd`). Where is the real concentration? Three "different"
  longs that are all AI semis are one bet, not three.
- **Concentration** (`signals.largest_position_pct_of_deployed`). One position that's 60% of deployed
  margin is a risk to name, even if it's green.
- **Idle drag** (`signals.idle_drag_pct`). Capital sitting in cash earns nothing. A book that's 70%
  idle is either patient or asleep — say which, and tee up the "put it to work" CTA.
- **Liquidation proximity.** `liq_px` vs the current mark — a position 5% from liquidation is a risk
  headline regardless of current PnL.

## 4. Don't misread the funding ledger

A small strategy balance is **not** proof of a loss. `netFunded = total_funded − total_withdrawn`
can be negative when profits (and then some) have been withdrawn — that's a *win* being harvested,
not a blow-up. Before calling anything "down," check the funding ledger. True performance is PnL
against `netFunded`, not the raw account value.

## 5. Compose the read

Lead with the money map (correctly bucketed and located), then per-strategy, then the position-vs-
market analysis, then the portfolio posture. End on the one or two things that actually matter — the
position fighting the tape, the concentration risk, the idle capital — not a recap of every row.

> "Your $2,933 is fully deployed to strategies — $0 sits idle in your embedded wallet, and $2,300 is
> free margin inside the strategy wallets. The only live book is cub-short: three shorts (ETH, SP500,
> XRP), all green and all *with* today's selloff — the short sleeve is doing exactly its job. cub-long
> and cub-preipo are 100% in cash, waiting. Net-short, well-hedged, but ~78% of your capital isn't
> working yet."
