# 🦅 RAPTOR v2.0 — Hot Streak Follower

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

When a quality trader (TCS ELITE/RELIABLE) crosses $5.5M+ in delta PnL (Tier 2 momentum event), they're on a hot streak. Raptor identifies their strongest position and follows them into it — confirmed by SM leaderboard alignment.

Different from Orca v2.0 (which uses momentum as Striker confirmation) and Sentinel v2.0 (which looks for multi-trader convergence). Raptor follows INDIVIDUAL hot traders into their best trade.

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 2 |
| Min score | 7 |
| Momentum tier | 2 ($5.5M+) |
| Quality gate | ELITE/RELIABLE only |
| Min concentration | 50% |
| DSL | Lifecycle hunter (180m timeout, no time cuts) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
