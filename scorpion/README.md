# SCORPION v3.0 — Multi-Market Active Trader

**The only predator that hunts across both crypto and commodities.**

Scorpion trades BOTH the main Hyperliquid DEX (crypto) and the XYZ DEX (commodities/indices) using SM concentration + 4H price trend alignment. Arena winner #2/#3 playbook.

## Quick Stats
- **Strategy**: SM trend-following across crypto + XYZ DEX
- **Universe**: BTC, ETH, SOL, HYPE, ZEC, LIT, GRASS, FARTCOIN, TAO, ONDO, SUI, ARB, WLD, DOGE, AVAX + xyz:CL, xyz:BRENTOIL, xyz:GOLD, xyz:SPX
- **Margin**: 30% per position (3 x 30% = 90% max exposure)
- **Leverage**: 5-10x (score-scaled)
- **Entry threshold**: Score 6+ (SM + trend + velocity + depth)
- **Max positions**: 3 concurrent
- **Hard timeout**: 720 minutes (12 hours)
- **Daily cap**: 6 entries, 120-min per-asset cooldown
- **Order type**: FEE_OPTIMIZED_LIMIT with ensureExecutionAsTaker

## What Makes This Different
No other Senpi predator trades XYZ DEX assets. Scorpion v3.0 trades crude oil, Brent, gold, and SPX alongside crypto — capturing opportunities across uncorrelated markets.

## XYZ DEX Handling
- XYZ assets use `xyz:` prefix in create_position calls (e.g., `coin="xyz:CL"`)
- XYZ assets require `leverageType="ISOLATED"`
- XYZ price thresholds are lower (commodities move 0.5-3% in 4h vs 1-5% for crypto)

## Install
See [SKILL.md](SKILL.md) for full setup instructions.

## License
MIT — Built by [Senpi](https://senpi.ai)
