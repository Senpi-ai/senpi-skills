# 🔥 PHOENIX v3.0 — Contribution Velocity Scanner

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## Thesis

SM profit velocity diverging from price. When `contribution_pct_change_4h` is surging but price hasn't moved, SM knows something the market doesn't. Best trade: HYPE SHORT at 54x divergence, +50% ROE.

## v3.0 Changelog (fleet-fix batch 4)

Phoenix was locked by the circuit breaker at -36.3% drawdown. Scanner diagnostics show 54% of losers were killed by `weak_peak_cut` — valid signals cut before they could run. Adopting Lemon's DSL profile + resetting the budget baseline.

- Removed `weak_peak_cut` block entirely
- `hard_timeout` 45m → 480m (winners need time)
- `dead_weight_cut` → 20m
- Phase 1 loosened (max_loss 15%, retrace 8, breaches 3)
- Phase 2 tiers widened (Lemon ladder: 5/20, 10/40, 15/60, 20/75, 30/85, 50/92)
- `STARTING_BUDGET` 1000 → 637.93 (current equity, unblocks pnl-aware cap)

## v1.0.1 Post-Mortem

The signal was never the problem. v1.0.1 found real winners (+$24, +$22, +$11 on 4/1). The infrastructure killed it: broken trade counter led to 24 entries in one day instead of 6. -$228 in one day. -40.6% total.

## v2.0 Retained Fixes

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
| DSL | Lemon profile (480m timeout, no weak_peak_cut, wider phase 2) |

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
