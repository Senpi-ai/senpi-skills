---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, loyalty tier and fees, and
  referral earnings. Use for "how many points do I have?", "what's my rank/tier?",
  "what are my fees?", "my referral rewards". Use this instead of calling
  user_get_senpi_points + get_loyalty_tiers one by one. A hidden engine (scripts/status.py)
  pulls it all in one call. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.2.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Account Status — your standing across Senpi

A hidden engine pulls the user's standing in one real-time call; **your job is to present it
cleanly** and point at the next milestone (next loyalty tier, etc.).

## Golden rules

- **Run the engine; never hand-pull.** `python3 scripts/status.py` gathers points, loyalty, and
  referral together. Read its JSON.
- **Real-time, not from memory.** Points/rank move — never quote a figure from earlier in the
  conversation; re-run.
- **Fees come from the data, never from memory.** Quote `loyalty.fee_bps` / `fee_discount_pct` as
  returned — the tier table changes; stale numbers mislead. (For the *full* tier table, that's
  `get_loyalty_tiers`; this skill returns the user's *own* tier.)
- **Lead with what they asked, then the milestone.** If they asked about points, lead with points +
  rank, then "X to the next tier." Don't dump every section if they asked one question — but the
  engine returns all of it so you can.
- **Always end with the two CTAs** (below).

## How to run the engine

```
python3 scripts/status.py
```

Returns `{identity, points, loyalty, referral, meta}`:
- `points` — `total`, `base` (Base copy-trading), `perp` (Hyperliquid), `multiplier`, `rank`,
  `rank_change`.
- `loyalty` — `tier`, `fee_bps` + `fee_pct`, `fee_discount_pct`, `next_tier`, `points_to_next` (the
  milestone), plus `demoted` / `previous_tier` / `maintenance_deadline` — if `demoted` is true,
  mention the tier they fell from and that maintenance volume wins it back.
- `referral` — `balance_usdc` (pending referral earnings, 25% of builder fee on referred trades).
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

Lead with the section they asked about; otherwise a tight standing card:

1. **Points & rank** — total, base/perp split, multiplier, rank (+ change).
2. **Loyalty** — current tier + fee/discount, and **`points_to_next` → next_tier** (the actionable
   milestone).
3. **Referral** — pending `balance_usdc` (and that it's claimable if > 0).

Formatting: clean, scannable, numbers from the data; emoji sparingly (🎯 next tier).

## Mandatory closing (verbatim)

> **Want me to break down what it takes to reach the next tier?**

On yes: use `loyalty.points_to_next` + `get_loyalty_tiers` for the tier path.

## ⚠ Token scope

Every tool here is **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`.
App-scoped → no user resolves and `meta.degraded`; say so rather than reporting zeros.

## Skill Attribution

Guide/analysis skill — it *reads* the user's standing; it does not mutate anything (claiming referral
rewards is a separate explicit action via `user_claim_referral_rewards`, which this skill never calls).


## Install — both scripts are required

The engine is **two files** in `scripts/`: `status.py` (the engine) and `mcp_client.py` (its vendored
MCP helper, imported at runtime). **Install the whole `scripts/` directory** — copying `status.py`
alone fails with `No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
