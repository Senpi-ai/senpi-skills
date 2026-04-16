# 🐻‍❄️ POLAR v2.4 — ETH Alpha Hunter (sniper recalibration)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Single-asset ETH lifecycle hunter. Scores ETH using Smart Money concentration,
multi-timeframe contribution velocity, price momentum, and funding alignment.
Enters on high conviction, lets DSL manage all exits.

Best gross trader in the Predators fleet (+$196 lifetime gross PnL).

## v2.3 Changes (April 8, 2026)

From overnight position-level analysis of 5 trades:

- **UTC midnight cooldown bug fix** — cooldown timestamps now persist across
  date rollover. Previously, a trade at 22:42 UTC could be followed by a
  re-entry at 00:03 UTC (81 min later) because the date change wiped the
  cooldown memory.
- **Move-exhaustion scoring** — penalizes entering after large 4h moves.
  A 3% 4h move gets net +1 (confirmed). A 5% move gets net 0 (exhausted).
  Prevents the "catch breakout, exit, re-enter the same move at the top" pattern.
- **Same-direction re-entry cooldown** — after a winning exit, blocks
  re-entering the same direction for 60 minutes.

## Key Settings

| Setting | Value |
|---|---|
| Asset | ETH only |
| Leverage | 7x (score 8), 10x (9), 15x (10), 20x (11+) |
| Max positions | 1 |
| Min score | 8 |
| DSL hard timeout | 180 min |
| Margin | 50% |
| General cooldown | 120 min |
| Same-direction cooldown | 60 min (after wins) |
| Max daily entries | 4 |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
