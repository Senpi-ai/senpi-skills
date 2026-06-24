---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi's programs — Senpi points + rank,
  loyalty tier + fee discount, Agents Arena position + prize context, referral
  earnings, and shareable wins. Use when the user asks "how many points do I
  have?", "what's my rank?", "what tier am I?", "what are my fees?", "how am I
  doing in the Arena?", "what have I earned from referrals?", "show my best
  trades / wins". A hidden engine (scripts/status.py) pulls all of it in one
  real-time call; you present the standing and the next milestone. Requires a
  USER-scoped Senpi MCP token. For fleet-wide fee tiers (not the user's own),
  call get_loyalty_tiers; for live market/portfolio data use the other skills.
license: Apache-2.0
compatibility: OpenClaw, Hyperclaw, Claude Code
metadata:
  author: Senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Account Status — your standing across Senpi

A hidden engine pulls the user's standing in one real-time call; **your job is to present it
cleanly** and point at the next milestone (next loyalty tier, Arena prize, etc.).

## Golden rules

- **Run the engine; never hand-pull.** `python3 scripts/status.py` gathers points, loyalty, Arena,
  referral, and wins together. Read its JSON.
- **Real-time, not from memory.** Points/rank/Arena move — never quote a figure from earlier in the
  conversation; re-run.
- **Fees come from the data, never from memory.** Quote `loyalty.fee_bps` / `fee_discount_pct` as
  returned — the tier table changes; stale numbers mislead. (For the *full* tier table, that's
  `get_loyalty_tiers`; this skill returns the user's *own* tier.)
- **Lead with what they asked, then the milestone.** If they asked about points, lead with points +
  rank, then "X to the next tier." Don't dump every section if they asked one question — but the
  engine returns all of it so you can.
- **Arena scope.** `arena.enrolled: false` means they're not in the competition — say so plainly and
  point to senpi.ai/arena; don't report a rank they don't have.
- **Always end with the two CTAs** (below).

## How to run the engine

```
python3 scripts/status.py
```

Returns `{identity, points, loyalty, arena, referral, wins, meta}`:
- `points` — `total`, `base` (Base copy-trading), `perp` (Hyperliquid), `multiplier`, `rank`,
  `rank_change`.
- `loyalty` — `tier`, `fee_bps`, `fee_discount_pct`, `next_tier`, `points_to_next` (the milestone).
- `arena` — `enrolled`; if enrolled: `rank`, `roe_pct`, `total_pnl_usd`, `qualified` (hit the volume
  threshold), `week_pool_usd`, `prize_estimate_usd` (if top-5).
- `referral` — `balance_usdc` (pending referral earnings, 25% of builder fee on referred trades).
- `wins` — recent profitable closed trades (asset, realized PnL, return %) for sharing.
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

Lead with the section they asked about; otherwise a tight standing card:

1. **Points & rank** — total, base/perp split, multiplier, rank (+ change).
2. **Loyalty** — current tier + fee/discount, and **`points_to_next` → next_tier** (the actionable
   milestone).
3. **Arena** — if `enrolled`: rank, ROE %, qualified-or-not, and the prize context (week pool;
   estimate if top-5). If not enrolled: one line + the arena link.
4. **Referral** — pending `balance_usdc` (and that it's claimable if > 0).
5. **Wins** — a couple of brag-worthy recent trades, if any.

Formatting: clean, scannable, numbers from the data; emoji sparingly (🏆 Arena, 🎯 next tier).

## Mandatory closing (verbatim)

> **1. Want me to share one of these wins?**
> **2. Want me to break down what it takes to reach the next tier (or climb the Arena)?**

- **CTA 1 → share.** Format a `wins[]` entry into a shareable line (the user posts it); never post on
  their behalf.
- **CTA 2 → milestone.** Use `loyalty.points_to_next` + `get_loyalty_tiers` for the tier path, or the
  Arena `qualified`/volume threshold + `week_pool`/prize split for the Arena path.

## ⚠ Token scope

Every tool here is **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`.
App-scoped → no user resolves and `meta.degraded`; say so rather than reporting zeros.

## Skill Attribution

Guide/analysis skill — it *reads* the user's standing; it does not mutate anything (claiming referral
rewards is a separate explicit action via `user_claim_referral_rewards`, which this skill never calls).
