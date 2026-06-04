# Elephant — Skill Attribution

**Skill:** elephant-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-03

## What Elephant is

The **Global Macro** pillar — the fourth and final of the four hedge-fund
agents complementing Spider (directional). Octopus is Relative-Value; Camel is
Carry; Caracal is Volatility; Elephant is Global Macro. Elephant adds a
cross-asset macro return stream over the asset classes none of the other funds
focus on — equity indices, precious metals, energy, FX — plus BTC as the macro
risk asset.

## Lineage

- **Architecture** — the Spider/Octopus/Camel/Caracal helpers-native two-leg
  pattern: ONE leg-parameterized producer (`ELEPHANT_LEG`), two wallets, two
  runtime YAMLs, `producer_daemon` + fcntl lock, `SenpiClient.push_signal()`
  ingest, runtime-owned LLM gate + DSL + risk.
- **Thesis** — global macro: the cross-asset complex moves on regime, not crypto
  noise. A `trend` book rides the durable macro direction (multi-TF trend); a
  `fade` book catches macro over-extensions reverting to regime.

## Design decisions specific to Elephant

- **The universe is the differentiator.** A curated macro whitelist (XYZ
  indices / metals / energy / FX + BTC), intersected with the live instrument
  board so dead names are skipped. Deliberately excludes AI/Tech equities (those
  are Spider's domain) — Elephant is the *macro* complex, not single stocks.
- **Two complementary books, both bidirectional.** Trend and fade on the same
  universe but different timeframes/conditions (4h backbone trend vs 1h RSI/
  stretch reversion) — they never contradict because they operate on different
  signals and separate wallets. Together they harvest macro across regimes.
- **Theme-aware, not theme-chasing.** Oil/Iran shows up as an *energy macro
  trend* (or a fade of an over-extension); the AI-equity bid shows up as an
  *index macro trend*. Elephant trades these as macro direction/reversion, never
  as a momentum chase of the headline.
- **Asymmetric DSL by book.** Trend = wide let-it-run (macro trends are slow +
  persistent; all time-cuts off, 7d timeout). Fade = tight fast-capture (a
  reversion resolves fast or the thesis failed; stall-cuts on, 2d timeout).

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense; indices/metals/FX
  cap lower at venue and the clamp respects it).
- Per-position margin **18% (trend) / 15% (fade)** (≤ 25% fleet cap).
- Drawdown halt **22% (trend) / 18% (fade)**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / candidates / signals_pushed`.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate.
  Elephant: strict 5x, low turnover on the trend book (slow macro, 4 entries/day),
  hard drawdown halt.
- **Fees are the biggest killer** — the trend book is fee-efficient (slow,
  let-it-run); the fade book is the higher-turnover one and is funded smaller
  (40%) and capped tighter (`per_asset_cooldown` 120m) to keep fees in check.
- **XYZ markets trade 24/7** — no market-hours gating; the macro complex
  (indices/metals/oil) stays active through weekends.

## Capital provenance

New capital, funded **60/40** across the trend and fade wallets (the
fee-efficient trend engine carries the larger share).
