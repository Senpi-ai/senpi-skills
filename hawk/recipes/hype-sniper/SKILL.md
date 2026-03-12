# HYPE Momentum Sniper v2

10x leveraged HYPE trading on Hyperliquid via Senpi. Smart entries with multi-timeframe confirmation, DSL v5.2 trailing stops with time decay, and ALO fee optimization.

## Architecture

| Script | Freq | Purpose |
|--------|------|---------|
| `scanner_v3.py` | 30s | Smart entry: 5m+15m momentum, volume, chop filter, OI |
| `dsl-v52.py` | 3min | DSL v5.2 trailing stops + time decay + partial TP |
| `hedge-monitor-v2.py` | 60s | Funding + market-risk hedging with ALO |
| `health.py` | 10min | Portfolio health + Telegram reports |
| `risk-guardian.py` | 5min | Account-level guard rails: daily loss, drawdown, max entries, cooldown |
| `dsl-cleanup.py` | manual | Strategy cleanup when all positions closed |

## Entry Logic v2 (scanner_v3.py)

**Multi-filter entry system:**
1. **5m momentum ≥ 1.0%** (was 0.6%) — only decisive moves, not noise dips
2. **1h trend hard block** — if signal direction conflicts with 1h trend, skip. MIXED/UNKNOWN trends also blocked — must have clear directional bias.
2. **15m trend agreement** — only trade with the trend
3. **Volume spike ≥ 1.5x** average — confirms conviction
4. **Chop detection** — skip if 30m range < 0.5%
5. **OI confirmation** — rising OI for longs, flat/dropping for shorts
6. **Funding alignment** — same as v1
7. **Direction block** — no same-direction re-entry within 20min of loss
8. **Adaptive cooldown** — 15min normal, 30min after SL loss

**Strong momentum override (1.8x threshold):** Ignores funding and 15m disagreement.

**Execution:** `FEE_OPTIMIZED_LIMIT` with ALO (saves ~3 bps per entry)

## DSL v5.2 (dsl-v52.py)

ROE-based trailing stop loss with time decay and partial take-profit.

**Phase 1 (Let it breathe):** 4% ROE retrace from HWM, 5 consecutive breaches, 8% ROE hard floor.

**Phase 2 (Lock the bag):** Activated at Tier 1 (8% ROE). 2% ROE retrace, 2 breaches.

**Tiers:**
| Tier | Trigger | Lock | Retrace |
|------|---------|------|---------|
| 1 | 8% ROE | 3% | 2.0% |
| 2 | 15% ROE | 10% | 1.8% |
| 3 | 25% ROE | 18% | 1.5% |
| 4 | 40% ROE | 30% | 1.2% |
| 5 | 60% ROE | 48% | 1.0% |
| 6 | 80% ROE | 65% | 0.8% |
| 7 | 100% ROE | 80% | 1.0% |

**Time Decay (cut stale losers):**
| After | If ROE < | Action |
|-------|----------|--------|
| 5 min | 3% | Tighten floor to 6% ROE |
| 15 min | 5% | Tighten floor to 4% ROE |
| 30 min | 8% | Force close (not a momentum trade) |

**Partial Take-Profit:** At Tier 3 (25% ROE), reduce position 25% via `edit_position(targetMargin)`. Let rest ride.

**Pyramiding (add to winners):**
- Triggered on tier upgrades in Phase 2 (confirmed trend)
- Adds 15% margin per pyramid via `edit_position(targetMargin)`
- Max 2 adds per trade, once per tier
- Entry price blends automatically (Senpi handles the math)

**HL-native SL + TP:**
- Stop loss synced to Hyperliquid via `edit_position(stopLoss)`
- Take profit set at next tier target via `edit_position(takeProfit)` — HL executes natively, no cron dependency
- Both use `LIMIT` order type for fee optimization

**Scanner Feedback:** On close, DSL updates scanner_state.json with loss flag → triggers 30min cooldown.

## Hedge Logic v2 (hedge-monitor-v2.py)

- Funding hedge: threshold raised to 0.015 (was 0.01)
- Market-risk hedge: if HYPE >15% ROE and BTC drops 1%+ → hedge
- Hedge entry: ALO (was MARKET) — saves fees
- Auto-close when main position closes

## Risk Management

- Max single position: 25% of margin
- Max margin usage: 60%
- Max daily loss: 5%
- Max drawdown: 15%
- Max positions: 3
- Entry cooldown: 15min normal / 30min post-SL

## Fee Optimization (ALO)

- Entries: `FEE_OPTIMIZED_LIMIT` + ALO
- Stop losses Phase 1: `MARKET` (speed > cost)
- Stop losses Phase 2: `LIMIT`
- Partial TP: `FEE_OPTIMIZED_LIMIT`
- Hedge entries: `FEE_OPTIMIZED_LIMIT`

## Cron Setup

4 crons total:
1. Scanner v2: every 30s, `OPENCLAW_WORKSPACE=/data/workspace python3 scripts/scanner_v3.py`
2. DSL v5.2: every 3min, `DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID={id} python3 scripts/dsl-v52.py`
3. Hedge v2: every 60s, `OPENCLAW_WORKSPACE=/data/workspace python3 scripts/hedge-monitor-v2.py`
4. Health: every 10min, `OPENCLAW_WORKSPACE=/data/workspace python3 scripts/health.py`

## Config

`hype-config.json` — `entry_v2` section controls scanner v2 params.

## State

- Scanner state: `state/scanner_state.json`, `state/price_history.json`
- DSL state: `/data/workspace/dsl/{strategy_id}/HYPE.json`
- Hedge state: `state/hedge_state.json`
- Health state: `state/health_state.json`

## Legacy Scripts (kept for reference)

- `scanner.py` — v1 scanner (0.2% threshold, no filters)
- `dsl-v5.py` — v5.0 DSL (3% retrace, 3 breaches, no time decay)
- `hedge-monitor.py` — v1 hedge (MARKET orders, lower threshold)
