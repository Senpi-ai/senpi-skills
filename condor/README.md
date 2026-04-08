# 🦅 CONDOR v2.0 — Multi-Asset Thesis Picker

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Evaluates BTC, ETH, SOL, HYPE simultaneously. Picks the single strongest thesis. Conviction-scaled margin: 25% at base, 35% at medium, 45% at high conviction. Patient — only enters when one asset has a clear directional edge.

## Key Settings

| Setting | Value |
|---|---|
| Assets | BTC, ETH, SOL, HYPE |
| Leverage | 7x |
| Max positions | 1 (best thesis only) |
| Min score | 8 |
| DSL hard timeout | 180 min |
| Margin scaling | 25% / 35% / 45% by conviction |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
