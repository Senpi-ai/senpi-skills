# 🐻 KODIAK v2.0 — SOL Alpha Hunter

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

SOL-only lifecycle hunter. Three modes: HUNT (scan for entry) → RIDE (DSL trails) → STALK (watch for reload). Based on Grizzly's BTC lifecycle, adapted for SOL's volatility.

## v2.0 Highlights

- **+$134 on a single SOL SHORT** — DSL trailed to Tier 4, locked 85% of peak
- Thesis exit removed — DSL manages all exits
- DSL state includes wallet + size (fixes the bug that left positions unprotected)
- Leverage capped at 7x (was 10-12x)
- Retrace widened to 0.08 (positions hold through normal SOL oscillation)

## Key Settings

| Setting | Value |
|---|---|
| Asset | SOL only |
| Leverage | 7x max |
| Max positions | 1 |
| Entry score | 10+ |
| DSL retrace | 0.08 |
| Trailing tiers | 7%/40%, 12%/55%, 15%/75%, 20%/85% |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
