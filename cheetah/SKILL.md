---
name: cheetah-strategy
description: >-
  CHEETAH v7.0.0 — Multi-signal confluence sniper, senpi_runtime_helpers
  migration. Plumbing-only flip from openclaw-CLI subprocess + mcporter
  subprocess to in-process SenpiClient (direct HTTPS for MCP, direct
  HTTP POST to runtime /signals, long-lived producer_daemon). Thesis
  preserved verbatim from v6.1: refuses to trade unless ALL major
  signals align — SM consensus, velocity, acceleration, dual price
  confirmation, volume spike, quality-trader alignment, rank climb.
  Score 10/15 floor, score-scaled leverage (3x/5x/7x/8x),
  FEE_OPTIMIZED_LIMIT entries AND exits, held-asset dedup, post-close
  cooldown.
license: MIT
metadata:
  author: jason-goldberg
  version: "7.1.2"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=2.0.0
---

# 🐆 CHEETAH v7.1.0 — Multi-Signal Confluence Sniper

**SM commits. Quality traders commit. Price confirms. Volume commits. All at once. Cheetah pounces once. The runtime DSL ratchets to lock the win.**

## v7.1.1 (2026-05-11) — positions parser fix (patch)

Live verification of v7.1.0 found `positions_map` was STILL empty even though the widened filter returned the full 25-trader pool. Root cause: `leaderboard_get_trader_positions` actually returns

```
{ "data": { "positions": { "trader_id": ..., "rank": ..., "positions": [...] } } }
```

— the per-trader positions array is nested one level deeper than the schema guide documents. The v7.0/v7.1 parser looked at `data.positions` expecting a list, got a dict, hit `if not isinstance(positions, list): continue` and silently skipped every trader.

v7.1.1 parser handles BOTH shapes:
- list-direct (matches schema guide)
- nested-dict `{trader_id, rank, positions: [...]}` (actual prod response)

Plus a `POSITIONS_SHAPE_WARN` log line for any future schema drift.

NO change to scoring components, MIN_SCORE, leverage tiers, margin, cooldowns, or DSL. Same thesis. Now the +3 QUALITY_TRADER bonus can actually fire when the per-asset overlap exists.

## v7.1.0 (2026-05-11) — quality-trader filter widened (patch)

Post-migration diagnostic on the live wallet revealed `quality-cache.json` was always empty (`positions_map = {}`). Root cause: `discovery_get_top_traders(time_frame=WEEKLY, consistency=[ELITE,RELIABLE], open_position_filter=True, limit=8)` returned **zero traders** under the current platform regime. With no quality-trader pool, the +3 `QUALITY_TRADER` scoring bonus never fires; candidates cap at score 8 (needs 4-of-4 secondary confluence to reach the MIN_SCORE=10 floor without that bonus), and that 4-of-4 only hits in strong-trending regimes.

Filter changes (live-verified):
- `time_frame: WEEKLY → MONTHLY` — broader history, more traders accumulate ELITE/RELIABLE labels
- `consistency: [ELITE, RELIABLE] → [ELITE, RELIABLE, STREAKY]` — slight loosen at the lower end
- `limit: 8 → 25` — wider pool surfaces more per-asset overlap
- `open_position_filter` dropped — the per-trader `leaderboard_get_trader_positions` fetch already filters flat traders implicitly via empty position arrays

NO change to scoring components, weights, MIN_SCORE, leverage tiers, margin, cooldowns, or DSL. Same thesis, wider input.

Telemetry: producer now emits a `QT_POOL_WARN` log line whenever the trader pool is unhealthily small (< QT_POOL_SIZE / 2) or positions_map ends up empty. Future regressions surface in one grep instead of post-hoc archaeology.

## v6.0 thesis (preserved from v5.x)

Refuse to trade unless ALL major signals align simultaneously:

| Component | Signal | Pts |
|---|---|---|
| SM consensus | pct_of_top_traders ≥ 10%, traders ≥ 25 | +4 (hard gate) |
| Velocity | 15m contrib ≥ 1.0 OR 1h contrib ≥ 3.0 | +2 (hard gate) |
| Accelerating | 15m > 1h > 0 (SM building) | +2 |
| Dual price | 4h move ≥ 2% AND 1h aligned same direction | +2 |
| Volume | current ≥ 2x 6h avg | +1 |
| Quality trader | ≥ 1 ELITE/RELIABLE positioned same direction | +3 |
| Rank climb | ≥ 5 positions in last 2 scans, top 15 | +1 |

**Max score: 15. v6.0 floor: 10.** Universe is the top 100 SM leaderboard (`leaderboard_get_markets`), refreshed every cron tick. XYZ permanently banned.

Score-scaled leverage:

| Score | Leverage |
|---|---|
| 10 | 3x |
| 11 | 5x |
| 12-13 | 7x |
| 14-15 | 8x |

Leverage is clamped to each asset's Hyperliquid max via `strategy_get_asset_trading_limits` (preserves v5.1.1 leverage safety fix).

## v6.0 architectural changes

### v1 scanner → v2 producer
- v5.x: `cheetah-scanner.py` scored signals, called `create_position` directly, maintained Python-side cooldowns/counters/resting-order guards
- v6.0: `cheetah-producer.py` emits signals via `external-scanner ingest`. Runtime LLM gate (decision_mode: llm) is pass-through. Risk gates declarative in `runtime.yaml`.

### MIN_SCORE 11 → 10
v5.2 tightened MIN_SCORE 10 → 11. Result: 8 consecutive days of zero trades. Pre-tightening (v5.0/v5.1 at MIN_SCORE 10) produced **+$182 net across 11 trades** with 60%+ win rate. v6.0 restores the trade flow.

### FEE_OPTIMIZED_LIMIT on EXITS too
v5.x used MARKET (taker) closes. v6.0's DSL fires `FEE_OPTIMIZED_LIMIT` on every exit — maker-first, 60s ALO timeout, taker fallback. HL maker is 0.015% vs taker 0.045% = 0.020-0.030% saved per close.

### Held-asset dedup (3-layer defense)
1. Producer pre-filter: `held_assets` from clearinghouse, drops candidates already held
2. LLM gate hard skip: `heldAssets` array in signal payload, LLM rejects if asset is held
3. Runtime `per_asset_cooldown_minutes: 240` (known to silently not-enforce; layer 1+2 are the real defense)

### Post-close cooldown (Pangolin v2.1.2 pattern)
Diff `held_assets` across ticks. When asset disappears from held set, record close timestamp. Producer skips emission for 240 min after any close. Backstops the runtime cooldown bug.

### Reentrancy lockfile
fcntl-based lock at `state/<wallet-hash>/producer.lock`. Prevents two cron runs racing.

## DSL preset (v6.0 — fleet-standard T0/T1 ladder)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 6 |
| Phase 1 | consecutive_breaches | 3 |
| Phase 2 T0 | trigger 5% / lock 35% | (fleet-standard, closes T0→T1 dead zone) |
| Phase 2 T1 | trigger 10% / lock 50% | |
| Phase 2 T2 | trigger 20% / lock 65% | |
| Phase 2 T3 | trigger 35% / lock 80% | |
| Phase 2 T4 | trigger 50% / lock 90% | (apex) |
| hard_timeout | 720 min (12h) | enabled |
| weak_peak_cut | 90 min, min 3.0 | enabled |
| dead_weight_cut | 60 min | enabled |

Time cuts kept: Cheetah is multi-asset rotation, NOT single-asset patience. Idle positions have opportunity cost.

## Risk gates (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting |
|---|---|
| daily_loss_limit_pct | 25% |
| max_entries_per_day | 8 |
| max_consecutive_losses | 3 |
| cooldown_minutes (post-loss) | 30 |
| drawdown_halt_pct | 25% |
| **drawdown_reset_on_day_rollover** | **false** (Roach Sat-Sun bleed lesson) |
| per_asset_cooldown_minutes | 240 (4h) |

## Operator install

See [README.md](README.md) for fresh-install + migration commands from v5.2.

## Fleet patches incorporated

- ✓ **Held-asset dedup** (3-layer; v2.1 Pangolin pattern)
- ✓ **Post-close cooldown** (v2.1.2 Pangolin pattern; runtime cooldown backstop)
- ✓ **Fleet-standard T0/T1 ladder** (commit 6cad383 pattern)
- ✓ **Reentrancy guard** (v7.0.0: producer_daemon scanner_lock with stale-PID auto-recovery; replaces hand-rolled fcntl)
- ✓ **Wallet-from-config** (no hardcoding; senpi-skills is public)
- ✓ **drawdown_reset_on_day_rollover: false** (Roach lesson)
- ✓ **senpi_runtime_helpers** (v7.0.0: in-process MCP + signal POST; replaces mcporter + openclaw-CLI subprocess)
- ✗ **FP-001 quiet hours** — deferred until fleet telemetry can validate the time-of-day P&L correlation

## Hard rule for user-conversation Claude sessions

User-conversation Claude sessions MUST NOT call any of:
`create_position`, `close_position`, `edit_position`,
`ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
`cancel_order`, `strategy_close`, `strategy_close_positions`.

These tools are reserved for the **producer cron** (entry path) and the **DSL ratchet engine** (exit path). User-conversation sessions are **read-only**.

If the user asks a question that implies action ("anything close to triggering?"), respond by reading the current state — DO NOT execute. The producer cron will handle real signals on its next tick.

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
