---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, the Senpi trading fee (none on a
  paid plan), referral earnings — and explain the AI-credits usage meter. Use for "how many points
  do I have?", "what's my rank/tier?", "where did my tier go?", "what are my fees?", "my referral
  rewards", and for credits: "what is the bubble/meter in the header?", "why are my credits going
  down?", "how much did that cost?", "does my strategy use credits?", "can I trade or withdraw my
  credits?", and for plans: "what plans are there?", "how much
  does Senpi cost?", "what do I get on Pro/Quant?", "how do I get free credits?", "where are my
  milestone credits?", "do I need a subscription to keep my strategy running?", "what happens when my
  trial ends?". Use this instead of calling user_get_senpi_points by hand. A hidden engine
  (scripts/status.py) pulls points, the fee and referral in one call; the credit
  BALANCE is explained, never read — no tool returns it — while the plan catalog and the end of the
  free-credit milestones are published here and may be quoted. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.7.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Account Status — your standing across Senpi

A hidden engine pulls the user's standing in one real-time call; **your job is to present it
cleanly**: points and rank, the Senpi fee, and referral earnings.

## Golden rules

- **Run the engine; never hand-pull.** `python3 scripts/status.py` gathers points, the fee, and
  referral together. Read its JSON.
- **Real-time, not from memory.** Points/rank move — never quote a figure from earlier in the
  conversation; re-run.
- **The fee comes from the data, never from memory.** Quote `fee.pct` as returned: `0%` on a paid
  plan (Starter, Pro, Advanced, Quant), the flat free rate without one, free trials included.
- **Tiers carry no benefit right now: no fee change, nothing unlocked. Do not bring tiers up.** Never
  suggest reaching, keeping or regaining a tier, and never close with a next-tier offer. If the user
  asks about their tier or where it went, say tiers carry no benefit right now. Points still accrue
  at the plan multiplier and still set leaderboard rank.
- **Lead with what they asked.** If they asked about points, lead with points + rank. Don't dump
  every section if they asked one question — but the engine returns all of it so you can.
- **Credits are explained, never read.** No tool returns the AI-credit balance; the meter in the app
  header is the balance. Rules in "AI credits — the usage meter" below.
- **Plans and free credits are published; the balance is not.** Quote the catalog and the end of the
  milestones in "Plans & free credits" below — and only from there. A figure about *this user* (what
  is left, what they were charged, where their bar sits) comes from their page, never from you.

## How to run the engine

```
python3 scripts/status.py
```

Returns `{as_of, identity, points, fee, referral, meta}`:
- `points` — `total`, `base` (Base copy-trading), `perp` (Hyperliquid), `multiplier` (the plan's
  points rate), `rank`, `rank_change`.
- `fee` — `bps` + `pct`, the Senpi fee this user pays: `0` / `0%` on a paid plan, the flat free rate
  otherwise (`bps` is in tenths of a bp, so 30 = 0.03%).
- `referral` — `balance_usdc` (pending referral earnings, 25% of builder fee on referred trades).
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

Lead with the section they asked about; otherwise a tight standing card:

1. **Points & rank** — total, base/perp split, multiplier, rank (+ change).
2. **Fee** — `fee.pct` (`0%` = a paid plan, no Senpi fee).
3. **Referral** — pending `balance_usdc` (and that it's claimable if > 0).

Formatting: clean, scannable, numbers from the data; emoji sparingly.

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
  (each firing is a full model call), so name any the agent runs for this user. Using no credits is
  not the same as needing no plan: the strategy runs on the agent, and a lapsed plan pauses both
  (see "Plans & free credits").
- **Not trading money.** Credits cannot be traded, withdrawn, deposited into a strategy, or moved
  anywhere — they are separate from the funding wallet and from every strategy budget. A shrinking
  meter is usage, never a trading loss; a losing trade never touches the meter.
- **You cannot read the balance.** No Senpi tool returns it (`user_get_me` → id / wallets / createdAt
  / referralCode; `user_get_senpi_points` → points / fees) and the engine has no `credits`
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
| "Your strategy doesn't use credits, but it runs on your agent, and the agent needs an active plan. If the trial ends, a renewal fails or the credits run out, the agent and your strategies pause until you subscribe or credits return." | "no subscription is needed" / "your strategy keeps running without a plan" |

**Routing.** "What is the bubble / meter in the header?", "why are my credits going down?", "how much
did that cost?", "does my strategy use credits?", "can I trade / withdraw / deposit my credits?" all
come here, in any language. "Credits" (Ukrainian "кредити", Spanish "créditos") means these AI credits
unless the user says leverage, a loan or funding; when it is unclear, ask which before answering. A
credits question is answered from this section alone — no engine run, no number — and closes by
pointing at the meter. "What plans are there / how much
does it cost / what do I get on Pro / how do I get free credits / where are my milestone credits /
do I need a subscription / what happens when my trial ends" are **catalog** questions: answer them from "Plans & free credits" below, still without a figure
about this user.

## Plans & free credits

Published facts — quote them. Billed per **30-day cycle, rolling from the user's own start date**,
never a calendar month.

| Plan | Price | Credits | Points per $1 volume |
|---|---|---|---|
| Starter | $25 | $25 | 1 |
| Pro (default) | $50 | $55 (+10%) | 1.5 |
| Advanced | $100 | $115 (+15%) | 2 |
| Quant | $200 | $240 (+20%) | 3 |

- **Every plan includes every model.** Plans differ on credits and points rate only — never on model
  access, so a cheaper plan never locks anyone out of the better model.
- **No Senpi trading fee on any paid plan** — only Hyperliquid's own exchange fees. Without a paid
  plan, free trials included, Senpi charges a flat 0.03% on trades; points do not change it.
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
- **A lapsed plan pauses everything, not just chat.** Strategies run on the agent. When a trial or
  plan expires, a renewal payment fails, or the cycle's credits run out, Senpi pauses the agent and
  every strategy on it stops with it: no scans, no new entries, no stop moves. Open positions stay
  open, and a stop-loss already resting on the exchange still triggers. Funds stay in their wallets,
  withdrawable in the app. Subscribing, a successful renewal or an upgrade brings the agent and its
  strategies back. You cannot see the user's plan, trial or renewal dates: point at
  senpi.ai/settings/subscription, and never tell them a subscription is not needed.

**Free credits — the milestones ended on 2026-10-26.** Every free-credit milestone is over: your
first strategy, funding a wallet, the trading-volume ladder and the first-subscription bonus. None of
them pays any more, and the "Unlock Free AI Credits" card shows nothing. Its "Deploy your agent" row
went with it: the app built that row from the trial credit, and it was never a milestone.

- **Milestone credit already earned stays until the next renewal** — a different clock from the
  14-day trial expiry, so credit that vanished at a renewal boundary is working as designed.
- **The trial credit still activates when the user deploys their agent** (see Free trial above).
- Never offer a bonus for a strategy, a deposit, volume or subscribing. If the user's own page or
  card disagrees with this text, their page is right — never argue a user out of what is on their
  screen.

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
