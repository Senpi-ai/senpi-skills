# 🐋 ORCA v1.2 — Hardened Dual-Mode Scanner + Fox's Lessons

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What v1.2 Changes

Fox v1.0 ran the Orca scanner live for 5+ days. 17 of 20 closed positions were Stalker entries at score 6-7 with a 17.6% win rate (-$91.32 net). The "weak peak bleed": trades bump +0.5%, stall, DSL cuts for $3-$10.

| Fix | v1.1.1 | v1.2 |
|---|---|---|
| Stalker minScore | 6 | 7 |
| Stalker minTotalClimb | 5 | 8 |
| Low-score floor | -20% ROE | -18% ROE |
| Low-score timeout | 30 min | 25 min |
| Low-score dead weight | 10 min | 8 min |
| Streak gate | none | 3 Stalker losses → minScore 9 |

Striker is unchanged. No new API calls, no new data sources. Same single `leaderboard_get_markets` call.

## Changelog

### v1.2
- Stalker minScore: 6 → 7, minTotalClimb: 5 → 8
- Low-score conviction tier tightened: floor -18%, timeout 25 min, dead weight 8 min
- Stalker consecutive-loss streak gate: 3 losses → minScore raised to 9
- `record_stalker_result()` added to orca_config.py for streak tracking
- SKILL.md hardened with all 8 agent rules

### v1.1.1
- Fixed DSL field names: `phase1MaxMinutes`, `deadWeightCutMin`
- `highWaterPrice` initialized as `null`
- Removed static `absoluteFloor` price values

## License

MIT — see root repo LICENSE.
