---
name: dog-strategy
description: >-
  DOG v3.0.0 — Contrarian Pup (SM Exhaustion Fader), senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess + mcporter
  subprocess + Python state to in-process SenpiClient (direct HTTPS for
  MCP, direct HTTP POST to runtime /signals, long-lived producer_daemon).
  Thesis preserved verbatim from v2.5: multi-asset (BTC/ETH/SOL/HYPE)
  contrarian fader, 3.0% exhaustion gate, regime hard-gate, 15m freshness
  gate, MIN_SCORE 8, conservative leverage (7x base, 10x at score 12+),
  wide DSL for reversal development.
license: MIT
metadata:
  author: jason-goldberg
  version: "3.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐕 DOG v3.0.0 — The Contrarian Pup

Smart Money goes one way. Dog goes the other. The crowd is already in — let them eat the unwind.

## v3.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v2.5 scoring tables, contrarian flip, exhaustion gate, regime confirmation, persistence bonus, hard gates all preserved verbatim. Six-layer plumbing flip:

1. **MCP calls**: `cfg.mcporter_call(...)` → `senpi_runtime_helpers.SenpiClient.mcp_call()` — direct HTTPS, kills mcporter subprocess cold-start (~280 ms vs 2.5–5 s per call).
2. **Signal emit**: `create_position` call site removed; producer emits via `cfg._wrapper_client.push_signal(...)` to runtime `/signals`. Runtime opens the position via the LLM-gated `dog_entry` action.
3. **Reentrancy**: hand-rolled fcntl lockfile dropped. `producer_daemon` owns the per-tick `scanner_lock` with stale-PID auto-recovery.
4. **Tick scheduling**: bash `while true; sleep 180` loop replaced by `producer_daemon(fn=main, interval_seconds=180, ...)`. Long-lived Python process, no per-tick cold-start cost.
5. **Risk gates**: Python `MAX_DAILY_ENTRIES`, `COOLDOWN_MINUTES`, dynamic daily cap circuit breaker → declarative `risk.guard_rails` block in runtime.yaml. No state files to crash.
6. **Exit fee**: DSL exits switched from MARKET (taker, 0.045%) to FEE_OPTIMIZED_LIMIT (maker-first, 0.015%, 60 s ALO timeout, taker fallback). Saves ~0.020-0.030% per maker-filled close.

## Why the contrarian thesis works (preserved from v2.0)

Hyperliquid is dominated by leverage traders chasing momentum. When a coin moves 3%+ in 4 hours and SM consensus piles in 15%+, the crowd is already maximum exposed. The unwind happens for two reasons:

1. **Funding pressure** — extreme positioning generates extreme funding rates, forcing the late entrants to capitulate.
2. **Mean reversion** — overextended moves draw counter-trend liquidity from contrarian traders.

Dog's edge is being on the unwind side BEFORE the capitulation accelerates. The wide DSL (`hard_timeout: 6h`, Phase 2 tiers starting at +5%/20% lock) lets real reversals develop without premature cuts.

## What Dog does each tick (3-minute cadence)

1. Pull `leaderboard_get_markets` (top 100 by SM activity)
2. For each of BTC, ETH, SOL, HYPE: identify SM dominant direction
3. **Hard gate**: 4H price moved ≥ 3.0% in the SM direction (exhaustion required)
4. Fetch funding regime + per-asset funding_history for context
5. Score the contrarian setup (SM concentration + exhaustion + 15m freshness + regime confirmation + persistence)
6. **Contrarian flip**: emit the OPPOSITE direction
7. Push signals at `score ≥ 8` via `push_signal()` to runtime `/signals`
8. Runtime LLM gate evaluates → opens via FEE_OPTIMIZED_LIMIT if confident
9. DSL takes over: trailing exits with maker-first close

## Scoring (preserved from v2.5)

| Signal | Points |
|---|---|
| SM concentration ≥ 15% (DOMINANT) | 3 |
| SM concentration 10-15% (STRONG) | 2 |
| SM concentration 5-10% (ALIGNED) | 1 |
| Trader count ≥ 100 (DEEP_CONSENSUS) | 1 |
| 4H price aligned with SM ≥ 2% (STRONG_4H) | 2 |
| 4H price aligned with SM 0.5-2% (4H_CONFIRMS) | 1 |
| Deep exhaustion (|4H| ≥ 4.0%) — contrarian BONUS | 2 |
| Moderate exhaustion (|4H| ≥ 2.5%) — contrarian BONUS | 1 |
| 1H momentum confirms SM | 1 |
| 15m velocity strong spike (cc_15m > 2.0) | 3 |
| 15m velocity spike (cc_15m > 0.5) | 2 |
| 15m velocity building (cc_15m > 0.1) | 1 |
| 1h SM contribution accelerating | 1 |
| Funding pays the fade direction | 1 |
| Regime confirms fade (LONG_CROWDED for SHORT, etc.) | 2 |
| Mature crowding (persistence ≥ 12h) | 2 |
| Stable crowding (persistence ≥ 6h) | 1 |
| Crowding unwinding (DECREASING trend) | 1 |
| Crowding still building (INCREASING) | -1 |
| 4H opposing SM direction (penalty) | -1 |
| US session bonus (13:00-21:00 UTC) | 1 |

**Min score: 8** (producer-level floor). **Max leverage: 10x.**

### Hard gates (any single failure → SKIP)

- **SM minimums**: trader_count ≥ 30
- **4H exhaustion**: |priceChange4hPct| ≥ 3.0% AND aligned with SM direction
- **15m freshness**: contribChange15m > 0 (SM must still be building, not unwinding)
- **Regime hard-gate**: skip if `regime_confirms_fade(direction)` returns False (e.g. SHORT signal but regime is SHORT_CROWDED — Dog would be fighting, not fading)
- **XYZ DEX banned**: contrarian thesis is for crypto majors only
- **Held-asset dedup**: producer skips emission if asset already held (defense in depth; runtime per_asset_cooldown is the authoritative gate)

## Leverage tiers

| Score | Leverage |
|---|---|
| ≥ 12 | 10x |
| 8-11 | 7x |

Asset caps applied (BTC 40x, ETH 25x, SOL 20x, HYPE 10x); clamped at MAX_LEVERAGE=10.

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting | Replaces |
|---|---|---|
| max_entries_per_day | 3 | v2.5 `MAX_DAILY_ENTRIES` |
| per_asset_cooldown_minutes | 120 | v2.5 `COOLDOWN_MINUTES=180` + `SAME_DIR_COOLDOWN=90` |
| daily_loss_limit_pct | 15 | v2.5 dynamic cap's -15% defensive level |
| max_consecutive_losses | 3 | v2.5 (implicit; not enforced) |
| cooldown_minutes (post-loss) | 60 | new (fleet-standard backstop) |
| drawdown_halt_pct | 25 | v2.5 dynamic cap's -25% HARD STOP |
| drawdown_reset_on_day_rollover | false | fleet-standard (Roach lesson) |

v2.5's dynamic daily cap had intermediate tiers (12/8/5/3/1 entries based on session P&L). v3.0 drops the intermediate tiers in favor of static `max_entries_per_day=3` + `drawdown_halt_pct=25`. The conservative ceiling and hard-stop are preserved; the "ride the hot hand" tier (12 entries when up 5%+) is sacrificed for state-file-free reliability.

## DSL preset (wide, patient — preserved from v2.5)

| Phase | Component | Setting | Why |
|---|---|---|---|
| Phase 1 | max_loss_pct | 15% | Tighter than fleet 25% — contrarian losses should be small |
| Phase 1 | retrace_threshold | 8 | Standard |
| Phase 1 | consecutive_breaches_required | 1 | Single-breach (Senpi runtime no longer supports multi-breach) |
| Time cuts | hard_timeout | 360 min (6h) | Reversals take time |
| Time cuts | weak_peak_cut | 90 min, min_value 2.0 | Cut if peak ROE < 2% in 90 min |
| Time cuts | dead_weight_cut | 60 min | v2.4 loosened from 30 → 60 (let fades develop) |
| Phase 2 | T0 | +5% / 20% lock | Don't bank too early |
| Phase 2 | T1 | +10% / 40% lock | |
| Phase 2 | T2 | +15% / 60% lock | |
| Phase 2 | T3 | +20% / 75% lock | |
| Phase 2 | T4 | +30% / 85% lock | apex |

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon → runtime action path** (entry) and the **DSL ratchet engine** (exit). User-conversation sessions are **read-only**.

If the user asks an action-implying question ("anything close to triggering?"), respond by reading the current state — DO NOT execute. The producer daemon will handle real signals on its next tick.

## Best for

- Operators who believe momentum chasing is a losing edge on Hyperliquid
- Multi-asset diversification across BTC/ETH/SOL/HYPE
- Patient holds (1-6 hours per trade — reversals take time)
- Counter-trend traders who want a deterministic execution layer

## Not for

- Momentum followers
- High-frequency scalping (Dog targets 2-3 trades per day max)
- Single-asset specialists
- Anyone who wants the scanner to make exit decisions (DSL handles all exits)

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v2.5.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai). The Contrarian Pup.
