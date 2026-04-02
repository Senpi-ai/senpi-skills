# 🦉 OWL v6.0 — Pure Contrarian Crowding Scanner

The only agent that enters AGAINST the crowd.

## Thesis

When SM is heavily concentrated (12%+, 60+ traders), funding is extreme
(crowd is paying to hold), and price has stalled (the trade stopped working),
the crowd is trapped. OWL enters opposite to ride the liquidation unwind.

## v5.2 → v6.0 Changes

| v5.2 | v6.0 |
|---|---|
| 3-phase pipeline (crowding + persistence + exhaustion) | **Simplified: crowding + funding + price exhaustion** |
| Persistence timer (4+ hours) | **Removed — complexity prevented firing** |
| RSI divergence, volume declining, OI concentration | **Removed — too many API calls, too many gates** |
| Old DSL cron architecture | **Plugin runtime** |
| Funding floor 12% annualized | **Funding rate 0.015%/hr minimum** |
| Re-crowding exit at score 6 | **Re-crowding exit: SM must increase 5+ points AND funding 1.5x worse** |

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 1 |
| Min score | 8 |
| Cooldown | 360 min (6 hours) |
| DSL max loss | 30% ROE (widest in fleet) |
| DSL retrace | 12% ROE (widest in fleet) |
| DSL Phase 2 trigger | +10% ROE |
| Hard timeout | 360 min (6 hours) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
