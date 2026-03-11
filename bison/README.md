# 🦬 BISON — Conviction Holder

The big-game hunter. BISON only trades the top 10 highest-volume assets on Hyperliquid. Enters when 4h trend structure, 1h momentum, smart money, and funding all converge on a directional thesis. Holds through pullbacks that would stop out every other skill. Exits when the thesis breaks — not when price retraces.

## What Makes BISON Different

Every other Senpi skill uses tight DSL bands designed for trades that play out in minutes to hours. BISON uses the same DSL engine but with radically wider bands designed for multi-day conviction holds:

- **Phase 1 floor:** 3% notional (0.03/leverage) = ~30% ROE at 10x. Normal hourly candle noise won't trigger it.
- **Early tiers — breathe:** +10% ROE confirms the trade is working but locks nothing. +20% ROE only locks 5%. The trade has room to develop through multi-hour consolidation.
- **Late tiers — choke:** +100% ROE locks 80%. +200% locks 170%. Once a conviction trade becomes a runner, protect it aggressively.
- **Stagnation TP:** 15% ROE stale for 2 hours (not 30 min like other skills). Conviction trades need patience.
- **No time-based exits.** No dead weight, no weak peak, no hard timeout. The only exits are structural invalidation (floor) or thesis invalidation (signals reversed).

## Thesis-Based Exits

BISON's scanner runs a unique dual loop every 5 minutes:

1. **Re-evaluate held positions first.** For every open position, check: is the 4h trend still intact? Has SM flipped against us? Has funding gone extreme? Has volume dried up for 3+ hours? If ANY of these invalidate, output a thesis exit signal — the agent closes the position because the reason it entered is dead.

2. **Then scan for new entries.** Only if held positions are healthy and slots are available.

This means BISON can hold a position for 48 hours through multiple 5% pullbacks as long as the 4h structure, SM, and funding all still support the thesis. But the moment the 4h trend flips, it's out — regardless of P&L.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `bison-scanner.py` | 5 min | Thesis builder (new entries) + thesis evaluator (held positions) |
| DSL v5 (shared) | 3 min | Wide trailing stops — safety net, not primary exit |

## Entry Requirements (all must pass)

| Signal | Requirement |
|---|---|
| 4h trend structure | BULLISH (higher lows) or BEARISH (lower highs) — no neutral |
| 1h trend | Must agree with 4h |
| 1h momentum | ≥ 0.5% in direction over last 2 bars |
| Smart money | Must not oppose (hard block if SM is opposite direction) |
| RSI | Not overbought (< 72 for longs) or oversold (> 28 for shorts) |
| Min score | 8 (from convergence of all signals above + funding, volume, OI boosters) |

## Thesis Invalidation Triggers (any one exits)

| Trigger | What It Means |
|---|---|
| 4h trend flips | The macro structure broke. Conviction is dead. |
| SM flips against position | The whales changed their mind. |
| Funding extreme against position | The trade is crowded. Reversal risk is high. |
| Volume dried up 3+ hours | Conviction left. The move is over. |

## DSL: High Water Mode

BISON uses [DSL High Water Mode](https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md) — percentage-of-high-water locks that trail infinitely instead of fixed ROE tiers. The stop loss is always a percentage of the peak, and it has no ceiling.

### Why High Water Mode for BISON

Standard DSL locks fixed ROE amounts: "at +100% ROE, lock +80%." If the trade runs to +500% ROE, the lock is still +170% (capped at the highest tier). That's a 66% giveback on a multi-day BTC trend — exactly the kind of trade BISON is designed to hold.

High Water Mode at +500% ROE locks +425% (85% of peak). The geometry stays constant no matter how far the trade runs.

### Tier Design (Wide → Tight, Pct of High Water)

| Tier | Trigger ROE | Lock (% of HW) | Breaches | Philosophy |
|---|---|---|---|---|
| 1 | 10% | 0% | 3 | Confirms working. No lock — BISON breathes. |
| 2 | 20% | 25% | 3 | Light lock. Wide room for multi-hour pullbacks. |
| 3 | 30% | 40% | 2 | Starting to protect. |
| 4 | 50% | 60% | 2 | Meaningful lock. Trade proven. |
| 5 | 75% | 75% | 1 | Tightening. |
| 6 | 100%+ | 85% | 1 | Maximum lock. Infinite trail at 85% of peak. |

Phase 2 triggers at +10% ROE (more patient than FOX's +7%). BISON conviction trades consolidate longer before trending.

### Phase 1: Conviction-Scaled Floors

| Entry Score | Max ROE Loss | Time Exits |
|---|---|---|
| 6-7 | -25% ROE | None (structural only) |
| 8-9 | -30% ROE | None |
| 10+ | Unrestricted | None |

No hard timeout. No weak peak cut. No dead weight cut. The only Phase 1 exit is the absolute floor (structural invalidation) or thesis invalidation from the scanner's re-evaluation loop.

### Critical: State File Must Include Tiers

Every DSL state file must include the `tiers`, `lockMode`, and `phase2TriggerRoe` fields at creation time. Without them, the engine falls back to flat 1.5% retrace and High Water Mode is silently disabled. See the [full spec](https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md) for the required state file format and a backfill script for existing positions.

## Risk Management

| Rule | Value | Notes |
|---|---|---|
| Absolute floor | 3% notional (~30% ROE at 10x) | Emergency parachute only |
| G5 per-position cap | 8% of account | Force close if single position threatens too much |
| G2 drawdown halt | 25% from peak | Halt all trading |
| Daily loss limit | 10% | Wider than most skills — conviction trades have larger stops |
| Max consecutive losses | 3 → 120 min cooldown | Longer cooldown — thesis trades shouldn't chain-fail |
| Stagnation TP | 15% ROE stale 2 hours | Very patient — conviction trades consolidate |

## Dynamic Slots

| Day P&L | Max Entries |
|---------|------------|
| Base | 3 |
| ≥ $100 | 4 |
| ≥ $250 | 5 |
| ≥ $500 | 6 |

Conservative base (3) because each trade is a large conviction bet. Unlock more only when the thesis-builder is proven profitable that day.

## Conviction-Scaled Margin

| Score | Margin |
|-------|--------|
| 8-9 | 25% of account |
| 10-11 | 31% |
| 12+ | 37% |

3 positions at 25% each = 75% deployed. High conviction, concentrated bets.

## Deployment

### Bootstrap Gate
On first session, the agent must:
1. Verify Senpi MCP
2. Create scanner cron (5 min, isolated) and DSL cron (3 min, isolated)
3. Write `config/bootstrap-complete.json`
4. Send: "🦬 BISON is online. Scanning top 10 for conviction thesis. Silence = no conviction."

### Notification Policy
**ONLY alert:** Position OPENED, position CLOSED (DSL or thesis exit), risk guardian triggered, critical error.
**NEVER alert:** Scanner found nothing, DSL routine check, thesis re-evaluation passed, health check OK.
All crons isolated. `NO_REPLY` for idle cycles.

## Setup
1. Set `BISON_WALLET` and `BISON_STRATEGY_ID` env vars (or fill `bison-config.json`)
2. Top up strategy to full budget — BISON takes concentrated positions
3. Agent creates crons via bootstrap gate

## Expected Behavior

| Metric | Expected |
|---|---|
| Trades/day | 1-3 (conviction trades are rare) |
| Avg hold time | 4-48 hours |
| Win rate | ~50-55% (wider stops = more room for both directions) |
| Avg winner | 30-100%+ ROE (wide bands let runners run) |
| Avg loser | -20 to -30% ROE (wide floor, structural invalidation) |
| Fee drag/day | $5-10 (very few trades, all maker entries) |
| Profit factor | Target 1.5-2.0 (big winners, managed losers) |

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
