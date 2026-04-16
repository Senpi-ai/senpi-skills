# 🐋 ORCA v3.0 — Gen-1 Vanilla Striker

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Pure FIRST_JUMP explosion detection on `leaderboard_get_markets`. Detect violent rank jumps (rank ≥ 25 → ≥ 15-position jump), confirm with volume spike (≥ 1.5x day vs previous day), require 4H price alignment with SM direction. Single API call, minimum latency.

## v3.0 Changelog (fleet-fix batch 4)

Reverted Gen-2 quality confirmation per Orca's own self-diagnosis: "Gen-2 confirmation adds latency and buys local tops after the move." Back to vanilla Gen-1.

- Removed `leaderboard_get_momentum_events` API call
- Removed TCS ELITE/RELIABLE gate and ELITE_BONUS score booster
- Removed `contribution_pct_change_4h` acceleration booster
- Single API call per scan (down from 2)
- Leverage clamping applied to emitted entry (fleet-wide batch-4 safety fix)

## v1.x Post-Mortem

- v1.1: 1,204 fills, -19.3% ROE. Stalker + Striker dual mode. Stalker churned at 43% win rate.
- v1.3: 336 fills, -14.8% ROE. Stalker experiment confirmed: 58 Stalker trades lost, 1 Striker trade won.
- v2.0: Gen-2 quality confirmation added latency, bought local tops. Reverted in v3.0.

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 3 |
| Min score | 9 |
| API calls | 1 per scan (markets only) |
| DSL | Fast-cycling (30m timeout, 15m weak peak) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
