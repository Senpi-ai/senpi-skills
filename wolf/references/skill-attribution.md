# Wolf — Skill Attribution

**Skill:** wolf-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-15

## What Wolf is

The **Event-Driven / Regime-Rotation** pillar — the sixth hedge-fund agent,
extending the line beyond Spider (AI/Tech), Octopus (Relative-Value), Camel
(Carry), Caracal (Volatility), and Elephant (Global Macro). Where Elephant
trades each macro asset on its own trend, Wolf adds a *regime-rotation* return
stream: a single market-wide regime read decides which side of the book works,
and capital rotates to whichever regime is in force. Its edge is the macro
**transition** itself.

## Lineage

- **Architecture** — the Spider/Octopus/Camel/Caracal/Elephant helpers-native
  two-leg pattern: ONE leg-parameterized producer (`WOLF_LEG`), two wallets, two
  runtime YAMLs, `producer_daemon` + fcntl lock, `SenpiClient.push_signal()`
  ingest, runtime-owned LLM gate + DSL + risk.
- **New wrinkle** — a **shared cross-asset "brain"** the producer computes once
  per tick BEFORE either book scores: a regime read (equities + oil + gold + BTC
  + the dollar 4h votes) that gates which book may fire. (Rhino shares this
  shared-brain shape with a stress read.)
- **Thesis** — trade the turn: in a confirmed risk-on regime, long beaten-down
  beta; in a confirmed risk-off regime, long defensives + short risk; in NEUTRAL,
  stand down.

## Design decisions specific to Wolf

- **No single asset flips the book.** The regime is declared only when the NET
  cross-asset vote clears `regimeThreshold` (default 2) — the whole complex has
  to lean one way, which is what separates Wolf from a per-asset trend follower.
- **Rotation, not a fixed bet.** Usually only one book is active at a time (the
  one the regime favors); the other waits in cash. This is the rotation — and
  what separates Wolf from a Thesis Fund (fixed side) and Coyote (single-asset,
  crypto-only regime tag).
- **A regime flip is handled on the ENTRY side.** The losing-regime book stops
  *adding*; open winners still trail out via the DSL ladder — Wolf does not dump
  a book just because the tape turned.
- **Asymmetric DSL by book.** Risk-on = wide let-it-run (a relief rally runs;
  all time-cuts off, 5d timeout). Risk-off = tighter (risk-off moves are sharp
  and reverse fast on a headline; stall-cuts on, 3d timeout).

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense; indices/FX cap lower
  at venue and the clamp respects it).
- Per-position margin **20% (risk_on) / 18% (risk_off)** (≤ 25% fleet cap).
- Drawdown halt **22% (risk_on) / 18% (risk_off)**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): publishes the live `regime` read +
  `scanned / candidates / signals_pushed` every tick, including a `STANDING
  DOWN` tick when the regime doesn't favor the book.
- Sizes off `max(main, xyz)` account value — never the sum (the cross-margin
  two-views double-count fix).

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate.
  Wolf: strict 5x, *regime-gated* rotation (only rotates when the whole complex
  agrees, not on noise), hard drawdown halt per book.
- **Fees are the biggest killer** — regime transitions are infrequent, so entries
  are few (`max_entries_per_day` 5 risk_on / 8 risk_off); the rotation does not
  churn within a regime.
- **XYZ markets trade 24/7** — the regime probes (equities/oil/gold/dollar) and
  the risk-off defensives stay active through weekends; no market-hours gating.

## Capital provenance

New capital, funded **50/50** across the risk_on and risk_off wallets — Wolf
rotates, so each regime gets equal firepower since the dominant regime of a
given period can't be known in advance.
