# Octopus — Skill Attribution

**Skill:** octopus-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-03

## What Octopus is

The first **market-neutral** fund in the Senpi fleet. Octopus is one of the
four hedge-fund pillars built to complement Spider (a directional equity-style
fund): **Relative Value (Octopus)**, Carry, Volatility, and Global Macro. The
fleet was almost entirely directional before this — Octopus adds a near-zero-beta,
low-correlation return stream.

## Lineage

- **Architecture** — the Spider v5.1 helpers-native two-leg pattern: ONE
  leg-parameterized producer (`OCTOPUS_LEG`), two wallets, two runtime YAMLs,
  `senpi_runtime_helpers.producer_daemon` with an fcntl reentrancy lock,
  `SenpiClient.push_signal()` ingest, runtime-owned LLM gate + DSL + risk.
  Spider's two-wallet structure was validated live (a +35% long book) before
  Octopus reused it.
- **Thesis** — classic cross-sectional **dispersion / relative-value**: long
  the leaders, short the laggards of one liquid peer group, sized so the
  notionals offset. Returns come from the spread, not market direction.

## Design decisions specific to Octopus

- **Neutrality at the fund level, not via atomic pairs.** The Senpi runtime
  emits one position per signal, so Octopus achieves neutrality with two
  equally-funded single-direction books rather than paired orders. Simpler,
  fits the runtime, and lets each side's DSL manage independently. Trade-off:
  the hedge is statistical (balanced baskets), not a locked 1:1 pair — equal
  funding is the operator's responsibility.
- **One-call relative-strength rank.** 24h excess return is computed from the
  instrument board (`markPx`/`prevDayPx`) for the whole cross-section in a
  single call — no per-asset candle fetch for the rank. Only the top/bottom
  `rankPoolSize` names get candles for trend confirmation. Bounds per-tick cost.
- **Absolute-trend confirmation on both sides.** Relative strength alone would
  long the "least-bad" laggard in a crash and short the "least-good" leader in
  a melt-up. Octopus requires the 4h structure to confirm (bullish for longs,
  bearish for shorts) and the name's own 24h momentum to agree — so it trades
  genuine leaders and genuine laggards, not just relative ones.
- **Stall-cuts ON.** Unlike a let-winners-run momentum leg, a dispersion
  position whose relative trend mean-reverts should be recycled. `weak_peak_cut`
  and `dead_weight_cut` are enabled to rotate stale names out of the basket.

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense).
- Per-position margin **20%** (≤ 25% fleet cap).
- Drawdown halt **20%**, `drawdown_reset_on_day_rollover: true` (a closed
  dispersion winner's spiked PnL peak must not lock out next-day entries — the
  lesson learned on Spider's swing book).
- Mandatory DSL on every position; entries + exits use `FEE_OPTIMIZED_LIMIT`
  with taker fallback.
- Verbose per-tick JSON output (never silent) — every scan emits
  `scanned / ranked_pool / candidates / signals_pushed / mean_rs_24h`.

## Negative-lesson inputs

- **Cobra antipattern** — fixed 10x + rotation + no drawdown gate = fee-driven
  death spiral. Octopus answers each: strict 5x, low/moderate turnover (basket
  rotation gated by `per_asset_cooldown` + `max_entries_per_day`), hard drawdown halt.
- **Fees are the biggest killer** — dispersion is not a high-frequency rotation
  scanner. Entries are gated to leadership/laggard *changes*, not every tick.
- **Equity double-count / sizing-off-equity** (Spider June 2026) — `get_positions()`
  takes `accountValue` once via `max()`; sizing is `account_value × margin_pct`.

## Capital provenance

New capital, funded **50/50** across the long and short wallets. Equal funding
is structural: it is what keeps the fund beta-neutral.
