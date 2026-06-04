---
name: camel-strategy
description: >-
  CAMEL v1.0 — Carry Hedge Fund. Two single-direction books on two wallets,
  one producer, harvesting funding carry. The HARVEST book shorts the
  most-positive-funding names (longs pay shorts → short collects); the
  PAYOUT book longs the most-negative-funding names (shorts pay longs → paid
  to hold). Both are gated to EXHAUSTING crowds (never a fresh trend
  against the carry), with trend/RSI as confirmation + risk control. The
  edge is carry — a structural, recurring funding inefficiency — not market
  direction. NOT a copy-trader: each book scores its own universe and pushes
  signals; the runtime owns the LLM gate (pass-through), DSL exits, and all
  risk.guard_rails. CAMEL_LEG env selects the book.
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

# 🐫 CAMEL v1.0 — Carry Hedge Fund

Camel runs **two concurrent strategy wallets** that harvest **funding
carry** — it takes the side that *collects* the funding payment, on names
where the crowded trade is exhausting so price doesn't fight the carry.
**One producer script** (`camel-producer.py`) serves both; the `CAMEL_LEG`
env var selects which book a given daemon is.

> **The edge is carry, not direction.** On Hyperliquid, positive funding
> means longs pay shorts; negative means shorts pay longs. Camel
> systematically takes the **collecting** side of the most extreme funding,
> gated to crowds that are rolling over (shorts) or capitulating (longs).
> Funding is a recurring, structural inefficiency — income that compounds.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `harvest` | Short the most-positive-funding names (short collects) | `CAMEL_HARVEST_WALLET` | `runtime-harvest.yaml` | `camel_harvest_signals` |
| `payout` | Long the most-negative-funding names (paid to hold) | `CAMEL_PAYOUT_WALLET` | `runtime-payout.yaml` | `camel_payout_signals` |

Taking some shorts (harvest) and some longs (payout) also skews the fund
slightly net-neutral as a by-product — but the return driver is the funding,
not the net direction.

## The funding rank (shared by both books)

Each tick the producer pulls the live instrument board **once** and reads the
per-asset `funding` field (hourly decimal) for every liquid main-DEX perp
(`dayNtlVlm ≥ volFloorUsd`, default $20M). Annualized ≈ `funding × 24 × 365`.
The **harvest** book ranks funding **descending** (most positive); the
**payout** book ranks **ascending** (most negative). Only the top
`rankPoolSize` names (default 12) then get 1h+4h candles for confirmation.

> **Data robustness:** funding comes from the always-available instrument
> board — NOT the ClickHouse-backed `funding_history` endpoint (which can
> 503 or require elevated scope). `funding_history` is optional enrichment only.

## HARVEST book — short the expensive longs (carry + fade)

### Scoring (raw integer; `minScore` 4)

| Component | Pts | Source |
|---|---|---|
| Funding (annualized) | +3 (≥~88%/yr) / +2 (≥~53%) / +1 (≥~26%) / **disqualify** (< floor) | instrument `funding` |
| 4h trend | +2 BEARISH / +1 NEUTRAL / **disqualify** BULLISH | 4h candles |
| RSI overbought | +1 (RSI ≥ `rsiOverbought` 70) | 1h RSI |
| 24h roll-over | +1 (24h ≤ 0) / −1 (24h ≥ +5%, still ripping) | instrument ctx |

Disqualifies a fresh 4h uptrend — the squeeze risk dwarfs the carry. Shorts
only crowded longs that are rolling over.

## PAYOUT book — long the expensive shorts (paid to hold)

### Scoring (raw integer; `minScore` 4)

| Component | Pts | Source |
|---|---|---|
| Funding (annualized, negative) | +3 (≤~−88%/yr) / +2 (≤~−53%) / +1 (≤~−26%) / **disqualify** (> −floor) | instrument `funding` |
| 4h trend | +2 BULLISH / +1 NEUTRAL / **disqualify** BEARISH | 4h candles |
| RSI oversold | +1 (RSI ≤ `rsiOversold` 30) | 1h RSI |
| 24h bounce | +1 (24h ≥ 0) / −1 (24h ≤ −5%, still crashing) | instrument ctx |

Disqualifies a fresh 4h downtrend — never longs a knife. Longs only crowded
shorts that are capitulating.

## Execution & exit (both books)

- slots **4**, `margin_pct` **18%**, tick **300s**
- **strict 5x** leverage clamp, then each asset's Hyperliquid venue max
- DSL **carry management (tighter than a momentum leg)**: carry P&L per period
  is small, so a price loss must be cut before it dwarfs the funding —
  phase1 max_loss **10%** / retrace 6 / 1 breach; `weak_peak_cut` **ON** (4h @ 1.5),
  `dead_weight_cut` **ON** (8h), `hard_timeout` **3d** (funding regimes decay);
  phase2 ladder `6%→lock0 / 14%→45 / 28%→65 / 50%→80 / 90%→90`

## Leverage clamping (both books)

Desired leverage = the book cap (`maxLeverage` 5). The producer clamps to each
asset's Hyperliquid venue max; the runtime gate rejects any leverage above 5.

## XYZ handling

Camel ranks the **main-DEX crypto** cross-section only — XYZ funding is sparse.
The `main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined
wallet, so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | harvest | payout |
|---|---|---|
| daily_loss_limit_pct | 10 | 10 |
| max_entries_per_day | 6 | 6 |
| max_consecutive_losses | 4 | 4 |
| cooldown_minutes | 60 | 60 |
| drawdown_halt_pct | 18 | 18 |
| per_asset_cooldown_minutes | 180 | 180 |
| data_retention_hours | 96 | 96 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker`
true; 45s maker-first window). **Fee note:** carry P&L is small per period —
keep turnover modest so fees don't eat the funding collected.

## Files

| File | Purpose |
|---|---|
| `runtime-harvest.yaml` | Harvest-book runtime spec (wallet, DSL, risk, LLM gate) |
| `runtime-payout.yaml` | Payout-book runtime spec |
| `scripts/camel-producer.py` | Book-aware producer daemon (one script, both books) |
| `scripts/camel_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/camel-harvest-config.json` | Harvest-book tunables (funding floor/tiers) |
| `config/camel-payout-config.json` | Payout-book tunables |

## Operator install

See [README.md](README.md) — the two books are two daemons
(`CAMEL_LEG=harvest` and `CAMEL_LEG=payout`) on two **equally-funded** wallets,
each with its own runtime YAML.

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
