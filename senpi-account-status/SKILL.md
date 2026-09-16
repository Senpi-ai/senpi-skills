---
name: senpi-account-status
description: >-
  Show the user's standing across Senpi programs — points and rank, loyalty tier and fees, referral
  earnings — and explain the AI-credits usage meter. Use for "how many points do I have?", "what's my
  rank/tier?", "what are my fees?", "my referral rewards", and for credits: "what is the bubble/meter
  in the header?", "why are my credits going down?", "how much did that cost?", "does my strategy use
  credits?", "can I trade or withdraw my credits?". Use this instead of calling user_get_senpi_points +
  get_loyalty_tiers one by one. A hidden engine (scripts/status.py) pulls points/loyalty/referral in one call; credits are
  explained, never read — no tool returns the balance. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.3.0"
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
"my credits"). How many a plan includes depends on your plan — never quote a plan size.

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
  section. Point at the meter in the app header; never invent a number, a percentage or a "roughly",
  and never guess what the meter shows.
- **Chat is not free of credits.** Every turn spends them — including the turn that asks what a turn
  cost. Say so plainly; never "nothing".

| Say | Never say |
|---|---|
| "Chat and tool use spend AI credits from your plan; the meter in the app header is the balance — I can't read it from here. Longer threads cost more because the whole conversation is re-sent each turn." | "chatting is free" / "that cost nothing" |
| "How many credits you get depends on your plan — the meter shows what is left." | Any balance, percentage or plan size you did not read from the app |
| "Credits aren't money: they can't be traded, withdrawn or put into a strategy — your funding wallet and strategy budgets are separate." | "credits" as a tradable or withdrawable balance |
| "The meter going down is usage — it moves with messages and tool turns, not with your trades." | Blaming a shrinking meter on trading losses |
| "Your strategy runs on its own without the AI, so running it doesn't use credits. Chat does, and so does any scheduled check-in I run for you." | "running the strategy costs nothing" while the agent runs a check-in cron on it; a wallet or strategy balance offered as proof about credits |

**Routing.** "What is the bubble / meter in the header?", "why are my credits going down?", "how much
did that cost?", "does my strategy use credits?", "can I trade / withdraw / deposit my credits?" all
come here, in any language. "Credits" (Ukrainian "кредити", Spanish "créditos") means these AI credits
unless the user says leverage, a loan or funding; when it is unclear, ask which before answering. A
credits question is answered from this section alone — no engine run, no number — and closes by
pointing at the meter; the next-tier CTA is for standing answers.

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
