# Reading Senpi Signals — what each read means

## A read, as the feed prints it

- **Badge and score** — 🔥 80+ strong · 🟠 65–79 worth a look · 🟡 under 65 context. The score is a
  0–100 checklist, not a probability. ⭐ marks the top reads, ⚑ a named wallet.
- **Trade feed vs news feed** — the trade feed rewards a clear side, price confirming it and a book
  deep enough to act on; the news feed rewards surprise, and keeps thin markets (flagged).
- **Timing** — `early` (positioning in, price not moved yet), `late`, `against`. A `[2h ago · from
  $41.20]` tag dates the read; no tag means it can't be dated, not that it is fresh.
- **Source** — only the proven cohort is ever "smart money". The 4h leaderboard, momentum events,
  cross-asset flows and market data are named as what they are.

## The reads

- **Smart money vs the crowd** (`sm_divergence`) — the proven cohort's majority on one side, the 4h
  crowd (or funding, when the name is off the board) on the other. The gap is the story.
- **Whale open** (`whale_open`) — a proven wallet opened a large position recently. Say *opened*,
  never *added* or *flipped*.
- **Momentum event** (`momentum_event`) — a top trader's 4h PnL crossed a tier; the largest position
  is named.
- **Laggard** (`cross_asset_laggard`) — a name that usually follows BTC and hasn't yet.
- **Funding extreme** (`funding_extreme`) — funding at an extreme of the market: a carry read, never a
  direction.
- **OI surge** (`oi_surge`) — open interest building fast while price is quiet. Say what the line says
  and no more.
- Any other change read: its line says what changed. Never add a "since" it did not state.

`Not measured this run: …` names a source that couldn't be read. A read that was never measured is not
one that looked and found nothing.

## Glossary (use these words in output)

- **Proven traders / the cohort** — wallets on Hyperliquid with ≥ $1M lifetime realized PnL, "the
  proven money". A **skilled** tier (≥ $500k) may appear on whale reads, labelled as such.
- **The 4h leaderboard** — Hyperliquid's live 4-hour rolling board: who is winning right now,
  survivorship included. Never smart money.
- **Headcount %** — the share of the proven cohort holding this position, always with the positioned
  split (`N short vs N long`).
- **% of top traders' 4h gains** — of the open profit the 4h leaders hold, the share on this name's
  side.
- **Funding %/yr** — a percent of position size per year at the current rate, never of margin.
