# Ox — Skill Attribution

**Skill:** ox-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-16

## What Ox is

The **Risk-Parity / All-Weather** pillar — the eighth hedge-fund agent, and the
fund line's **core holding**. Where the other seven funds are each a way to
*bet* (Spider AI/Tech, Octopus market-neutral, Camel carry, Caracal volatility,
Elephant macro, Wolf regime-rotation, Rhino tail-risk), Ox is the steady,
diversified, low-drawdown *core* you hold while betting with them. Its edge is
**risk balancing**, not a view.

## Lineage

- **Architecture** — the Spider/Octopus/Camel/Caracal/Elephant/Wolf/Rhino
  helpers-native two-book pattern: ONE leg-parameterized producer (`OX_LEG`),
  two wallets, two runtime YAMLs, `producer_daemon` + fcntl lock,
  `SenpiClient.push_signal()` ingest, runtime-owned LLM gate + DSL + risk.
- **New mechanic** — **inverse-volatility position sizing.** Every other fund
  sizes positions at a flat `margin_pct`; Ox sizes each sleeve by
  `w_i = (1/vol_i) / Σ(1/vol_j)`, so a low-vol sleeve carries more notional than
  a high-vol one and no single asset class dominates portfolio risk. Weights are
  computed over the FULL basket so a re-entering sleeve gets its correct
  fractional weight, never the whole budget.
- **Thesis** — risk parity / all-weather: a vol-balanced basket across
  uncorrelated-ish sleeves (crypto, equities, metals, energy, FX) is lower
  drawdown than any single sleeve, and the genuinely-diversifying defensive
  sleeves (gold, dollar, yen) cushion risk-off without timing it.

## Design decisions specific to Ox

- **Inverse-vol over the full basket, not the un-held subset.** Computing weights
  over only the names being entered this tick would size a single re-entry to the
  entire budget — a real bug. Ox sizes vol for every sleeve (held + un-held) and
  emits the un-held ones at their full-basket weight.
- **Always invested, LONG only, never to cash.** All-weather behavior comes from
  diversification + defensive sleeves + the ballast overlay — not from rotating
  out or shorting (those are Wolf's and Rhino's jobs).
- **Low leverage (3x), low turnover (600s tick).** A core is held and rebalanced,
  not traded. The inverse-vol sizing is the primary risk control; the wide DSL is
  the backstop. Knife guard only governs *adding* a sleeve (it won't buy a hard
  downtrend); existing sleeves ride through normal drawdowns.
- **Ballast is always-on, budget-scaled — not gated dormant.** Unlike Rhino's
  stress-gated escalation book, Ox's ballast always holds some defensives and
  simply scales the budget up (×2) on a light risk-off lean. The cushion is
  permanent; the size is tactical.

## Fleet-standard compliance

- Max leverage **3x** (strict clamp + runtime gate defense — deliberately lower
  than the 5x fleet cap; it's a core, not a bet).
- Per-sleeve sizing via the producer's inverse-vol `marginUsd`; the YAML
  `margin_pct` (6 core / 5 ballast) is a fallback cap, all ≤ the 25% fleet cap.
- Drawdown halt **18% (core) / 16% (ballast)**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / sized / signals_pushed` + each
  sleeve's `weight_pct`; ballast publishes its `risk_off` read.
- Sizes off `max(main, xyz)` account value — never the sum (cross-margin
  two-views double-count fix).

## Sizing dependency (flagged)

Ox's risk parity depends on the runtime **honoring the per-sleeve
`signal.data.marginUsd`** (the inverse-vol weight) rather than re-sizing every
position to a flat `strategy.margin_pct`. This is the same code path as the
known runtime sizing issue (cross-margin equity double-count); once the runtime
honors per-signal `marginUsd`, Ox weights correctly. Until then it degrades
toward equal-weight rather than risk-weight.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate. Ox:
  low 3x, no rotation (always-invested core), inverse-vol risk control + hard
  drawdown halt.
- **Fees are the biggest killer** — Ox is the lowest-turnover fund in the line
  (600s tick, builds the basket then holds; `per_asset_cooldown` 360m blocks
  re-churn).
- **XYZ markets trade 24/7** — most sleeves are XYZ; no market-hours gating.

## Capital provenance

New capital, funded **70/30** across the core and ballast wallets — the
all-weather core carries the larger share; the ballast is the smaller defensive
overlay.
