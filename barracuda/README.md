# 🐟 BARRACUDA v1.0.1 — Funding Decay Collector

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## What BARRACUDA Does

Finds assets where extreme funding has persisted for 6+ hours, confirmed by SM alignment from Hyperfeed and 4H trend structure. Enters to collect funding while riding the trend. Double edge: price appreciation + funding income.

Fixes Croc's -42.7% failure by requiring SM and trend confirmation before entering any funding trade.

## Six Gates

1. Extreme funding (30%+ annualized)
2. Persistent 6+ hours (not a spike)
3. SM aligned (Hyperfeed)
4. 4H trend confirms
5. RSI safe
6. Leverage >= 5x

## Quick Start

1. Deploy config and scripts
2. Create scanner cron (15 min, isolated) and DSL cron (3 min, isolated)
3. Fund with $1,000

## License

MIT — see root repo LICENSE.

## Changelog

### v1.0.1
- Fixed DSL field names: `phase1MaxMinutes` (was `hardTimeoutMinutes`), `deadWeightCutMin` (was `deadWeightCutMinutes`)
- `highWaterPrice` initialized as `null` (was `0`) — lets dsl-v5.py set from actual entry price on first tick
- Removed static `absoluteFloor` price values — dsl-v5.py now calculates dynamically from `absoluteFloorRoe`
- Standalone config helper (no Tiger dependencies), all noise gates enforced
- Requires dsl-v5.py with Patch 1 (dynamic absoluteFloorRoe calculator) and Patch 2 (highWaterPrice null handling)
