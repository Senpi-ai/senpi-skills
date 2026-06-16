# Cougar — Skill Attribution

**Skill:** cougar-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-16

## What Cougar is

The **U.S. Equity Long/Short** pillar — the ninth hedge-fund agent, and the
first *fund* dedicated to the tokenized U.S. equity market on Hyperliquid. Built
in response to the June 2026 structural shift: HIP-3 stock markets did >$18B in
the first half of June (more than crude + Brent combined), 23 of the top-30 HL
assets by OI are equities + commodities, and trade.xyz holds 91% of HIP-3 OI.
Equity coverage existed only at the single-agent level (Bobcat long-only big-tech,
Lemur pre-IPO, Falcon conversions); Cougar is the equity *hedge fund*.

## Lineage

- **Architecture** — the Spider/Octopus/.../Ox helpers-native two-book pattern:
  ONE leg-parameterized producer (`COUGAR_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk.
- **Method** — Octopus's cross-sectional dispersion scorer (rank by 24h excess
  return vs the universe mean; long leaders / short laggards, trend-confirmed,
  with blow-off / capitulation guards) applied to a **different universe**: the
  curated tokenized-US-equity whitelist instead of the crypto cross-section.
- **Thesis** — equity dispersion is at a multi-decade extreme (top-decile S&P
  names beating the bottom decile by ~65pp, widest since 2008), and the tokenized
  equity universe is now deep + liquid enough on HL to run a real cross-sectional
  long/short.

## Design decisions specific to Cougar

- **Universe is a curated equity whitelist**, not a volume-floored scan of the
  whole board — so it ranks a coherent peer group (US stocks), not a mix of
  stocks/commodities/FX. Intersected with the live board + liquidity floor; new
  trade.xyz listings auto-join once added to `config.equities`.
- **Lower liquidity floor than Octopus** (500k vs 20M) — tokenized equities are
  liquid but thinner than crypto majors; the floor still screens out dead names.
- **Slightly longer DSL timeouts than Octopus** (7d vs 4d hard_timeout, 8h/16h
  stall-cuts vs 6h/12h) — equity relative trends persist longer than crypto.
- **Beta-neutral by construction** — long-leaders + short-laggards on equally
  funded wallets; the P&L is the dispersion spread, not market direction.

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate; equities cap lower at venue).
- Per-position margin **20%** (≤ 25% fleet cap); slots 4.
- Drawdown halt **20%**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / ranked_pool / candidates /
  signals_pushed` + `mean_rs_24h`.
- Sizes off `max(main, xyz)` account value — never the sum (cross-margin
  two-views double-count fix).

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Cougar: strict
  5x, hard drawdown halt, trend-confirmed entries (never long a downtrend / short
  an uptrend).
- **Fees are the biggest killer** — basket rotation is capped (`max_entries` 6,
  `per_asset_cooldown` 240m) so leadership shifts don't churn fees.
- **XYZ markets trade 24/7** — no market-hours gating; equities trade weekends.

## Capital provenance

New capital, funded **50/50** across the long and short wallets — equal funding
is required for the pair to offset and stay beta-neutral.
