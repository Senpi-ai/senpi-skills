# Hydra — Skill Attribution

**Skill:** hydra-strategy v2.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-18 (v1.0) · 2026-06-19 (v2.0)

## What Hydra is

A **Single-Coin Conviction Fund with a cross-asset hedge** — a long-term conviction
book on ONE major (core trend-confirmed long-term hold + dip tactical add),
cushioned by a diversified short of OTHER assets. The hedge protects the market
risk that would also hurt the thesis coin *without ever betting against the thesis*.

**v2.0 redesign** (from the v1.0 single-coin portfolio fund): (1) the core became a
long-term hold on a catastrophic-only DSL — you ride normal volatility instead of
being shaken out; (2) the hedge changed from a *same-asset* stress short on the
thesis coin to a **cross-asset blend short** — it shorts other assets actually
breaking down (vol-parity sized, scaled by thesis stress), never the thesis coin.
This fixes v1.0's same-asset hedge weaknesses (lag, V-recovery whipsaw, fighting
the thesis) while keeping zero risk of shorting the coin you believe in.

## Lineage

- **Architecture** — the helpers-native leg-parameterized pattern (Lion/Cub
  family): ONE producer, parameterized by `HYDRA_COIN` (the asset) + `HYDRA_LEG`
  (core/dip/hedge), `producer_daemon` + fcntl lock, `SenpiClient.push_signal()`
  ingest, runtime-owned LLM gate + DSL + risk. Signature-adaptive daemon launch.
- **Methods** — the **core** head is the Kodiak-family single-asset alpha hunter
  (Polar/Kodiak/Wolverine logic: 4h-trend + 1h confirm + RSI guard, conviction-
  tiered leverage); the **dip** head is a Salamander-style pullback-buyer gated to
  uptrends; the **hedge** head (v2.0) is a cross-asset blend short — it shorts a
  diversified set of OTHER assets that are actually breaking down (trend-confirmed,
  vol-parity sized, thesis-stress-scaled), never the thesis coin. All three on one asset, one codebase.
- **Thesis** — a single coin deserves a *portfolio*, not one bet: ride the trend,
  press the dips, and carry a fast cushion for the break.

## Design decisions specific to Hydra

- **Named variants over one engine.** Shipped as **Hydra-ETH / Hydra-SOL /
  Hydra-HYPE** — three self-contained catalog entries the picker recommends
  directly (no parameterized-fund prompt needed), all sharing one producer +
  runtimes. `HYDRA_COIN` × `HYDRA_LEG` → 3 wallets per variant; nine wallets total.
  Each variant loads its own per-coin config set (`hydra-<coin>-<leg>-config.json`)
  with **per-coin vol tuning** — the hedge `stressDropPct` widens for higher-vol
  coins (ETH 8 / SOL 10 / HYPE 13) so it doesn't arm on normal swings. Extensible
  to any major by adding a `hydra-<coin>-*` set.
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
