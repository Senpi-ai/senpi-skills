---
name: komodo-strategy
description: >-
  KOMODO v1.0 — Momentum Event Consensus. Uses leaderboard_get_momentum_events
  (real-time threshold crossings) to detect when 2+ quality SM traders cross
  momentum thresholds on the same asset/direction within 60 minutes. Confirmed
  by market concentration + volume. Enters with the momentum.
  Replaces MANTIS v1.0 and SCORPION v1.1 (both used stale position data).
license: Apache-2.0
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# 🦎 KOMODO v1.0 — Momentum Event Consensus

Patient ambush. Devastating when it commits. Follow smart money ACTIONS, not stale positions.

## Why KOMODO Exists

**MANTIS v1.0 and SCORPION v1.1 were broken at the data source level.** Both used `discovery_get_top_traders` to find historically good whales, then read their current open positions. This returned ALL positions including legacy ones — BTC shorts opened months ago at $89-108k showed up as "fresh 8-whale consensus." The aging filters only checked scan duration, not when positions were actually opened.

**KOMODO uses `leaderboard_get_momentum_events` as the primary data source.** These are real-time threshold-crossing events ($2M+/$5.5M+/$10M+) within a 4-hour rolling window. They capture when a trader's delta PnL crosses a significance level — this is an ACTION, not a stale position.

| Predecessor | Problem | Result |
|---|---|---|
| SCORPION v1.1 | 2 whales, 10min aging, stale positions | 406 trades, -24.2% |
| MANTIS v1.0 | 4 whales, 30min aging, stale positions | 0 trades (gates correct, data wrong) |
| **KOMODO v1.0** | **Real-time momentum events, quality filtering** | **New — tracking fresh** |

## MANDATORY: DSL High Water Mode

**KOMODO MUST use DSL High Water Mode. This is not optional.**

Spec: https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

DSL tiers in `komodo-config.json`. Arm DSL immediately after every entry fill. Zero naked positions.

## Five-Gate Entry Model

### Gate 1 — Momentum Events (primary signal)

Poll `leaderboard_get_momentum_events` for recent tier 1+ crossings within the last 60 minutes. These are traders whose delta PnL just crossed $2M+ (T1), $5.5M+ (T2), or $10M+ (T3).

Each event includes:
- `top_positions`: snapshot of which markets drove the momentum, with direction and delta PnL
- `concentration`: how focused the trader's gains are (0-1)
- `trader_tags`: TCS (Elite/Reliable/Streaky/Choppy) and TAS (Patient/Tactical/Active/Degen)
- `tier`: 1/2/3 significance level

Group events by asset+direction. **2+ unique traders** on the same asset/direction = consensus.

### Gate 2 — Trader Quality Filter

Not all momentum events are equal. Filter by:
- **TCS (Trading Consistency Score)**: Only Elite and Reliable. Streaky/Choppy filtered out.
- **TAS (Trading Activity Style)**: Block Degen. Allow Patient, Tactical, Active.
- **Concentration ≥ 0.4**: Trader's gains are focused, not spread thin across 20 positions.

### Gate 3 — Market Confirmation

Call `leaderboard_get_markets` to check aggregate SM concentration on the asset. Requires 5+ top traders with significant positions in this market. Confirms momentum events aren't isolated.

### Gate 4 — Volume Confirmation

Call `market_get_asset_data` to check 1h volume. Current volume must be ≥ 50% of 6h average. Don't enter into dead markets.

### Gate 5 — Regime Filter (penalty, not block)

Check BTC 4h regime. Counter-trend entries get -3 penalty. Regime-aligned entries get +1 bonus. SM momentum may override regime, so this is a penalty not a block.

### Scoring

| Component | Points | Notes |
|---|---|---|
| Trader count | 2 per trader | Core signal strength |
| Avg tier | 1-3 | Higher tiers = bigger moves |
| Avg concentration | 1-2 | High conviction traders |
| Market confirmation | 1-2 | Hot market bonus |
| Volume strength | 0-1 | Strong volume bonus |
| Regime alignment | -3 to +1 | Penalty/bonus |

**Minimum score: 10** to trigger entry.

## Entry Direction

KOMODO enters **WITH** the smart money momentum. If SM is long, KOMODO goes long. This is follow-the-leader, not contrarian (that's OWL's job).

## Risk Management

| Rule | Value |
|---|---|
| Max positions | 3 |
| Max entries/day | 3 base, up to 6 on profitable days |
| Absolute floor | 3% notional |
| Drawdown halt | 20% from peak |
| Daily loss limit | 8% |
| Cooldown after 3 consecutive losses | 90 min |
| Stagnation TP | 10% ROE stale for 1 hour |

## Cron Architecture

| Cron | Interval | Session | Purpose |
|---|---|---|---|
| Scanner | 5 min | isolated | Momentum events + consensus + all gates |
| DSL v5 | 3 min | isolated | High Water Mode trailing stops |

Both crons MUST be isolated sessions with `agentTurn` payload. Use `NO_REPLY` for idle cycles.

## Notification Policy

**ONLY alert:** Position OPENED (asset, direction, trader count, avg tier, score breakdown), position CLOSED (DSL or thesis exit with reason), risk guardian triggered, critical error.

**NEVER alert:** Scanner found no events, quality filter removed events, no consensus, gates failed, DSL routine check, any reasoning.

## What "Working Correctly" Looks Like

- **Zero trades for hours:** Normal. Momentum events that pass ALL five gates are rare by design.
- **Events seen but no consensus:** The quality filter and 2+ trader requirement are doing their job.
- **Consensus found but score too low:** Volume or regime gates keeping us out of bad setups.
- **1-2 trades per day:** Ideal. The Komodo waits, then strikes once.

## Bootstrap Gate

On EVERY session, check if `config/bootstrap-complete.json` exists. If not:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦎 KOMODO v1.0 is online. Monitoring momentum events for SM consensus. Silence = no conviction."

## Files

| File | Purpose |
|---|---|
| `scripts/komodo-scanner.py` | Five-gate scanner (momentum events → quality → markets → volume → regime) |
| `scripts/komodo_config.py` | Shared config, MCP helpers, state I/O |
| `config/komodo-config.json` | All configurable variables with DSL High Water tiers |
