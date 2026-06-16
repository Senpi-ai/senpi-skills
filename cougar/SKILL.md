---
name: cougar-strategy
description: >-
  COUGAR v1.0 — U.S. Equity Long/Short Hedge Fund. Two books on two wallets, one
  producer, over the tokenized U.S. equity universe on Hyperliquid XYZ
  (trade.xyz: NVDA, TSLA, AAPL, AMZN, … + index products). The LONG book longs
  the relative-strength leaders of the equity cross-section; the SHORT book
  shorts the laggards — both trend-confirmed. Run on two equally-funded wallets
  the pair is ~market-neutral and harvests EQUITY DISPERSION (the spread between
  the best and worst stocks), which is at multi-decade extremes. The edge is
  stock-selection, not market direction. NOT a copy-trader: each book ranks and
  scores its own equity cross-section; the runtime owns the LLM gate
  (pass-through), DSL exits, and all risk.guard_rails. COUGAR_LEG env selects the book.
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

# 🐆 COUGAR v1.0 — U.S. Equity Long/Short Hedge Fund

Cougar trades the **tokenized U.S. equity market on Hyperliquid** — now the
fastest-growing corner of the venue (HIP-3 stock markets did >$18B in the first
half of June 2026, and 23 of the top-30 HL assets by open interest are equities
+ commodities, not crypto). It runs the classic equity hedge-fund play: **long
the strongest stocks, short the weakest, market-neutral** — harvesting the
**dispersion** between leaders and laggards, which is at a multi-decade extreme.
One producer script (`cougar-producer.py`) serves both books; the `COUGAR_LEG`
env var selects which.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | Long the RS leaders | `COUGAR_LONG_WALLET` | `runtime-long.yaml` | `cougar_long_signals` |
| `short` | Short the RS laggards | `COUGAR_SHORT_WALLET` | `runtime-short.yaml` | `cougar_short_signals` |

Funded ~equally, the two books' notional offsets → **~beta-neutral**: Cougar
makes money on the *spread* between the best and worst tokenized stocks, not on
whether the stock market goes up or down.

## What makes Cougar different

- **vs. Octopus** (market-neutral): same dispersion *method*, different universe —
  Octopus ranks the crypto cross-section, Cougar ranks **tokenized US equities**.
- **vs. Bobcat** (XYZ big-tech, single-agent): Bobcat is long-only on a fixed
  set; Cougar is a **long/short fund** that ranks the whole equity cross-section.
- **vs. Spider** (AI/Tech): Spider is an AI/tech *sector* momentum book; Cougar is
  broad **stock-selection** across the equity universe, both directions.

## The equity universe

A curated tokenized-US-equity whitelist (`config.equities`), intersected each
tick with the live instrument board + a liquidity floor — so dead/thin names
are skipped and new trade.xyz listings auto-join once added:

NVDA · TSLA · AAPL · META · MSFT · GOOGL · AMZN · AMD · MU · INTC · TSM · ORCL · NFLX · AVGO · CRM · COIN · MSTR · PLTR · SMCI · UBER · SHOP · SPCX

Tokenized equities trade **24/7** on Hyperliquid (no market-hours gating).

## Scoring (both books; `minScore` 5)

Each tick: rank the universe by **24h relative strength** = own 24h return − the
equity-universe mean; take the top (long) / bottom (short) `rankPoolSize`, then:

| Component | Pts |
|---|---|
| Relative strength | +3 (≥2×`rsThresholdPct` 3%) / +2 (≥1×) / +1 (any lead/lag) |
| 4h absolute trend | +2 (confirms direction) / **skip** (opposes — never long a downtrend / short an uptrend) |
| 1h confirmation | +1 (confirms) / −1 (opposes) |
| Own 24h momentum sign | +1 (with direction) / −1 (against) |
| RSI guard | −2 (long: blow-off >80 / short: capitulation <20) |

## Execution & exit

- both books: slots **4**, margin_pct **20%**, **strict 5x** clamp (equities cap low at venue), tick **300s**
- DSL **moderate dispersion management**: phase1 max_loss **14%**, `weak_peak_cut` ON (8h @ 2.0), `dead_weight_cut` ON (16h — recycle a stalled leg into a fresher leader), `hard_timeout` **7d** (equities trend longer than crypto); phase2 `8%→0 / 18%→40 / 35%→60 / 60%→78 / 100%→88`

## XYZ handling

The universe is all XYZ — candle fetches route with `dex="xyz"` (the producer
keys off the `xyz:` prefix). The `main`/`xyz` clearinghouse sections are two
VIEWS of ONE cross-margined wallet, so `get_positions()` takes `accountValue`
ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | long | short |
|---|---|---|
| daily_loss_limit_pct | 12 | 12 |
| max_entries_per_day | 6 | 6 |
| max_consecutive_losses | 4 | 4 |
| cooldown_minutes | 60 | 60 |
| drawdown_halt_pct | 20 | 20 |
| per_asset_cooldown_minutes | 240 | 240 |
| data_retention_hours | 168 | 168 |
| drawdown_reset_on_day_rollover | true | true |

## Files

| File | Purpose |
|---|---|
| `runtime-long.yaml` / `runtime-short.yaml` | Per-book runtime spec |
| `scripts/cougar-producer.py` | Book-aware producer (equity cross-section RS rank + dispersion scoring) |
| `scripts/cougar_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/cougar-long-config.json` / `config/cougar-short-config.json` | Per-book tunables (equity whitelist, RS threshold) |

## Operator install

See [README.md](README.md) — two daemons (`COUGAR_LEG=long` and
`COUGAR_LEG=short`) on two **equally-funded** wallets (50/50, to stay
beta-neutral), each with its own runtime YAML.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the
**DSL ratchet engine** (exit path). User-conversation sessions are
**read-only**.

## License

Apache-2.0 — Copyright 2026 Senpi (https://senpi.ai)
