---
name: bison-strategy
description: >-
  BISON v3.0.0 — Conviction Holder, senpi_runtime_helpers migration.
  Plumbing-only flip from openclaw cron + mcporter subprocess to
  in-process SenpiClient (direct HTTPS for MCP, direct HTTP POST to
  runtime /signals, long-lived producer_daemon). Thesis preserved
  verbatim from v2.1: BTC/ETH/SOL asset whitelist, MIN_SCORE 11,
  conviction-scaled margin (25%/31%/37%), wide DSL with time-cuts
  DISABLED. Fewer trades, longer holds, bigger moves.
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "3.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🦬 BISON v3.0.0 — Conviction Holder

**Asset whitelist (BTC/ETH/SOL).** **Conviction floor (minScore 11).**
**No time-cuts** — Phase 1 + Phase 2 ratchet ladder own all exits.

## v3.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v2.1 asset whitelist, minScore 11, conviction-scaled margin tiers, direction waterfall, 9-component scoring, and DSL preset all preserved verbatim. Six-layer plumbing flip:

1. **MCP transport**: `mcporter` subprocess (2.5-5s cold-start per call) → `senpi_runtime_helpers.SenpiClient.mcp_call()` in-process HTTPS (~280ms).
2. **Signal emit**: scanner called `create_position` directly (Wolverine pattern); producer now emits via `push_signal()` to runtime `/signals`. Runtime LLM-gated `bison_entry` action opens via FEE_OPTIMIZED_LIMIT, passing `marginUsd` + `leverage` through from signal data for tier-based sizing.
3. **Reentrancy**: no v2 lockfile to replace (Bison v2.x ran on openclaw cron with no producer-side lock); `producer_daemon` owns the per-tick `scanner_lock` with stale-PID auto-recovery.
4. **Scheduler**: openclaw cron (5 min) → `producer_daemon(interval_seconds=300)`. Long-lived process; zero per-tick cold-start cost.
5. **Risk gates**: Python `MAX_DAILY_LOSS_PCT`, dynamic-slots PnL-based cap, per-asset cooldown state files → declarative `risk.guard_rails` block. `state/trade-counter.json` and `state/asset-cooldowns.json` are vestigial in v3.0 and can be deleted.
6. **Exit fee**: DSL exits switched from MARKET (taker, 0.045%) to FEE_OPTIMIZED_LIMIT (maker-first, 0.015%, 60s ALO timeout, taker fallback). Entry exits keep `ensure_execution_as_taker: false` (v2.1 ALO patience rule).

## CRITICAL RULES

### RULE 1: Producer enters. DSL exits. Producer NEVER exits positions.
v1.x had thesis re-evaluation that chopped winners. v2.0 removed it. v3.0 makes it structurally impossible — the producer has no `close_position` call site.

### RULE 2: Producer emits signals via push_signal(); runtime opens
The producer scores conviction theses and emits via `client.push_signal()`. The runtime's LLM-gated `bison_entry` action opens the position via FEE_OPTIMIZED_LIMIT with `ensure_execution_as_taker: false` (v2.1 patience rule preserved — entry ALO waits 30s for maker fill).

### RULE 3: All signals are score contributors, not hard gates
4H trend, 1H trend, 1H momentum, SM direction, funding — all add/subtract points. The minScore threshold (v2.1+: 11) is the ONLY gate. Nothing kills a signal before scoring.

### RULE 4: Asset whitelist enforced (v2.1+)
Only BTC/ETH/SOL by default (configurable via `allowedAssets` in `bison-config.json`). Pre-v2.1 fetched top 10 by 24h notional volume — let small-caps that volume-spiked into the universe (e.g. ZEC on May 4) consume the single daily slot.

### RULE 5: ensureExecutionAsTaker = false on ENTRIES
Entries use FEE_OPTIMIZED_LIMIT with `ensure_execution_as_taker: false` (v2.1 patience rule). Exits use `ensure_execution_as_taker: true` (closes MUST happen — taker fallback is the safety floor).

### RULE 6: Use the DSL preset in runtime.yaml — not a custom DSL state file
v2.x shipped a hardcoded DSL state template in scanner output. v3.0 uses the runtime YAML's `dsl_preset` block as the single source of truth. No scanner-side DSL state.

## How BISON v3.0 trades

### Entry (all score contributors — preserved from v2.1)

| Signal | Points | Type |
|---|---|---|
| 4H trend aligned | +3 | Score |
| 4H trend opposing | -1 | Score |
| 1H trend agrees | +2 | Score |
| 1H trend opposing | -1 | Score |
| 1H strong momentum (≥1%) | +2 | Score |
| 1H moderate momentum (≥0.5%) | +1 | Score |
| 1H counter momentum | -1 | Score |
| SM aligned | +2 | Score |
| SM opposing | -2 | Score |
| Funding aligned | +2 | Score |
| Funding crowded | -1 | Score |
| Volume rising | +1 | Score |
| OI growing | +1 | Score |
| RSI room | +1 | Score |
| RSI extreme | -1 | Score |
| 4H momentum (>1.5%) | +1 | Score |

**Min score: 11. Max possible: ~16.**

### Direction determination (waterfall)

1. 4H trend structure → LONG if BULLISH, SHORT if BEARISH
2. If 4H NEUTRAL → follow SM direction
3. If SM NEUTRAL → follow 1H momentum (>0.5%)
4. If all neutral → no signal

### Conviction-scaled margin

| Score | Margin |
|---|---|
| 8-9 | 25% of account |
| 10-11 | 31% |
| 12+ | 37% |

Producer computes `marginUsd` from tier; signal data carries it; LLM gate passes through to runtime payload. Same pattern for `leverage` (default 10x, MIN 7x, MAX 10x).

### Exit — RatchetStop only

DSL preset wide-by-design for conviction breathing:
- +10% ROE: no lock (confirms working)
- +20% / +25% lock
- +30% / +40% lock
- +50% / +60% lock
- +75% / +75% lock
- +100% ROE: lock 85% — infinite trail

Phase 1 max_loss: 30%. Time-cuts: hard_timeout + dead_weight_cut DISABLED. weak_peak_cut KEPT (self-limiting at 60min/3.0% peak).

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting | Replaces |
|---|---|---|
| max_entries_per_day | 3 | v2.1 base daily cap |
| per_asset_cooldown_minutes | 120 | v2.1 `cooldownMinutes` |
| daily_loss_limit_pct | 10 | v2.1 `MAX_DAILY_LOSS_PCT` |
| max_consecutive_losses | 3 | v2.1 (implicit) |
| cooldown_minutes (post-loss) | 30 | new (fleet-standard) |
| drawdown_halt_pct | 25 | new (fleet-standard, Roach lesson) |
| drawdown_reset_on_day_rollover | false | fleet-standard |

v2.1's PnL-aware dynamic daily cap (3 base / 6 hard cap, tiered by realized PnL) is dropped in favor of static `max_entries_per_day=3` + `drawdown_halt_pct=25`. The conservative ceiling and -25% hard stop are preserved; the "ride the hot hand" tier is sacrificed for state-file-free reliability.

## Hardcoded constants (not configurable)

- MAX_LEVERAGE: 10
- MIN_LEVERAGE: 7
- XYZ_BANNED: true (banned at producer scan level)

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the **DSL ratchet engine** (exit path). User-conversation sessions are **read-only**.

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v2.1.

## Changelog

### v3.0.0 (2026-05-12) — plumbing-only migration
Migrated to helpers-native (`senpi_runtime_helpers`). Thesis preserved verbatim. See six-layer flip above.

### v2.1 (2026-05-07)
- Asset whitelist (BTC/ETH/SOL); pre-v2.1 fetched top-10-by-volume which let small-caps like ZEC consume the daily slot
- minScore 8 → 11; was firing on first mediocre setup post-midnight, now demands real conviction
- DSL time-cuts disabled in runtime.yaml per single-asset pattern

### v2.0 (2026-04-06)
- ALL hard gates converted to score contributors
- Thesis exit (evaluate_held_position) REMOVED
- Scanner calls create_position internally (Wolverine pattern; superseded in v3.0 by helpers-native push_signal)
- Direction waterfall: 4H → SM → 1H momentum

## License

Apache-2.0 — attribution required for derivative works. Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
