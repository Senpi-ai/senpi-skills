# 🐍 PYTHON v1.0 — The Patience Hunter

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

The fleet's first multi-day hold agent. While every other Senpi predator rotates in hours, Python waits days. Derived from pr0br000's Arena Week 2-3 pattern (+221% in 27 days, 36% win rate, 3.14:1 win/loss ratio).

## Key Settings

| Setting | Value |
|---|---|
| Universe | Top 50 HL perps by 24h volume (crypto only) |
| Min OI | $1M |
| Min trader_count | 30 |
| Min score | 8 |
| Max positions | 2 concurrent |
| Leverage cap | 7x (hard) |
| Margin | 25% / 30% / 40% by score tier |
| Daily cap | 3 entries (dynamic) |
| Per-asset cooldown | 12h |
| DSL | Patient profile (96h timeout) |

## Core differences vs other predators

- **Multi-day holds** (up to 96h) — every other agent rotates <24h
- **Low leverage** (3x base, 7x apex) — other agents hit 10x
- **Wide universe, low gate** — MIN_SCORE=8 vs Condor's 11
- **LONG-biased** — pr0br000's top 5 winners were all LONG
- **Loose early DSL locks** — +5% only locks 15% (lets winners breathe)
- **Tight late DSL locks** — +200% locks 94% (captures monster trails)

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
