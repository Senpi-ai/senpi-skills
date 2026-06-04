# Camel — Skill Attribution

**Skill:** camel-strategy v1.0.0
**Author:** jason-goldberg
**License:** Apache-2.0
**Created:** 2026-06-03

## What Camel is

The **Carry** pillar of the four hedge-fund agents complementing Spider
(directional). Octopus is Relative-Value; Camel is Carry; Caracal is
Volatility; Elephant is Global Macro. Camel adds a funding-income return
stream — structurally low-correlation to the directional and dispersion books.

## Lineage

- **Architecture** — the Spider/Octopus helpers-native two-leg pattern: ONE
  leg-parameterized producer (`CAMEL_LEG`), two wallets, two runtime YAMLs,
  `producer_daemon` + fcntl lock, `SenpiClient.push_signal()` ingest,
  runtime-owned LLM gate + DSL + risk. Reuses Octopus's rank → bounded-fetch
  → trend-confirm spine, swapping the rank metric from relative strength to funding.
- **Thesis** — classic **carry**: collect the funding the crowd pays. Take the
  side that *receives* funding on the most extreme names, gated to crowds that
  are exhausting so price doesn't fight the carry.

## Design decisions specific to Camel

- **Funding from the instrument board, not `funding_history`.** The dedicated
  `market_get_funding_history` / `funding_regime` endpoints are ClickHouse-backed
  and can 503 or require elevated leaderboard-service scope (confirmed 401 on a
  read token during build). The instrument board's per-asset `context.funding`
  (hourly decimal) is always available in one call — verified live (BTC +9%/yr,
  HYPE +40%/yr, BSV −224%/yr) — so it is the primary signal; `funding_history`
  is optional enrichment only.
- **Exhaustion gating both sides.** Pure carry has tail risk: short a crowded
  long that keeps ripping (squeeze) or long a crowded short that keeps dumping.
  Camel disqualifies a fresh 4h trend against the carry and requires
  trend/RSI/own-momentum to confirm the crowd is rolling over (harvest) or
  capitulating (payout). Carry is the income; the gate is the risk control.
- **Tighter DSL than a momentum leg.** Funding P&L per period is small, so a
  price loss must be cut before it dwarfs the funding collected (phase1 10%,
  stall-cuts ON, 3d timeout as funding regimes decay).

## Fleet-standard compliance

- Max leverage **5x** (strict clamp + runtime gate defense).
- Per-position margin **18%** (≤ 25% fleet cap).
- Drawdown halt **18%**, `drawdown_reset_on_day_rollover: true`.
- Mandatory DSL; entries + exits use `FEE_OPTIMIZED_LIMIT` with taker fallback.
- Verbose per-tick JSON (never silent): `scanned / ranked_pool / candidates /
  signals_pushed / top_funding_annpct`.

## Negative-lesson inputs

- **Cobra antipattern** — fixed high leverage + rotation + no drawdown gate.
  Camel answers each: strict 5x, modest turnover (carry persists; cooldowns),
  hard drawdown halt.
- **Fees are the biggest killer** — carry income is small per period, so
  turnover is gated (`per_asset_cooldown` 180m, `max_entries_per_day` 6) to keep
  fees from eating the funding. This is the dominant risk for a carry strategy.
- **Inspect MCP before coding extraction** — the `funding` field name + hourly
  scale were verified against a live `market_list_instruments` call before
  coding the rank, avoiding the silent-None / wrong-units bug class.

## Capital provenance

New capital, funded **50/50** across the harvest and payout wallets.
