# Why the hard rules exist

The live failures behind SKILL.md's hard rules. Read when a user disputes one; the rules themselves
stay in SKILL.md.

## Gutting one sleeve of a multi-wallet strategy

HARD rule — the no-no (this is a real failure that broke live strategies).

The agent has done exactly this and it is catastrophic:

- "Close ox's \$600 wallet, keep the \$1,400 one" — **gutting one sleeve of a risk-parity core destroys
  the design.** The two sleeves are balanced *against each other*; keeping one is a different, unbalanced
  strategy the user never chose.
- "Keep cougar's short sleeve, repurpose its flat long sleeve" — **closing one sleeve of a long/short
  strategy leaves a NAKED directional position.** A market-neutral book with only its short leg is just
  a short — the exact opposite of neutral.

## Positions-first answers

Positions-first is the failure mode — the agent kept answering "analyze my strategies" with a raw positions dump.

## Caving to "it's closed"

**Don't cave to a claim the wallet contradicts, and NEVER fabricate account history to agree.** If the
  user says a strategy is "closed / has no funds" but its `account_value > 0`, it is **live** — say so with
  the number ("wolf is live — $X in the wallet, flat right now, waiting for its signal"). Do not abandon a
  correct reading, and do not invent a story to justify agreeing (a "strategy-grinder cascade," a "close at
  14:58," "funds returned to embedded").

This is the real failure this section prevents: a **live** strategy was re-narrated as closed — with a fabricated close-cascade — because the model deferred to a mistaken "it's closed" instead of re-reading the clearinghouse.

## A successful order is not the position

- **A successful open/close order is NOT proof of the resulting position.** After you place or close a
  trade, confirm the resulting state here before telling the user what they hold — a position in a
  scanner-managed wallet can be reconciled as foreign and DSL-flattened within minutes (order "succeeds,"
  position gone; a raw read of the wrong sub-wallet then shows it "phantom").
- **Quote a close with its UTC date and time from `closed_at_utc`, never from a raw epoch** such as
  `closed_time` — a bare number read by eye is how an older close gets called today's.

## Raw MCP dumps

**Use this skill FIRST — before any raw MCP.** For *any* question about the user's portfolio,
positions, balances, PnL, or trade history, run this engine **before** reaching for raw
`strategy_get_clearinghouse_state` / `account_get_portfolio` / `strategy_list`. Those return
un-bucketed dumps that mislead — idle-vs-deployed conflation, per-wallet collateral double-counting,
and **sub-wallets mistaken for separate strategies** (a strategy's `main`/`hedge` legs are ONE
strategy, not two). The engine already de-duplicates and classifies; a raw dump is a wrong answer.

## A flat sleeve is committed capital

An instance with **no open positions** inside a multi-wallet strategy is **its other book waiting for
its signal** — e.g. cougar's long book sitting flat while its short book trades, or ox's ballast sleeve
holding cash by design. The engine names these in `strategy_groups[].flat_instances`. **It is NOT idle
capital to redeploy elsewhere, and never "dead money."** That capital is *committed to the strategy* —
it's the dry powder the other half of the design needs to do its job. Only truly-free
`idle_in_embedded` (and, with care, a *whole* strategy's idle) is redeployable — a flat sleeve of a
live multi-wallet strategy is not.

## Grading against a momentum benchmark

This is the core of the analysis. Every strategy was deployed to do a *specific* job. "Is it working?"
means "**is it behaving the way its design says it should**," NOT "is it up this week" and NOT "is it
riding the same move a trend-follower would." Grading every strategy against a generic momentum
benchmark is the failure mode this skill exists to prevent — it graded an all-weather core, a crisis
hedge, and a waiting strategy each as "dead weight" when all three were doing exactly their job.

## Hand-closing a scanner's position

For an **autonomous strategy the scanner owns entries and exits** — it opens and closes positions every
tick per its DSL and signal logic. **Hand-closing an individual position it will simply re-open on the
next tick is futile** (and pays fees twice). The levers that actually change anything are at the
**STRATEGY** level: **close it, pause it, adjust its config, or top up the whole strategy** — not its
individual positions. So frame recommendations as strategy-level actions ("pause cougar," "tighten
cub's risk config," "top up ox"), not "close this one ETH short." (Exception: a genuinely ad-hoc /
custom one-off position the user placed by hand, not run by a scanner — that one you can manage
directly.)

## Narrating through numbers that do not tie out

- **If the engine's own signals disagree, STOP and re-run — do NOT narrate through it.** A `reconciles:
  false` in `totals`, or the `money` and `strategies` steps reporting a different strategy count/set, means
  the numbers didn't tie out. Re-run the step fresh and reconcile BEFORE you say a word — above all before
  any close / rebalance recommendation. Recommending action on a strategy that turns out to be already
  closed is exactly the failure this guards against.

## Skill Attribution (moved from SKILL.md)

Guide/analysis skill — it *reads* the account and *recommends*; it does not place a trade or move
funds. Attribution happens downstream when the execution tools / strategy skills act on a CTA.
