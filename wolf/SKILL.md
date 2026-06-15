---
name: wolf-strategy
description: >-
  WOLF v1.0 — Event-Driven / Regime-Rotation Hedge Fund. Two books on two
  wallets, one producer, that both read a shared cross-asset REGIME detector
  (equities + oil + gold + BTC + the dollar) and fire only when the regime
  agrees with their mandate. The RISK-ON book longs beaten-down beta in a
  confirmed risk-on regime; the RISK-OFF book longs defensives and shorts risk
  in a confirmed risk-off regime. Capital rotates to whichever book the regime
  favors — the edge is trading the macro TURN, not a fixed bet and not
  per-asset trend. NOT a copy-trader: each book scores its own universe and
  pushes signals; the runtime owns the LLM gate (pass-through), DSL exits, and
  all risk.guard_rails. WOLF_LEG env selects the book.
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

# 🐺 WOLF v1.0 — Event-Driven / Regime-Rotation Hedge Fund

Wolf trades **the turn.** It computes a single market-wide **regime** from
cross-asset confirmation — equities, oil, gold, BTC, the dollar — and a book
only fires when the regime **agrees with its mandate.** So the fund *rotates*:
in a confirmed risk-on regime the risk-on book works and the risk-off book
stands down; when the regime flips, they swap. **No single asset can flip the
book** — the whole macro complex has to lean one way. One producer script
(`wolf-producer.py`) serves both books; the `WOLF_LEG` env var selects which.

| Book | Style | Fires when | Wallet env | Runtime | Scanner |
|---|---|---|---|---|---|
| `risk_on` | Long beaten-down beta | regime == RISK_ON | `WOLF_RISK_ON_WALLET` | `runtime-risk_on.yaml` | `wolf_risk_on_signals` |
| `risk_off` | Long defensives + short risk | regime == RISK_OFF | `WOLF_RISK_OFF_WALLET` | `runtime-risk_off.yaml` | `wolf_risk_off_signals` |

The two books are mutually exclusive in practice: at any moment one regime is
(usually) in force, so one book trades while the other waits in cash. That *is*
the rotation. In NEUTRAL (no net cross-asset agreement) both stand down.

## What makes Wolf different

- **vs. a Thesis Fund** (e.g. "Bet against the Trump economy"): a Thesis Fund
  takes a *fixed* side. Wolf is **adaptive** — it detects which way the regime
  is shifting and takes that side, flipping as conditions change.
- **vs. Elephant** (global macro): Elephant trades each asset on its *own*
  trend. Wolf gates every entry on a *market-wide* regime read — its edge is
  the cross-asset **transition**, not the per-asset trend.
- **vs. Coyote** (regime classifier): Coyote tags BTC's regime, crypto-only,
  single agent. Wolf reads the whole macro complex and runs a two-wallet
  long/short fund off it.

## The shared regime detector (the brain)

Each tick, before either book scores anything, Wolf reads the **4h trend** of a
basket of cross-asset probes and tallies risk-on vs. risk-off votes:

| Probe | Votes RISK-ON when | Votes RISK-OFF when |
|---|---|---|
| Equities (xyz:XYZ100 → SP500 fallback) | 4h BULLISH | 4h BEARISH |
| Oil (xyz:BRENTOIL) | 4h BEARISH (falling) | 4h BULLISH (spiking) |
| Gold (xyz:GOLD) | 4h BEARISH (safe-haven soft) | 4h BULLISH (bid) |
| BTC | 4h BULLISH | 4h BEARISH |
| Dollar (xyz:DXY) | 4h BEARISH (soft $) | 4h BULLISH (bid) |

`net = on_votes − off_votes`. **RISK_ON** if `net ≥ regimeThreshold` (default 2);
**RISK_OFF** if `net ≤ −regimeThreshold`; else **NEUTRAL**. A book whose regime
isn't in force emits a `STANDING DOWN` tick and trades nothing.

## RISK-ON book — long beaten-down beta (LONG only)

Universe = the risk complex (`riskAssets`: BTC/ETH/SOL/HYPE/SUI + growth
indices). Fires only in RISK_ON, then scores each name for a clean turn up:

| Component | Pts |
|---|---|
| 4h BULLISH backbone | +2 (required — skip if not BULLISH) |
| 1h confirmation | +2 (BULLISH) / −1 (BEARISH) |
| 24h momentum | +2 (≥`momThresholdPct` 1.0%) / +1 (≥0) |
| RSI room | +1 (< overbought) |

`minScore` 5. The regime is the green light; the name still has to be turning up.

## RISK-OFF book — defensives + short-risk (both directions)

Fires only in RISK_OFF. **LONG** the defensives (`defensives`:
gold/silver/oil/dollar/yen) that are trending up; **SHORT** the `riskAssets`
that are rolling over. Same trend/confirm/momentum/RSI scoring, evaluated in the
mandated direction (LONG wants 4h BULLISH; SHORT wants 4h BEARISH). `minScore` 5.

## Execution & exit

- both books: slots **3**, **strict 5x** clamp (indices/metals/FX cap low at venue), tick **300s**
- **RISK-ON** `margin_pct` **20%** — DSL **wide let-it-run** (a relief rally runs): phase1 max_loss **14%**, **all time-cuts OFF**, `hard_timeout` **5d**; phase2 `12%→0 / 25%→45 / 45%→65 / 80%→80 / 130%→90`
- **RISK-OFF** `margin_pct` **18%** — DSL **tighter** (risk-off moves are sharp + reverse fast): phase1 max_loss **10%**, `weak_peak_cut` **ON** (4h @ 1.5), `dead_weight_cut` **ON** (8h), `hard_timeout` **3d**; phase2 `8%→35 / 15%→55 / 30%→70 / 50%→85`

A regime flip is handled on the **entry** side (the losing-regime book stops
adding); open winners trail out via the phase2 ladder — Wolf does not dump a
book just because the regime turned.

## XYZ handling

Much of the universe + every regime probe except BTC is XYZ — candle fetches
route with `dex="xyz"` (the producer keys off the `xyz:` prefix). XYZ trades
24/7 (no market-hours gating). The `main`/`xyz` clearinghouse sections are two
VIEWS of ONE cross-margined wallet, so `get_positions()` takes `accountValue`
ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | risk_on | risk_off |
|---|---|---|
| daily_loss_limit_pct | 12 | 10 |
| max_entries_per_day | 5 | 8 |
| max_consecutive_losses | 4 | 4 |
| cooldown_minutes | 90 | 60 |
| drawdown_halt_pct | 22 | 18 |
| per_asset_cooldown_minutes | 240 | 180 |
| data_retention_hours | 168 | 120 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker` true).

## Files

| File | Purpose |
|---|---|
| `runtime-risk_on.yaml` | Risk-on-book runtime spec (wide let-it-run DSL) |
| `runtime-risk_off.yaml` | Risk-off-book runtime spec (tighter DSL) |
| `scripts/wolf-producer.py` | Book-aware producer daemon (shared regime detector + both books) |
| `scripts/wolf_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/wolf-risk_on-config.json` | Risk-on tunables (riskAssets, regimeThreshold) |
| `config/wolf-risk_off-config.json` | Risk-off tunables (defensives + riskAssets) |

## Operator install

See [README.md](README.md) — the two books are two daemons (`WOLF_LEG=risk_on`
and `WOLF_LEG=risk_off`) on two wallets (default 50/50 funding so each regime
has equal firepower), each with its own runtime YAML.

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
