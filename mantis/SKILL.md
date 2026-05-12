---
name: mantis-strategy
description: >-
  MANTIS v6.0.0 — Slipstream (Cross-Asset Catchup Hunter),
  senpi_runtime_helpers migration. Plumbing-only flip from
  openclaw cron + mcporter subprocess + in-Python state to
  in-process SenpiClient (direct HTTPS for MCP, direct HTTP POST
  to runtime /signals, long-lived producer_daemon). Thesis preserved
  verbatim from v5.0: when BTC (or another leader) makes a significant
  4h move and a correlated alt hasn't responded yet, Mantis strikes
  the alt before the catchup completes. Confidence-tier sizing
  preserved. Leader-reversal veto NOW WORKS (v5.0 was silent no-op).
license: MIT
metadata:
  author: jason-goldberg
  version: "6.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🦎 MANTIS v6.0.0 — Slipstream

The mantis is patience plus speed. Stillness, then strike.

## v6.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v5.0 entry filters, confidence-tier sizing, dynamic hard_timeout, and leader-reversal veto logic all preserved verbatim. Six-layer plumbing flip:

1. **MCP transport**: `mcporter` subprocess (2.5-5s cold-start) → `SenpiClient.mcp_call()` in-process HTTPS (~280ms).
2. **Signal emit**: scanner called `create_position` directly; producer now emits via `push_signal()` to runtime `/signals`. Runtime LLM-gated `mantis_entry` action opens via FEE_OPTIMIZED_LIMIT, passing `leverage` + `marginUsd` through from signal data for tier-based sizing.
3. **Leader-reversal veto NOW WORKS.** v5.0 emitted `VETO_CLOSE` stdout JSON that no runtime action consumed (silent no-op). v6.0 wires it as a direct producer-authoritative action — `cfg.mcp_call("close_position", ...)` fires when the leader reverses >1% from entry value. The runtime cannot express "close this position if a separate asset's 4h move reverses by X%"; that thesis lives in the producer.
4. **Scheduler**: openclaw cron (60s) → `producer_daemon(interval_seconds=60)`. Long-lived process; scanner_lock with stale-PID auto-recovery.
5. **Risk gates**: Python `MAX_DAILY_ENTRIES` + `asset-cooldowns.json` → declarative `risk.guard_rails` block. `state/asset-cooldowns.json` deleted (runtime `per_asset_cooldown_minutes: 240` is authoritative).
6. **Exit fee**: DSL exits switched from MARKET (taker, 0.045%) to FEE_OPTIMIZED_LIMIT (maker-first, 0.015%, 60s ALO timeout, taker fallback).

State files retained (producer-authoritative, NOT runtime-leaky):
- `state/position-metadata.json` — `leader_pct_at_entry` per asset (required for veto pass)
- `state/entry-log.jsonl` — observability only (no longer used for daily-cap counting)

## What Mantis does

Mantis monitors `market_get_cross_asset_flows` every 60 seconds. When a leader (currently BTC) makes a significant 4h move, the tool surfaces correlated alts that historically follow that leader but haven't yet responded. Mantis filters those laggards by strict criteria, picks the highest-confidence one, and emits a signal to enter the laggard in the direction the leader moved — sized by confidence, with a hard timeout calibrated to the alt's typical lag distribution.

## The thesis

Pure statistical edge with quantified time bounds:

- **Edge**: alts that follow a leader 85%+ of the time, with low timing variance, are likely to catch up after a leader move
- **Timing**: the alt's historical `avg_lag_minutes` defines the trade's window
- **Exit**: the catchup either happens within `avg_lag × 1.5` minutes — or it doesn't happen at all
- **Veto**: if the leader reverses mid-position, the entire thesis is invalidated and Mantis closes immediately (producer-side `close_position` call)

## Entry criteria (all must pass — preserved from v5.0)

| Filter | Threshold | What it ensures |
|---|---|---|
| `follow_rate` | ≥ 0.85 | Alt historically follows leader 85%+ of moves |
| `confidence` | ≥ 0.75 | Composite of correlation + follow + lag + SM alignment |
| `gap_pct` | ≥ 1.5% (abs) | Catchup opportunity is meaningful |
| `sm_starting_to_rotate` | true | Smart money is confirming the catchup |
| `lag_stddev_minutes` | ≤ 90 | Timing is predictable enough to trade |
| Asset | not already open | No doubling up on same asset |
| Asset | not in `per_asset_cooldown_minutes` | runtime-enforced (240min) |

## Sizing — conviction tiers (preserved from v5.0)

Sizing scales directly off the `confidence` score. Producer computes the tier-based `marginUsd` and `leverage`, embeds in signal `data`; LLM passes both through to the runtime's `OPEN_POSITION` payload.

| Confidence | Margin % | Leverage |
|---|---|---|
| ≥ 0.92 | 75% | 8x |
| ≥ 0.85 | 50% | 7x |
| ≥ 0.75 | 25% | 5x |

Hard cap: 8x leverage, 75% notional of available margin.

## Direction

Match the leader: positive leader 4h move → LONG the laggard, negative → SHORT. Mantis never goes against the leader's direction; the entire setup is "the alt is going to catch up to where the leader is."

## Exit logic — three layers in priority order

1. **Leader-reversal veto** (producer-side, v6.0 wired correctly) — every 60s tick, Mantis checks the current 4h leader move vs. the leader's move at entry. If the leader has reversed by >1.0% from entry, Mantis calls `close_position` directly. The thesis was the leader's move; if it dies, the trade dies.

2. **Dynamic hard timeout** (producer-side, via signal data) — `avg_lag_minutes × 1.5`, clamped to [30, 240] minutes, passed in signal `data.hardTimeoutMinutes`. If the alt hasn't caught up within that window, the catchup probably isn't happening — DSL `hard_timeout: 240` ceiling backstops.

3. **DSL trail** — standard Phase 1 / Phase 2 trailing logic for in-window winners:
   - Phase 1 max loss: 12%
   - Phase 2 trail tiers: 5/30, 10/60, 15/75, 25/85
   - Weak peak cut: 20 min, 1.5% min value
   - Exits via FEE_OPTIMIZED_LIMIT (v6.0 win)

## Risk controls

| Control | v6.0 source | Value |
|---|---|---|
| Max concurrent positions | `strategy.slots` | 2 |
| Per-asset cooldown | `risk.guard_rails.per_asset_cooldown_minutes` | 240 min |
| Daily entry cap | `risk.guard_rails.max_entries_per_day` | 6 |
| Max position notional | producer-side cap | 75% of available margin |
| Daily loss limit | `risk.guard_rails.daily_loss_limit_pct` | 10% |
| Max consecutive losses | `risk.guard_rails.max_consecutive_losses` | 3 |
| Cooldown after loss | `risk.guard_rails.cooldown_minutes` | 60 min |
| Drawdown halt | `risk.guard_rails.drawdown_halt_pct` | 20% |
| Reset on day rollover | `risk.guard_rails.drawdown_reset_on_day_rollover` | false |

## MCP tools used

Producer side (in-process via `SenpiClient.mcp_call`):
- `market_get_cross_asset_flows` — primary signal source
- `strategy_get_clearinghouse_state` — current positions + account value
- `close_position` — direct call when leader-reversal veto fires

Runtime side (called by senpi-trading-runtime in response to the signal):
- `create_position` (FEE_OPTIMIZED_LIMIT entry via `mantis_entry` action)
- DSL trailing exits via `FEE_OPTIMIZED_LIMIT` close

## Cron cadence

Producer daemon runs every 60 seconds. The cross-asset flow data itself is refreshed daily at 00:00 UTC by the `hyperliquid-cross-asset-flow-cron` job; Mantis consumes the live output.

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon → runtime action path** (entry), the **DSL ratchet engine** (DSL exits), and the **producer's leader-reversal veto pass** (the only producer-direct `close_position` call site, scoped strictly to assets in `position-metadata.json` whose tracked leader has reversed). User-conversation sessions are **read-only**.

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v5.0.

## Status

**v6.0.0 — helpers-native migration.** Currently BTC-only as the leader. Adding ETH/SOL/HYPE as leaders is a one-line config change in `mantis_config.py` once pre-computed lag data for those assets ships.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).
