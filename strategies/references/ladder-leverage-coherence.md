# Leverage/ROE ladder coherence — ten pending packages

Every DSL threshold is ROE, and the engine converts ROE to a price floor by **dividing by
leverage** (`senpi-strategy-author/references/dsl-configuration.md`). A package that spreads
leverage across score tiers therefore carries a ladder whose meaning in PRICE changes per tier —
and in the wrong direction, since the lowest-conviction tier gets the lowest leverage and so the
widest stop.

`cheetah` had this and was fixed in #806 by flattening leverage and scaling conviction through
`marginPct` instead. These ten still have it. They are NOT a mechanical sweep, for the reasons
under each table.

## The spread, per package

| package | slots | lev tiers | marginPct | rung 0 @ max lev | @ min lev | spread | shape |
|---|---|---|---|---|---|---|---|
| `lemon` | 1 | 5/7/10 | 30 | 0.50% | 1.00% | 2.0× | ok |
| `otter` | 2 | 5/7/10 | 25 | 0.50% | 1.00% | 2.0× | ok |
| `polar` | 1 | 5/7/10 | 50 | 0.80% | 1.60% | 2.0× | ok |
| `kestrel` | 2 | 3/5 | 30 | 1.00% | 1.67% | 1.7× | ok |
| `wolverine` | 1 | 3/5 | 25 | 2.00% | 3.33% | 1.7× | labelled tier |
| `pangolin` | 2 | 3/5 | 25 | 1.60% | 2.67% | 1.7× | ok |
| `jaguar` | 2 | 7/10 | 50 | 0.70% | 1.00% | 1.4× | ok |
| `owl` | 2 | 7/8/10 | 25 | 0.50% | 0.71% | 1.4× | ok |
| `raptor` | 2 | 7/8/10 | None | 0.80% | 1.14% | 1.4× | no marginPct |
| `kodiak` | 1 | 5/6/7 | 20 | 1.43% | 2.00% | 1.4× | ok |

## Why this is not a sweep

**There is no neutral direction.** Exposure can be held exactly constant (`new_margin =
old_lev × old_margin / flat_lev`), but the ladder's price meaning must change for every tier
except the one chosen.

- **Flatten UP** (to the max tier): margins fall, no cap is breached — but the stop halves in
  price and liquidation comes twice as close. `otter`'s low tier goes from a 2.00% stop and ~20%
  to liquidation, to a 1.00% stop and ~10%.
- **Flatten DOWN** (to the min or `default_leverage`): the stop keeps its width, but margin
  inflates past what the book can hold — `polar` would need **100%**, `otter` 2 slots × 50% is
  the whole wallet, `jaguar` 2 × 71% is impossible.

So the flat leverage has to be the one its trades ACTUALLY fire at, which needs the score
distribution from telemetry. Picking it blind is a tuning decision disguised as a refactor.

**Two are a different bug.** `raptor` has no `marginPct` — it sizes via `marginPctBase` /
`marginPctHighConv`. `wolverine`'s third tier element is a LABEL, not a margin:
`[[11, 5, 'apex'], [9, 3, 'standard']]` — whatever reads those labels has to change too.

## Also worth noting

- **`wolverine`'s rung 0 arms at 2.00%–3.33% of price.** Against the
  measured median peak of 0.84% of price (92 closes, 41 assets), that rung likely never arms
  at any tier — the stop is doing all the work and a position that goes green has no floor.
- **`pangolin`'s rung 0 arms at 1.60%–2.67% of price.** Against the
  measured median peak of 0.84% of price (92 closes, 41 assets), that rung likely never arms
  at any tier — the stop is doing all the work and a position that goes green has no floor.
- **`kodiak`'s rung 0 arms at 1.43%–2.00% of price.** Against the
  measured median peak of 0.84% of price (92 closes, 41 assets), that rung likely never arms
  at any tier — the stop is doing all the work and a position that goes green has no floor.

## A second shape the guard does NOT catch

A package can also split leverage through the FALLBACK rather than the tiers: if `minScore` sits
below the lowest tier threshold, an unmatched score falls through to `strategy.default_leverage`,
which may differ from every tier. `grizzly` looks like this at a glance — tiers `[[14,10],[12,10]]`
with a comment reading "default 7 below 12" — but its `minScore` is 12, so the 7x fallback is
unreachable and the 7 is dead config. Swept on 2026-10-01: **no package has a reachable fallback
that differs from its tiers.** If one ever does, the ladder is incoherent the same way and the
tier-based guard will not see it.

## Procedure when telemetry is back

1. Pull the score distribution per package — which tier actually fires, and how often.
2. Flatten to that leverage; compute `new_margin = old_lev × old_margin / flat_lev` per tier so
   exposure is preserved exactly, and check `slots × max(new_margin) <= 100`.
3. Re-express the ladder only if its rung 0 then sits above ~1% of price.
4. Add the package to `COHERENT` in `strategies/tests/test_penguin_family_drift.py`.

