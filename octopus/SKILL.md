---
name: octopus-strategy
description: >-
  OCTOPUS v1.0 — Market-Neutral Hedge Fund. Two single-direction books on
  two wallets, one producer. The LONG book ranks the live liquid main-DEX
  crypto cross-section by 24h relative strength and LONGS the strongest,
  trend-confirmed names; the SHORT book mirror-images it and SHORTS the
  weakest, trend-confirmed laggards. The two books net to ~beta-neutral —
  the edge is DISPERSION (leaders minus laggards), not market direction.
  NOT a copy-trader: each book scores its own universe and pushes signals;
  the runtime owns the LLM gate (pass-through), DSL exits, and all
  risk.guard_rails. OCTOPUS_LEG env selects the book.
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

# 🐙 OCTOPUS v1.0 — Market-Neutral Hedge Fund

Octopus runs **two concurrent strategy wallets** whose net market exposure
is ~zero. **One producer script** (`octopus-producer.py`) serves both; the
`OCTOPUS_LEG` env var selects which book a given daemon is. Each book binds
to its own wallet, runtime YAML, DSL, and risk envelope.

> **The edge is dispersion, not direction.** Octopus longs the relative
> leaders and shorts the relative laggards of the same liquid crypto
> cross-section. Long-book gains + short-book gains capture the **spread**
> between strong and weak names while the long and short notionals offset
> market beta. In a HYPE-dominates / alts-crushed regime the long book
> gravitates to the leaders and the short book to the carnage — market-neutrally.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `long` | Cross-sectional relative-strength LONG | `OCTOPUS_LONG_WALLET` | `runtime-long.yaml` | `octopus_long_signals` |
| `short` | Cross-sectional relative-weakness SHORT | `OCTOPUS_SHORT_WALLET` | `runtime-short.yaml` | `octopus_short_signals` |

## How market-neutral is achieved

Octopus does **not** emit atomic pairs. Neutrality emerges at the **fund
level**: two equally-funded single-direction books. The long book holds a
basket of leaders; the short book holds a basket of laggards. With balanced
funding (50/50) and balanced sizing (both `margin_pct 20`, `slots 4`), the
long and short notionals roughly offset, so the portfolio's net beta ≈ 0 and
its return is driven by the **dispersion** between the two baskets. Each book
has its own wallet + DSL, so a position exiting on one side does not force the
other — the books re-balance continuously as leadership rotates.

## The relative-strength rank (shared by both books)

Each tick the producer pulls the live instrument board **once** and computes,
for every liquid main-DEX perp (`dayNtlVlm ≥ volFloorUsd`, default $20M), its
**24h return** from `markPx` vs `prevDayPx`. The **universe mean** of those
returns is "the market." Each asset's **excess = own 24h − universe mean** is
its cross-sectional relative strength. This rank costs **zero candle fetches**.
Only the top (long) / bottom (short) `rankPoolSize` names by excess (default
12) then get 1h+4h candles pulled for absolute-trend confirmation — bounding
per-tick fetches.

## LONG book — long the leaders (trend-confirmed)

### Scoring (raw integer; `minScore` 5)

| Component | Pts | Source |
|---|---|---|
| Relative strength (excess) | +3 (≥2×`rsThresholdPct`) / +2 (≥1×) / +1 (≥0) / **disqualify** (<0) | 24h excess vs cross-section |
| 4h trend structure | +2 BULLISH / **disqualify** BEARISH | 4h candles |
| 1h trend confirmation | +1 BULLISH / −1 BEARISH | 1h candles |
| Own absolute momentum | +1 (24h ≥ 0) / −1 (< 0) | instrument ctx |
| RSI blow-off guard | −2 (RSI > `rsiOverbought` 80) | 1h RSI |

Disqualifies any name that is a relative *under*performer or in a 4h
downtrend — it never longs a "least-bad" laggard in a crash.

## SHORT book — short the laggards (trend-confirmed)

### Scoring (raw integer; `minScore` 5)

| Component | Pts | Source |
|---|---|---|
| Relative weakness (excess) | +3 (≤−2×`rsThresholdPct`) / +2 (≤−1×) / +1 (≤0) / **disqualify** (>0) | 24h excess vs cross-section |
| 4h trend structure | +2 BEARISH / **disqualify** BULLISH | 4h candles |
| 1h trend confirmation | +1 BEARISH / −1 BULLISH | 1h candles |
| Own absolute momentum | +1 (24h ≤ 0) / −1 (> 0) | instrument ctx |
| RSI capitulation guard | −2 (RSI < `rsiOversold` 20) | 1h RSI |

Disqualifies any name that is a relative *out*performer or in a 4h uptrend —
it never shorts a rising leader.

## Execution & exit (both books)

- slots **4**, `margin_pct` **20%** (4 × 20 = 80% max committed), tick **300s**
- **strict 5x** leverage clamp, then to each asset's Hyperliquid venue max
- DSL **moderate dispersion management**: phase1 max_loss **14%** / retrace 8 / 1 breach; `weak_peak_cut` **ON** (6h @ 2.0), `dead_weight_cut` **ON** (12h), `hard_timeout` **4d**; phase2 ladder `8%→lock0 / 18%→40 / 35%→60 / 60%→78 / 100%→88`
- stall-cuts are **ON** by design: a position whose relative trend mean-reverts should be recycled into a fresher leader/laggard, not ridden indefinitely.

## Leverage clamping (both books)

Desired leverage = the book cap (`maxLeverage` 5). The producer clamps to each
asset's **Hyperliquid venue max** from `market_list_instruments`. The runtime
decision gate also rejects any leverage above 5 as a clamp-breach defense.

## XYZ handling

Octopus ranks the **main-DEX crypto** cross-section only — XYZ equities are
excluded (no clean cross-sectional peer group, and they're Spider's domain).
The `main` and `xyz` clearinghouse sections are two VIEWS of ONE
cross-margined wallet, so `get_positions()` takes `accountValue` ONCE via
`max()` across the two — never sums it (summing double-counts and sizes 2x
too large).

## Race-window dedup

Each `push_signal(coin)` is recorded in `state/recent-signals-<leg>.json`
(per-book). The producer skips any coin seen within 180s before scoring —
covering the gap between a push returning OK and the position appearing in the
next-tick clearinghouse pull. On-chain held-asset filtering is the safety floor.

## Risk gates (`risk.guard_rails`)

| Gate | long | short |
|---|---|---|
| daily_loss_limit_pct | 12 | 12 |
| max_entries_per_day | 6 | 6 |
| max_consecutive_losses | 4 | 4 |
| cooldown_minutes | 60 | 60 |
| drawdown_halt_pct | 20 | 20 |
| per_asset_cooldown_minutes | 180 | 180 |
| data_retention_hours | 120 | 120 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker`
true; 45s maker-first window).

## Files

| File | Purpose |
|---|---|
| `runtime-long.yaml` | Long-book runtime spec (wallet, DSL, risk, LLM gate) |
| `runtime-short.yaml` | Short-book runtime spec |
| `scripts/octopus-producer.py` | Book-aware producer daemon (one script, both books) |
| `scripts/octopus_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/octopus-long-config.json` | Long-book tunables (universe floor, RS threshold) |
| `config/octopus-short-config.json` | Short-book tunables |

## Operator install

See [README.md](README.md) — the two books are two daemons
(`OCTOPUS_LEG=long` and `OCTOPUS_LEG=short`) on two **equally-funded** wallets,
each with its own runtime YAML. Equal funding is what keeps the fund neutral.

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
