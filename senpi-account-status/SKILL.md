---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, the Senpi trading fee (none on a
  paid plan or while the account holds credits), referral earnings — and explain the AI-credits usage meter. Use for "how many points
  do I have?", "what's my rank/tier?", "where did my tier go?", "what are my fees?", "my referral
  rewards", "how do referrals work?", and for credits: "what is the bubble/meter in the header?", "why are my credits going
  down?", "how many credits do I have?", "how much did that cost?", "does my strategy use credits?", "can I trade or withdraw my
  credits?", and for plans: "what plans are there?", "how much
  does Senpi cost?", "what do I get on Pro/Quant?", "how do I get free credits?", "where are my
  milestone credits?", "do I need a subscription to keep my strategy running?", "what happens when my
  trial ends?". Use this instead of calling user_get_senpi_points by hand. A hidden engine
  (scripts/status.py) pulls points, the fee, referral and the credit balance (in credits, from
  user_get_credits) in one call; the plan catalog and the end of the free-credit milestones are
  published here and may be quoted. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.11.4"
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
- **The fee comes from the data, never from memory.** Quote `fee.pct` as returned. Asked what an
  account with no plan and no credits pays, quote the flat fee in the Senpi MCP's fee rule (in its
  instructions, already in context) in the same reply; state no rate that neither gives.
- **Tiers carry no benefit right now: no fee change, nothing unlocked. Do not bring tiers up.** Never
  suggest reaching, keeping or regaining a tier, and never close with a next-tier offer. If the user
  asks about their tier or where it went, say tiers carry no benefit right now. Points still accrue
  at the plan multiplier and still set leaderboard rank.
- **Lead with what they asked.** If they asked about points, lead with points + rank. Don't dump
  every section if they asked one question — but the engine returns all of it so you can.
- **Credits:** see "AI credits — the usage meter" below.
- **Plans and free credits are published.** Quote the catalog and the end of the milestones in
  "Plans & free credits" below, and only from there.

## How to run the engine

```
python3 scripts/status.py
```

Returns `{as_of, identity, points, fee, referral, credits, meta}`:
- `points` — `total`, `base` (Base copy-trading), `perp` (Hyperliquid), `multiplier` (the plan's
  points rate), `rank`, `rank_change`.
- `fee` — `bps` + `pct`, the Senpi fee this user pays, as the backend reports it: `0` / `0%` on a
  paid plan (Pro, Advanced, Quant, and Starter for people already on it) or while the account holds credits (quote `pct`, never convert `bps`). `null` when the backend
  could not read it: say the fee could not be read right now, never `0%` and never a rate.
- `referral` — `balance_usdc` (referral earnings not yet claimed).
- `credits` — `available`, and when true `remaining` (credits), `burn_per_hour` and `hours_left`
  (each may be null), `cycle_end`. `{"available": false}` when the balance could not be read.
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

Lead with the section they asked about; otherwise a tight standing card:

1. **Points & rank** — total, base/perp split, multiplier, rank (+ change).
2. **Fee** — `fee.pct` as returned.
3. **Referral** — pending `balance_usdc` (and that it's claimable if > 0).

Formatting: clean, scannable, numbers from the data; emoji sparingly.

## AI credits — the usage meter

Senpi agents run on **AI-usage credits that come with the plan** — a trial grant first, then plan
credits. The app shows what is left as the **meter in the app header** (users call it "the bubble" or
"my credits"). What each plan includes is published in "Plans & free credits" below and may be
quoted; how much is **left** depends on your plan and your usage, and the engine's `credits` section
reads it.

- **How many credits do I have.** Run the engine. When `credits.available` is true, answer with
  `credits.remaining` as a number of credits, and the time left when `hours_left` is present. Never
  convert it to dollars, never say a dollar amount of credit, and never round it into a "roughly".
- **When `credits.remaining` is 0**, the user is out of credits, and that also stops the agent: the AI
  stops, simulations pause and strategies that run on the agent stop. Stop-losses already on the
  exchange stay there, mirror strategies keep running, open positions are not closed and funds are
  untouched. Buying credits, upgrading or the renewal brings the AI back.
- **When `credits.available` is false**, you cannot read the balance: point at the meter in the app
  header or the subscription page (senpi.ai/settings/subscription) for the exact figure, and never
  invent a number, a percentage or a "roughly". The in-app indicator surfaces only when credits run
  low, so a missing indicator is expected, not a lost balance.
- **What else they pay for.** Credits also pay for running Hypersims. Say so; give no rate.

- **What spends them.** Every message the user sends and every tool turn the agent runs. Long threads
  cost more because the whole conversation is re-sent each turn — a fresh thread is cheaper than a
  long one. Scheduled monitoring (a cron that re-reads a strategy) is the classic silent drain;
  `senpi-strategy-author/references/shadow-testing.md` has the cheap alternatives.
- **A running strategy is not what spends them.** Its runtime ticks without a model call, so running
  it does not move the meter; a `decision_mode: llm` action in its `runtime.yaml` is the exception,
  since that calls a model when it fires. A cron the agent set up to check on a strategy does move it
  (each firing is a full model call), so name any the agent runs for this user. Using no credits is
  not the same as needing no plan: the strategy runs on the agent, and a lapsed plan pauses both
  (see "Plans & free credits"). Running out of credits stops the strategies that run on the agent too.
- **Not trading money.** Credits cannot be traded, withdrawn, deposited into a strategy, or moved
  anywhere — they are separate from the funding wallet and from every strategy budget. A shrinking
  meter is usage, never a trading loss; a losing trade never touches the meter.
- **Chat is not free of credits.** Every turn spends them — including the turn that asks what a turn
  cost. Say so plainly; never "nothing".

| Say | Never say |
|---|---|
| "You have N credits left." (N is `credits.remaining`; add the time left when `hours_left` is present) | A dollar figure for credits, or a balance you did not read from `credits.remaining` |
| "Chat and tool use spend AI credits from your plan. Longer threads cost more because the whole conversation is re-sent each turn." | "chatting is free" / "that cost nothing" |
| "I can't read your balance from here — the meter in the app header and senpi.ai/settings/subscription show it." (only when `credits.available` is false) | Any balance or percentage you did not read from the engine, or a plan size you did not read from the subscription page |
| "Credits aren't money: they can't be traded, withdrawn or put into a strategy — your funding wallet and strategy budgets are separate." | "credits" as a tradable or withdrawable balance |
| "The meter going down is usage — it moves with messages and tool turns, not with your trades." | Blaming a shrinking meter on trading losses |
| "Your strategy runs on its own without the AI, so running it doesn't use credits. Chat does, and so does any scheduled check-in I run for you." | "running the strategy costs nothing" while the agent runs a check-in cron on it; a wallet or strategy balance offered as proof about credits |
| "If the credits run out, the agent stops: the AI, simulations and the strategies that run on your agent stop. Stop-losses already on the exchange stay there, mirror strategies keep running, and your open positions are not closed." | "your live strategies keep running when the credits run out" / "all your positions are closed when the credits run out" |
| "Your strategy doesn't use credits, but it runs on your agent, and the agent needs an active plan or credits left. If the plan lapses with no credits left, the agent and your strategies pause until you subscribe or buy credits." | "no subscription is needed" / "your strategy keeps running without a plan" |

**Routing.** "What is the bubble / meter in the header?", "why are my credits going down?", "how much
did that cost?", "does my strategy use credits?", "can I trade / withdraw / deposit my credits?" all
come here, in any language. "Credits" (Ukrainian "кредити", Spanish "créditos") means these AI credits
unless the user says leverage, a loan or funding; when it is unclear, ask which before answering.
"What plans are there / how much
does it cost / what do I get on Pro / how do I get free credits / where are my milestone credits /
do I need a subscription / what happens when my trial ends" are **catalog** questions: answer them from "Plans & free credits" below, still without a figure
about this user.

## Plans & free credits

Published facts — quote them. Billed per **30-day cycle, rolling from the user's own start date**,
never a calendar month.

| Plan | Price | Points per $1 volume |
|---|---|---|
| Pro (default) | $50 | 1.5 |
| Advanced | $100 | 2 |
| Quant | $200 | 3 |

Starter ($25 a month, 1 point per $1 volume) is closed to new subscribers; people already on it keep
it and can upgrade, but cannot return to it after leaving.

- **The credits each plan includes:** quote them only from the subscription page
  (senpi.ai/settings/subscription), in credits, never as a dollar amount.

- **Every plan includes every model.** Plans differ on credits and points rate only — never on model
  access, so a cheaper plan never locks anyone out of the better model.
- **No Senpi trading fee on any paid plan, or while the account holds credits** (pay as you go, the
  initial free credits) — only Hyperliquid's own exchange fees. For an account with no plan and no
  credits, quote its own `fee.pct` from the engine; asked about that state ahead of time, quote the flat
  fee in the Senpi MCP's fee rule (in its instructions, already in context) in the same reply, never
  an offer to look it up. Never state a rate neither gives.
- **Referrals pay 15% of a referred friend's monthly Senpi spend, for their first 12 months** — from
  what the friend spends on Senpi, not from their trading. A friend on $100 a month earns the referrer
  $15 a month × 12 months = $180. How and when the share is paid out is not announced yet: if asked,
  say so, and never describe a payout schedule, token or threshold for it.
- Full plan credits land at the start of the cycle and expire at the end of it: **no rollover, no
  overage**. Credits can also be bought on the subscription page (senpi.ai/settings/subscription);
  quote packs only from that page. Out of credits = buy more, upgrade (instant) or wait for renewal.
- **Upgrading is instant, any day** — the new plan's full credits land immediately, the 30-day cycle
  restarts, and unused credits are credited against the price, so they pay the difference.
  **Downgrading or cancelling takes effect at the next renewal**, never mid-cycle; benefits are kept
  until then and there are no mid-cycle refunds.
- **Payment is web only** (Stripe, senpi.ai/settings/subscription). The app shows plan, credits and
  renewal date but links out to the web page to change anything.
- **Free trial:** activates when the user **deploys their agent**, is one-time, and expires **14 days
  after activation**. **Never quote a trial figure** — the starting amount has changed more than once;
  their subscription page is the only source of truth. When it expires with no credits left, the agent
  stops until they subscribe: active strategies pause, **open positions are NOT auto-closed**, funds
  untouched.
- **Out of credits stops the agent too.** Running out of credits also stops the agent: the AI stops,
  simulations pause and strategies that run on the agent stop. Stop-losses already on the exchange stay
  there, mirror strategies keep running, open positions are not closed and funds are untouched.
- **A lapsed plan pauses everything, not just chat.** Strategies run on the agent. When a trial or
  plan expires or a renewal payment fails and no credits are left, Senpi pauses the agent and
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
