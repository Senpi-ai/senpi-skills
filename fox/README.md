# 🦊 FOX v3.0 — Contra-Trend Striker

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Every Striker requires 4H price alignment. Fox v3.0 is the opposite: it REQUIRES the 4H price to oppose SM direction. When SM violently enters against the trend, they're front-running a reversal. Tighter gates (rank jump 20+, score 10+, SM traders 20+) because contra-trend is inherently riskier.

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 2 |
| Min score | 10 |
| Min rank jump | 20 (vs 15 for normal Striker) |
| Margin | 15% (smaller — higher risk) |
| DSL | Lifecycle hunter (180m, no time cuts) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
