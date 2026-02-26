---
name: lion-strategy
description: >-
  LION v1 — Liquidation Intelligence & Order-flow Network for Hyperliquid
  perps via Senpi MCP. Detects liquidation cascades, order flow imbalances,
  and squeeze setups, then trades the dislocation snapback. 3 patterns:
  Cascade Reversal, Book Imbalance Fade, Squeeze Detection. Patience-first
  design — 0-4 trades per day. 6-cron architecture. Pure Python analysis.
  Requires Senpi MCP, python3, mcporter CLI, and OpenClaw cron system.
license: Apache-2.0
compatibility: >-
  Python 3.8+, no external deps (stdlib only). Requires mcporter
  (configured with Senpi auth) and OpenClaw cron system.
metadata:
  author: jason-goldberg
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# LION v1 — Liquidation Intelligence & Order-flow Network

Wait for the market to break. Trade the repair.

**Philosophy:** WOLF chases what's moving. TIGER calculates what it needs. LION waits for everyone else to panic, then calmly steps in.

---

## Architecture

```
┌──────────────────────────────────────────┐
│           6 OpenClaw Crons               │
│  OI Mon(60s) Cascade(90s) Book(30s)      │
│  Squeeze(15m) Exit(60s) Health(10m)      │
├──────────────────────────────────────────┤
│           Python Scripts                  │
│  lion_lib.py  lion_config.py             │
│  oi-monitor / cascade-detector /          │
│  book-scanner / squeeze-monitor /         │
│  lion-exit / lion-health                  │
├──────────────────────────────────────────┤
│           Senpi MCP (via mcporter)        │
│  market_list_instruments                  │
│  market_get_asset_data                    │
│  create_position / close_position         │
│  edit_position / cancel_order             │
│  strategy_get_clearinghouse_state         │
│  leaderboard_get_markets                  │
├──────────────────────────────────────────┤
│           State Files                     │
│  lion-config.json → lion_config.py        │
│  state/{instance}/*.json (atomic writes)  │
│  history/oi-history.json (shared signal)  │
└──────────────────────────────────────────┘
```

**State flow:** OI Monitor samples all assets every 60s → Cascade Detector combines OI + price + volume + funding → Book Scanner checks L2 order books → Squeeze Monitor tracks funding buildup → Exit manages open positions with pattern-specific rules. OI history is a shared signal; position state is instance-scoped.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` shows `senpi`)
2. Create a custom strategy: `strategy_create_custom_strategy`
3. Fund the wallet: `strategy_top_up` ($3K-$10K recommended)
4. Run setup:
   ```bash
   python3 scripts/lion-setup.py --wallet 0x... --strategy-id UUID \
     --budget 5000 --chat-id 12345
   ```
5. Create 6 OpenClaw crons from `references/cron-templates.md`

**First hour:** LION needs ~1 hour of OI history before cascade detection is reliable. Expect no trades while baseline builds.

---

## 3 Hunt Patterns

### 1. Cascade Reversal (Primary)

Liquidation cascade creates a price cliff → enter counter-trade at stabilization → ride the snapback.

**Detection signals (ALL must be true):**

| Signal | Threshold | Config Key | Default |
|--------|-----------|-----------|---------|
| OI cliff (15min) | > 8% drop | `cascadeOiCliffPct` | 8 |
| Price velocity (15min) | > 2% move | `cascadePriceVelocityPct` | 2 |
| Funding spike | > 0.05% per 8h | `cascadeFundingSpikePer8h` | 5 |
| Volume spike (15min) | > 3× 4h average | `cascadeVolumeMultiplier` | 3 |

**Entry:** Direction OPPOSITE cascade. Wait for stabilization candle (first 5-min candle closing against cascade direction). Leverage 5-7x. Size by cascade magnitude.

**Cascade lifecycle — enter only at Phase 3:**

```
BUILDING → ACTIVE → STABILIZING → ENTRY_WINDOW → EXPIRED
                                       ↑
                                  Enter here
```

### 2. Book Imbalance Fade

L2 order book imbalance → thin side gets swept → fade the sweep.

| Signal | Threshold | Config Key | Default |
|--------|-----------|-----------|---------|
| Bid/ask ratio | > 3:1 (top 5 levels) | `bookImbalanceRatio` | 3 |
| Asset volume | > $20M 24h | `bookMinDailyVolume` | 20000000 |
| Persistence | 2+ consecutive 30s checks | `bookPersistenceChecks` | 2 |
| Proximity | Thick side within 0.5% of price | `bookProximityPct` | 50 |

**Entry:** Fade the imbalance after sweep confirmation. Leverage 5x. Stop 1.5× ATR. Hold 5-30 minutes.

### 3. Squeeze Detection

Extreme sustained funding + rising OI + price compression → crowded side will capitulate.

| Signal | Threshold | Config Key | Default |
|--------|-----------|-----------|---------|
| Funding rate | > 0.08% per 8h for 2+ periods | `squeezeFundingPer8h` | 8 |
| OI trend | Rising during extreme funding | — | — |
| Price compression | < 1% move in 24h | `squeezeCompressionPct` | 1 |
| SM divergence | Smart money shifting opposite | — | — |

**Entry:** Only on trigger event (price breaks 24h range, OI starts dropping, or cascade begins on crowded side). Never on buildup alone. Leverage 7-10x. Funding works for us.

---

## Pattern-Specific Exit Rules

| Pattern | Target | Time Stop | Trailing Lock | Emergency Exit |
|---------|--------|-----------|---------------|----------------|
| CASCADE_REVERSAL | 40-60% of cascade move recaptured | 2 hours | 70% lock at 50% of target | OI drops 5% more after entry (second wave) |
| BOOK_IMBALANCE | Price returns to pre-sweep level | 30 min | 80% lock at 60% of target | Imbalance ratio flips to other side |
| SQUEEZE | 3-5% move in squeeze direction | 12 hours | 50% lock at 2%, 70% lock at 3% | Funding normalizes below 0.03% per 8h |

All percentage values are whole numbers (5 = 5%). Config keys in `references/state-schema.md`.

---

## Risk Management

| Rule | Limit | Config Key | Default |
|------|-------|-----------|---------|
| Max single trade loss | 5% of balance | `maxSingleLossPct` | 5 |
| Max daily loss | 8% of balance | `maxDailyLossPct` | 8 |
| Max drawdown from peak | 15% | `maxDrawdownPct` | 15 |
| Max concurrent positions | 2 | `maxSlots` | 2 |
| Max daily trades | 4 | `maxDailyTrades` | 4 |
| Threshold escalation after max trades | +25% on all thresholds | `thresholdEscalationPct` | 25 |
| Margin warning | 50% available | `marginWarningPct` | 50 |
| Margin critical | 30% available → reduce size 50% | `marginCriticalPct` | 30 |

### Position Sizing (conviction-weighted)

| Signal Quality | Size (% of balance) | Config Key |
|---------------|---------------------|-----------|
| Strong cascade (OI > 15%, vol > 5×) | 20 | `sizingStrongCascade` | 
| Normal cascade (OI 8-15%, vol > 3×) | 12 | `sizingNormalCascade` |
| Book imbalance | 8 | `sizingBookImbalance` |
| Squeeze (strong) | 15 | `sizingStrongSqueeze` |
| Squeeze (moderate) | 10 | `sizingModerateSqueeze` |

### Why Only 2 Slots

Dislocations = high volatility. Multiple positions during cascades risks correlated losses. Two slots max.

---

## The Patience Engine

LION trades the least of all skills. 0-4 trades per day. Often zero. That's by design.

- **Daily minimum: zero.** Never lower thresholds to find trades.
- **Daily maximum: 4.** After 4, all thresholds increase 25% for the rest of the day.
- A good day: 1-2 trades, 70%+ win rate.
- A great day: 0 trades (market didn't break).

---

## Anti-Patterns

1. **NEVER enter during an active cascade.** Wait for stabilization candle. ACTIVE ≠ ENTRY_WINDOW.
2. **NEVER trade more than 4 times in a day.** Chaos = raise thresholds, not trade count.
3. **NEVER hold cascade reversal > 2 hours.** No snapback in 2h = regime shift, not dislocation.
4. **NEVER enter book imbalance during a cascade.** Cascade overrides book signals.
5. **NEVER lower thresholds to "find trades."** Patience is the edge.
6. **NEVER assume a squeeze will accelerate.** Enter on trigger events only, not buildup.

---

## OI Monitoring — Technical Core

OI Monitor builds a local time-series (not available from any API):

```
Per asset every 60s:
- oi_change_5m_pct, oi_change_15m_pct, oi_change_1h_pct
- oi_velocity (rate of change of rate of change)
- oi_acceleration (is the drop accelerating or decelerating?)
- cascade_candidate flag, volume_spike flag, funding_spike flag
```

**OI acceleration** determines cascade phase:
- Phase 1: velocity ↓, acceleration ↓ → cascade accelerating → DON'T ENTER
- Phase 2: velocity ↓, acceleration ↑ → cascade decelerating → WATCH
- Phase 3: velocity → 0, acceleration ↑ → stabilization → ENTRY WINDOW

Rolling 240 entries per asset (4 hours at 60s). Stored in `history/oi-history.json` (shared signal, not instance-scoped).

---

## API Dependencies

| Tool | Used By | Purpose |
|------|---------|---------|
| `market_list_instruments` | oi-monitor | OI, funding, volume for all assets |
| `market_get_asset_data` | cascade-detector, squeeze-monitor, book-scanner | Candles for price velocity, stabilization; L2 book via `include_order_book` |
| `leaderboard_get_markets` | squeeze-monitor | SM alignment for squeeze confirmation |
| `create_position` | cascade-detector, book-scanner, squeeze-monitor | Limit order entries |
| `close_position` | lion-exit | Position exits |
| `edit_position` | lion-exit | Partial position reduction |
| `strategy_get_clearinghouse_state` | lion-health | Margin, positions |

---

## State Schema

See `references/state-schema.md` for full schema with field descriptions.

Key state files:
```
state/{instanceKey}/
├── lion-state.json           # Positions, watchlist, safety, daily stats
└── trade-log.json            # All trades with outcomes

history/
└── oi-history.json           # Shared OI time-series (market-wide)
```

All state files include `version`, `active`, `instanceKey`, `createdAt`, `updatedAt`. All writes use `atomic_write()`.

---

## Cron Setup

See `references/cron-templates.md` for ready-to-use OpenClaw cron payloads.

| # | Job | Interval | Script | Model Tier |
|---|-----|----------|--------|------------|
| 1 | OI Monitor | 60s | `oi-monitor.py` | Tier 1 |
| 2 | Cascade Detector | 90s | `cascade-detector.py` | Tier 2 |
| 3 | Book Scanner | 30s | `book-scanner.py` | Tier 1 |
| 4 | Squeeze Monitor | 15 min | `squeeze-monitor.py` | Tier 2 |
| 5 | Risk & Exit | 60s | `lion-exit.py` | Tier 2 |
| 6 | Health & Report | 10 min | `lion-health.py` | Tier 1 |

**Tier 1** (fast/cheap): data collection, binary threshold checks, status parsing.
**Tier 2** (capable): entry decisions, exit judgment, multi-factor signal evaluation.

---

## Expected Performance

| Metric | Target |
|--------|--------|
| Trades per day | 0-4 (avg 1-2) |
| Win rate | 65-75% |
| Avg winner ROE | 2-4% |
| Avg loser ROE | 1-2% |
| Profit factor | 2.0-3.0 |
| Best conditions | Volatile days with leverage flushes |
| Worst conditions | Low-vol grinding (LION sits idle) |

Evaluate on a 2-week window, not daily.

---

## Optimization Levers

| Lever | Config Key | Conservative | Default | Aggressive |
|-------|-----------|-------------|---------|------------|
| OI cliff % | `cascadeOiCliffPct` | 12 | 8 | 6 |
| Volume multiplier | `cascadeVolumeMultiplier` | 5 | 3 | 2 |
| Cascade leverage | `leverageCascade` | 4 | 6 | 8 |
| Squeeze leverage | `leverageSqueeze` | 5 | 8 | 10 |
| Snapback target % | `snapbackTargetPct` | 30 | 50 | 70 |
| Cascade time stop (min) | `cascadeTimeStopMin` | 90 | 120 | 180 |
| Max daily trades | `maxDailyTrades` | 3 | 4 | 6 |
| Max slots | `maxSlots` | 1 | 2 | 3 |

Start conservative. LION's edge = high selectivity. Widening thresholds dilutes signal quality.

---

## Known Limitations

- **OI history bootstrap.** First hour has limited cascade detection (needs ~60 min of 60s samples).
- **No historical OI from API.** Must be built locally via oi-monitor.
- **Book imbalance latency.** 30s scan interval means some fast imbalances are missed.
- **L2 orderbook format assumption.** Book scanner uses `market_get_asset_data` with `include_order_book=True`. Bid/ask level parsing may need adjustment after testing with live API responses.
- **5-min candle availability.** Cascade stabilization detection ideally uses 5-min candles. If `5m` interval is not supported by `market_get_asset_data`, falls back to 1h candle wick analysis.
- **Liquidation detection is indirect.** No direct liquidation event stream. LION infers cascades from OI cliff + price velocity + volume spike. This is by design (more robust than event dependency) but means some small liquidation events may be missed.
- **Squeeze persistence.** Squeeze setups can persist for days without triggering. Capital may be idle.
- **Cascade vs. regime change.** Not all OI cliffs are temporary dislocations — some are the start of a new trend. The 2-hour time stop mitigates but doesn't eliminate this risk.
- **Single strategy wallet.** v1 operates on one instance.

---

## Gotchas

- `cascadeOiCliffPct` is a whole number: `8` = 8%, not `0.08`.
- `cascadeFundingSpikePer8h` is in bps × 100: `5` = 0.05% per 8h.
- `bookProximityPct` uses bps: `50` = 0.5%.
- `snapbackTargetPct` is percentage of cascade move: `50` = recapture 50% of the drop.
- OI history is stored in `history/` (shared), not `state/{instance}/` (instance-scoped).
- Trailing lock values are whole numbers: `70` = lock 70% of gains.
