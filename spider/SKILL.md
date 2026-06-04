---
name: spider-strategy
description: >-
  SPIDER v5.1.1 — Two autonomous style legs on two wallets, one producer.
  NOT a copy-trader: each leg scores its own universe to a STYLE and
  pushes signals; the runtime owns the LLM gate (pass-through), DSL
  exits, and all risk.guard_rails. SWING leg = Tech & AI multi-day
  momentum on a DYNAMIC XYZ-equity universe (curated semis/AI/space
  include-set + auto-caught fresh listings like CBRS/Cerebras and
  SPCX/SpaceX) plus static crypto alts (SUI/ONDO/HYPE/NIL/GRASS/ZEC),
  LONG only, 4h+1h trend structure + 24h relative-strength, conviction
  leverage clamped 10x, wide let-winners-run DSL. SCALP leg = Macro &
  majors fast mean-reversion
  (BTC/ETH/SOL/HYPE + xyz:BRENTOIL/xyz:CL), BOTH directions, short-TF
  stretch + RSI extreme with a 1h trend filter, strict 5x, tight
  fast-capture DSL. SPIDER_LEG env selects the leg.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "5.1.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🕷️ SPIDER v5.0 — Two-Persona Style Hunter

Spider runs **two concurrent strategy wallets**, each a distinct trading
style. **One producer script** (`spider-producer.py`) serves both; the
`SPIDER_LEG` env var selects which leg a given daemon is. Each leg binds
to its own wallet, runtime YAML, DSL, and risk envelope.

> **Not a copy-trader.** Earlier Spider versions chased other traders'
> books. v5.0 hunts the **style**: each leg scores its own universe with
> its own technicals and emits its own signals. The runtime LLM gate is
> pass-through; the runtime owns execution + DSL + risk.

| Leg | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `swing` | Tech & AI multi-day momentum, LONG only | `SPIDER_SWING_WALLET` | `runtime-swing.yaml` | `spider_swing_signals` |
| `scalp` | Macro & majors fast mean-reversion, BOTH dirs | `SPIDER_SCALP_WALLET` | `runtime-scalp.yaml` | `spider_scalp_signals` |

## What changed from v4.0

v4.0 was a single-leg "patient anchor sniper" — one 7-day LONG scored on
arena-leader alignment + SM consensus. v5.0 is a full thesis
**replacement**: two style legs, multi-signal baskets, both directions on
the scalp leg, and a leg-parameterized producer. The old single-leg
`runtime.yaml` / `spider-config.json` are removed.

**v5.1** made the swing XYZ-equity universe **dynamic**: instead of a fixed
`allowedAssets` list, the leg rebuilds its equity pool each tick from the
live instrument board and **auto-catches freshly listed Pre-IPO Perpetuals
/ AI IPOs** (the edge behind Spider's CBRS/Cerebras win — a name that did
not exist as a tradeable ticker when a static list would have been written).
The scalp universe stays static (majors + energy). See the SWING universe
section above.

---

## SWING leg — Tech & AI multi-day momentum (LONG only)

Multi-day trend rider. Holds a small basket of the strongest names in a
semiconductor/AI + high-beta-alt universe, sits through drawdowns while
the multi-timeframe trend holds.

**Universe** (`config/spider-swing-config.json`) — **dynamic**, rebuilt
every tick by `build_universe()`:

- **Static crypto alts** (`cryptoAlts`): `SUI / ONDO / HYPE / NIL / GRASS / ZEC`.
- **Dynamic XYZ equities**: derived from the live `market_list_instruments`
  board (the same call already made for venue-leverage caps — zero extra
  cost). An XYZ name is eligible if it is **liquid** (`dayNtlVlm ≥
  xyzVolFloorUsd`, default $5M) **and** either (a) its bare ticker is in the
  curated tech/AI/space **`xyzIncludeSet`** (`NVDA AMD INTC MRVL MU TSM ASML
  ARM SMSN SKHX DRAM SNDK DELL LITE CRWV PLTR ORCL GOOGL META MSFT AMZN AAPL
  NFLX IBM COIN MSTR CRCL HOOD SPCX RKLB CBRS`), or (b) it was **first seen <
  `xyzFreshDays`** (default 21d) ago **and** is **not** in the commodity/FX/
  index **`xyzExcludeSet`**. Branch (b) auto-catches new **Pre-IPO
  Perpetuals / AI IPOs** (Spider's CBRS/Cerebras and SPCX/SpaceX wins) with
  no code edit. Qualifiers are capped to the top **`xyzMaxNames`** (default
  20) by 24h volume to bound per-tick candle fetches; the score gate below
  does the final quality filtering.

First-seen timestamps live in `state/xyz-first-seen-swing.json`. On first
run every current name is back-dated as already-old, so the auto-catch fires
only on names that appear **after** deploy. A freshly listed perp needs ~24h
of candles (≥6 4h bars) before it can score. If the instrument board is
unavailable, the leg falls back to a static `allowedAssets` list.

### Scoring (raw integer; `minScore` 5)

| Component | Pts | Source |
|---|---|---|
| 4h trend structure | +3 BULLISH / −4 BEARISH | `market_get_asset_data` 4h candles |
| 1h trend confirmation | +2 BULLISH / −1 BEARISH | 1h candles |
| 24h relative-strength proxy | +3 (≥8%) / +2 (≥4%) / +1 (≥1%) / −1 (<0) | `markPx` vs `prevDayPx` |
| RSI room | +1 (<50) / −2 (> `rsiMaxLong` 78) | 1h RSI |
| Funding | +1 (negative) / −1 (crowded long) | instrument context |
| SM consensus bonus | +2 LONG / −2 SHORT | `leaderboard_get_markets` (alts only; XYZ has none) |

Direction is always LONG — a bearish 4h structure drives the score below
`minScore`, so the leg simply waits. Requires 4h **and** 1h bullish
alignment to clear the floor.

### Execution & exit

- slots **3**, `margin_pct` **28%** (3 × 28 = 84% max committed), tick **300s**
- conviction leverage clamped **10x**, then to each asset's venue max
- DSL **wide let-winners-run**: phase1 max_loss **22%** / retrace 12 / 1 breach; **all time-cuts OFF**; `hard_timeout` **7d** staleness backstop; phase2 ladder `15%→lock0 / 30%→45 / 60%→68 / 100%→80 / 150%→90`
- low turnover: `max_entries_per_day` **12** (raised from 4 — don't throttle the winning style by trade count; the loss-based gates own risk), `per_asset_cooldown` **4h**, `bypass_max_entries_per_day_on_profit` **true**

---

## SCALP leg — Macro & majors fast mean-reversion (BOTH directions)

Fades short-timeframe stretch and rides the snap-back. Long-biased but
actively shorts when overbought. Strict 5x, minutes-to-hour holds, high
win rate, small per-trade size.

**Universe** (`config/spider-scalp-config.json` → `allowedAssets`):
majors `BTC / ETH / SOL / HYPE` + energy `xyz:BRENTOIL / xyz:CL`.

### Scoring (raw integer; `minScore` 4)

Picks the more-extreme side (oversold → LONG, overbought → SHORT), then:

| Component | Pts |
|---|---|
| RSI extreme | +3 (≤20 / ≥80) / +2 (≤25 / ≥75) / +1 (≤ `rsiOversold` 30 / ≥ `rsiOverbought` 70) |
| Stretch from 20-bar 15m MA | +2 (≥2× `stretchThresholdPct` 0.8%) / +1 (≥1×) |
| 1h trend filter | +1 fading **with** higher-TF bias / −2 against a strong trend (knife guard) |
| Funding tiebreak | +1 (shorts pay a LONG / longs pay a SHORT) |

The −2 knife guard keeps the leg from longing a falling knife or shorting
a ripping uptrend — it only fires on genuine intraday extremes.

### Execution & exit

- slots **4**, `margin_pct` **15%** (small scalp size), tick **60s**
- **strict 5x** leverage clamp (the style's defining discipline), then venue max
- DSL **tight fast-capture**: phase1 max_loss **5%** (tightened from 7% to lose less) / retrace 3 / 1 breach; `weak_peak_cut` **ON** (30m @ 1.5), `dead_weight_cut` **ON** (25m), `hard_timeout` **2h**; phase2 ladder `3%→lock35 / 6%→55 / 12%→70 / 25%→85`
- **turnover**: `max_entries_per_day` **18** (trimmed from 30) — *fee-sensitive*; lower further if net-of-fees underperforms

---

## Leverage clamping (both legs)

Desired leverage = the leg cap (`swingMaxLeverage` 10 / `scalpMaxLeverage`
5). The producer then clamps to each asset's **Hyperliquid venue max**,
read from `market_list_instruments` → `instruments[].max_leverage` (one
call per tick, whole universe). This prevents emitting an unfillable
order — e.g. `GRASS` and `NIL` cap at **3x**, so a swing 10x desire is
clamped to 3x for those names. The runtime decision gate also rejects any
leverage above the leg cap as a clamp-breach defense.

## XYZ (HIP-3) asset handling

XYZ equities/energy require `dex="xyz"` on `market_get_asset_data` (an
empty dex errors with `Asset prefix "xyz" must match dex`). Main-DEX
assets pass `dex=""`. The `main` and `xyz` sub-DEX sections are two
VIEWS of ONE cross-margined wallet (not two collateral silos), so both
report the same `accountValue`. `get_positions()` takes `accountValue`
ONCE via `max()` across the two views — never sums it. Summing
double-counts the balance and sizes every position 2x too large
(`margin_usd = account_value * margin_pct`). `assetPositions` ARE
per-sub-DEX, so those are enumerated across both. Candle fields are
HL-native (`o/h/l/c/v`).

## Race-window dedup

Each `push_signal(coin)` is recorded in `state/recent-signals-<leg>.json`
(per-leg). The producer skips any coin seen within 180s **before**
scoring — covering the gap between a push returning OK and the position
appearing in the next-tick clearinghouse pull. On-chain held-asset
filtering (`get_positions`) is the safety floor underneath.

## Risk gates (`risk.guard_rails`)

| Gate | swing | scalp |
|---|---|---|
| daily_loss_limit_pct | 15 | 10 |
| max_entries_per_day | 12 (bypass-on-profit) | 18 (fee-sensitive) |
| max_consecutive_losses | 4 | 5 |
| cooldown_minutes | 90 | 30 |
| drawdown_halt_pct | 25 | 20 |
| per_asset_cooldown_minutes | 240 (4h) | 10 |
| data_retention_hours | 168 (7d) | 72 |
| drawdown_reset_on_day_rollover | **true** | false |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker`
true; swing 60s maker-first window, scalp 20s).

## Files

| File | Purpose |
|---|---|
| `runtime-swing.yaml` | Swing-leg runtime spec (wallet, DSL, risk, LLM gate) |
| `runtime-scalp.yaml` | Scalp-leg runtime spec |
| `scripts/spider-producer.py` | Leg-aware producer daemon (one script, both legs) |
| `scripts/spider_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/spider-swing-config.json` | Swing-leg tunables (dynamic-universe sets, floors) |
| `config/spider-scalp-config.json` | Scalp-leg tunables |
| `state/xyz-first-seen-swing.json` | Auto-generated first-seen ledger for fresh-listing detection |

## Operator install

See [README.md](README.md) — the two legs are two daemons
(`SPIDER_LEG=swing` and `SPIDER_LEG=scalp`) on two wallets, each with its
own runtime YAML.

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
