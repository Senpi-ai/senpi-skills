# Caracal — Skill Attribution

**Skill:** caracal-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-03

## What Caracal is

The **Volatility** pillar of the four hedge-fund agents complementing Spider
(directional). Octopus is Relative-Value; Camel is Carry; Caracal is
Volatility; Elephant is Global Macro. Caracal adds a convexity / managed-futures
return stream — it profits from *movement* (volatility expansion), independent
of market direction.

## Lineage

- **Architecture** — the Spider/Octopus/Camel helpers-native two-leg pattern:
  ONE leg-parameterized producer (`CARACAL_LEG`), two wallets, two runtime
  YAMLs, `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Thesis** — volatility **compression precedes expansion**: a breakout from a
  low-volatility coil has higher follow-through than a breakout in already-
  volatile tape. Caracal trades the break direction (long or short) of a coiled
  name when an expansion surge confirms.

## Design decisions specific to Caracal

- **Both legs trade both directions.** Unlike Octopus/Camel (fixed per-book
  direction), Caracal's direction is the *break* direction — it is direction-
  agnostic by construction, which is what makes it a true volatility play.
- **Two universes, one engine.** The `breakout` book runs on main-DEX crypto;
  the `catalyst` book runs the identical engine on XYZ (equities/energy/metals/
  indices). The catalyst book converts macro themes (oil/Iran geopolitics, AI
  infra) into vol sources — it rides whichever way the catalyst breaks, 24/7
  (XYZ trades weekends; no market-hours gating per the XYZ-markets reference).
- **Compression precondition is the edge.** Distinct from the fleet's existing
  breakout agents (Hawk/Badger 4h breakouts, Stag parabolic) which fire on any
  breakout. Caracal requires a prior ATR squeeze (recent/baseline ATR ≤ 0.7-0.9)
  AND an expansion surge (breakout-bar TR ≥ 1.3-2.0× baseline ATR) — a coiled
  spring, not a chase.
- **Tight, early-locking DSL.** Volatility expansions can round-trip fast, so
  phase1 cuts at 12% and phase2 starts locking at +8%→30%; stall-cuts ON; 2d
  hard_timeout (vol events resolve fast).

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense).
- Per-position margin **18%** (≤ 25% fleet cap).
- Drawdown halt **20%**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / candidates / signals_pushed`
  + per-name `squeeze` and `surge`.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate.
  Caracal: strict 5x, episodic (only coiled breakouts), hard drawdown halt.
- **Fees are the biggest killer** — Caracal is selective (coil + break + surge),
  so most ticks are empty; `per_asset_cooldown` 120m + `max_entries_per_day` 8
  bound turnover. Selectivity is the fee defense.
- **Hard TP antipattern** — no fixed take-profit; the DSL ratchet owns exits
  and lets a real expansion run while locking gains progressively.

## Capital provenance

New capital, funded **50/50** across the breakout (crypto) and catalyst (XYZ)
wallets.
