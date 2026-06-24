# senpi-account-status

A Senpi skill that shows the user's **standing across Senpi's programs** in one real-time call —
Senpi points + rank, loyalty tier + fee, Agents Arena position + prize context, referral earnings,
and shareable wins.

Same hidden-engine pattern as the other read skills. Collapses these read tools off the eager tool
list (per the context-reduction plan): `arena_leaderboard`, `arena_pool`, `arena_prizes`,
`arena_roe_chart`, `user_get_senpi_points`, `user_get_senpi_points_leaderboard`, `get_loyalty_tiers`,
`get_share_your_wins`, `user_get_referral_rewards`.

## What it returns

`{identity, points, loyalty, arena, referral, wins, meta}`:
- **points** — total, base (Base copy-trading) / perp (Hyperliquid) split, multiplier, rank, change.
- **loyalty** — tier, fee bps + discount, and `points_to_next` → `next_tier` (the milestone; derived
  from `get_loyalty_tiers` if the points response doesn't carry it).
- **arena** — `enrolled`; if so: rank, ROE %, qualified flag, week pool, and prize estimate if top-5.
- **referral** — pending `balance_usdc` (25% of builder fee on referred trades).
- **wins** — recent profitable closed trades for sharing.

## Run

```sh
python3 scripts/status.py
python3 scripts/status.py --dry      # raw schema dump
python3 scripts/status.py --fixture tests/fixtures/status_fixture.json   # offline (tests)
```

## ⚠ Token scope

All tools are **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`.
App-scoped → no user resolves and `meta.degraded`.

## Status / review notes

- Input params verified against the live MCP tool schemas (`user_get_senpi_points` takes
  `input.{walletAddress|userId}`; arena tools take `period_type`). Output field names use defensive
  alias fallbacks — run `--dry` to confirm the live response shape and adjust if needed.
- Read-only: never calls `user_claim_referral_rewards` (claiming is a separate explicit action).
- Offline fixture test included; no network.
