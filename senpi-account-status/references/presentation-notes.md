# Presentation notes — standing, not analysis

This skill *presents* the user's standing; it doesn't analyze a market. A few rules so it reads well.

## Lead with the question, carry the rest

The engine returns everything (points, loyalty, arena, referral) in one call, but the user
usually asked one thing. Lead with that section, then offer the rest. "How many points do I have?" →
lead with `points.total` + `rank`, then *"you're 5,000 from Gold"* — don't open with the Arena.

## Always point at the next milestone

Standing is more motivating with a target. Pair every section with its next step:
- **Loyalty** → `points_to_next` to `next_tier` (and what the next tier's fee discount buys).
- **Arena** → if not `qualified`, the volume threshold to become prize-eligible; if ranked, the
  prize at their rank vs. the rank above.
- **Referral** → if `balance_usdc > 0`, that it's claimable (a separate explicit action — this skill
  doesn't claim it).

## Honesty rules

- **Fees from the data, never memory.** Quote `loyalty.fee_bps` / `fee_discount_pct` as returned —
  the tier table changes.
- **Arena scope.** `enrolled: false` means not in the competition — say so and link senpi.ai/arena;
  never invent a rank.
- **`found: false` on points** means no leaderboard entry yet (base-tier defaults) — present it as
  "you haven't started earning yet," not "0 / error."
- **Demotion is a story, not a stat.** If `loyalty.demoted` is true, say it plainly — "you held
  APEX, maintenance lapsed, you're at BRONZE now" — and pair it with the way back
  (`maintenance_deadline`, maintenance volume). Never present a demoted user's tier as if it were
  their lifetime standing.
- **Decimals arrive as strings** from several of these tools (roePct, prizeAmount, balance_usdc) — the
  engine parses them; just present clean numbers.
