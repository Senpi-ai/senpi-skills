---
name: elephant-strategy
description: >-
  ELEPHANT v1.0 — Global-Macro Hedge Fund. Two macro books on two wallets,
  one producer, over the cross-asset macro complex — equity indices, precious
  metals, energy, FX (all on XYZ) plus BTC as the macro risk asset. The TREND
  book rides the medium-term multi-timeframe macro trend (both directions);
  the FADE book fades short-TF macro over-extensions back to regime (both
  directions, with a knife guard). The edge is GLOBAL MACRO — the cross-asset
  complex moves on regime, not crypto noise. NOT a copy-trader: each book
  scores its own universe and pushes signals; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. ELEPHANT_LEG env
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

# 🐘 ELEPHANT v1.0 — Global-Macro Hedge Fund

Elephant trades the **cross-asset macro complex** — equity indices, precious
metals, energy, FX (all on XYZ) plus **BTC** as the macro risk asset — which
moves on macro regime, not crypto noise. **One producer script**
(`elephant-producer.py`) serves both books; the `ELEPHANT_LEG` env var selects
which book a given daemon is. It is **aware of the themes** (oil/Iran, the
AI-equity bid, risk-on/off) and trades them **as macro**, not as momentum chases.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `trend` | Macro multi-TF trend, both directions | `ELEPHANT_TREND_WALLET` | `runtime-trend.yaml` | `elephant_trend_signals` |
| `fade` | Macro mean-reversion, both directions | `ELEPHANT_FADE_WALLET` | `runtime-fade.yaml` | `elephant_fade_signals` |

The two books are complementary: the trend book rides durable macro direction;
the fade book catches macro over-extensions. On separate wallets, they never
conflict — a name in a macro uptrend can still print a short-term overbought fade.

## The macro universe (both books)

A **curated macro whitelist** (`config.allowedAssets`), intersected each tick
with the live instrument board so unavailable names are skipped:

- **Indices** (XYZ): SP500, XYZ100, JP225, KR200, NIFTY, IBOV
- **Metals** (XYZ): GOLD, SILVER, PLATINUM, COPPER
- **Energy** (XYZ): BRENTOIL, CL, NATGAS
- **FX** (XYZ): EUR, JPY, GBP, DXY
- **Crypto macro**: BTC

This is the asset complex none of the other funds focus on — true global macro.
XYZ trades 24/7 on Hyperliquid, so the macro book runs through weekends.

## TREND book — ride the macro trend (both directions)

### Scoring (raw integer; `minScore` 5)

| Component | Pts | Source |
|---|---|---|
| 4h trend backbone | +3 (sets direction: BULLISH→LONG / BEARISH→SHORT) / **skip** NEUTRAL | 4h candles |
| 1h confirmation | +2 (confirms) / −1 (opposes) | 1h candles |
| 24h momentum | +2 (≥`momThresholdPct` 1.5%) / +1 (≥0) — sign matches direction | instrument ctx |
| RSI room | +1 (not overbought for a long / not oversold for a short) | 1h RSI |

A NEUTRAL 4h structure = no clean macro trend → the book waits.

## FADE book — fade macro over-extensions (both directions)

### Scoring (raw integer; `minScore` 4)

Picks the more-extreme side (oversold → LONG, overbought → SHORT):

| Component | Pts |
|---|---|
| RSI extreme | +3 (≤20 / ≥80) / +2 (≤25 / ≥75) / +1 (≤`rsiOversold` 30 / ≥`rsiOverbought` 70) |
| Stretch from 20-bar 1h MA | +2 (≥2×`stretchThresholdPct` 1.0%) / +1 (≥1×) |
| 4h regime knife guard | +1 (fading **with** a higher-TF mean-reversion bias) / −2 (against a strong macro trend) |

The −2 knife guard keeps it from fading a strong macro trend (don't short a
ripping oil breakout, don't long a collapsing index).

## Execution & exit

- both books: slots **3**, **strict 5x** clamp (indices/metals/FX cap low at venue), tick **300s**
- **TREND** `margin_pct` **18%** — DSL **wide let-it-run** (macro trends are slow + persistent): phase1 max_loss **18%**, **all time-cuts OFF**, `hard_timeout` **7d**; phase2 `12%→0 / 25%→45 / 45%→65 / 75%→80 / 120%→90`
- **FADE** `margin_pct` **15%** — DSL **tight fast-capture** (a reversion resolves fast): phase1 max_loss **8%**, `weak_peak_cut` **ON** (4h @ 1.5), `dead_weight_cut` **ON** (8h), `hard_timeout` **2d**; phase2 `4%→35 / 8%→55 / 15%→70 / 28%→85`

## XYZ handling

Most of the macro complex is XYZ — candle fetches route with `dex="xyz"` (the
producer keys off the `xyz:` prefix). XYZ trades 24/7 (no market-hours gating).
The `main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined
wallet, so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | trend | fade |
|---|---|---|
| daily_loss_limit_pct | 12 | 10 |
| max_entries_per_day | 4 | 12 |
| max_consecutive_losses | 4 | 5 |
| cooldown_minutes | 90 | 45 |
| drawdown_halt_pct | 22 | 18 |
| per_asset_cooldown_minutes | 360 | 120 |
| data_retention_hours | 168 | 96 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker` true).

## Files

| File | Purpose |
|---|---|
| `runtime-trend.yaml` | Trend-book runtime spec (wide let-it-run DSL) |
| `runtime-fade.yaml` | Fade-book runtime spec (tight fast-capture DSL) |
| `scripts/elephant-producer.py` | Book-aware producer daemon (one script, both books) |
| `scripts/elephant_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/elephant-trend-config.json` | Trend-book tunables (macro whitelist, momentum) |
| `config/elephant-fade-config.json` | Fade-book tunables (RSI/stretch thresholds) |

## Operator install

See [README.md](README.md) — the two books are two daemons
(`ELEPHANT_LEG=trend` and `ELEPHANT_LEG=fade`) on two wallets (default 60/40
trend/fade funding), each with its own runtime YAML.

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
