# Eel — Skill Attribution

**Skill:** eel-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-17

## What Eel is

The **Electrons vs Hydrocarbons** thesis fund — an **energy-sector-neutral**
long/short on the AI-power crunch. The cleanest-hedged member of the Lion thesis-
fund family: both legs sit in one sector, so an energy-wide move cancels.

Built around the June 2026 energy story: AI datacenters are a structural new
source of *electricity* demand. Eel LONGs the power complex (uranium, gas-fired
power, grid copper, fuel cells, rare-earth) and SHORTs crude oil — the legacy
transport fuel barely levered to AI — profiting from the electrons-beat-barrels
spread rather than energy direction.

## Lineage

- **Architecture** — the Spider/Octopus/…/Lion helpers-native two-book pattern:
  ONE leg-parameterized producer (`EEL_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Direct template** — **Lion** (the thesis-as-hedged-long/short pattern), with
  the universe swapped from the AI complex to the energy pair.
- **Thesis** — the AI electricity-demand supercycle: power/nuclear/grid/gas win,
  crude oil lags. Expressed as a same-sector pair so the bet is dispersion, not
  energy beta.

## Design decisions specific to Eel

- **Same-sector pair = the tightest hedge.** Unlike Lion (cross-asset AI vs broad
  market), both Eel legs are energy, so a broad energy selloff hurts both and
  cancels — the P&L is purely power-minus-oil.
- **Absolute trend is the gate; relative strength is a tiebreaker.** A power name
  is longed only while genuinely trending up; crude shorted only while rolling
  over (+ a capitulation guard so it never shorts an exhausted oil bottom).
- **Per-group conviction sizing weights** — `margin = account_value × marginPct ×
  sizingWeights[name]`. URNM 1.2× (purest AI-power), COPPER 1.1×, NATGAS 1.0×,
  USAR 0.7×, BE 0.6× (speculative). Conviction as a multiplier on a budget-scaled
  slot, never a hardcoded dollar amount.
- **The long/short balance is an explicit operator decision** — set by the
  power/oil wallet funding split, defaulting to a slight long-power tilt (~55/45)
  since the thesis is directionally pro-power.
- **Oil book runs tighter than the power book** — 4x leverage cap (vs 5x), tighter
  max-loss (12% vs 15%), faster stall-cuts — an oil supply-shock spike is violent.
- **XLE deliberately not shorted** — the energy ETF is oil-major-heavy but also
  holds power names, which would muddy the pair; the short is pure crude.
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

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Eel: strict
  5x/4x clamp, hard drawdown halt, absolute-trend-confirmed entries (never long a
  downtrend / short an uptrend).
- **Fees are the biggest killer** — basket rotation is capped (`max_entries` 6/4,
  `per_asset_cooldown` 240/300m) so leadership shifts don't churn fees.
- **Oil supply shocks** — the short crude book is deliberately tighter and smaller;
  a Mideast/OPEC spike is violent, and the capitulation guard avoids shorting an
  exhausted oil bottom.
- **Commodities trade ~24/7** — no market-hours gating.

## Capital provenance

New capital, split across the power and oil wallets per the operator's chosen
balance (default slight long-power tilt, ~55/45). The two legs are
energy-sector-neutral by construction.
