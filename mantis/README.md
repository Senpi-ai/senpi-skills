# 🦗 MANTIS v3.0 — Dual-Mode Scanner + Contribution Threshold Experiment

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## The Experiment

MANTIS v3.0 is identical to the hardened base scanner with one tweak: **contribution acceleration threshold raised from 0.001 to 0.003, and the weak +1 tier eliminated**. Only genuine SM acceleration (delta > 0.003 per scan) earns the +2 CONTRIB_ACCEL bonus. Weak positive velocity is ignored.

## All Live Trading Fixes Included

| Fix | Value |
|---|---|
| Stalker minScore | 7 (was 6) |
| Stalker minTotalClimb | 8 (was 5) |
| Contrib accel threshold | **0.003 (experiment, was 0.001)** |
| Weak +1 tier | **Eliminated (experiment)** |
| Low-score Phase 1 | -18% floor, 25 min timeout, 8 min dead weight |
| Streak gate | 3 Stalker losses → minScore 9 |

Striker is unchanged. Same single `leaderboard_get_markets` call.

## License

MIT — see root repo LICENSE.
