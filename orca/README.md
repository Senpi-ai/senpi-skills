# 🐋 ORCA v2.0 — Gen-2 Striker with Quality Confirmation

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Same FIRST_JUMP explosion detection as Roach/Jaguar/Mantis v4.0, but enhanced with Gen-2 Hyperfeed signals. Tier 2 momentum events ($5.5M+ threshold) confirm that quality traders (TCS ELITE or RELIABLE) are driving the move — filtering out pump-and-dumps from CHOPPY/DEGEN traders.

## v1.x Post-Mortem

- v1.1: 1,204 fills, -19.3% ROE. Stalker + Striker dual mode. Stalker churned at 43% win rate.
- v1.3: 336 fills, -14.8% ROE. Stalker experiment confirmed: 58 Stalker trades lost, 1 Striker trade won.

## What's New in v2.0

- Stalker: **permanently removed**
- Gen-2 quality confirmation: Tier 2 momentum events cross-referenced with TCS trader quality tags
- ELITE trader bonus: extra conviction when the best traders are moving
- Contribution acceleration as score booster
- Quality confirmation is a booster, not a hard gate — agent still trades when Striker signals are strong enough alone

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 3 |
| Min score | 9 |
| API calls | 2 per scan (markets + momentum events) |
| DSL | Fast-cycling (30m timeout, 15m weak peak) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
