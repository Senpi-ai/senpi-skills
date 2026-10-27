# Presentation notes — standing, not analysis

This skill *presents* the user's standing; it doesn't analyze a market. A few rules so it reads well.

## Lead with the question, carry the rest

The engine returns everything (points, fee, referral) in one call, but the user
usually asked one thing. Lead with that section, then offer the rest. "How many points do I have?" →
lead with `points.total` + `rank` — don't open with the referral balance.

## What a standing answer holds

A standing answer is points, rank, the fee and referral. Tiers carry no benefit right now, so it has
no tier line: no distance to a higher one, no story of a lost one, never a closing offer about one.

On referral, if `balance_usdc > 0`, say it's claimable (a separate explicit action — this skill
doesn't claim it).

## Honesty rules

- **The fee from the data, never memory.** Quote `fee.pct` as returned — `0%` on a paid plan, the
  flat free rate otherwise.
- **`found: false` on points** means no leaderboard entry yet (base defaults) — present it as
  "you haven't started earning yet," not "0 / error."
- **Decimals arrive as strings** from several of these tools (e.g. balance_usdc) — the
  engine parses them; just present clean numbers.
