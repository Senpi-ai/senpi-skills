# 🐉 HYDRA v2.0 — Squeeze Detector

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Finds crowded trades about to unwind. Funding extreme + SM positioned against the crowd + price starting to move = squeeze. Goes opposite to the funding crowd. Only liquid assets ($20M+ daily volume).

v1.0 had 20% win rate trading illiquid garbage on minimum conviction. v2.0: clean 2-source scanner (funding + SM divergence), $20M volume gate, direction-agnostic.

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 2 |
| Min score | 7 |
| Min daily volume | $20M |
| DSL hard timeout | 180 min |
| Phase 2 tiers | 6 tiers: 5%/25% → 50%/90% |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
