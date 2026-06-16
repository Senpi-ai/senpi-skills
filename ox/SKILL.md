---
name: ox-strategy
description: >-
  OX v1.0 — Risk-Parity / All-Weather Hedge Fund. Two books on two wallets, one
  producer. Its distinctive mechanic is INVERSE-VOLATILITY position sizing: each
  sleeve's margin is proportional to 1/realized_vol, normalized across the
  basket, so no single asset class dominates portfolio risk — true risk parity.
  The CORE book holds a vol-balanced LONG basket across asset-class sleeves
  (crypto majors + equity indices + metals + energy + FX); the BALLAST book
  holds defensives (gold/dollar/yen) at a small base budget that scales up when
  a risk-off lean confirms. Always invested, low leverage, low turnover — the
  fund line's steady core, not a directional bet. NOT a copy-trader: each book
  sizes its own basket and pushes signals; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. OX_LEG env selects the book.
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

# 🐂 OX v1.0 — Risk-Parity / All-Weather Hedge Fund

Ox is the fund line's **core holding** — the steady thing you hold while betting
with the others. Its edge isn't a view; it's **risk balancing.** Each sleeve is
sized by **inverse realized volatility** (`w_i = (1/vol_i) / Σ(1/vol_j)`), so a
low-vol sleeve (gold, indices) carries more notional than a high-vol one (a
crypto alt) and **no single asset class dominates portfolio risk** — true risk
parity. It is always invested, low leverage, low turnover. One producer script
(`ox-producer.py`) serves both books; the `OX_LEG` env var selects which.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `core` | Vol-balanced all-weather basket (LONG) | `OX_CORE_WALLET` | `runtime-core.yaml` | `ox_core_signals` |
| `ballast` | Defensives, scaled up on risk-off (LONG) | `OX_BALLAST_WALLET` | `runtime-ballast.yaml` | `ox_ballast_signals` |

## What makes Ox different

- **vs. every other fund:** all of them use a flat `margin_pct` per position. Ox
  sizes each sleeve by **inverse volatility** — that *is* the product.
- **vs. Wolf** (regime rotation): Wolf rotates fully and can go to cash. Ox is
  **always invested** in a balanced basket and never shorts.
- **vs. Rhino** (tail-risk): Rhino is asymmetric (bleeds in calm, pays in shocks).
  Ox is the **low-drawdown core** — it doesn't try to time anything.
- **vs. Elephant** (macro): Elephant trades each macro asset on its own trend.
  Ox holds a *diversified, risk-balanced* basket and rebalances, no trend call.

## The all-weather sleeve basket (core)

Spans risk sleeves **and** genuinely-diversifying defensive sleeves, intersected
each tick with the live instrument board:

- **Crypto:** BTC, ETH, SOL
- **Equity indices** (XYZ): SP500, XYZ100
- **Metals** (XYZ): GOLD, COPPER
- **Energy** (XYZ): BRENTOIL
- **FX / defensive** (XYZ): DXY (dollar), JPY (yen)

The defensive sleeves (gold, dollar, yen) are what make the core *all-weather* —
they tend to rise when risk assets fall, so the vol-balanced basket has a built-in
cushion before the BALLAST book even engages.

## CORE book — risk-parity sizing (LONG only)

Each tick:
1. Estimate **realized vol** (stdev of 1h returns over `volBars` 30) for the *full* basket.
2. Compute **inverse-vol weights** over the whole basket (so a re-entering sleeve gets its correct fractional weight, never the full budget).
3. Size each un-held sleeve: `marginUsd = portfolioBudgetPct (0.60) × equity × weight`, capped at `maxWeightPct` (22%) of equity.
4. **Knife guard:** decline to *add* a sleeve in a hard 4h downtrend (≥0.8 strength); existing sleeves are held through normal drawdowns by the DSL.

Holds up to **10 slots** (the whole basket), **3x** leverage, **600s** tick (slow rebalance).

## BALLAST book — defensive overlay (LONG only)

Always-on. Sizes the defensive basket (gold/silver/dollar/yen) inverse-vol to a
small base budget (`portfolioBudgetPct` 0.18), and **scales it up ×`riskOffMultiplier`
(2.0)** when a light risk-off lean confirms — equities 4h bearish + gold/dollar
4h bullish, `riskOffThreshold` 1 vote. Never shorts, never fully to cash; it's
the active downside cushion on top of the core's static balance.

## Execution & exit

- both books: **LOW leverage clamp (3x)**, then each asset's HL venue max; `FEE_OPTIMIZED_LIMIT` entries + exits
- **CORE** slots 10, `portfolioBudgetPct` 60% — DSL **wide let-it-hold** (a core is rebalanced, not stopped on noise): phase1 max_loss **16%**, **all time-cuts OFF**, `hard_timeout` **14d**; phase2 `12%→0 / 25%→45 / 50%→65 / 90%→80`
- **BALLAST** slots 4, `portfolioBudgetPct` 18% (×2 on risk-off) — DSL **wide**: phase1 max_loss **14%**, time-cuts OFF, `hard_timeout` **10d**; phase2 `10%→0 / 22%→45 / 45%→65 / 80%→80`

> **Sizing dependency:** Ox emits a *different* `marginUsd` per sleeve (the
> inverse-vol weight). Its risk parity depends on the runtime **honoring
> per-signal `signal.data.marginUsd`** rather than collapsing every position to a
> flat `strategy.margin_pct`. The YAML `margin_pct` (6 core / 5 ballast) is only a
> fallback cap.

## XYZ handling

Most sleeves are XYZ — candle fetches route with `dex="xyz"` (the producer keys
off the `xyz:` prefix). XYZ trades 24/7 (no market-hours gating). The
`main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined wallet,
so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | core | ballast |
|---|---|---|
| daily_loss_limit_pct | 8 | 8 |
| max_entries_per_day | 12 | 6 |
| max_consecutive_losses | 5 | 4 |
| cooldown_minutes | 60 | 90 |
| drawdown_halt_pct | 18 | 16 |
| per_asset_cooldown_minutes | 360 | 360 |
| data_retention_hours | 168 | 168 |
| drawdown_reset_on_day_rollover | true | true |

## Files

| File | Purpose |
|---|---|
| `runtime-core.yaml` | Core-book runtime spec (wide let-it-hold DSL) |
| `runtime-ballast.yaml` | Ballast-book runtime spec (wide, defensive) |
| `scripts/ox-producer.py` | Book-aware producer daemon (realized-vol + inverse-vol weighting + both books) |
| `scripts/ox_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/ox-core-config.json` | Core tunables (sleeves, budget, vol window) |
| `config/ox-ballast-config.json` | Ballast tunables (defensives, risk-off scaler) |

## Operator install

See [README.md](README.md) — the two books are two daemons (`OX_LEG=core` and
`OX_LEG=ballast`) on two wallets (default 70/30 core/ballast funding), each with
its own runtime YAML.

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
