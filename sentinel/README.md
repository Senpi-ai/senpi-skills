# 🛡️ SENTINEL v2.2 — Quality Trader Convergence Scanner

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

Inverted pipeline: start with QUALITY TRADERS (ELITE/RELIABLE TCS), find where they converge. When 5+ historically-profitable traders independently arrive at the same trade, that's informed consensus — not coincidence. Cross-confirmed with SM leaderboard concentration.

## v2.2 Changelog (fleet-fix batch 4)

At -21.44% drawdown with daily cap = 1, the 45% win rate confirmed the signal is valid. DSL was bleeding value via slow cuts on 17/23 losers. Widening Phase 2 and resetting the budget baseline.

- Phase 2 tiers widened to Sentinel's own rec: `[15/35, 30/60, 50/75, 75/85, 100/92]`
- `STARTING_BUDGET` 1000 → 786.60 (current equity, unblocks pnl-aware cap)

## Key Settings

| Setting | Value |
|---|---|
| Leverage | 7x |
| Max positions | 2 |
| Min score | 7 |
| Min convergence | 5 weighted traders |
| API calls | 2 per scan |
| DSL | Lifecycle hunter (240m timeout, wider Phase 2 tiers) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
