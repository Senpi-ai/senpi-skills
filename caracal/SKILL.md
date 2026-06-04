---
name: caracal-strategy
description: >-
  CARACAL v1.0 — Volatility Hedge Fund. Two volatility-expansion books on
  two wallets, one producer, trading BOTH directions. The BREAKOUT book
  scans liquid crypto for names coiled in a low-volatility squeeze that
  break their range, and rides the break; the CATALYST book runs the same
  compression→expansion engine on XYZ (equities / energy / metals / indices)
  — capturing oil-geopolitics and AI-infra moves as direction-agnostic vol
  events, 24/7. The edge is VOLATILITY (compression precedes expansion), not
  a directional view. NOT a copy-trader: each book scores its own universe
  and pushes signals; the runtime owns the LLM gate (pass-through), DSL
  exits, and all risk.guard_rails. CARACAL_LEG env selects the book.
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

# 🐱 CARACAL v1.0 — Volatility Hedge Fund

Caracal runs **two concurrent strategy wallets** that harvest **volatility
expansion**. **One producer script** (`caracal-producer.py`) serves both; the
`CARACAL_LEG` env var selects which book a given daemon is. It trades
**movement, not direction** — each book takes the breakout long or short,
whichever way it breaks.

> **The edge is volatility.** Compression precedes expansion, and a breakout
> *from a low-volatility coil* has higher follow-through than a breakout in
> already-volatile tape. Caracal only fires when a name has been coiling
> (recent ATR << baseline ATR) **and** breaks its range **and** the breakout
> bar is an outsized move.

| Book | Universe | Wallet env | Runtime | Scanner |
|---|---|---|---|---|
| `breakout` | Liquid main-DEX crypto | `CARACAL_BREAKOUT_WALLET` | `runtime-breakout.yaml` | `caracal_breakout_signals` |
| `catalyst` | Liquid XYZ (equities/energy/metals/indices) | `CARACAL_CATALYST_WALLET` | `runtime-catalyst.yaml` | `caracal_catalyst_signals` |

The two books differ only in **universe** (crypto vs XYZ) — they share the
same volatility-breakout engine. The `catalyst` book turns Jason's macro
themes (oil/Iran, AI infra) into **vol sources**: it rides whichever way the
catalyst breaks, 24/7 (XYZ trades through weekends).

## The volatility-breakout signal (both books)

Each tick the producer scans the leg's liquid universe (top `universeMaxNames`
by 24h volume) and, per name, fetches 1h+4h candles and computes:

- **Range breakout** — current price vs the prior `breakoutBars` (default 20)
  bar high/low. Break up → LONG; break down → SHORT. No break → skip.
- **Compression precondition** — recent ATR (`recentBars`) ÷ baseline ATR
  (`baseBars`). `≤ squeezeTight` (0.70) = tight coil; `≤ squeezeLoose` (0.90)
  = mild coil; otherwise penalized (a break with no prior coil has less edge).
- **Expansion surge** — breakout-bar true range ÷ baseline ATR. `≥ surgeStrong`
  (2.0×) / `≥ surgeMod` (1.3×).
- **Higher-TF agreement** — the break aligned with 4h structure follows through more.

### Scoring (raw integer; `minScore` 5)

| Component | Pts |
|---|---|
| Range breakout (the trigger) | +3 |
| Compression coil | +2 (≤0.70) / +1 (≤0.90) / −1 (no coil) |
| Expansion surge | +2 (≥2.0×) / +1 (≥1.3×) |
| 4h agreement | +1 (with break) / −1 (against) |

Direction is the break direction — both books trade LONG **and** SHORT.

## Execution & exit (both books)

- slots **3**, `margin_pct` **18%**, tick **300s**
- **strict 5x** leverage clamp, then each asset's Hyperliquid venue max
- DSL **tight fast-capture**: a failed breakout reverses fast, so cut quickly —
  phase1 max_loss **12%** / retrace 7 / 1 breach; `weak_peak_cut` **ON** (3h @ 2.0),
  `dead_weight_cut` **ON** (6h), `hard_timeout` **2d** (vol events resolve fast);
  phase2 ladder `8%→lock30 / 18%→50 / 35%→68 / 60%→80 / 100%→90` (locks early —
  expansion can round-trip)

## Leverage clamping (both books)

Desired = `maxLeverage` 5; clamped to each asset's HL venue max; the runtime
gate rejects any leverage above 5.

## XYZ handling

The `catalyst` book trades XYZ — candle fetches route with `dex="xyz"` (the
producer keys off the `xyz:` prefix). XYZ trades 24/7 on Hyperliquid, so there
is **no** weekend gating (oil stayed active through recent geopolitics). The
`main`/`xyz` clearinghouse sections are two VIEWS of ONE cross-margined wallet,
so `get_positions()` takes `accountValue` ONCE via `max()` — never sums.

## Risk gates (`risk.guard_rails`)

| Gate | breakout | catalyst |
|---|---|---|
| daily_loss_limit_pct | 12 | 12 |
| max_entries_per_day | 8 | 8 |
| max_consecutive_losses | 5 | 5 |
| cooldown_minutes | 45 | 45 |
| drawdown_halt_pct | 20 | 20 |
| per_asset_cooldown_minutes | 120 | 120 |
| data_retention_hours | 72 | 72 |
| drawdown_reset_on_day_rollover | true | true |

Entries and exits both use `FEE_OPTIMIZED_LIMIT` (`ensure_execution_as_taker`
true; 30s maker-first window — breakouts need timely fills).

## Files

| File | Purpose |
|---|---|
| `runtime-breakout.yaml` | Crypto breakout-book runtime spec |
| `runtime-catalyst.yaml` | XYZ catalyst-book runtime spec |
| `scripts/caracal-producer.py` | Book-aware producer daemon (one script, both books) |
| `scripts/caracal_config.py` | Leg resolution + SenpiClient wrapper + helpers |
| `config/caracal-breakout-config.json` | Crypto book tunables (squeeze/surge thresholds) |
| `config/caracal-catalyst-config.json` | XYZ book tunables |

## Operator install

See [README.md](README.md) — the two books are two daemons
(`CARACAL_LEG=breakout` and `CARACAL_LEG=catalyst`) on two **equally-funded**
wallets, each with its own runtime YAML.

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
