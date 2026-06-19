# Caribou — Skill Attribution

**Skill:** caribou-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-19

## What Caribou is

The first **Cross-Asset Trend Fund** (managed futures / CTA) in the fleet — it
trend-follows a maximally diversified universe spanning EVERY asset class on
Hyperliquid (crypto, xyz stocks, indices, metals, energy), long the uptrends and
short the downtrends, sized to equal risk (volatility parity) and capped per asset
class. Two independent sleeves on separate wallets (long / short) so the fund may
hold the same asset in opposite directions across the two wallets.

## Lineage

- **Architecture** — the helpers-native leg-parameterized two-sleeve pattern
  (Octopus family): ONE producer, parameterized by `CARIBOU_LEG` (long/short),
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest, runtime-owned
  LLM gate + DSL + risk. Universe discovery + `max(main, xyz)` account-value read
  reused from Octopus; signature-adaptive daemon launch.
- **Methods** — TIME-SERIES trend (each asset vs its own 4h/daily structure +
  momentum + RSI guard), distinct from Octopus's cross-SECTIONAL relative strength.
  Volatility-parity position sizing (inverse-ATR, normalized + clamped) and
  per-asset-class margin caps are new to the fleet here.
- **Thesis** — managed futures: trends persist and appear in different markets at
  different times; trend-following a maximally diversified, uncorrelated universe
  long/short produces a smooth, low-net-beta, crisis-positive return stream.

## Design decisions specific to Caribou

- **All asset classes, one universe.** The instrument board is bucketed into
  crypto / equity / index / metal / energy; each class is liquidity-gated
  *within itself* (relative-median, so a thin stock isn't measured against BTC
  volume) and momentum-ranked before candle confirmation — diversification across
  classes is the core edge, not a bolt-on.
- **Volatility parity — the differentiator.** `margin = equity × baseRiskPct ×
  (referenceVol / ATR%)`, clamped to [minMarginPct, maxMarginPct]. Equal risk per
  position keeps the book from being dominated by high-vol crypto.
- **Per-class margin cap (40%).** Hard diversification floor — no single class can
  dominate, which is what makes the equity curve smooth and the hedge real.
- **Two independent sleeves on separate wallets.** Chosen over a single
  cross-margined book specifically so the fund can hold the same asset long and
  short simultaneously (clean trend-flip handling — the short sleeve catches a new
  downtrend immediately without waiting for the long sleeve's DSL to fully exit).
- **Asymmetric trend DSL.** Tight Phase 1 (cut losers fast) + wide Phase 2 ladder
  (let winners run to +150% before locking 84%) + time-cuts OFF — the literal
  "cut losers short, let winners run" that makes trend-following work. Taker-fallback
  entries (a trend-follower must catch the move; the opposite of a fader).

## Fleet-standard compliance

- Max leverage 5x (strict clamp, then venue max); conviction-scaled (base 3x → 5x
  apex). Vol-parity margin (no hardcoded $); per-class cap 40%; up to 8 slots.
- Drawdown halt 22%, daily loss limit 12%, max_consecutive_losses 6 (trend takes
  many small losses before the big trend). Mandatory DSL; FEE_OPTIMIZED_LIMIT with
  taker fallback on entries + exits. Verbose per-tick JSON.
- Sizes off `max(main, xyz)` account value — never the sum.

## Negative-lesson inputs

- **Bison done right.** Bison is a single-asset 10x trend-follower that gets chopped
  to breakeven on any one bad day. Caribou is the same trend edge spread across 30+
  uncorrelated markets with vol-parity sizing — chop in one market is offset by
  trends in others. The diversification IS the hedge; no bolted-on fader required.
- **Cobra antipattern** — fixed high leverage + no diversification + no drawdown
  gate. Caribou: vol-parity (no fixed size), class caps, conviction-clamped leverage,
  hard drawdown halt.
- **Fees are the biggest killer** — slow cadence (trend-following is not
  high-frequency), per-class caps prevent over-trading one class, time-cuts off
  avoid premature churn.

## Capital provenance

New capital, split across the two sleeve wallets per the operator's chosen funding
(default ~50/50 long/short). Net-beta-neutral with crisis-positive tail (the short
sleeve). Deployable as a standalone fund.
