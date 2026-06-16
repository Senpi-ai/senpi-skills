---
name: magpie-strategy
description: >-
  MAGPIE v1.0 — IPO / New-Listing Event Hedge Fund. Two books on two wallets,
  one producer, trading the full pre-IPO → listing → graduation arc of tokenized
  equities on Hyperliquid XYZ (trade.xyz IPOPs like SpaceX). The PRE-LISTING
  book auto-discovers IPOPs (pre-IPO perpetuals) by their funding signature and
  rides the pre-listing trend into the IPO; the GRADUATION book detects the
  IPOP→equity conversion (funding jumps ~100x, leverage cap lifts, price
  throttle comes off) and rides the explosive first-days price discovery — the
  SpaceX $1.4B-day-1 pattern. The edge is EVENT alpha around new listings. NOT a
  copy-trader: each book scores its own set and pushes signals; the runtime owns
  the LLM gate (pass-through), DSL exits, and all risk.guard_rails. MAGPIE_LEG
  env selects the book.
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

# 🐦 MAGPIE v1.0 — IPO / New-Listing Event Hedge Fund

Magpie trades the **new-listing event** that just became Hyperliquid's biggest
story: SpaceX (SPCX) did **$1.4B in day-1 perp volume** — 30% of all HIP-3
volume — while real exchanges ran out of shares. Magpie productizes the full
arc: accumulate the pre-IPO perpetual, then ride the explosive momentum when it
converts to a full equity perp. One producer script (`magpie-producer.py`)
serves both books; the `MAGPIE_LEG` env var selects which.

| Book | Style | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `pre_listing` | Accumulate IPOPs into the IPO | `MAGPIE_PRE_LISTING_WALLET` | `runtime-pre_listing.yaml` | `magpie_pre_listing_signals` |
| `graduation` | Ride post-conversion momentum | `MAGPIE_GRADUATION_WALLET` | `runtime-graduation.yaml` | `magpie_graduation_signals` |

## What makes Magpie different

- **vs. Cougar** (equity long/short): Cougar trades the *ongoing* equity market;
  Magpie trades the *event* — the pre-IPO ramp and the conversion pop.
- **vs. Lemur / Falcon** (single agents): Lemur holds IPOPs, Falcon trades the
  conversion. Magpie packages both into a two-wallet **fund** with its own risk
  controls — and reuses their exact, proven detection logic.

## How an IPOP is detected (both books)

trade.xyz pre-IPO perpetuals carry a structural **funding signature**: very low
funding (`|funding| ≤ ipopFundingMaxAbs` ~1e-7, a ~1% multiplier vs the ~100×
standard) and a low leverage cap (`≤ ipopMaxLeverageCap` 5). When the company
IPOs and the product **converts** to a standard equity perp, funding jumps ~100×
and the cap lifts — Magpie keys off exactly that transition.

## PRE-LISTING book — accumulate the ramp (both directions)

Each tick: `fetch_ipop_universe` filters the xyz board to IPOPs (funding +
leverage + min-volume signature). For each, score the pre-listing trend — 4h
structure sets direction, 1h confirms, Smart-Money confirms (sparse pre-listing
→ trend-only fallback). `minScore` 5. Holds up to **3** IPOPs (today ~just SPCX;
auto-expands as trade.xyz lists ANTHROPIC / OPENAI / STRIPE / …). **margin_pct
12%, 3x** (Discovery Bounds throttle IPOP velocity).

## GRADUATION book — ride the conversion (both directions)

Each tick: classify every xyz instrument IPOP-vs-STANDARD, compare to the
prior-tick class cache to detect **IPOP→STANDARD flips**, and stamp each flip
into a `conversionWindowHours` (72h) eligibility window. Within the window, score
post-conversion **momentum** (`minMomentumPct` 3% / `strongMomentumPct` 8%) +
SM + rising-volume bonuses, and ride the move. `minScore` 5. **margin_pct 15%,
5x** (the venue cap lifts post-conversion). The window means momentum that
develops over hours/days is still tradeable, not just the single flip tick.

## Execution & exit

- both books: `FEE_OPTIMIZED_LIMIT` entries + exits, taker-true
- **PRE-LISTING** slots 3, 3x — DSL **moderate-wide** (multi-day discovery): phase1 max_loss **10%**, time-cuts OFF, `hard_timeout` **7d**; phase2 `10%→0 / 22%→45 / 45%→65 / 80%→80 / 130%→90`
- **GRADUATION** slots 3, 5x — DSL **wide let-winners-run** (price discovery trends hard): phase1 max_loss **12%**, time-cuts OFF, `hard_timeout` **5d**; phase2 `12%→0 / 28%→45 / 55%→65 / 100%→80 / 160%→90`

## State (graduation book)

The graduation book persists a **class-state cache** (`class-state-graduation.json`,
last-tick IPOP/STANDARD per instrument) and a **conversion-window cache**
(`conversions-graduation.json`, when each flip was detected). The first tick just
seeds the class cache (no flips); flips are only detected against a known prior.

## XYZ handling + auth

Everything is XYZ — candle/instrument fetches pass `dex="xyz"`. **Requires
user-scope auth** for `leaderboard_get_markets` (Smart-Money confirmation). The
`main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined wallet,
so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | pre_listing | graduation |
|---|---|---|
| daily_loss_limit_pct | 12 | 12 |
| max_entries_per_day | 4 | 4 |
| max_consecutive_losses | 4 | 4 |
| cooldown_minutes | 90 | 90 |
| drawdown_halt_pct | 18 | 20 |
| per_asset_cooldown_minutes | 360 | 360 |
| data_retention_hours | 168 | 168 |
| drawdown_reset_on_day_rollover | true | true |

## Files

| File | Purpose |
|---|---|
| `runtime-pre_listing.yaml` / `runtime-graduation.yaml` | Per-book runtime spec |
| `scripts/magpie-producer.py` | Book-aware producer (IPOP discovery + conversion detection + scoring) |
| `scripts/magpie_config.py` | Leg resolution + SenpiClient wrapper + class-state/conversion caches |
| `config/magpie-pre_listing-config.json` / `config/magpie-graduation-config.json` | Per-book tunables |

## Operator install

See [README.md](README.md) — two daemons (`MAGPIE_LEG=pre_listing` and
`MAGPIE_LEG=graduation`) on two wallets (default 50/50), each with its own
runtime YAML.

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
