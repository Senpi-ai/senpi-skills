---
name: mantis-strategy
description: >-
  MANTIS v5.0 — Slipstream. Cross-asset catchup hunter built on the
  market_get_cross_asset_flows MCP tool. When BTC (or another leader)
  makes a significant 4h move and a correlated alt hasn't responded
  yet, Mantis strikes the alt before the catchup completes. Trades
  the statistical lag, not the momentum itself. Hard veto if the
  leader reverses mid-position. First Predator built around cross-
  asset flow detection.
license: MIT
metadata:
  author: jason-goldberg
  version: "5.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# 🦎 MANTIS v5.0 — Slipstream

The mantis is patience plus speed. Stillness, then strike.

## What Mantis does

Mantis monitors the cross-asset flow tool every 60 seconds. When a leader (BTC, eventually ETH/SOL/HYPE) makes a significant 4h move (>2%), the tool surfaces correlated alts that *historically follow that leader* but haven't yet responded. Mantis filters those laggards by strict criteria, picks the highest-confidence one, and opens a position in the direction the leader moved — sized by confidence, with a hard timeout calibrated to the alt's typical lag distribution.

## The thesis

Pure statistical edge with quantified time bounds:

- **Edge:** alts that follow a leader 85%+ of the time, with low timing variance, are likely to catch up after a leader move
- **Timing:** the alt's historical `avg_lag_minutes` defines the trade's window
- **Exit:** the catchup either happens within `avg_lag × 1.5` minutes — or it doesn't happen at all
- **Veto:** if the leader reverses mid-position, the entire thesis is invalidated and Mantis closes immediately

## Entry criteria (all must pass)

| Filter | Threshold | What it ensures |
|---|---|---|
| `follow_rate` | ≥ 0.85 | Alt historically follows leader 85%+ of moves |
| `confidence` | ≥ 0.75 | Composite of correlation + follow + lag + SM alignment |
| `gap_pct` | ≥ 1.5% (abs) | Catchup opportunity is meaningful |
| `sm_starting_to_rotate` | true | Smart money is confirming the catchup |
| `lag_stddev_minutes` | ≤ 90 | Timing is predictable enough to trade |
| Asset | not in cooldown | 4-hour per-asset cooldown after any strike |
| Asset | not already open | No doubling up on same asset |

## Sizing — conviction tiers

Sizing scales directly off the `confidence` score:

| Confidence | Margin % | Leverage |
|---|---|---|
| ≥ 0.92 | 75% | 8x |
| ≥ 0.85 | 50% | 7x |
| ≥ 0.75 | 25% | 5x |

Hard cap: 8x leverage, 75% notional of available margin.

## Direction

Match the leader: positive leader 4h move → LONG the laggard, negative → SHORT. Mantis never goes against the leader's direction; the entire setup is "the alt is going to catch up to where the leader is."

## Exit logic

Three layers, in priority order:

1. **Leader-reversal veto** — every scan tick, Mantis checks the current 4h leader move vs. the leader's move at entry. If the leader has reversed by >1% from entry, Mantis closes immediately. The thesis was the leader's move; if it dies, the trade dies.

2. **Dynamic hard timeout** — `avg_lag_minutes × 1.5`, clamped to [30, 240] minutes. If the alt hasn't caught up within that window, the catchup probably isn't happening — exit.

3. **DSL trail** — standard Phase 1 / Phase 2 trailing logic for in-window winners:
   - Phase 1 max loss: 12%
   - Phase 2 trail tiers: 5/30, 10/60, 15/75, 25/85
   - Weak peak cut: 20 min, 1.5% min value

## Risk controls

| Control | Value | Why |
|---|---|---|
| Max concurrent positions | 2 | Diversify across catchup setups, don't concentrate |
| Per-asset cooldown | 240 min | Prevent re-strike whipsaw on same asset |
| Daily entry cap | 6 | Behavioral fee-churn ceiling |
| Max position notional | 75% | of available margin |

## State files (persistent, survive session clears)

- `state/asset-cooldowns.json` — per-asset cooldown timestamps
- `state/entry-log.jsonl` — append-only log of every Mantis decision (STRIKE, NO_ENTRY, LEADER_REVERSAL_EXIT, DAILY_CAP_REACHED, POSITION_CLOSED_DETECTED)
- `state/position-metadata.json` — per-position leader/lag metadata for veto tracking

## MCP tools used

- `market_get_cross_asset_flows` — primary signal source (the new cross-asset flow detection tool)
- `strategy_get_clearinghouse_state` — current positions + account value
- `create_position` — entry execution (called by runtime from STRIKE action)
- `close_position` — exit execution (called by runtime from VETO_CLOSE action or DSL)

## Cron cadence

Scanner runs every 60 seconds. The cross-asset flow data itself is refreshed daily at 00:00 UTC by the `hyperliquid-cross-asset-flow-cron` job; Mantis just consumes the live output.

## Install

```bash
mkdir -p /data/workspace/skills/mantis-strategy/{config,scripts,state}

gh repo clone Senpi-ai/senpi-skills /tmp/senpi-skills
cp -r /tmp/senpi-skills/mantis/* /data/workspace/skills/mantis-strategy/

cp /data/workspace/skills/mantis-strategy/config/mantis-config.example.json \
   /data/workspace/skills/mantis-strategy/config/mantis-config.json

# Set wallet + telegram in runtime.yaml
sed -i 's/${WALLET_ADDRESS}/<WALLET>/' /data/workspace/skills/mantis-strategy/runtime.yaml
sed -i 's/${TELEGRAM_CHAT_ID}/<CHAT_ID>/' /data/workspace/skills/mantis-strategy/runtime.yaml

openclaw senpi runtime create --path /data/workspace/skills/mantis-strategy/runtime.yaml
openclaw senpi runtime list
```

## Status

**v5.0 — initial release.** Scanner is production-ready. Currently BTC-only as the leader (per the cross-asset flow tool's pre-computed lag coverage). Adding ETH/SOL/HYPE as leaders is a one-line config change in `mantis_config.py` once Sarvesh ships pre-computed lag data for those assets.
