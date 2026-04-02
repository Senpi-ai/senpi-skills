# 🔥 PHOENIX v2.0 — Contribution Velocity Scanner (Hardened)

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

SM profit velocity diverging from price. When `contribution_pct_change_4h` is surging but price hasn't moved, SM knows something the market doesn't. Best trade: HYPE SHORT at 54x divergence, +50% ROE.

## v1.0.1 Post-Mortem

The signal was never the problem. v1.0.1 found real winners (+$24, +$22, +$11 on 4/1). The infrastructure killed it: broken trade counter led to 24 entries in one day instead of 6. -$228 in one day. -40.6% total.

## What v2.0 Fixes

- Trade counter increments BEFORE signal output (not dependent on exit path)
- Stale date detection forces reset
- Daily cap reduced to 4 entries (v1.0.1's best days had 3-5 winners)
- Budget set to $600 (remaining capital after v1.0.1 losses)

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 10x |
| Max positions | 3 |
| Max entries/day | 4 |
| Min score | 7 |
| DSL | Lifecycle hunter (180m, no time cuts) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
