# 🕷️ SPIDER v1.0 — Elite Convergence Scanner

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Spider enters a trade only when two or more of Hyperliquid's highest-quality
traders independently converge on the same asset and direction, using real-time
SM velocity as the timing trigger.

Every 5 minutes it builds a convergence map by fetching the top weekly
ELITE/RELIABLE traders with SNIPER/AGGRESSIVE risk profiles and mapping their
open positions. Every 90 seconds it checks 15-minute SM velocity on assets
where convergence exists — the velocity spike is the entry trigger.

## Key Settings

| Setting | Value |
|---|---|
| Assets | Multi-asset (any with elite convergence) |
| Leverage | 7x (score 8-9), 10x (score 10+) |
| Max positions | 1 |
| Min score | 8 |
| Min convergence | 2 ELITE/RELIABLE traders |
| DSL hard timeout | 180 min |
| Margin | 50% |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
