# 🐺 JACKAL v1.0 — The Smart Stalker

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The fleet's first SECONDARY-SIGNAL agent. Observes top-performing Senpi users and executes filtered, consensus-weighted trades with its own sizing and own DSL. NOT a passive mirror — a smart stalker with independent decision logic.

> Replaces the earlier Jackal v2.0 concept (FOX config-override pyramider). That design is preserved in `legacy-fox-pyramid-concept/` for reference.

## Key Settings

| Setting | Value |
|---|---|
| Pool architecture | Two-tier (Watchlist ~200 + Active ~30) |
| Quality scoring | 6-component trajectory-based composite |
| Min signal score | 65 (BASE) / 75 (STRONG) / 85 (GOLD) |
| Consensus multiplier | 1.0x / 1.8x / 3.0x (1/2/3+ sources) |
| Max positions | 3 concurrent |
| Leverage cap | 7x |
| Margin tier | 20% / 35% / 55% by score |
| Per-source cap | 40% of budget |
| TA gate | Required — SM, 4h trend, 1h momentum |
| DSL | Consensus-aware patient profile (72h timeout) |

## What's different

- **Reads other traders' actions as signals** — via the new any-user-lookup MCP capability
- **Trajectory scoring** — catches rising traders BEFORE rank-1
- **Consensus multiplier** — 3+ trader agreement = max conviction
- **GOLD SIGNAL** — newly-promoted source + existing pool consensus = biggest sizing
- **Scalper filter** — drops traders whose avg winning hold <2h
- **Auto-demotion** — drawdown >10% or score <50 = source demoted, 48h cooldown
- **Independent DSL** — exits on own terms, not source's

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
