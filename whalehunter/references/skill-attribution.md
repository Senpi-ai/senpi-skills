# WhaleHunterHedge — Skill Attribution

**Skill:** whalehunter-strategy v1.1.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-19 (v1.0) · 2026-06-19 (v1.1 tiered sizing)

## v1.1 — tiered sizing

The pool widened from ELITE+PATIENT-only to **four consistency×style tiers**, each
with a `tagWeights` multiplier that scales **both** the base margin and the
per-position cap (ELITE+PATIENT 1.0 / ELITE+TACTICAL 0.75 / RELIABLE+PATIENT 0.50 /
RELIABLE+TACTICAL 0.40). A higher tier always has a higher ceiling; conviction +
consensus scale within a tier's range. Solves the thin-pool risk (ELITE+PATIENT alone
rarely fires) while keeping size proportional to trader quality. The pool is queried
once per tier so each trader is tagged exactly (no reliance on per-trader label fields).
Every other tag pair (Streaky/Choppy, Degen/Active) is excluded.

## What WhaleHunterHedge is

A long/short copy book that follows the SINGLE highest-conviction trades of
CONSISTENT + PATIENT Hyperliquid winners. It shadows traders tagged ELITE
(consistency) AND PATIENT (activity) on Senpi Discover — winners who rarely trade —
and strikes only when one opens a NEW position that is a large share of their OWN
balance. Two independent sleeves on separate wallets (long / short) so the book can
hold conflicting positions on one asset; mirrors the strike and rides it on a wide DSL.

## Lineage

- **Architecture** — the helpers-native leg-parameterized two-sleeve pattern
  (Caribou/Octopus family): ONE producer, `WHALEHUNTER_LEG` (long/short),
  `producer_daemon` + lock, `SenpiClient.push_signal()` ingest, runtime-owned LLM gate
  + DSL + risk. `max(main, xyz)` account read; signature-adaptive launch.
- **Methods** — the copy/follower machinery (daily-cached trader pool from
  `discovery_get_top_traders`, per-tick `discovery_get_trader_state` diff for new
  positions, baseline-seed guard, consensus enrichment) is the Jackal pattern,
  re-pointed at a sharper target.
- **Thesis** — patient + consistent winners have no routine trades to copy, so their
  rare, large, conviction-sized strike is the highest-signal event on the board.

## Design decisions specific to WhaleHunterHedge

- **The three-gate strike (the new edge vs Jackal).** Jackal copies *every* new entry
  from top-ROI traders. WhaleHunter fires only when **all three** hold: `consistency ==
  ELITE`, `activity == PATIENT`, and a new position whose `marginUsed / accountValue`
  (capital at risk as a share of *their* balance) clears `convictionPct`. The
  patience + conviction-% combination is the differentiator — sparse, pristine signals.
- **Conviction measured as capital-at-risk %**, not notional (not leverage-inflated) —
  the truest read of how much the whale is actually committing.
- **Two independent sleeves on separate wallets** — chosen so different whales' opposite
  convictions on the same asset can BOTH be held (long sleeve + short sleeve), instead
  of netting in one account. Makes it a genuine whale-conviction long/short hedge;
  funding default 50/50 (no directional bias).
- **Wide ride-the-whale DSL** — wide disaster stop, weak_peak OFF, time-cuts OFF, late
  Phase 2. A patient whale holds for a long time, so a tight stop would shake you out of
  exactly the trade you came to ride. v1.1: "follow them out" (close when the whale closes).
- **Consensus, not duplication** — multiple whales agreeing on a coin+direction scale the
  one position up (within-tick dedup), they don't open two.
- **Leverage capped** — conviction shows in size, never inherited leverage.

## Fleet-standard compliance

- Max leverage 5x (clamp + venue). Conviction-scaled margin (no hardcoded $); per-position
  cap; up to 6 slots/sleeve. Drawdown halt 25%, daily loss 15%, baseline-seed guard.
- Mandatory DSL; FEE_OPTIMIZED_LIMIT with taker fallback on entries + exits. Verbose
  per-tick JSON. Sizes off `max(main, xyz)` account value — never the sum.
- Requires a USER-scoped auth token (discovery_* needs a user id) — documented in README/SKILL.

## Negative-lesson inputs

- **Jackal's baseline-seed bug** — on an empty baseline, never treat existing positions as
  new (Jackal v2.0.3 opened unintended positions on first install). WhaleHunter seeds the
  baseline silently on first run and fires nothing.
- **Cobra antipattern** — no fixed high leverage / no drawdown gate. WhaleHunter: leverage
  capped, conviction in size, hard drawdown halt.
- **Don't copy churn** — the PATIENT gate exists precisely so the wide-DSL ride matches the
  source trader's own hold horizon; copying a DEGEN with a wide stop would be a mismatch.

## Capital provenance

New capital, split 50/50 across the two sleeve wallets — a balanced whale-conviction
long/short book with no directional bias. Deployable as a standalone fund.
