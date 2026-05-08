# Cross-Asset Flow Tool — Mantis Integration Guide

This document explains how Mantis interprets the `market_get_cross_asset_flows` MCP tool's output. For the tool's full release notes, see the Cross-Asset Flow Detection release.

## The tool's output shape

```json
{
  "leader_asset": "BTC",
  "leader_move_pct": 3.2,           // 4h price change
  "computed_at": "2026-04-22T13:00:00Z",
  "laggards": [
    {
      "asset": "SOL",
      "follow_rate": 0.91,
      "avg_lag_minutes": 47,
      "lag_stddev_minutes": 22,
      "gap_pct": 1.8,
      "sm_starting_to_rotate": true,
      "confidence": 0.86,
      "correlation_30d": 0.79
    }
    // ... up to 10 laggards
  ]
}
```

## Field-by-field interpretation

### `leader_move_pct`
The leader's 4h price change. If absolute value < ~2%, the laggards array is empty (this is correct — Mantis only fires on confirmed leader moves).

### `follow_rate` (0.0 – 1.0)
How often this alt has historically followed the leader's direction within `avg_lag_minutes × 2`.
- **0.85+:** strong, predictable follower → eligible
- **0.70–0.85:** moderate → too noisy for Mantis v5.0
- **<0.70:** filtered out at the tool level usually

Mantis floor: **0.85**.

### `avg_lag_minutes`
The historical median time between leader move and the alt's catchup move.
- Used to compute Mantis's dynamic hard_timeout: `avg_lag_minutes × 1.5`
- Clamped to [30, 240] so very fast (sub-30min) and very slow (>4h) alts both get sane bounds

### `lag_stddev_minutes`
How consistent the timing is. Lower = more predictable.
- **<30 min:** very tight timing → ideal
- **30–60 min:** acceptable
- **60–90 min:** marginal but Mantis still trades
- **>90 min:** filtered out — too unpredictable

### `gap_pct`
Expected catchup move minus actual move so far. The opportunity size.
- Positive value when leader moved up and alt hasn't caught up → LONG opportunity
- Negative when leader moved down and alt hasn't caught down → SHORT opportunity
- Mantis floor: |gap_pct| ≥ **1.5%**

### `sm_starting_to_rotate` (bool)
Smart-money positioning is starting to enter the alt. This is the key confirmation signal — without it, the catchup thesis is purely statistical; with it, smart money is voting on the same trade.

Mantis **requires this to be true** for any entry. This is the most important filter — without SM rotation, false positives are too high.

### `confidence` (0.0 – 1.0)
Composite of correlation, follow_rate, lag consistency, and SM alignment. Mantis uses this for sizing tiers:
- **0.92+:** 75% margin, 8x leverage
- **0.85–0.92:** 50% margin, 7x leverage
- **0.75–0.85:** 25% margin, 5x leverage
- **<0.75:** filtered out

### `correlation_30d`
30-day correlation coefficient. Informational; Mantis doesn't directly filter on it (the tool already factors it into `confidence`).

## Empty laggards is normal

Per the tool's release notes, an empty `laggards` array means the leader hasn't moved enough to qualify. This is correct behavior. Mantis emits `NO_ENTRY` with reason `no_qualifying_laggards` and waits for the next 60s tick.

## BTC-only in v1

The tool only has pre-computed lag data for BTC at release. Passing other leaders works for correlation, but `avg_lag_minutes`, `lag_stddev_minutes`, and `follow_rate` will be zero (or omitted) — which means those leaders' laggards will fail Mantis's entry filters automatically.

When Sarvesh ships pre-computed lag data for ETH/SOL/HYPE, expand `LEADER_UNIVERSE` in `mantis_config.py`. No other code changes required.

## Warmup period

After a fresh deploy of the cron job, price snapshots accumulate over time. The 4h window needs ~4 hours before `leader_move_pct` shows real values. During warmup, Mantis sees an empty `laggards` array and emits `NO_ENTRY` cleanly. No special handling needed.

## The leader-reversal veto loop

Mantis re-calls `market_get_cross_asset_flows(leader_asset=...)` on every 60s scan tick to refresh the current `leader_move_pct`. For any open position, if the current leader move has reversed by >1% from the move-at-entry, Mantis closes immediately.

Example:
- Entry: BTC was at +3.2%, Mantis took SOL long
- 30 min later: BTC is now at +1.9% (a 1.3% reversal against entry direction)
- Mantis closes SOL — the leader's move has died, the catchup thesis is dead

This veto prevents Mantis from holding losing positions through a leader reversal, which is the most common failure mode for cross-asset catchup strategies.
