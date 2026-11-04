---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, the Senpi trading fee (none on a
  paid plan), referral earnings — and explain the AI-credits usage meter. Use for "how many points
  do I have?", "what's my rank/tier?", "where did my tier go?", "what are my fees?", "my referral
  rewards", "how do referrals work?", and for credits: "what is the bubble/meter in the header?", "why are my credits going
  down?", "how much did that cost?", "does my strategy use credits?", "can I trade or withdraw my
  credits?", and for plans: "what plans are there?", "how much
  does Senpi cost?", "what do I get on Pro/Quant?", "how do I get free credits?", "where are my
  milestone credits?", "do I need a subscription to keep my strategy running?", "what happens when my
  trial ends?", and for strategy limits: "what are my strategy limits?", "when can I start a new
  strategy?", "what are the strategy limits on the paid plans?". Use this instead of calling user_get_senpi_points by hand. A hidden engine
  (scripts/status.py) pulls points, the fee, referral and strategy limits in one call; the credit
  BALANCE is explained, never read — no tool returns it — while the plan catalog and the end of the
  free-credit milestones are published here and may be quoted. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.11.0"
  platform: senpi
  exchange: hyperliquid
---

# Senpi Account Status — your standing across Senpi

A hidden engine pulls the user's standing in one real-time call; **your job is to present it
cleanly**: points and rank, the Senpi fee, referral earnings, and strategy limits.

## Golden rules

- **Run the engine; never hand-pull.** `python3 scripts/status.py` gathers points, the fee,
  referral and strategy limits together. Read its JSON.
- **Real-time.** Points/rank move — never quote an earlier figure; re-run.
- **The fee comes from the data, never from memory.** Quote `fee.pct` as returned: `0%` on a paid
  plan (Pro, Advanced, Quant, and Starter for people already on it), the flat free rate without one,
  free trials included.
- **Tiers carry no benefit right now: no fee change, nothing unlocked. Do not bring tiers up.** Never
  suggest reaching, keeping or regaining a tier, and never close with a next-tier offer. If the user
  asks about their tier or where it went, say tiers carry no benefit right now. Points still accrue
  at the plan multiplier and still set leaderboard rank.
- **Lead with what they asked.** Points question: lead with points + rank. Don't dump every section.
- **Credits are explained, never read.** No tool returns the AI-credit balance; the meter in the app
  header is the balance. Rules in "AI credits — the usage meter" below.
- **Plans and free credits are published; the balance is not.** Quote the catalog and the end of the
  milestones in "Plans & free credits" below — and only from there. A figure about *this user* (what
  is left, what they were charged, where their bar sits) comes from their page, never from you.

## How to run the engine

```
python3 scripts/status.py
```

Returns `{as_of, identity, points, fee, referral, limits, meta}`:
- `points` — `total`, `base` (Base copy-trading), `perp` (Hyperliquid), `multiplier` (the plan's
  points rate), `rank`, `rank_change`.
- `fee` — `bps` + `pct`, the Senpi fee this user pays, as the backend reports it: `0` / `0%` on a
  paid plan (`bps` is in tenths of a bp).
- `referral` — `balance_usdc` (referral earnings not yet claimed).
- `limits` — MCP `strategy_get_limits` as returned; rules in "Strategy limits" below.
- `meta.warnings` / `meta.degraded` — narrate honestly.
- Fails open — partial data still returns valid JSON.

## Output contract

Lead with the section they asked about; otherwise a tight standing card:

1. **Points & rank** — total, base/perp split, multiplier, rank (+ change).
2. **Fee** — `fee.pct` (`0%` = a paid plan, no Senpi fee).
3. **Referral** — pending `balance_usdc` (and that it's claimable if > 0).
4. **Strategy limits** — only when a limit is enforced: `limits.summary`, usage and the reset time (on a free trial, when it ends). No
   limits apply (grandfathered, or both limits `null`): one line at most, "no strategy limits apply".
   `enforced: false` or `limits: {}`: leave it off. Full detail only when asked (see below).

Formatting: scannable, numbers from the data.

## Strategy limits

`limits` is MCP `strategy_get_limits`: the user's own plan, usage and reset time. When a limit is
enforced, lead with `limits.summary`, the sentence to quote. Then: `running` of `maxRunning` running,
`starts` of `maxStarts` new this cycle (the last 30 days when `window` is not `CYCLE`), and
`startsResetAt` in UTC (in a cycle, when the count resets; on a free trial that is the trial's end, and a start comes back only if the trial converts; otherwise when the next single start frees up). At a 0 limit waiting frees nothing; only an upgrade does.
Their own usage and limits come from this live read, never from memory. Another plan's limits are a
catalog question: quote the "Plans & free credits" table as written, never a number of your own.

- **"What are the limits on Pro / the paid plans?"** is a catalog question: answer from that table, no
  engine run. Their own limits or usage ("what are my limits", "how many have I used") come from `limits`.
- **A free trial** is a `source: "trial"` read. Call it the **free trial**, never "Pro trial" or
  "<plan> trial", and never pair it with a plan name ("Free trial — Pro plan", "trial of Pro"). When quoting
  `summary`, say "free trial".
  Its new-strategy allowance covers the 14-day trial, never a "per billing cycle" or 30-day count, and
  `startsResetAt` is when the trial ends, not a reset.

- **A `null` limit is no limit, never 0.** `source: "grandfathered"`: their account predates strategy
  limits, so none apply. Both limits `null` (some `source: "override"` accounts): no limits apply to
  their account. Either way the card says "no strategy limits apply" at most. `limits.plan` is present
  only when the limits come from a plan (`source: "plan"`); never infer the user's subscription from
  `limits`. A `0%` fee means a paid plan; name no plan you didn't read.
- **`enforced: false`**: limits are not enforced right now, whatever the numbers say. Leave limits off
  the card and do not raise them; when asked, say limits are not enforced right now.
- **How it counts**: every wallet is one strategy (a 3-wallet strategy uses 3); a PAUSED strategy counts
  as running; closing frees a running slot, never a start;
  only a strategy that ends FAILED uses no start (one stuck in MANUAL_REVIEW still uses it);
  the browser extension's own wallet never counts.
- **Unread**: `limits: {}` with a `strategy_get_limits failed` warning. Leave limits off the card and
  say nothing about it unless the user asked about their limits. If they did, run the engine once more at most
  (not when the warning says `NOT_FOUND`) and, if it still fails, say their limits couldn't be read
  right now. Never guess, and never say they have none. Deploys never wait on this read: the runtime
  and strategy-manager still enforce the limits.

## Strategy limits

`limits` is MCP `strategy_get_limits`: the user's own plan, usage and reset time. When a limit is
enforced, lead with `limits.summary`, the sentence to quote. Then: `running` of `maxRunning` running,
`starts` of `maxStarts` new this cycle (the last 30 days when `window` is not `CYCLE`), and
`startsResetAt` in UTC (when the count resets in a cycle; otherwise when the next start frees up).
Never quote limit numbers from memory or from the plan table.

- **A `null` limit is no limit, never 0.** `source: "grandfathered"`: their account predates strategy
  limits, so none apply. Both limits `null` (some `source: "override"` accounts): no limits apply to
  their account. Either way the card says "no strategy limits apply" at most.
- **`enforced: false`**: limits are not enforced right now, whatever the numbers say. Leave limits off
  the card; explain only when the user asks about their limits.
- **How it counts**: every wallet is one strategy (a 3-wallet strategy uses 3); a PAUSED strategy counts
  as running; closing frees a running slot, never a start;
  only a strategy that ends FAILED uses no start (one stuck in MANUAL_REVIEW still uses it);
  the browser extension's own wallet never counts.
- **Unread**: `limits: {}` with a `strategy_get_limits failed` warning. Leave limits off the card and
  say nothing about it unless the user asked about their limits. If they did, run the engine once more at most
  (not when the warning says `NOT_FOUND`) and, if it still fails, say their limits couldn't be read
  right now. Never guess, and never say they have none. Deploys never wait on this read: the runtime
  and strategy-manager still enforce the limits.

## AI credits — the usage meter

Senpi agents run on **AI-usage credits that come with the plan** — a trial grant first, then plan
credits. The app shows what is left as the **meter in the app header** (users call it "the bubble" or
"my credits"), and their subscription page is the source of truth for the number. What each plan
includes is published in "Plans & free credits" below and may be quoted; how much is **left** depends
on your plan and your usage, and only the user's own page shows that.

- **What spends them.** Every message the user sends and every tool turn the agent runs. Long threads
  cost more because the whole conversation is re-sent each turn — a fresh thread is cheaper. A cron that
  re-reads a strategy is the classic silent drain; `senpi-strategy-author/references/shadow-testing.md`
  has the cheap alternatives.
- **A running strategy is not what spends them.** Its runtime ticks without a model call; a
  `decision_mode: llm` action in its `runtime.yaml` is the exception (it calls a model when it fires).
  A cron the agent set up to check on a strategy does move the meter (each firing is a full model
  call), so name any the agent runs for this user. Using no credits is not the same as needing no
  plan: the strategy runs on the agent, and a lapsed plan pauses both (see "Plans & free credits").
- **Not trading money.** Credits cannot be traded, withdrawn, deposited into a strategy, or moved
  anywhere — they are separate from the funding wallet and from every strategy budget. A shrinking
  meter is usage, never a trading loss; a losing trade never touches the meter.
- **You cannot read the balance.** No Senpi tool returns it and the engine has no `credits`
  section. Point at the meter in the app header, or at their subscription page for the exact figure —
  the in-app indicator surfaces only when credits run low, so a missing one is not a lost balance. Never invent a number, a percentage or a "roughly", and never guess what the meter
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
do I need a subscription / what happens when my trial ends / what are the strategy limits on the paid plans" are **catalog** questions: answer them from "Plans & free credits" below, still without a figure
about this user.

## Plans & free credits

Published facts — quote them. Billed per **30-day cycle, rolling from the user's own start date**,
never a calendar month.

| Plan | Price | Credits | Points per $1 volume | Running at once | New strategies per cycle |
|---|---|---|---|---|---|
| Pro (default) | $50 | $55 (+10%) | 1.5 | 10 | 40 |
| Advanced | $100 | $115 (+15%) | 2 | 25 | 100 |
| Quant | $200 | $240 (+20%) | 3 | no limit | no limit |

Free trial: 1 running at once, 3 new strategies during the 14-day trial. Starter ($25 a month, $25
credits, 1 point per $1 volume, 3 running at once, 8 new per cycle) is closed to new subscribers; people
already on it keep it and can upgrade, but cannot return to it after leaving. Accounts that predate
strategy limits (`source: "grandfathered"`) have none on any plan, and the user's own `limits` read
always wins for them.

- **Every plan includes every model.** Plans differ on credits, points rate and strategy limits — never on model
  access, so a cheaper plan never locks anyone out of the better model.
- **No Senpi trading fee on any paid plan** — only Hyperliquid's own exchange fees. For any other
  account, quote its own `fee.pct` from the engine; never state a rate from memory.
- **Referrals pay 15% of a referred friend's monthly Senpi spend, for their first 12 months** — from
  what the friend spends on Senpi, not from their trading. A friend on $100 a month earns the referrer
  $15 a month × 12 months = $180. How and when the share is paid out is not announced yet: if asked,
  say so, and never describe a payout schedule, token or threshold for it.
- **$1 of credit = $1 of AI usage.** Full credits land at the start of the cycle and expire at the end
  of it: **no rollover, no top-ups, no overage**. Out of credits = upgrade (instant) or wait for renewal.
- **Upgrading is instant, any day** — the new plan's full credits land immediately, the 30-day cycle
  restarts, and unused credits are credited against the price, so they pay the difference.
  **Downgrading or cancelling takes effect at the next renewal**, never mid-cycle; benefits are kept
  until then and there are no mid-cycle refunds.
- **Payment is web only** (Stripe, senpi.ai/settings/subscription); the app links out to change anything.
- **Free trial:** activates when the user **deploys their agent**, is one-time, and expires **14 days
  after activation**. Always "free trial", never "Pro trial" or a plan beside it. **Never quote a trial credit figure** — the starting
  amount has changed more than once; their subscription page is the only source of truth. On expiry or exhaustion the agent stops until
  they subscribe: active strategies pause, **open positions are NOT auto-closed**, funds untouched.
- **A lapsed plan pauses everything, not just chat.** Strategies run on the agent. When a trial or
  plan expires, a renewal payment fails, or the cycle's credits run out, Senpi pauses the agent and
  every strategy on it stops with it: no scans, no new entries, no stop moves. Open positions stay
  open, and a stop-loss already resting on the exchange still triggers. Funds stay in their wallets,
  withdrawable in the app. Subscribing, a successful renewal or an upgrade brings the agent and its
  strategies back. `limits.plan` is present only when the limits come from a plan
  (`source: "plan"`), so its absence says nothing: never conclude "no plan" from `limits`.
  `source: "trial"` means a trial; `limits: {}` means the read failed. You cannot see trial or renewal
  dates: point at senpi.ai/settings/subscription, and never tell them a subscription is not needed.

**Free credits — the milestones ended on 2026-10-26.** Every free-credit milestone is over: your
first strategy, funding a wallet, the trading-volume ladder and the first-subscription bonus. None of
them pays any more, and the "Unlock Free AI Credits" card shows nothing. Its "Deploy your agent" row
went with it: the app built that row from the trial credit, and it was never a milestone.

- **Milestone credit already earned stays until the next renewal** — a different clock from the
  14-day trial expiry; credit that vanished at a renewal boundary is working as designed.
- **The trial credit still activates when the user deploys their agent** (see Free trial above).
- Never offer a bonus for a strategy, a deposit, volume or subscribing. If the user's own page or
  card disagrees with this text, their page is right — never argue a user out of what is on their
  screen.

## ⚠ Token scope

Every tool here is **USER-scoped** (the user's own account): needs a USER-scoped `SENPI_AUTH_TOKEN`.
App-scoped → no user resolves and `meta.degraded`; say so rather than reporting zeros.

## Skill Attribution

Read-only: it never mutates (claiming referral rewards is `user_claim_referral_rewards`, which this skill never calls).

## Install — both scripts are required

The engine is **two files** in `scripts/`: `status.py` and its vendored helper `mcp_client.py`.
**Install the whole `scripts/` directory** — `status.py` alone fails with `No module named 'mcp_client'`.
Stdlib only.
