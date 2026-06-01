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
  version: "7.2.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐆 CHEETAH v7.2.0 — Multi-Signal Confluence Sniper

**SM commits. Quality traders commit. Price confirms. Volume commits. All at once. Cheetah pounces once. The runtime DSL ratchets to lock the win.**

## v7.2.0 (2026-06-01) — DSL exit overhaul: disable time cuts, let winners run

**Entries are fine. Exits were the bottleneck.** A 100-trade-by-score
analysis on the live wallet showed win rate scaling cleanly with score —
40.0% @ score 10, 41.7% @ score 11 (n=84), 50.0% @ score 12, 55.6% @ score
13 — confirming the accuracy filter works. But **Avg ROE was negative across
EVERY score bracket** (−1.90% / −0.35% / −0.20% / −0.12%).

| Score | Trades | Win rate | Avg ROE |
|---|---:|---:|---:|
| 10 | 5 | 40.0% | −1.90% |
| 11 | 84 | 41.7% | −0.35% |
| 12 | 2 | 50.0% | −0.20% |
| 13 | 9 | 55.6% | −0.12% |

**Diagnosis: "death by a thousand cuts."** The tight Phase 2 locks plus the
60m/90m time-cuts made it mechanically impossible to hold for a big win —
Cheetah took the full brunt of −15% stops / −5% chops but was forced to give
up every +15% / +30% runner. The two big HYPE longs that actually paid
(+13.9%, +8.9%) only landed because they hit the **12h hard timeout** — not a
trailing stop, not a 60m dead_weight_cut. When forced to hold, the position
printed.

**Fix (mirrors the live deploy):**
- `weak_peak_cut` → **disabled** (was 90m @ 3.0 — chopping winners-in-waiting)
- `dead_weight_cut` → **disabled** (was 60m — forcing exits before trenders accelerate)
- `hard_timeout` → **kept** (12h fail-safe — the one cut that captured the winners)
- Phase 2 ladder already widened to the "let winners run" shape (T0 +10% / lock 0 → +100% / lock 85) on 2026-05-21

Together this removes the glass ceiling so heavy trenders can run. NO change
to scoring, MIN_SCORE, leverage tiers, margin, cooldowns, or risk gates —
exit-mechanics only. Hypothesis test; re-evaluate after ~50 more trades.

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

**Max score: 15. v6.0 floor: 10.** Universe is the top 100 SM leaderboard (`leaderboard_get_markets`), refreshed every producer tick. XYZ permanently banned.

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
- v6.0: `cheetah-producer.py` emits signals via `SenpiClient.push_signal()` (direct HTTP POST). Runtime LLM gate (decision_mode: llm) is pass-through. Risk gates declarative in `runtime.yaml`.

### MIN_SCORE 11 → 10
v5.2 tightened MIN_SCORE 10 → 11. Result: 8 consecutive days of zero trades. Pre-tightening (v5.0/v5.1 at MIN_SCORE 10) produced **+$182 net across 11 trades** with 60%+ win rate. v6.0 restores the trade flow.

### FEE_OPTIMIZED_LIMIT on EXITS too
v5.x used MARKET (taker) closes. v6.0's DSL fires `FEE_OPTIMIZED_LIMIT` on every exit — maker-first, 60s ALO timeout, taker fallback. HL maker is 0.015% vs taker 0.045% = 0.020-0.030% saved per close.

### Held-asset dedup (3-layer defense)
1. Producer pre-filter: `held_assets` from clearinghouse, drops candidates already held
2. LLM gate hard skip: `heldAssets` array in signal payload, LLM rejects if asset is held
3. Runtime `per_asset_cooldown_minutes: 240` (known to silently not-enforce; layer 1+2 are the real defense)

### Post-close cooldown (producer-side backstop)
Diff `held_assets` across ticks. When asset disappears from held set, record close timestamp. Producer skips emission for 240 min after any close. Backstops the runtime cooldown bug.

### Reentrancy guard
`producer_daemon` owns a per-tick `scanner_lock` with stale-PID auto-recovery — replaces v6.x's hand-rolled fcntl lockfile. Prevents two ticks racing.

## DSL preset (v7.2.0 — "let winners run" ladder, time cuts disabled)

| Phase | Component | Setting |
|---|---|---|
| Phase 1 | max_loss_pct | 15% |
| Phase 1 | retrace_threshold | 10 (v6.x raise from 6) |
| Phase 1 | consecutive_breaches | 1 |
| Phase 2 T0 | trigger 10% / lock 0% | (lets a +10% mover retrace fully before any lock) |
| Phase 2 T1 | trigger 20% / lock 25% | |
| Phase 2 T2 | trigger 30% / lock 40% | |
| Phase 2 T3 | trigger 50% / lock 60% | |
| Phase 2 T4 | trigger 75% / lock 75% | |
| Phase 2 T5 | trigger 100% / lock 85% | (apex) |
| hard_timeout | 720 min (12h) | **enabled** (the only time cut left) |
| weak_peak_cut | — | **disabled** (v7.2.0) |
| dead_weight_cut | — | **disabled** (v7.2.0) |

v7.2.0: weak_peak_cut + dead_weight_cut disabled. The 100-trade analysis
showed the time cuts were the bottleneck — they forced exits before
trenders could accelerate and capped the right-tail move that pays for
everything. The two big HYPE wins (+13.9%, +8.9%) only landed via the 12h
hard timeout, so that fail-safe is kept. Median trade gives back a bit;
the right tail is now uncapped.

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

- ✓ **Held-asset dedup** (3-layer: producer pre-filter, LLM gate, runtime per_asset_cooldown)
- ✓ **Post-close cooldown** (producer-side backstop for the runtime cooldown silent-enforcement bug)
- ✓ **Fleet-standard T0/T1 ladder**
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

These tools are reserved for the **producer daemon** (entry path) and the **DSL ratchet engine** (exit path). User-conversation sessions are **read-only**.

If the user asks a question that implies action ("anything close to triggering?"), respond by reading the current state — DO NOT execute. The producer daemon will handle real signals on its next tick.

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai)
