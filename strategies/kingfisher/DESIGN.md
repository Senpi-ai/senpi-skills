# Kingfisher — blocked, not deployable (2026-10-16)

Kingfisher is **tracked and tested in this repo but removed from the catalog** (`catalog.status: blocked`).
1.3.0 fixed the card and the code, but the replay of every candidate configuration loses after fees,
and a starter template that loses as written is not something to offer.

## The replay

208 days of hourly BTC, ETH, SOL and HYPE through this package's own scanner and DSL ladder, at 0.18% per
round trip (PR #885):

| Configuration | Trades / 30d | Winners | Per trade | Profit factor |
|---|---|---|---|---|
| as written in 1.2.0 (8% ROE at 4x, floor 6) | 60 | 29% | −0.29% | 0.81 |
| stop 15% ROE | 36 | 41% | −0.50% | 0.78 |
| stop 15% ROE at 3x | 24 | 39% | −0.71% | 0.77 |
| floor 8 | 55 | 28% | −0.30% | 0.81 |
| floor 8 + stop 15 + 3x | 23 | 42% | −0.46% | 0.84 |
| floor 9 + stop 15 + 3x + 12-hour dedup | 13 | 40% | −0.15% | 0.95 |

No configuration clears fees.

## What would unblock it

A configuration of this signal (RSI + MACD crossover on liquid majors) that clears fees on the same replay,
with the result recorded here. Then remove `status: blocked`, bump the version, rerun `gen_catalog.py`,
and restore the `kingfisher` row in `min_budget_golden.json`.

Wallets already running kingfisher keep running; it is only no longer offered.
