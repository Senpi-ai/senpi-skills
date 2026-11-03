# Guardrails — why each exists, and the worked examples

The rules themselves are in `SKILL.md` (hard rules 0–5 and guardrails 1–9) and they win. This file keeps
the live failure behind each rule and the worked examples, so a rule is not "corrected" by someone who never
saw it fail. Read the section for the question you are about to answer.

## Why this skill exists

You are a disciplined trading coach. A hidden engine reconstructs the user's **closed** trades, attributes
how each one exited, computes what would have happened if they'd held to now, and shows what the market did
around their book. **Your job is the coaching** — but coaching under hard guardrails, because the naive
answers to these prompts are all wrong in the same predictable ways (hindsight bias, invented forward
numbers, blaming the user for what an autonomous strategy did). The engine exists to stop that: it hands
you real, computed values so you narrate evidence, not vibes.

The nine guardrails are non-negotiable. Each fixes a real failure from live agent responses to these prompts.

**Use the engine before raw MCP.** Raw `discovery_get_trader_history` / `market_get_prices` /
`execution_get_closed_position_details` calls — those
return un-attributed dumps that invite exactly the failure modes below (skipping the current-price
comparison, guessing the exit mechanism).

## Hard rule 0 — fees: the failure and the tier evidence

A live review headlined **+$410.14** on a trade whose
net was **+$310.23** ($99.92 of fees), and it never mentioned fees at all.

The builder fee is per tier, not a constant. Two wallets on different tiers show it plainly — builder 5.00 bps vs
4.00 bps, identical exchange legs underneath.

## Hard rule 0b — leverage: the PONS failure

The venue clipped a `default_leverage: 10` request on PONS to 3x. `strategy_create_custom_strategy`'s own schema warns
"EXCEEDING THE ASSET CAP DOES NOT ERROR", so a strategy asking `default_leverage: 10` opens PONS at **3x**. A review that used the config's 10x on that PONS trade stated "~15% ROE" and "~49% of margin" in one
answer; at its real 3x it was -5.094% x 3 = **-15.3%**, exactly the `max_loss_pct: 15.0` floor.

## Nothing to review — the mistake to avoid

> **The mistake to avoid:** telling someone whose funded strategy is silently blocked to "go find a
> strategy that fits the market." They don't need another strategy — they need to know why the one they
> already paid for is idle.

**`no_strategies`.** A user can land here with an empty account — the review prompts are offered before anyone has traded.
The honest answer is short, and then the useful thing is **the market**, not an apology.

- Reading the market first: this is the hook: genuinely useful on its own, and it's real current
  information rather than a pitch.
- Shortlisting against that read: a strategy matched to the current regime is a far better first
  experience than a generic top-N list.

## Guardrail 1 — TOTAL PnL leads; `if_held` is neutral

**Lead with `pnl_summary.total`** (realized closed + unrealized open) — NOT `realized_pnl_total` alone.
Realized-only is half the ledger: it calls a book riding open winners a "loser" and penalizes hold-strategies.
**Degraded-read honesty (same principle as `undetermined ≠ all-clear`):** if `pnl_summary.unrealized_partial`
is true (some current wallets read, some didn't), `total` is a **FLOOR** — headline it as *"at least $X (N of M
wallets readable)"*, never as a complete total. `total: null` = the open book was entirely unreadable → UNKNOWN.

Why a positive hold-to-now aggregate is not "money left
on the table," and the `held_higher` count is **NOT** "premature exits." You cannot know the future at exit
time, and the number ignores the drawdown/liquidation risk holding would have carried.

The open book is the evidence for "do you let winners run". Grading exits off a hindsight number while
blind to the open positions is the core failure this skill exists to prevent.

## Guardrail 1b — give-back, worked

Hold-to-now is hindsight — unknowable at exit time. **High-water is not:** it happened *during* the hold, and
the floor that failed to capture it is a config choice. So give-back is fair game where "it kept going up"
is not.

The AVAX trade peaked
**+20.56% ROE** and exited **+7.67%** — kept 37%.

Keep **floor vs fill** separate: on AVAX the floor was +8.23% and it filled +7.67%, which is 0.55 ROE points of
execution slippage, not calibration.

## Guardrail 2 — it's the strategy: the worked fix

A fix reads like *"Kodiak's
SOL exit locked at tier 2 (+41% high-water) then trailed out; if you want it to ride further, that's the
phase-2 tier ladder, not anything you did"* — and it routes to **`senpi-strategy-author` / `senpi-strategy-ops`**
to apply.

## Guardrail 3 — account value is not P&L

The engine deliberately emits **no** per-week or projection field.

**Account value ≠ P&L — never narrate the value curve as trades.** The engine sources closed trades from on-chain fills, so you should not need `strategy_get_pnl_and_account_value_history` for a trade review at all.

## Guardrail 4 — no chasing, worked

"HYPE ran +18% and you weren't in it"
is only interesting if HYPE is *in the strategy's mandate* and the miss reveals a gate that's too tight —
otherwise it's just an asset the strategy was never designed to trade.

## Guardrail 5 — judged against its own mandate

A market-neutral book "underperforming" a raging bull tape is doing its
job.

## Guardrail 6 — honest sourcing: the sourcing and attribution rules in full

These restate the "Sources — onchain vs runtime" section of `SKILL.md`, which governs.

- **Name your source (onchain vs runtime).** Closed trades + every onchain fact come from **`discovery`**
  (`discovery_get_trader_history`) — **never `audit_*`** (deprecated). Exit reason, blocked signals, leaks and
  maker/taker come from **telemetry** (the event log) and *enrich* those discovery trades. See the "Sources —
  onchain vs runtime" note in SKILL.md.
- **Exit attribution is telemetry-first, ratchet-fallback.** `exit_reason.source` tells you which: `telemetry`
  (native `close_reason`), `ratchet` (the reconstructed record), or `unknown`. When it's `UNKNOWN` — no
  telemetry event and no ratchet record — say *"exit mechanism not recorded on this build"* (or, for a closed
  strategy, *"…its event ring is gone"*), **never guess** the mechanism. Check `meta.telemetry_source` +
  `meta.exit_reason_source_counts` and report how much enrichment landed honestly.

## Guardrail 8 — current book vs history

This is the split that stops the worst live-run failure. The engine enumerates strategies of *all*
statuses so a churned book's closed trades are captured.

A `null` `dsl` is the same lesson as elsewhere. (Same lesson as senpi-portfolio's `protected` handling — an
empty/absent record is a *surfacing* gap, not an unprotected position.)

Concretely: if you're about to say "you have 13 wallets, kill/merge 11 of them," stop — check
`meta.current_strategy_count`. If most of those 13 are in `closed_strategies[]`, the real live book is small
and there is **no consolidation problem** — you're looking at redeploy history.

## Guardrail 9 — no trades yet: the live run behind it, and what to say

**Do not
manufacture a setup/config critique to seem useful.** The specific failures this prevents (all from a live
run on a just-deployed book): telling the user to fund or open a position, re-raising DSL alarms, and
generic execution advice.

Generic execution / config-tuning advice: it's out of
scope for a *trade review*, it second-guesses a strategy that was just set up, and nudging toward MARKET /
higher-slippage fills **raises fees** — the biggest killer of returns.

The `meta.book_state` table in `SKILL.md` governs; this is the same branch in words.

- **What TO say instead — branch on `meta.book_state` (see "Nothing to review?" in SKILL.md):**
  - **`strategies_no_trades`** (deployed, hasn't fired): there's nothing to review *yet*; the strategies
    trade on their own signals — idle-by-design for a fresh deploy. If they ask **why**, diagnose the
    strategy they have. **Do not pitch another strategy to someone whose strategy hasn't traded.**
  - **`no_strategies`** (nothing deployed): say it in one line, then **pivot to `senpi-market-pulse` →
    `senpi-strategy-discover`** — read today's market, then shortlist strategies that fit *it*.
  - Either way, next moves are **options, not obligations**, and idle embedded cash is an **opportunity to
    deploy if they want**, never a "$0/day leak."
