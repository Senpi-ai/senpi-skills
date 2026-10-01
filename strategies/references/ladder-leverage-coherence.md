# Leverage/ROE ladder coherence

Every DSL threshold is ROE, and the engine converts ROE to a price floor by **dividing by
leverage** (`senpi-strategy-author/references/dsl-configuration.md`). A package that spreads
leverage across score tiers therefore carries a ladder whose meaning in PRICE changes per tier —
and in the wrong direction, since the lowest-conviction tier gets the lowest leverage and so the
widest stop.

`cheetah` had this and was fixed in #806 by flattening leverage and scaling conviction through
`marginPct` instead. These ten still have it. They are NOT a mechanical sweep, for the reasons
under each table.

## Measured 2026-10-01 — which of these actually fire

Flattening needs a target, and the only defensible one is the leverage a package's trades actually
run at. From 14 days of real emits:

| package | wallets | scan lines | emits | modal leverage | status |
|---|---|---|---|---|---|
| `raptor` | 6 | 13,314 | **1,066** | 10x (67%) | **FIXED** → flat 10x |
| `jaguar` | 2 | 2,169 | **684** | 10x (67%) | **FIXED** → flat 10x |
| `owl` | 6 | 2,716 | **645** | 8x (66%) | **FIXED** → flat 8x |
| `pangolin` | 4 | 4,455 | **252** | 3x (63%) | **FIXED** → flat 3x, ladder kept |
| `wolverine` | 5 | 4,403 | 11 | — | ~dormant |
| `polar` | 2 | 1,888 | 6 | — | ~dormant |
| `kestrel` | 1 | 1,255 | 4 | — | ~dormant |
| `otter` | 2 | 4,059 | **0** | — | scans, never emits |
| `kodiak` | 1 | 206 | **0** | — | scans, never emits |
| `lemon` | — | — | — | — | not deployed |

**Five of the ten produce no signals at all**, so their ladders are theory. `otter` ran 4,059 scan
lines and emitted nothing; `kodiak` 206 and nothing.

**All four that fire are now fixed.** The five dormant ones were checked rather than assumed:
`otter` logs *"no candidate passed score+cooldown"* (1,360x) and *"all top 3 failed spread/dedup"*
(1,233x) with near-misses at score 8.0 needing 9 — a working, selective scanner finding nothing.
`kodiak` logs *"SOL HOLD (gate): 4h=BULLISH 80% | 1h=BEARISH"* — one asset, held by its own
multi-timeframe gate. Neither is a dead gate like condor's old unreachable threshold.

## What was fixed

| | before | after | exposure |
|---|---|---|---|
| `owl` | 7/8/10x at a flat 25% | flat **8x** at 31.2 / 25.0 / 21.9% | identical per tier |
| `jaguar` | 7/10x at a flat 50% | flat **10x** at 50.0 / 35.0% | identical per tier |
| `raptor` | 7/8/10x, margin stepping separately on `highConvScore` | flat **10x** at 35.0 / 20.0 / 17.5% | identical per tier |
| `pangolin` | 3/5x at a flat 25% | flat **3x** at 41.7 / 25.0% | identical per tier |

`raptor` had TWO conviction ladders on different boundaries — leverage stepped on 10/8/6 while
margin flipped at `highConvScore` 10 via `margin_pct_for`. Both now ride one per-tier value;
`margin_pct_for` survives as a thin wrapper and `marginPctBase` is kept at the **smallest** tier
margin (17.5) because `min_budget` reads it from an allowlist and cannot see the third tier element,
so a higher value there under-computes the minimum budget.

`pangolin`'s LADDER IS DELIBERATELY UNCHANGED. Measured on its own 20 closes — median peak 0.57%
of price, p75 3.51%, p90 5.39% — its rung 0 at 2.67% arms on **40%** of positions and rung 1 on
10%. The top two rungs (10.0% and 16.7% of price) did not arm in 20 positions, but a tail rung
exists for the rare runner and removing it caps winners, which is the reasoning penguin's own
ladder records for its 1.5–4% tail tier. n=20 is too thin to retune four rungs on.

That also corrected an earlier claim in this file: a universal "rung 0 must be under 1% of price"
bar came from PENGUIN's median peak and does not transfer between universes. The test now keeps
leverage-flatness and rung-0 arming as SEPARATE assertions, and an exemption must carry its own
measurement (`RUNG0_MAX_PRICE_PCT`).

`new_margin = old_lev × old_margin / flat_lev`, so each tier's exposure is unchanged to the
decimal. Both now arm rung 0 inside the median peak — owl at 0.62% of price, jaguar at 0.70%.
Each scanner keeps `get_leverage_for_score` as a thin wrapper over a new `get_sizing_for_score`
that also returns the tier's margin, so any other caller is unaffected.

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

