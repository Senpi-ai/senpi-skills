# Lion — Skill Attribution

**Skill:** lion-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-17

## What Lion is

The **Two-Speed-Market (K-Shaped) Cross-Asset Long/Short** pillar — the eleventh
hedge-fund agent, and the first *fund* to span **both tokenized U.S. equities and
main-DEX crypto on one cross-margined wallet** in a single thematic book.

Built around a specific June 2026 macro view: a **K-shaped divergence** in which
the AI complex and the crypto winners (HYPE, SOL) keep booming while the broad
U.S. economy and the rest of crypto struggle. Lion is the fund that *trades that
view directly* — long the haves, short the have-nots, profiting from the
dispersion between the two speeds.

## Lineage

- **Architecture** — the Spider/Octopus/…/Cougar helpers-native two-book pattern:
  ONE leg-parameterized producer (`LION_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Direct template** — Cougar (U.S. equity long/short). Lion generalizes Cougar's
  single-universe equity cross-section into a **cross-asset thematic** book.
- **Thesis** — a K-shaped, two-speed world. The AI capex/compute cycle and HYPE's
  venue dominance keep compounding; the broad economy and laggard alts do not.

## Design decisions specific to Lion

- **Cross-asset universes on one wallet.** Each book mixes xyz equities and
  main-DEX crypto; `_dex_for()` routes each asset, and `get_positions()` reads
  both clearinghouse sections (account value via `max()`, never summed).
- **Thematic curated universes, not a cross-sectional scan.** The long universe
  is the "haves" (AI complex + HYPE/SOL); the short universe is the "have-nots"
  (SP500 + laggard alts). Membership *is* the thesis.
- **Absolute trend is the gate; relative strength is a tiebreaker** (vs Cougar's
  pure cross-sectional gate). A structural winner is longed while it is genuinely
  trending up, even on a day its peers ran harder — so the fund actually holds
  its highest-conviction names instead of benching them on relative-strength
  noise. It still *never* longs a confirmed downtrend or shorts a confirmed
  uptrend.
- **Per-group conviction sizing weights** — `margin = account_value × marginPct ×
  sizingWeights[name]`. HYPE 1.5×, SOL 0.6×, SP500 1.2×, laggard alts 0.7×.
  Conviction is expressed as a multiplier on a budget-scaled slot, never as a
  hardcoded dollar amount.
- **Net exposure is an explicit operator decision** — not a hardcoded constant.
  Set by the long/short wallet funding split + per-leg knobs, defaulting to a
  modest net-long tilt (~60/40) that matches the directional AI/HYPE conviction.
- **Long-AI + short-SP500 overlap is intentional** — the index contains the AI
  names, so the pair isolates the pure AI-vs-broad-market spread.
- **Short book runs tighter than the long book** — 4x leverage cap (vs 5x),
  tighter max-loss (12% vs 16%), faster stall-cuts, smaller alt weights, and BTC
  deliberately omitted from the default short basket — because short squeezes are
  violent and the thesis is directionally bullish-AI/HYPE.
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
- Budget-relative liquidity floor: an instrument's 24h volume must be ≥
  `liqVolMultiple × position notional` — no hardcoded dollar volume floor, so a
  bigger book demands a deeper market.
- Per-candidate affordability cap — never emits an order the wallet can't fund.
- Sizes off `max(main, xyz)` account value — never the sum.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Lion: strict
  5x/4x clamp, hard drawdown halt, absolute-trend-confirmed entries (never long a
  downtrend / short an uptrend).
- **Fees are the biggest killer** — basket rotation is capped (`max_entries` 6/5,
  `per_asset_cooldown` 240/300m) so leadership shifts don't churn fees.
- **Short squeezes** — the short book is deliberately tighter and smaller; alt
  shorts are gated hard and BTC is omitted by default.
- **XYZ markets trade 24/7** — no market-hours gating; equities trade weekends.

## Capital provenance

New capital, split across the long ("haves") and short ("have-nots") wallets per
the operator's chosen net exposure (default modest net-long tilt, ~60/40).
