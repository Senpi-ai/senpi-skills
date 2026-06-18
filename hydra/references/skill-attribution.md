# Hydra — Skill Attribution

**Skill:** hydra-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-18

## What Hydra is

The first **Single-Coin Portfolio Fund** — a complete book on ONE major (a
directional thesis bet + a complementary dip-buyer + a stress-gated short hedge,
each on its own wallet). It productizes the *portfolio / hedge-pairing* pattern
(producer-patterns → *Running a portfolio*) into a single multi-wallet fund:
instead of the operator hand-assembling Polar + a fader + a hedge, Hydra ships
the three heads as one coin-parameterized engine.

## Lineage

- **Architecture** — the helpers-native leg-parameterized pattern (Lion/Cub
  family): ONE producer, parameterized by `HYDRA_COIN` (the asset) + `HYDRA_LEG`
  (core/dip/hedge), `producer_daemon` + fcntl lock, `SenpiClient.push_signal()`
  ingest, runtime-owned LLM gate + DSL + risk. Signature-adaptive daemon launch.
- **Methods** — the **core** head is the Kodiak-family single-asset alpha hunter
  (Polar/Kodiak/Wolverine logic: 4h-trend + 1h confirm + RSI guard, conviction-
  tiered leverage); the **dip** head is a Salamander-style pullback-buyer gated to
  uptrends; the **hedge** head is a Rhino-style stress-gated short, scoped to one
  coin. All three on one asset, one codebase.
- **Thesis** — a single coin deserves a *portfolio*, not one bet: ride the trend,
  press the dips, and carry a fast cushion for the break.

## Design decisions specific to Hydra

- **Coin- AND leg-parameterized.** `HYDRA_COIN` × `HYDRA_LEG` → 3 wallets per coin;
  ETH/SOL/HYPE = nine wallets, one engine. Extensible to any major.
- **Regime-disjoint heads — the core design invariant.** core (any trend), dip
  (uptrends only), hedge (downtrends + stress only) are gated so the fund never
  holds opposing positions across its wallets: uptrend → core long + dip; downtrend
  → core short + hedge. The dip's uptrend-only gate is specifically what stops it
  knife-catching against the hedge.
- **Stress-gated short hedge** (chosen over market-neutral-vs-BTC or always-on
  convexity): directly anti-correlated to the long heads, cushions the flip, and
  idle (tiny bleed) in uptrends — the cleanest cushion for a net-long single-coin
  fund. Capitulation-guarded so it never shorts an exhausted bottom; lowest
  leverage of the three (3x) because short squeezes are violent.
- **Net-long the coin by construction** — core + dip are long-biased; the hedge
  only ever cushions. The funding split (default 50/25/25) is the operator's dial.
- **Single-asset DSL standard** — time-cuts off, weak_peak self-limiting on; Phase
  1 + Phase 2 own exits (the fleet rule for single-asset agents).

## Fleet-standard compliance

- Max leverage 5x core / 4x dip / 3x hedge (strict clamp; venue cap applies — HYPE
  lower automatically). Per-position margin 20/18/15% (≤ 25% cap); 1 position/head.
- Drawdown halt 22/20/18%; mandatory DSL; entries + exits FEE_OPTIMIZED_LIMIT with
  taker fallback. Verbose per-tick JSON. Budget-scaling notional floor (no hardcoded $).
- Sizes off `max(main, xyz)` account value — never the sum.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + no drawdown gate. Hydra: strict
  per-head clamps, hard drawdown halt, trend-confirmed entries.
- **Fees are the biggest killer** — heads are gated to disjoint regimes (no churn
  from overlapping entries), entries capped, single-asset time-cuts off.
- **Don't double up the same bet** — the heads are *different styles/regimes* on the
  coin, not three correlated momentum books; the hedge is genuinely anti-correlated.

## Capital provenance

New capital, split across a coin's three head wallets per the operator's chosen
funding (default ~50 core / 25 dip / 25 hedge). Net-long the coin with a built-in
cushion. Deployable per coin (ETH/SOL/HYPE).
