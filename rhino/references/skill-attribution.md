# Rhino — Skill Attribution

**Skill:** rhino-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-15

## What Rhino is

The **Tail-Risk / Crisis-Alpha** pillar — the seventh hedge-fund agent, and the
*portfolio hedge* of the line-up (Spider, Octopus, Camel, Caracal, Elephant,
Wolf, Rhino). Where the other funds seek a return stream, Rhino is asymmetric by
design: it carries **cheap convexity** — bleeds a little in calm and pays big in
shocks. It is the thing that's green on the days everything else is red.

## Lineage

- **Architecture** — the Spider/Octopus/Camel/Caracal/Elephant helpers-native
  two-leg pattern: ONE leg-parameterized producer (`RHINO_LEG`), two wallets, two
  runtime YAMLs, `producer_daemon` + fcntl lock, `SenpiClient.push_signal()`
  ingest, runtime-owned LLM gate + DSL + risk.
- **Shared brain** — like Wolf, the producer computes a cross-asset read once per
  tick before scoring: here a **stress detector** (oil / equities / gold / BTC
  breaks + a BTC vol-expansion flag) that gates the escalation book.
- **Thesis** — own the crisis trade cheaply before the shock (the always-on
  hedge), and add leveraged convexity the moment stress confirms (the escalation
  book), then bank it before the violent reversal. Inspired by the 2026 oil war:
  while everything else bled, the winners were long oil and gold.

## Design decisions specific to Rhino

- **Two asymmetric books, not two return streams.** `hedge` is always-on
  insurance (LONG defensives that are trending up — no falling-knife hedges),
  sized small so the *position size* is the cost control, with a wide DSL so a
  crisis winner runs. `escalation` sits in cash as dry powder and only deploys
  under a confirmed stress regime.
- **Cash drag IS the hedge.** The escalation book's idle capital in calm is the
  point — it's the convexity you're paying for, not under-utilization.
- **Bank the spike.** Crises reverse violently (a ceasefire dumps oil/gold; a
  relief bid squeezes shorts), so the escalation book runs a moderate-tight DSL
  with an early profit-lock ladder and a 2d timeout — don't give the convex gain
  back. The hedge book, by contrast, is wide (10d timeout) to let a real crisis
  winner run.
- **Stress is a measured signal, not a view.** No user conviction required — the
  escalation leg self-activates on `stressThreshold` cross-asset confirmations.

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense; XYZ defensives cap
  lower at venue and the clamp respects it).
- Per-position margin **10% (hedge) / 22% (escalation)** (≤ 25% fleet cap).
- Drawdown halt **20% (hedge) / 22% (escalation)**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): publishes the live `stress` read +
  `scanned / candidates / signals_pushed` every tick, including a `DORMANT` tick
  when the escalation book is gated off in calm.
- Sizes off `max(main, xyz)` account value — never the sum (the cross-margin
  two-views double-count fix).

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate.
  Rhino: strict 5x, small always-on hedge, escalation only on confirmed stress,
  hard drawdown halt per book.
- **Fees are the biggest killer** — the hedge book turns over slowly
  (`max_entries_per_day` 3); the escalation book is silent in calm and only
  fires in stress bursts, so it isn't paying fees to churn an empty signal.
- **XYZ markets trade 24/7** — the defensives carry and the stress detector
  watches through weekends; no market-hours gating.

## Capital provenance

New capital, funded **50/50** across the hedge and escalation wallets — the
hedge book runs a small `margin_pct` (10%) so even at half the pool it's lightly
deployed (cheap standing insurance), while the other half is the escalation
book's dry powder, deployed only under stress.
