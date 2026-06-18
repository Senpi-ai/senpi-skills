# Mongoose — Skill Attribution

**Skill:** mongoose-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-17

## What Mongoose is

The **On-Chain Finance vs Legacy** thesis fund — a long/short on the migration of
money on-chain. LONG the on-chain financial rails (crypto exchanges, stablecoin
issuers, BTC treasuries, HYPE), SHORT legacy finance + the broad market.

Built around the June 2026 tokenization wave: stablecoins (Circle's listing),
crypto exchanges (Coinbase, Robinhood), and BTC/HYPE treasuries are eating legacy
financial rails. Mongoose trades that disruption directly — long the disruptors,
short the incumbents — profiting from the disruptor-vs-incumbent spread.

## Lineage

- **Architecture** — the Spider/Octopus/…/Lion helpers-native two-book pattern:
  ONE leg-parameterized producer (`MONGOOSE_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Direct template** — **Lion** (the thesis-as-hedged-long/short pattern), with
  the universe swapped to on-chain finance vs legacy.
- **Thesis** — money migrating on-chain. Crypto-finance rails compound; legacy
  finance and the broad market are the relative losers.

## Design decisions specific to Mongoose

- **Strong long, deliberately-loose hedge.** The long book (the on-chain rails)
  is all hot, live, high-conviction names; the short book is **cross-sector and
  thin** (few pure legacy-finance names list on the venue), so it leans on SP500
  + BX. Documented honestly: it nets down market beta, not a clean same-sector
  pair. Sharpen as more legacy-finance names list.
- **Absolute trend is the gate; relative strength is a tiebreaker.** A rail is
  longed only while genuinely trending; an incumbent shorted only while rolling
  over (+ a capitulation guard).
- **Per-group conviction sizing weights** — `margin = account_value × marginPct ×
  sizingWeights[name]`. HYPE 1.3×, CRCL 1.2×, COIN/HOOD 1.0×, MSTR 0.7×,
  PURRDAT 0.6×. The treasury proxies (MSTR/PURRDAT) are sized down — they're
  levered BTC/HYPE proxies and double-count HYPE exposure.
- **The long/short balance is an explicit operator decision** — set by the wallet
  funding split, defaulting to a net-long tilt (the thesis is bullish the disruptors).
- **Legacy book runs tighter than the on-chain book** — 4x leverage cap (vs 5x),
  tighter max-loss (12% vs 16%), faster stall-cuts — short squeezes are violent.
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

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Mongoose: strict
  5x/4x clamp, hard drawdown halt, absolute-trend-confirmed entries (never long a
  downtrend / short an uptrend).
- **Fees are the biggest killer** — basket rotation is capped (`max_entries` 6/5,
  `per_asset_cooldown` 240/300m) so leadership shifts don't churn fees.
- **Short squeezes** — the short book is deliberately tighter and smaller; alt
  shorts are gated hard and BTC is omitted by default.
- **XYZ markets trade 24/7** — no market-hours gating; equities trade weekends.

## Capital provenance

New capital, split across the on-chain and legacy wallets per the operator's
chosen balance (default net-long tilt, since the thesis is directionally bullish
the disruptors). The hedge is cross-sector and index-led — looser than a
same-sector pair.
