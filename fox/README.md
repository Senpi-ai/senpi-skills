# 🦊 FOX v2.0 — Dual-Mode Scanner + minReasons Experiment

Part of the [Senpi Trading Skills](https://github.com/Senpi-ai/senpi-skills).

## The Experiment

FOX v2.0 is identical to the hardened base scanner with one tweak: **Stalker entries require at least 3 distinct scoring reasons**, not just a score >= 7. This tests whether requiring breadth of confirmation (not just depth of score) reduces the weak-peak bleed that cost Fox v1.0 $91.32 across 17 low-score Stalker trades.

## All Live Trading Fixes Included

| Fix | Value |
|---|---|
| Stalker minScore | 7 (was 6) |
| Stalker minTotalClimb | 8 (was 5) |
| Stalker minReasons | **3 (experiment)** |
| Low-score Phase 1 | -18% floor, 25 min timeout, 8 min dead weight |
| Streak gate | 3 Stalker losses → minScore 9 |

Striker is unchanged. Same single `leaderboard_get_markets` call.

## License

MIT — see root repo LICENSE.
