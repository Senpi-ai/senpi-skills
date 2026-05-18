---
name: condor-strategy
description: >-
  CONDOR v4.0.1 — One Amazing Trade per Day. Top 50 HL assets, pure
  trend continuation, apex confluence only. 3TF alignment hard gate
  + MACRO_TREND_GATE + SM consensus >=70%, MIN_SCORE 12, score-scaled
  sizing (50%/70%/80%), 10x leverage cap, 6-tier DSL ladder from
  Kodiak SOL empirical wins. v4.0.1 ships a race-window dedup cache
  that eliminates the ENGINE_FAILURE retry noise on already-held
  assets, plus a doubled DSL exit interval to throttle REDUCE_ONLY
  spam when runtime position-state lags HL.
license: MIT
metadata:
  author: jason-goldberg
  version: "4.0.1"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🦅 CONDOR v4.0.1 — One Amazing Trade per Day

Top 50 assets. Pure trend continuation. Apex confluence only. One trade a day.

## v4.0.1 (2026-05-18) — held-asset dedup race-fix + DSL throttle

Operational reliability patch. NO thesis change. NO scoring change.
NO gate change. Direct port of the Bison v3.0.1 patches to address
the same race-window + position-state-drift bug class observed on
Condor's decision log.

**Bug observed.** Audit on Condor2 (M193171) 2026-05-14 to
2026-05-17 showed **4 consecutive `CONDOR_APEX HYPE LONG`
ENGINE_FAILUREs**, all for HYPE LONG signals fired while HYPE was
already held by the runtime executor. The producer's on-chain
`held_assets` check leaks during the race window between
`push_signal()` returning OK and the resulting position appearing
in the next-tick `clearinghouseState` pull. Same bug class Bison
v3.0.1 fixed; same fix here.

**Fix 1 — recent-signals cache (`scripts/condor_config.py`).**
`record_signal(coin)` writes `{coin: epoch_seconds}` to
`state/recent-signals.json` after every successful push. `main()`
calls `was_recently_signaled(coin)` after candidate-best selection
and BEFORE `push_signal`, skipping emission for any coin seen
within `RECENT_SIGNAL_TTL_SEC` (default 240s ≈ 4× the typical ALO
open-fill window + next-tick cadence). Skipped coins are reported
with `DEDUP_SKIP` notes in the per-tick output for audit.

**Fix 2 — DSL exit interval 30s → 60s (`runtime.yaml`).**
Defensive mitigation of the runtime position-state drift bug seen
in Bison's audit (`CLOSE_FEE_OPTIMIZED_FAILED "Reduce only order
would increase position"` errors firing 1-2 ticks after a
successful close). Condor's close-side audit shows no such failures
yet, but the bug is fleet-wide — applying the throttle here
proactively. **Root cause (runtime-side position-state sync) is
escalated to the runtime team** — this YAML change is downstream
mitigation only.

**What this does NOT fix.** Phantom orphan positions where DSL
state and HL state are durably out of sync. That class of bug
needs runtime-side reconciliation; the producer has no authority
over DSL state.

## v4.0.0 (2026-05-12) — plumbing-only migration

NO thesis change. v3.4 scoring tables, hard gates, score-scaled sizing tiers, and DSL preset all preserved verbatim. Six-layer plumbing flip:

1. **MCP transport**: `mcporter` subprocess (2.5-5s cold-start per call) → `senpi_runtime_helpers.SenpiClient.mcp_call()` in-process HTTPS (~280ms).
2. **Signal emit**: scanner called `create_position` directly; producer now emits via `push_signal()` to runtime `/signals`. Runtime LLM-gated `condor_entry` action opens via FEE_OPTIMIZED_LIMIT, passing `marginUsd` + `leverage` through from signal data for score-tier sizing.
3. **Reentrancy**: no v3 lockfile to replace (Condor v3.x ran on openclaw cron with no producer-side lock); `producer_daemon` owns the per-tick `scanner_lock` with stale-PID auto-recovery.
4. **Scheduler**: openclaw cron (3 min) → `producer_daemon(interval_seconds=180)`. Long-lived process.
5. **Risk gates**: Python `MAX_POSITIONS`, dynamic daily cap (1 healthy / 0 at -25%), post-exit cooldown state files → declarative `risk.guard_rails`. `state/trade-counter.json` is vestigial in v4.0 (only the `last_entry_ts` field stays for legacy log compat; cooldown enforcement moves to runtime).
6. **Exit fee**: DSL exits switched from MARKET (taker, 0.045%) to FEE_OPTIMIZED_LIMIT (maker-first, 0.015%, 60s ALO timeout, taker fallback). Entries keep `ensure_execution_as_taker: false` per v3.x patience rule.

## ⛔ CRITICAL AGENT RULES

### RULE 1: Install path is `/data/workspace/skills/condor-strategy/`
### RULE 2: THE PRODUCER DOES NOT EXIT POSITIONS — DSL only.
### RULE 3: MAX 1 POSITION (the one amazing trade)
### RULE 4: Verify runtime + daemon on every session start
### RULE 5: Never modify scoring or gate parameters
### RULE 6: MAX 1 ENTRY per 24h (runtime `max_entries_per_day=1`)
### RULE 7: 120-min post-exit cooldown (runtime `per_asset_cooldown_minutes=120`)
### RULE 8: HARD_STOP circuit breaker at -25% drawdown (runtime `drawdown_halt_pct=25`)

## Thesis

**Pure trend continuation, never counter-trend.**

From Kodiak's lifetime top 3 winners (all SOL, +$133 / +$87 / +$78):

> "The absolute highest predictor of a massive directional swing is when
> the 4H, 1H, 15m, and 5m price momentum are perfectly unified in a
> single direction, AND the Smart Money leaderboard is heavily lopsided
> (>65% directional consensus) in that exact same direction."

From Wolverine's HYPE SHORT post-mortem (-$160 loss, 2026-04-16):

> "We stepped in front of a runaway 32% freight train to catch a 1%
> micro-retrace. The historical winners traded with the massive macro
> shift, not against it."

Condor enforces both insights as hard gates.

## Hard gates (all must pass — fail = skip asset)

1. **Not XYZ, not stablecoin**
2. **OI > $1M USD** (context-aware read)
3. **trader_count >= 50** (signal validity)
4. **3TF ALIGNMENT** — 4h_price + 1h_price + 15m SM velocity all aligned in entry direction, each clearing magnitude threshold (4h ≥ 1.0%, 1h ≥ 0.3%, 15m velocity ≥ 0.1)
5. **MACRO TREND GATE** — if `|4h_move| > 10%` in OPPOSITE direction of entry, BLOCK
6. **SM consensus >= 70%** in entry direction (v3.4 calibration)

## Scoring (max ~18 pts, MIN_SCORE = 12 since v3.4)

| Signal | Points |
|---|---:|
| 4h move magnitude: >2% (+2), >4% (+3), >6% (+4) | 1-4 |
| 1h confirmation: >0.5% (+1), >1% (+2) | 1-2 |
| 15m SM velocity: >1.0 (+1), >2.0 (+2) | 1-2 |
| 3TF_ALIGNED bonus | +3 |
| SM consensus: >=70% (+2), >=75% (+3), >=80% STRONGLY_TILTED (+4) | 2-4 |
| trader_count >= 100 (DEEP_CONSENSUS) | +1 |
| Funding pays direction | +1 |
| BTC macro confirms (aligned, >1.5%) | +1 |
| Peak session (13-19 UTC or 00-05 UTC) | +1 |

## Position sizing (score-scaled, 10x leverage cap)

| Score | Leverage | Margin |
|---|---:|---:|
| 11-12 | 10x | 50% of equity |
| 13-14 | 10x | 70% of equity (HIGH) |
| **15+ (APEX)** | **10x** | **80% of equity** |

**Never above 10x leverage.** Both Kodiak and Wolverine confirmed 10x is the empirical ceiling — above this, fees + wick vulnerability destroy the edge.

Leverage auto-clamped to per-asset Hyperliquid max via `strategy_get_asset_trading_limits`.

Producer computes `marginUsd` from score tier; signal data carries it; LLM gate passes through to runtime payload.

## DSL preset (mid-beta default, preserved from v3.3+/v3.4)

| Mechanism | Value | Rationale |
|---|---|---|
| hard_timeout | 1440 min (24h) | One trade per day discipline |
| weak_peak_cut | **DISABLED** | v3.3: apex thesis needs 4-8h to develop; 2h @ 3% was pre-empting |
| dead_weight_cut | 60 min | No movement = dead |
| Phase 1 max_loss | 20% | Same level that killed Wolverine's HYPE SHORT — but that was counter-trend. Kodiak's SOL trades didn't hit Phase 1. |
| Phase 1 retrace | 8% | |
| Phase 2 tier 1 | +8% / 30% HW lock | Kodiak's Tier 1 |
| Phase 2 tier 2 | +15% / 50% | Tier 2 |
| Phase 2 tier 3 | +25% / 70% | Kodiak's #3 exit tier |
| Phase 2 tier 4 | +40% / 85% | Kodiak's #1/#2 exit tier |
| Phase 2 tier 5 | +60% / 90% | Infinite trail on monster winners |
| Phase 2 tier 6 | +100% / 94% | Monster-class winners |

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting | Replaces |
|---|---|---|
| max_entries_per_day | 1 | v3.x dynamic daily cap |
| per_asset_cooldown_minutes | 120 | v3.x `POST_EXIT_COOLDOWN_MINUTES` |
| daily_loss_limit_pct | 15 | new (fleet-standard) |
| max_consecutive_losses | 3 | new (fleet-standard) |
| cooldown_minutes (post-loss) | 120 | new (fleet-standard) |
| drawdown_halt_pct | 25 | v3.x `get_dynamic_daily_cap()` hard-stop |
| drawdown_reset_on_day_rollover | false | fleet-standard (Roach lesson) |

## Hardcoded constants (not configurable)

- MAX_LEVERAGE: 10
- MIN_OI_USD: 1,000,000
- MIN_TRADER_COUNT: 50
- XYZ_BANNED: true
- STABLECOINS_BANNED: USDT, USDC, DAI, USDE, FDUSD, TUSD, BUSD
- UNIVERSE_SIZE: 50

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer daemon** (entry path) and the **DSL ratchet engine** (exit path). User-conversation sessions are **read-only**.

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v3.4.

## Changelog

### v4.0.0 (2026-05-12) — plumbing-only migration
Migrated to helpers-native (`senpi_runtime_helpers`). Thesis preserved verbatim from v3.4.

### v3.4 (2026-05-05) — gate calibration fix
- v3.2 over-tightening produced ZERO trades in 13 days. MIN_SCORE 13→12, MIN_SM 75→70, MIN_15M_VELOCITY 0.2→0.1.

### v3.3 — disable weak_peak_cut
- 2h @ 3% ROE was pre-empting apex thesis development.

### v3.2 (2026-04-22) — gate tightening (reverted in v3.4)

### v3.1 (2026-04-16) — BTC hard-gate removed
- BTC macro alignment moved from hard gate to scoring bonus only (HYPE/HIP-3 decoupling).

### v3.0 (2026-04-16) — COMPLETE REWRITE
- Thesis flipped: multi-asset thesis picker → single-position trend-continuation apex sniper
- Universe expanded: 4 majors → top 50 HL assets by 24h volume
- Added 3TF_ALIGNMENT hard gate + MACRO_TREND_GATE
- Sizing scales with score; leverage hard-capped at 10x
- DSL profile calibrated from Kodiak's SOL winner exit tiers
- ONE TRADE PER DAY discipline

## License

MIT — Built by Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
