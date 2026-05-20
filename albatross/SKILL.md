---
name: albatross-strategy
description: >-
  ALBATROSS v1.0.0 — Multi-week Arena conviction tracker. Mirrors trades
  from Senpi Arena participants whose ROE has been consistently strong
  across the last 4 weekly periods + the current monthly leaderboard.
  Composite conviction score (0.3 × monthly + 0.7 × weekly_mean − 0.5 ×
  weekly_stdev) selects the top-N pool; producer detects when a leader
  opens a new position and mirrors it. Wide Bison-pattern DSL ladder
  with a 96h hard timeout cap.
license: MIT
metadata:
  author: jason-goldberg
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime>=1.1.0
    - senpi_runtime_helpers
---

# 🐦‍⬛ ALBATROSS v1.0.0 — Multi-week Arena Conviction Mirror

**Mirror the consistent winners, not the lucky-week winners.** Albatross selects Arena leaders by a composite score that rewards multi-week persistence and penalizes one-shot lucky weeks.

## Why this strategy exists

The Senpi Arena resets every Thursday. A trader can post a +98% weekly ROE from one good week and three flat weeks — and they'll top the leaderboard, but mirroring them is a survivor-bias trap.

Albatross corrects for this by pulling **4 weekly snapshots + 1 monthly snapshot** and ranking by a composite that respects both magnitude and consistency:

```
conviction = 0.3 × monthly_roe
            + 0.7 × mean(weekly_roe across last 4 weeks)
            − 0.5 × stdev(weekly_roe)
```

A trader posting +30%/+25%/+20%/+15% across 4 weeks (mean +22.5%, stdev ±5.6%) scores **conviction ≈ 14.0**.
A trader posting +98%/0%/-5%/-3% (mean +22.5%, stdev ±42.5%) scores **conviction ≈ -5.8** despite the same mean. Albatross rejects the second; mirrors the first.

## CRITICAL RULES

### RULE 1: REQUIRES USER-SCOPE AUTH TOKEN
Service tokens cannot resolve other users' strategy wallets or call `discovery_get_trader_state` for other addresses. Both gate on user identity. **The operator must provide a Privy-issued Senpi user token via `SENPI_AUTH_TOKEN`**, not an MCP service key.

### RULE 2: Producer mirrors. DSL exits.
The producer detects when a leader opens a NEW position (diff against last-tick snapshot in `state/leader-positions/`) and emits one signal per detection. The runtime opens via FEE_OPTIMIZED_LIMIT. The DSL Phase 1 max_loss + Phase 2 wide ratchet manages exits.

### RULE 3: Leader pool refreshes every 4h, not every tick
Pulling 4 weekly leaderboards + 1 monthly on every 300s tick would burn MCP quota. The pool is cached in `state/leader-pool.json` with a `leaderRefreshHours` TTL (default 4). Only the per-leader position snapshots refresh per tick.

### RULE 4: Exclude betashop xHandle by default
Senpi's own fleet agents (Bison12, Cheetah, Jaguar2, etc.) often top the Arena. Mirroring them creates a feedback loop — Albatross would just be a slower copy of those agents. Config `excludeXHandles: ["betashop"]` filters them out. Operators can add other handles to skip.

### RULE 5: 96h hard timeout cap
We don't have visibility into the leader's exit logic. Hard timeout 96h (4 days) frees the slot if the position drifts without resolution. Phase 1 max_loss 20% catches catastrophic adverse moves; Phase 2 wide ladder lets winners run within the 96h window.

## How Albatross composes its leader pool

1. Pull `arena_leaderboard(period_type=WEEK, qualified=true)` for the current week + last 3 weeks.
2. Pull `arena_leaderboard(period_type=MONTH, qualified=true)` for the current month.
3. For each unique senpiUserId across those 5 calls:
   - Compute `mean(weekly_roe)` and `stdev(weekly_roe)` across the weeks the trader appeared.
   - Look up monthly_roe (default 0 if absent).
   - `conviction = 0.3 × monthly + 0.7 × weekly_mean − 0.5 × weekly_stdev`.
4. Filter to traders with:
   - `conviction > 0` (no negative-expectation leaders)
   - `weeks_traded >= 3` (default, ensures persistence)
   - Each week's `notionalVolume >= 50000` (default, no toy accounts)
   - `xHandle != "betashop"` (no Senpi fleet self-mirror)
5. Top N by conviction → pool (default 5).

## How Albatross detects new positions

Per tick (every 300s):
1. For each leader in the pool, call `strategy_list({userIds: [senpiUserId], status: ["ACTIVE"]})` → list of strategy wallet addresses.
2. Call `discovery_get_trader_state(trader_addresses=wallets, latest=true)` → current open positions.
3. Compare to last-tick snapshot in `state/leader-positions/<senpiUserId>.json`. Detect coins/directions present now but not in last snapshot.
4. For each NEW position: emit `ALBATROSS_ARENA_MIRROR` signal with `asset`, `direction`, our sized `marginUsd`, and our capped `leverage`.

## Mirror logic — what we copy and what we adapt

| Field | Adapt |
|---|---|
| Asset | **Copy exactly** (BTC, ETH, NVDA, BRENTOIL, whatever they opened) |
| Direction | **Copy exactly** |
| Leverage | **Cap at config.leverage (default 5x, max 5x)** — we don't follow into 25x leverage |
| Sizing | Fixed `marginPct * accountValue` (default 15%) — we don't size proportional to their conviction |
| Order type | FEE_OPTIMIZED_LIMIT, maker-first 30s ALO |
| TP/SL | Skip theirs, use our DSL |

## Risk guardrails (`runtime.yaml` `risk.guard_rails`)

| Gate | Setting | Why |
|---|---|---|
| max_entries_per_day | 5 | Multiple leaders fire — give the producer room |
| per_asset_cooldown_minutes | 120 | Don't re-mirror same asset within 2h |
| daily_loss_limit_pct | 15 | Cap a bad day |
| max_consecutive_losses | 4 | Halt if leader pool is wrong for current regime |
| cooldown_minutes (post-loss) | 30 | Cool off briefly |
| drawdown_halt_pct | 20 | Catastrophic floor |
| drawdown_reset_on_day_rollover | false | No grace |

## Scanner pattern

This strategy uses the **Trader follower (Jackal family)** scanner pattern with multi-week selection — see `senpi-trading-runtime/references/producer-patterns.md` for the canonical reference. Primary MCP calls: `arena_leaderboard` (×5 for pool composition), `strategy_list` (per-leader wallet resolution), `discovery_get_trader_state` (per-leader position fetch).

## Operator install

See [README.md](README.md). **Pay close attention to RULE 1 — service tokens will not work.**

## Changelog

### v1.0.0 (2026-05-20) — initial release

First multi-week Arena conviction mirror. Composite score weighting (0.3 monthly + 0.7 weekly_mean − 0.5 weekly_stdev) replaces the naive "mirror this week's #1" approach. Default pool size 5, refresh every 4h, max 5 entries/day.

## License

MIT — Copyright 2026 Senpi (https://senpi.ai).

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.
