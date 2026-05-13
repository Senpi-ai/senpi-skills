---
name: kestrel-strategy
description: >-
  KESTREL v3.0.0 — XYZ Macro Breakout Rider (senpi_runtime_helpers
  migration). Plumbing-only port from v2.0. NO thesis change. NO
  scoring change. NO threshold change. Producer ports onto
  `senpi_runtime_helpers` (in-process SenpiClient + direct HTTP POST
  to runtime /signals + producer_daemon long-lived loop). Detects
  >=1.5% hourly price breakouts on commodities, indices, and
  high-volume equities 24/7 on Hyperliquid XYZ DEX. LLM regime gate +
  FEE_OPTIMIZED_LIMIT entries/exits + held-asset dedup + post-close
  cooldown + chain DB telemetry. Conservative 3-5x leverage.
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

# 🦅 KESTREL v2.0 — XYZ Macro Breakout Rider

The first Senpi agent to trade Hyperliquid XYZ DEX (commodities/metals/indices/equities) on a v2 producer architecture.

## What v2.0 fixes from v1.x

v1.1 was a v1 full-agency Python scanner that:
- Called `create_position` directly (no LLM gate, no chain telemetry)
- Used `ensureExecutionAsTaker: false` on entries (resting-on-book bug)
- Had over-strict scoring math: MOVE_EXHAUSTION penalty cancelled out 4H alignment, leaving most breakouts below MIN_SCORE 6

v2.0 fixes all three:

### Architecture: v2 producer + LLM gate + DSL
- `kestrel-producer.py` emits scored breakout signals via `SenpiClient.push_signal()` (direct HTTP POST). NO execution code.
- Runtime LLM gate is regime-aware — applies macro vetoes (BTC drawdown propagating to risk-off equities, vol expansion spikes) the producer can't see.
- `risk.guard_rails` declarative — runtime owns daily caps, drawdown halt, cooldowns.
- Trade chain DB emits LIFECYCLE / DECISION_EXECUTED / ACTION_RESULT / DSL_CREATED / DSL_CLOSED — first time Kestrel has telemetry.

### Execution: maker-first, taker-fallback
- Both entries AND exits use `FEE_OPTIMIZED_LIMIT` with `ensure_execution_as_taker: true`.
- After 60s ALO timeout, falls back to taker — no more "resting on book" with phantom realized PnL.
- Saves ~0.020-0.030% per maker-filled close vs v1.x MARKET orders.

### Calibration: scoring relaxed to actually trade
v1.1 only fired 2 trades / $60 volume in its lifetime. v2.0 calibration:
- **1H base scores +1**: 1.5% triggers +3 (was +2), 2% triggers +4 (was +3), 3% triggers +5 (was +4). The 1H breakout is the PRIMARY signal — weight it heavier.
- **MOVE_EXHAUSTION threshold 4%→6%**, MOVE_TIRING 2.5%→4%. v1.1's penalty kicked in too early, cancelling 4H alignment bonus and yielding net 0-1 contribution.
- **MIN_SCORE 6→5** modest relaxation.
- **Spread gate 0.2%→0.35%** — XYZ tickers have wider natural spreads than crypto.

Expected impact: 2-3 trades/month → 5-15 trades/month.

## Thesis (preserved from v1.1.1)

When a macro asset moves >=1.5% in an hour with volume confirmation, the move usually continues for 1-3 hours. Ride the trend with wide DSL.

12-asset universe:
- **Commodities**: CL (oil), BRENTOIL
- **Precious metals**: GOLD (SILVER removed v1.1.1 — HL doesn't support)
- **Indices**: SP500, XYZ100
- **High-volume equities**: AAPL, NVDA, GOOGL, TSLA, AMZN, META, MSFT

24/7 trading. No market-hours gate — Hyperliquid XYZ trades all hours including weekends and overnight (Iran/oil scenarios are the whole point).

## Scoring components (v2.0)

| Component | Range | Source |
|---|---|---|
| 1H breakout magnitude | +3 to +5 | hard gate >=1.5% |
| 4H trend alignment | 0 to +2 | matches breakout direction |
| MOVE_EXHAUSTION penalty | 0 to -2 | when 4H >=6% (was >=4% in v1.1) |
| MOVE_TIRING penalty | 0 to -1 | when 4H >=4% (was >=2.5%) |
| Volume surge | 0 to +2 | vs 4-bar 1H average |
| SM confirmation | 0 to +2 | XYZ-filtered SM leaderboard |
| Funding alignment | 0 to +1 | favorable for breakout direction |

Score-tiered leverage:

| Score | Leverage |
|---|---|
| 9+ | 5x |
| 5-8 | 3x |

## DSL preset (v2.0)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 18% |
| Phase 1 | retrace_threshold | 8 |
| Phase 1 | consecutive_breaches | 3 |
| Phase 2 T0 | trigger 5% / lock 35% | fleet-standard |
| Phase 2 T1 | trigger 10% / lock 50% | |
| Phase 2 T2 | trigger 20% / lock 65% | |
| Phase 2 T3 | trigger 35% / lock 80% | |
| Phase 2 T4 | trigger 50% / lock 90% | apex |
| hard_timeout | 480 min (8h) | enabled |
| weak_peak_cut | 60 min, min 2.0 | enabled |
| dead_weight_cut | 45 min | enabled (catches false breakouts) |

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting |
|---|---|
| daily_loss_limit_pct | 10% |
| max_entries_per_day | 12 (producer dynamic-cap below) |
| max_consecutive_losses | 3 |
| cooldown_minutes (post-loss) | 30 |
| drawdown_halt_pct | 25% |
| drawdown_reset_on_day_rollover | false |
| per_asset_cooldown_minutes | 180 (3h) |

Producer-side dynamic daily cap (P&L-aware):
- +5% PnL → 12 trades/day
- ≥0% → 8
- -5% → 5
- -15% → 3
- -25% → 1
- <-25% → 0 (HARD STOP)

## Fleet patches incorporated

- ✓ **Held-asset dedup** (3-layer: producer pre-filter, LLM gate, runtime per_asset_cooldown)
- ✓ **Post-close cooldown** (180min; producer-side runtime-cooldown backstop)
- ✓ **Reentrancy guard** (producer_daemon scanner_lock with stale-PID auto-recovery)
- ✓ **Wallet-from-config** (no hardcoding)
- ✓ **drawdown_reset_on_day_rollover: false**
- ✓ **FEE_OPTIMIZED_LIMIT** entries AND exits with `ensure_execution_as_taker: true`
- ✓ **Fleet-standard T0/T1 ladder**
- ✗ **FP-001 quiet hours** — deferred fleet-wide

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and
the **DSL ratchet engine** (exit path). User-conversation sessions
are **read-only**.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
