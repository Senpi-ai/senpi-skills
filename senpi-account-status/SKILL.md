---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, loyalty tier and fees, referral
  earnings — and explain the AI-credits usage meter. Use for "how many points do I have?", "what's my
  rank/tier?", "what are my fees?", "my referral rewards", and for credits: "what is the bubble/meter
  in the header?", "why are my credits going down?", "how much did that cost?", "does my strategy use
  credits?", "can I trade or withdraw my credits?", and for plans: "what plans are there?", "how much
  does Senpi cost?", "what do I get on Pro/Quant?", "how do I get free credits?", "where are my
  milestone credits?". Use this instead of calling user_get_senpi_points +
  get_loyalty_tiers one by one. A hidden engine (scripts/status.py) pulls points/loyalty/referral in one call; the credit
  BALANCE is explained, never read — no tool returns it — while the plan catalog and the free-credit
  ladder are published here and may be quoted. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.4.0"
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
- **Credits are explained, never read.** No tool returns the AI-credit balance; the meter in the app
  header is the balance. Rules in "AI credits — the usage meter" below.
- **Plans and free credits are published; the balance is not.** Quote the catalog and the milestone
  ladder in "Plans & free credits" below — and only from there. A figure about *this user* (what is
  left, what they were charged, where their bar sits) comes from their page, never from you.
- **Always end with the CTA** (below) — for standing answers; a credits answer closes on the meter.

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

## AI credits — the usage meter

Senpi agents run on **AI-usage credits that come with the plan** — a trial grant first, then plan
credits. The app shows what is left as the **meter in the app header** (users call it "the bubble" or
"my credits"), and their subscription page is the source of truth for the number. What each plan
includes is published in "Plans & free credits" below and may be quoted; how much is **left** depends
on your plan and your usage, and only the user's own page shows that.

- **What spends them.** Every message the user sends and every tool turn the agent runs. Long threads
  cost more because the whole conversation is re-sent each turn — a fresh thread is cheaper than a
  long one. Scheduled monitoring (a cron that re-reads a strategy) is the classic silent drain;
  `senpi-strategy-author/references/shadow-testing.md` has the cheap alternatives.
- **A running strategy is not what spends them.** Its runtime ticks without a model call, so running
  it does not move the meter; a `decision_mode: llm` action in its `runtime.yaml` is the exception,
  since that calls a model when it fires. A cron the agent set up to check on a strategy does move it
  (each firing is a full model call), so name any the agent runs for this user.
- **Not trading money.** Credits cannot be traded, withdrawn, deposited into a strategy, or moved
  anywhere — they are separate from the funding wallet and from every strategy budget. A shrinking
  meter is usage, never a trading loss; a losing trade never touches the meter.
- **You cannot read the balance.** No Senpi tool returns it (`user_get_me` → id / wallets / createdAt
  / referralCode; `user_get_senpi_points` → points / tier / fees) and the engine has no `credits`
  section. Point at the meter in the app header, or at their subscription page for the exact figure —
  the in-app indicator surfaces only when credits run low, so a missing indicator is expected, not a
  lost balance. Never invent a number, a percentage or a "roughly", and never guess what the meter
  shows.
- **Chat is not free of credits.** Every turn spends them — including the turn that asks what a turn
  cost. Say so plainly; never "nothing".

| Say | Never say |
|---|---|
| "Chat and tool use spend AI credits from your plan; the meter in the app header is the balance — I can't read it from here. Longer threads cost more because the whole conversation is re-sent each turn." | "chatting is free" / "that cost nothing" |
| "What each plan includes is published — here's the table. How much is left depends on your plan and your usage; the meter shows that." | Any balance or percentage you did not read from the app, or a plan size you did not read from the table below |
| "Credits aren't money: they can't be traded, withdrawn or put into a strategy — your funding wallet and strategy budgets are separate." | "credits" as a tradable or withdrawable balance |
| "The meter going down is usage — it moves with messages and tool turns, not with your trades." | Blaming a shrinking meter on trading losses |
| "Your strategy runs on its own without the AI, so running it doesn't use credits. Chat does, and so does any scheduled check-in I run for you." | "running the strategy costs nothing" while the agent runs a check-in cron on it; a wallet or strategy balance offered as proof about credits |

**Routing.** "What is the bubble / meter in the header?", "why are my credits going down?", "how much
did that cost?", "does my strategy use credits?", "can I trade / withdraw / deposit my credits?" all
come here, in any language. "Credits" (Ukrainian "кредити", Spanish "créditos") means these AI credits
unless the user says leverage, a loan or funding; when it is unclear, ask which before answering. A
credits question is answered from this section alone — no engine run, no number — and closes by
pointing at the meter; the next-tier CTA is for standing answers. "What plans are there / how much
does it cost / what do I get on Pro / how do I get free credits / where are my milestone credits"
are **catalog** questions: answer them from "Plans & free credits" below, still without a figure
about this user.

## Plans & free credits

Published facts — quote them. Billed per **30-day cycle, rolling from the user's own start date**,
never a calendar month.

| Plan | Price | Credits | Points per $1 volume |
|---|---|---|---|
| Starter | $25 | $25 | 1 |
| Pro (default) | $50 | $50 | 1.5 |
| Advanced | $100 | $130 (+30%) | 2 |
| Quant | $200 | $270 (+35%) | 3 |

- **Every plan includes every model.** Plans differ on credits and points rate only — never on model
  access, so a cheaper plan never locks anyone out of the better model. Trading fees are separate and
  start at 0.05% on every plan.
- **$1 of credit = $1 of AI usage.** Full credits land at the start of the cycle and expire at the end
  of it: **no rollover, no top-ups, no overage**. Out of credits = upgrade (instant) or wait for renewal.
- **Upgrading is instant, any day** — the new plan's full credits land immediately, the 30-day cycle
  restarts, and unused credits are credited against the price, so they pay the difference.
  **Downgrading or cancelling takes effect at the next renewal**, never mid-cycle; benefits are kept
  until then and there are no mid-cycle refunds.
- **Payment is web only** (Stripe, senpi.ai/settings/subscription). The app shows plan, credits and
  renewal date but links out to the web page to change anything.
- **Free trial:** activates when the user **deploys their agent**, is one-time, and expires **14 days
  after activation**. **Never quote a trial figure** — the starting amount has changed more than once;
  their subscription page is the only source of truth. On expiry or exhaustion the agent stops until
  they subscribe: active strategies pause, **open positions are NOT auto-closed**, funds untouched.

**Free credits — the milestone ladder.** $315 of AI credit across 12 milestones, on the "Unlock Free
AI Credits" card:

| Milestone | Free credits |
|---|---|
| Deploy your agent | $10 |
| Launch your first strategy | $10 |
| Subscribe to a plan | $15 |
| Trade $2K / $25K / $50K volume | $10 each |
| Trade $100K / $250K volume | $20 / $30 |
| Trade $500K / $1M / $2M / $4M volume | $50 each |

- **Deploying the agent is the only one credited on day one.** Everything else is earned.
- **Not retroactive** — a milestone credits the next time it is achieved, counting from when
  milestones launched; activity before that does not fill the bar.
- **Milestone credits expire at the next renewal** — a different clock from the 14-day trial expiry,
  so credit that vanished at a renewal boundary is working as designed.
- **Volume progress is not live** — the card refreshes on a timer and shows a countdown, so a trade
  that has not moved the bar yet is normal, not a missed milestone.
- The bonus is **always AI credit** — it never lands in Balances as USDC.

**The card is the source of truth.** If the user's own page or card disagrees with any figure here,
their page is right and this table is stale — never argue a user out of what is on their screen, and
never tell them what their bar ought to read.

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
