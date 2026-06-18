# Boar — Skill Attribution

**Skill:** boar-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-17

## What Boar is

The **Hard Money vs Paper (Debasement)** thesis fund — a long/short on currency
debasement under fiscal dominance. LONG scarce real assets (precious metals +
BTC), SHORT the fiat-denominated paper economy (the broad market + rate-sensitive
long-duration growth).

Built around the dominant 2026 macro narrative: deficits compound, the term
premium rises, and scarce real assets outperform financial claims discounted at
those higher rates. Boar trades that directly — long hard money, short paper.

## Lineage

- **Architecture** — the Spider/Octopus/…/Lion helpers-native two-book pattern:
  ONE leg-parameterized producer (`BOAR_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Direct template** — **Lion** (the thesis-as-hedged-long/short pattern), with
  the universe swapped to hard money vs paper claims.
- **Thesis** — fiscal-dominance debasement. Gold/BTC/metals compound vs fiat;
  paper claims (esp. long-duration) de-rate as the term premium rises.

## Design decisions specific to Boar

- **The loosest hedge of the family — and the honest design response.** In a pure
  liquidity melt-up, gold AND stocks rise together (everything priced in debasing
  fiat rises), so a long-gold/short-index pair can correlate POSITIVELY. The short
  is therefore TILTED toward rate-sensitive long-duration growth (RIVN/DKNG/HIMS),
  which de-rates even in a melt-up as the term premium compresses multiples —
  the lever that tightens an inherently melt-up-correlated pair. Documented
  honestly; Boar is best deployed as a tilt, not a market-neutral bet.
- **Absolute trend is the gate; relative strength is a tiebreaker.** Hard money
  longed only while trending up; paper shorted only while rolling over (+ a
  capitulation guard).
- **Per-group conviction sizing weights** — `margin = account_value × marginPct ×
  sizingWeights[name]`. GOLD 1.2× + BTC 1.2× (co-cores), SILVER 1.0×, PLATINUM
  0.7×, PALLADIUM 0.6×; short SP500 1.2× anchor + rate-sensitive growth 0.6×
  (small, squeeze-prone). Conviction as a multiplier on a budget-scaled slot.
- **The long/short balance is an explicit operator decision** — set by the wallet
  funding split, defaulting to a net-long-hard-money tilt.
- **Paper book runs tighter than the hard-money book** — 4x leverage cap (vs 5x),
  tighter max-loss (12% vs 16%), faster stall-cuts; the rate-sensitive shorts are
  small + gated hard (squeeze-prone).
- **Signature-adaptive daemon launch** — introspects the installed
  `producer_daemon` signature and passes `wallet=`/`scanner=` only if supported,
  so it runs unpatched on both old and helpers-upgraded hosts.

## Fleet-standard compliance

- Max leverage **5x long / 4x short** (strict clamp + runtime gate; venue caps below).
- Per-position margin **18% long / 15% short** (× conviction weight, ≤ 25% cap).
- Drawdown halt **20% long / 18% short**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / ranked_pool / candidates /
  signals_pushed / emitted` + `mean_rs_24h`.
- Budget-scaling notional floor: `max(account_value × minNotionalPctOfEquity,
  venueMinNotionalUsd)` — no hardcoded dollar sizing floor (venueMinNotionalUsd
  is the venue's physical minimum order value, an exchange constant, not a knob).
- Relative-to-market liquidity gate: an instrument's 24h volume must be ≥
  `volFloorPctOfMedian × the universe median volume` — no hardcoded dollar volume
  floor; the gate is a market property, so it adapts to conditions (shared
  fleet-wide with the other universe funds).
- Per-candidate affordability cap — never emits an order the wallet can't fund.
- Sizes off `max(main, xyz)` account value — never the sum.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Boar: strict
  5x/4x clamp, hard drawdown halt, absolute-trend-confirmed entries (never long a
  downtrend / short an uptrend).
- **Fees are the biggest killer** — basket rotation is capped (`max_entries` 6/5,
  `per_asset_cooldown` 240/300m) so leadership shifts don't churn fees.
- **Short squeezes** — the paper book is tighter (4x, 12% max-loss); the
  rate-sensitive growth shorts are small and gated hard (meme-y, squeeze-prone).
- **Melt-up correlation** — the headline risk: in a liquidity blow-off the long
  and short legs can rise together. Addressed via the rate-sensitive short tilt,
  but imperfect — documented, and Boar is sized as a tilt, not market-neutral.
- **Commodities/crypto trade ~24/7** — no market-hours gating.

## Capital provenance

New capital, split across the hard-money and paper wallets per the operator's
chosen balance (default net-long-hard-money tilt). The hedge is the loosest of
the thesis-fund family — best deployed as a directional tilt.
