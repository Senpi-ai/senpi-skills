# Magpie — Skill Attribution

**Skill:** magpie-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-16

## What Magpie is

The **IPO / New-Listing Event** pillar — the tenth hedge-fund agent, built in
direct response to the June 2026 SpaceX listing (SPCX did $1.4B in day-1 perp
volume on Hyperliquid, 30% of all HIP-3 volume, while real exchanges ran out of
shares). It productizes the full pre-IPO → listing → graduation arc of tokenized
equities as a two-wallet fund. Pairs with Cougar (ongoing equity long/short) as
the equity side of the fund line.

## Lineage

- **Architecture** — the Spider/.../Cougar helpers-native two-book pattern: ONE
  leg-parameterized producer (`MAGPIE_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Detection reuses proven single-agent logic:**
  - PRE-LISTING book = **Lemur's** `fetch_ipop_universe` (IPOP funding-signature
    discovery) + 4h/1h trend + SM scoring.
  - GRADUATION book = **Falcon's** `classify_instrument` + `detect_conversion` +
    class-state/conversion-window persistence + post-conversion momentum scoring.
- **Thesis** — new equity listings on HL are an event with a repeatable
  structure: a throttled pre-IPO ramp, then a sharp de-throttling at conversion
  (funding ~100× up, leverage cap lifts, Discovery Bounds off) that opens free
  price discovery. Two books capture the two phases.

## Design decisions specific to Magpie

- **Two phases, two books, two risk profiles.** Pre-listing is throttled and
  multi-day → small size (12%), low leverage (3x), moderate-wide DSL.
  Graduation is de-throttled and trends hard → larger size (15%), 5x, wide
  let-winners-run DSL. They never contradict (different instruments / phases).
- **Conversion-window eligibility, not flip-tick only.** A detected IPOP→STANDARD
  flip stays tradeable for `conversionWindowHours` (72h), so momentum that
  develops over hours/days is still captured — the single flip tick is rarely
  the best entry.
- **First tick seeds, doesn't fire.** The class cache is seeded on the first
  tick with no flips (a flip requires a known prior class), avoiding a
  false-positive conversion on startup.
- **SM is a bonus, not a gate, on fresh names.** Smart-Money data is sparse on
  pre-listing and freshly-converted names, so it adds score when present but
  never blocks (pre-listing falls back to trend-only).

## Fleet-standard compliance

- Max leverage **3x (pre-listing) / 5x (graduation)** — strict clamp + runtime
  gate; the pre-listing cap matches the IPOP discovery-bounds regime.
- Per-position margin **12% / 15%** (≤ 25% fleet cap); slots 3.
- Drawdown halt **18% / 20%**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker-true.
- Verbose per-tick JSON (never silent): pre-listing publishes `ipop_universe`;
  graduation publishes `ipops_now` + `conversions_in_window`.
- Sizes off `max(main, xyz)` account value — never the sum (cross-margin
  two-views double-count fix).

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Magpie: low
  3x/5x, hard drawdown halt, event-gated entries.
- **Fees are the biggest killer** — both books are episodic (most ticks empty);
  `max_entries` 4 + `per_asset_cooldown` 360m prevent churning a thin universe.
- **XYZ markets trade 24/7** — IPOPs and equities trade weekends; no market-hours
  gating.
- **Requires user-scope auth** for `leaderboard_get_markets` (SM confirmation) —
  documented in README + config.

## Capital provenance

New capital, funded **50/50** across the pre-listing and graduation wallets —
the pre-listing book accumulates into the IPO; the graduation book holds dry
powder for the conversion pop.
