---
name: rhino-strategy
description: >-
  RHINO v1.0 — Tail-Risk / Crisis-Alpha Hedge Fund. Two books on two wallets,
  one producer, built to carry cheap convexity: bleed a little in calm, pay big
  in shocks. The HEDGE book holds a small, always-on long carry in the
  crisis-beneficiary complex (gold / oil / dollar / yen), entered only when a
  defensive is trending up. The ESCALATION book is dormant in calm and fires
  hard only when a shared cross-asset STRESS detector confirms a shock — long
  the spiking crisis assets, short the cratering risk assets. NOT a copy-trader:
  each book scores its own universe and pushes signals; the runtime owns the
  LLM gate (pass-through), DSL exits, and all risk.guard_rails. RHINO_LEG env
  selects the book.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🦏 RHINO v1.0 — Tail-Risk / Crisis-Alpha Hedge Fund

Rhino carries **cheap convexity:** it bleeds a little in calm and pays big in
shocks — geopolitics, macro stress, liquidation cascades. The mirror lesson of
the 2026 oil war: while everything else bled, the winners were long oil and
gold. Rhino productizes that as an always-on insurance book plus a dormant
convex add. One producer script (`rhino-producer.py`) serves both books; the
`RHINO_LEG` env var selects which.

| Book | Style | Gated by | Wallet env | Runtime | Scanner |
|---|---|---|---|---|---|
| `hedge` | Small always-on long defensives | nothing (always on) | `RHINO_HEDGE_WALLET` | `runtime-hedge.yaml` | `rhino_hedge_signals` |
| `escalation` | Long crisis + short risk, leveraged | the STRESS detector | `RHINO_ESCALATION_WALLET` | `runtime-escalation.yaml` | `rhino_escalation_signals` |

Together: the hedge book means you **already own** the crisis trade when the
shock hits; the escalation book **adds leveraged convexity** the moment stress
confirms, then banks it before the violent reversal.

## What makes Rhino different

- **vs. Elephant** (global macro): Elephant trades the macro trend in both
  directions for its own sake. Rhino is asymmetric by design — it is built to
  *lose small in calm and win big in stress*, not to track macro.
- **vs. a Thesis Fund** ("war escalation"): a Thesis Fund is a fixed manual
  bet you turn on. Rhino is always-on insurance that *self-activates* its convex
  leg on a measured stress signal, with no view required.
- It is the portfolio **hedge** of the fund lineup — the thing that's green on
  the days everything else is red.

## The shared stress detector (the brain)

Each tick, Rhino tallies cross-asset **stress probes** — each fires on a 4h
trend confirmation OR a 1h range break + ATR surge in the stress direction:

| Probe | Fires (stress) when |
|---|---|
| Oil (xyz:BRENTOIL) | spiking up (4h BULLISH or breakout-up + surge) |
| Equities (xyz:XYZ100 → SP500) | breaking down (4h BEARISH or breakdown) |
| Gold (xyz:GOLD) | bid (4h BULLISH or breakout-up) |
| BTC | rolling over (4h BEARISH or breakdown) |
| Vol (BTC ATR) | recent ATR / baseline ATR ≥ `volSurge` (1.5) |

`STRESS` is declared when the count clears `stressThreshold` (default 2). The
escalation book trades **only** under STRESS; the hedge book ignores the gate
(but reports the read for telemetry).

## HEDGE book — the standing insurance (LONG only, always on)

Universe = the defensive whitelist (`defensives`: gold/oil/dollar/yen). Scores
each for a clean uptrend (4h BULLISH backbone + 1h confirm + 24h momentum + RSI
room; `minScore` 5) and carries it LONG — **no falling-knife hedges** (a
defensive must be trending up to be added). Sized **small** (`margin_pct` 10%):
the position size is the cost control, so calm-time bleed is bounded while a
crisis winner is free to run on a wide DSL.

## ESCALATION book — the convex add (both directions, stress-gated)

Dormant in calm (cash = dry powder). When STRESS confirms, it goes **LONG** the
spiking crisis complex (`crisisLongs`: gold/silver/oil/CL/natgas/dollar/yen) and
**SHORT** the cratering risk complex (`riskAssets`: BTC/ETH/SOL/HYPE/SUI + growth
indices), each scored in its mandated direction (`minScore` 5). Sized **larger**
(`margin_pct` 22%) — this is the payoff leg.

## Execution & exit

- both books: slots **3**, **strict 5x** clamp, tick **300s**
- **HEDGE** `margin_pct` **10%** — DSL **wide let-it-run** (hold the insurance): phase1 max_loss **16%**, **all time-cuts OFF**, `hard_timeout` **10d**; phase2 `12%→0 / 25%→45 / 50%→65 / 90%→80 / 140%→90`
- **ESCALATION** `margin_pct` **22%** — DSL **moderate-tight** (crises reverse violently, bank the spike): phase1 max_loss **12%**, `weak_peak_cut` **ON** (3h @ 2.0), `dead_weight_cut` **ON** (6h), `hard_timeout` **2d**; phase2 `8%→35 / 18%→60 / 35%→75 / 60%→88`

## XYZ handling

Every defensive + most stress probes are XYZ — candle fetches route with
`dex="xyz"` (the producer keys off the `xyz:` prefix). XYZ trades 24/7 (no
market-hours gating), so the hedge carries and the stress detector watches
through weekends. The `main`/`xyz` clearinghouse sections are two VIEWS of ONE
cross-margined wallet, so `get_positions()` takes `accountValue` ONCE via
`max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | hedge | escalation |
|---|---|---|
| daily_loss_limit_pct | 8 | 14 |
| max_entries_per_day | 3 | 8 |
| max_consecutive_losses | 4 | 5 |
| cooldown_minutes | 120 | 45 |
| drawdown_halt_pct | 20 | 22 |
| per_asset_cooldown_minutes | 360 | 120 |
| data_retention_hours | 168 | 96 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker` true).

## Files

| File | Purpose |
|---|---|
| `runtime-hedge.yaml` | Hedge-book runtime spec (small, wide let-it-run DSL) |
| `runtime-escalation.yaml` | Escalation-book runtime spec (larger, moderate-tight DSL) |
| `scripts/rhino-producer.py` | Book-aware producer daemon (shared stress detector + both books) |
| `scripts/rhino_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/rhino-hedge-config.json` | Hedge-book tunables (defensive whitelist) |
| `config/rhino-escalation-config.json` | Escalation-book tunables (crisis longs / risk shorts, stress params) |

## Operator install

See [README.md](README.md) — the two books are two daemons (`RHINO_LEG=hedge`
and `RHINO_LEG=escalation`) on two wallets (default 50/50 funding — the hedge
runs small, the escalation holds dry powder), each with its own runtime YAML.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the
**DSL ratchet engine** (exit path). User-conversation sessions are
**read-only**. Each producer daemon handles real signals on its next tick.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
