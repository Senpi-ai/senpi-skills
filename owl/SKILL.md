---
name: owl-strategy
description: >-
  OWL v1 — Contrarian crowding-unwind trading for Hyperliquid perps via Senpi MCP.
  Detects one-sided market positioning (extreme funding, OI buildup, SM concentration),
  waits for exhaustion signals, then enters AGAINST the crowd to ride the liquidation
  cascade. 3-phase pipeline: Crowding Scanner → Exhaustion Detector → Entry Trigger.
  Crowding persistence minimum (2h at score ≥0.60). BTC correlation isolation (crowded BTC
  = trade BTC only, not correlated alts). Funding income factored into DSL floor.
  Wide Phase 1 (0.03/leverage, 60-75min timeout) — contrarian entries need room.
  6-cron architecture. DSL v5 trailing stops. Pure Python analysis.
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

# OWL v1 — Contrarian Crowding-Unwind Trading

Wait for the crowd to overcommit. Then eat their liquidations.

**Philosophy:** WOLF/FOX chase momentum early. TIGER trades technical breakouts. VIPER bounces ranges. OWL is the **pure contrarian** — it finds assets where funding, OI, smart money, and order book all scream one direction, waits for exhaustion, then trades the other side. The edge is that crowded trades unwind violently and predictably.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│             6 OpenClaw Crons                  │
│  Crowding(5m) Exhaustion(3m) Entry(3m)       │
│  Risk(5m) OI-Tracker(5m) DSL(3m)             │
├──────────────────────────────────────────────┤
│             Python Scripts                    │
│  owl_lib.py  owl_config.py                    │
│  crowding-scanner / exhaustion-detector /     │
│  owl-entry / owl-risk / oi-tracker            │
│  + DSL v5 (shared skill)                      │
├──────────────────────────────────────────────┤
│             Senpi MCP (via mcporter)          │
│  market_list_instruments (OI, funding, vol)   │
│  market_get_asset_data (candles, book, funding)│
│  leaderboard_get_markets (SM concentration)   │
│  leaderboard_get_top (SM sampling)            │
│  create_position / close_position / edit      │
│  strategy_get_clearinghouse_state             │
├──────────────────────────────────────────────┤
│             State Files                       │
│  owl-state.json (positions, config, daily)    │
│  crowding-history.json (OI/funding time-series)│
│  dsl/{strategyId}/*.json (trailing stops)     │
└──────────────────────────────────────────────┘
```

**Pipeline:** OI Tracker samples all assets → Crowding Scanner scores one-sidedness → Exhaustion Detector watches for trend fatigue → Entry Trigger fires contrarian position → DSL v5 manages trailing stops → Risk Guardian enforces limits and crowd-recrowding exits.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` shows `senpi`)
2. Create a custom strategy: `strategy_create_custom_strategy`
3. Fund the wallet: `strategy_top_up`
4. Run setup:
   ```bash
   python3 scripts/owl-setup.py --wallet 0x... --strategy-id UUID \
     --budget 2000 --chat-id 12345
   ```
5. Create 6 OpenClaw crons from `references/cron-templates.md`

**First 2 hours:** OI Tracker needs ~2h of snapshots before crowding persistence filter can qualify assets. Crowding Scanner will score assets immediately but won't promote to READY until persistence threshold is met (24 snapshots at 5min intervals = 2h).

---

## 3-Phase Signal Pipeline

### Phase 1: CROWDING SCANNER (every 5 min)

Scores every asset for how one-sided the market is. Runs on ALL assets via `market_list_instruments` (1 call), then deep-scans top candidates.

#### Crowding Score Factors

| Factor | Source | Weight | Crowded When |
|--------|--------|--------|-------------|
| Funding extremity | `market_list_instruments` | 0.25 | Annualized > 30% in either direction |
| OI vs rolling avg | `crowding-history.json` | 0.20 | OI 20%+ above 24h rolling average |
| SM concentration | `leaderboard_get_markets` | 0.20 | 70%+ of SM traders on one side |
| Order book imbalance | `market_get_asset_data` (L2 book) | 0.15 | Bid/ask depth ratio > 3:1 or < 1:3 |
| Funding acceleration | `market_get_asset_data` (funding history) | 0.10 | Funding rate getting MORE extreme over 24h |
| Volume declining | `market_get_asset_data` (1h candles) | 0.10 | Volume dropping while OI stays high = complacency |

**Crowding score ≥ 0.60** → asset enters CROWDED watchlist with `crowded_direction` (LONG or SHORT).

**Crowding persistence requirement:** Asset must maintain score ≥ 0.60 for **24 consecutive snapshots** (2 hours at 5min intervals) before it can advance to READY. This filters out flash crowding from single large positions. Tracked in `crowding-history.json` with `consecutive_crowded_count` per asset.

#### BTC Correlation Isolation

When BTC is the crowded asset:
- **Trade BTC itself** — the primary signal
- **Do NOT trade BTC-correlated alts** (SOL, ETH, DOGE, etc.) that are crowded in the same direction — they're just following BTC's crowding, not independently crowded
- Correlated alts are defined as: same `crowded_direction` as BTC AND their crowding score dropped >30% when measured with BTC OI/funding excluded from the calculation

When a non-BTC asset is crowded:
- Verify independence: its OI buildup, funding extreme, and SM concentration exist **regardless of BTC's state**
- If BTC is neutral but ALT is crowded → genuine independent signal → trade it

**Max 1 BTC-correlated position at a time.** Independent crowding events can coexist (e.g., short BTC + long an independently-crowded alt).

#### Data Cost
- `market_list_instruments`: 1 call (all assets, OI, funding)
- `leaderboard_get_markets`: 1 call (SM concentration)
- `market_get_asset_data`: top 6 candidates (candles, book, funding)
- **Total: ~8 calls, ~50s within timeout**

---

### Phase 2: EXHAUSTION DETECTOR (every 3 min, only CROWDED assets with persistence)

Once an asset has been CROWDED for 2+ hours, watch for signs the trend is running out of fuel.

#### Exhaustion Signals

| Signal | Source | Detection | Weight |
|--------|--------|-----------|--------|
| Funding plateau/decline | funding history | Was rising for hours, now flat or dropping — crowd not growing | 0.25 |
| OI starting to drop | `market_list_instruments` vs 15min ago | Smart money leaving before the crowd | 0.20 |
| Price stalling | 1h candles | 2-3 candles with shrinking range at price extreme | 0.20 |
| RSI divergence | 4h candles | Price new high, RSI lower high (bearish div) or inverse | 0.15 |
| Volume exhaustion | 1h candles | Each push higher/lower on declining volume | 0.10 |
| SM concentration shifting | `leaderboard_get_markets` | SM traders starting to reduce or flip | 0.10 |

**Exhaustion score ≥ 0.50** AND crowding still ≥ 0.60 → asset enters **READY** state.

**READY state persists for max 4 hours.** If no entry trigger fires in that window, asset drops back to CROWDED (may re-enter READY on new exhaustion signals).

#### Data Cost
- Only runs on 2-4 persistently-crowded assets
- ~3 calls per asset
- **Total: ~12 calls max, ~55s**

---

### Phase 3: ENTRY TRIGGER (every 3 min, only READY assets)

The moment the unwind begins. Requires ≥2 triggers firing simultaneously.

#### Trigger Signals

| Trigger | Detection | Confidence |
|---------|-----------|------------|
| **Price breaks key level** | 1h candle closes against crowd's direction + breaks 20-period SMA | HIGH |
| **OI drops >3% in one interval** | Current OI vs 5min-ago snapshot | HIGH — liquidations starting |
| **Funding rate flips sign** | Was positive, now negative (or vice versa) | MEDIUM — capitulation |
| **Book imbalance reversal** | Was 3:1 bid-heavy, now <1.5:1 or flipping | MEDIUM |
| **SM direction flip** | `leaderboard_get_markets` concentration shifting to opposite | HIGH |

**Entry rule:** ≥ 2 triggers firing simultaneously → ENTER contrarian direction.

**Direction:** Always OPPOSITE to `crowded_direction`. Crowd long → OWL shorts. Crowd short → OWL longs.

**Why 3-minute interval:** OWL might trade 3-5 times per week. The difference between catching a cascade at T+0 vs T+1 minute is negligible when unwinds take 30+ minutes to play out. 3min matches DSL interval and reduces scheduling noise (480 fires/day vs 720).

#### Data Cost
- 1-2 READY assets, ~2 calls each
- **Total: ~4 calls, ~25s**

---

## Position Sizing & Leverage

### Tiered Margin

| Slot | Margin % of Budget | Notes |
|------|-------------------|-------|
| 1 | 20% | First contrarian entry, highest conviction |
| 2 | 15% | Second independent signal |
| 3 | 12% | Third position, reduced size |

**Total at max fill: ~47% of budget.** Large buffer — contrarian strategies need room for drawdowns and funding income accumulation.

**Max 3 concurrent positions.** Crowding unwinds are correlated — 3 is already risky.

**Leverage:** 7-10x. Higher leverage amplifies the unwind payoff but tightens effective stops. Default 8x.

**Minimum leverage filter:** Asset must support ≥ 7x max leverage. Skip otherwise.

---

## Funding Income as Edge

### The Math

At 50% annualized funding, 10x leverage:
- **Hourly income:** ~0.057% of margin
- **Per 8-hour funding period:** ~0.46% of margin
- **Per day holding contrarian:** ~1.4% ROE

This is pure income for holding the position — the crowd pays you to wait.

### Funding-Adjusted DSL Floor

The absolute floor is calculated normally, but funding income effectively raises it over time:

```
effective_floor = absolute_floor + cumulative_funding_income
```

**Implementation:** `owl-risk.py` checks funding income every 5 minutes. If position has accumulated measurable funding (>0.1% of margin), it calls `edit_position` to tighten the on-chain SL by the funding amount earned. This locks in funding income as protected profit.

**Example:** Entry at $100, absolute floor at $97.14 (0.03/7x leverage). After 6 hours at 50% annualized funding:
- Funding earned: ~$4.10 on $1,000 notional = ~0.41% of entry
- New effective floor: $97.55 — you're now risking less than you started with

### Funding in Entry Decision

Crowding scanner output includes `estimated_daily_funding_yield` per CROWDED asset. When two READY assets have similar exhaustion scores, prefer the one with higher funding yield — you earn more for the same contrarian bet.

---

## DSL v5 Configuration — Contrarian-Specific

### Phase 1 (Pre-Tier 1): Wide and Patient

Contrarian entries go AGAINST the prevailing trend. The crowd often pushes one more time before the unwind. OWL's Phase 1 must survive that "one more push."

| Setting | Value | Rationale |
|---------|-------|-----------|
| `retraceThreshold` | 0.03/leverage | ~30% max ROE loss — wider than momentum strategies |
| `hardTimeoutMin` | 75 | Contrarian entries need time; cascade may not start for 60+ min |
| `weakPeakCutMin` | 40 | If peak ROE was < 3% and declining after 40 min → cut |
| `deadWeightCutMin` | 25 | Never positive after 25 min → cut (looser than FOX's 10min) |
| `greenIn10TightenPct` | 0 (disabled) | Not applicable — contrarian entries routinely go red initially |
| `consecutiveBreachesRequired` | 3 | Standard |

**absoluteFloor:** LONG = `entry × (1 - 0.03/leverage)`, SHORT = `entry × (1 + 0.03/leverage)`

**Why 60-75 minutes:** An entry that gets stopped at minute 45 and then works at minute 60 is the most expensive kind of loss — you were right and still lost money. The wider timeout accepts more risk per trade in exchange for not getting shaken out of correct contrarian calls.

### Phase 2 (Tier 1+): Standard Trailing

Once the unwind starts, it behaves like any trend. Standard trailing tiers.

| Tier | ROE Trigger | Lock % | Retrace | Breaches |
|------|-------------|--------|---------|----------|
| 1 | 8% | 3% | 2.0% | 2 |
| 2 | 15% | 8% | 1.5% | 2 |
| 3 | 25% | 18% | 1.2% | 2 |
| 4 | 40% | 30% | 1.0% | 1 |
| 5 | 60% | 48% | 0.8% | 1 |
| 6 | 80% | 68% | 0.6% | 1 |

**Tier 1 trigger at 8% ROE (not 5%):** Contrarian entries have wider Phase 1 floors. We don't want to lock trailing too early when the position might still be volatile. 8% confirms the unwind is real.

### DSL State File Creation

```json
{
  "active": true,
  "asset": "ASSET",
  "direction": "SHORT",
  "leverage": 8,
  "entryPrice": 100.00,
  "size": 12.5,
  "wallet": "0x...",
  "strategyId": "UUID",
  "phase": 1,
  "phase1": {
    "retraceThreshold": 0.03,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": 99.625,
    "hardTimeoutMin": 75,
    "weakPeakCutMin": 40,
    "deadWeightCutMin": 25,
    "greenIn10TightenPct": 0
  },
  "greenIn10": false,
  "score": null,
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 8, "lockPct": 3},
    {"triggerPct": 15, "lockPct": 8},
    {"triggerPct": 25, "lockPct": 18},
    {"triggerPct": 40, "lockPct": 30},
    {"triggerPct": 60, "lockPct": 48},
    {"triggerPct": 80, "lockPct": 68}
  ],
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": 100.00,
  "floorPrice": 99.625,
  "currentBreachCount": 0,
  "createdAt": "ISO_TIMESTAMP",
  "crowdingScoreAtEntry": 0.72,
  "exhaustionScoreAtEntry": 0.61,
  "crowdedDirection": "LONG",
  "entryTriggers": ["oi_drop", "sm_flip"],
  "fundingRateAtEntry": 0.00065
}
```

**Filename:** Main dex: `{ASSET}.json`. XYZ dex: `xyz--SYMBOL.json` (colon → double-dash).

---

## Risk Management

### Hard Limits

| Rule | Limit | Config Key |
|------|-------|-----------|
| Max concurrent positions | 3 | `maxSlots` |
| Max single trade loss | 6% of balance | `maxSingleLossPct` |
| Max daily loss | 10% of day-start balance | `maxDailyLossPct` |
| Max drawdown from peak | 18% | `maxDrawdownPct` |
| BTC-correlated positions | Max 1 | `maxBtcCorrelatedSlots` |
| Same-direction positions | Max 2 | `maxSameDirectionSlots` |

### Dynamic Exits (owl-risk.py)

| Condition | Action | Priority |
|-----------|--------|----------|
| **Crowd re-crowding** | Crowding score INCREASES after entry (crowd growing, not unwinding) | CRITICAL — close immediately |
| **OI recovery** | OI rebounds to pre-entry level within 30min | HIGH — thesis invalidated |
| **Funding flips to our side** | We're no longer contrarian; crowd has flipped | MEDIUM — tighten stops to breakeven |
| **Correlation cascade** | BTC dumps and our alt-short is just following BTC | MEDIUM — close if not independently crowded |

**Crowd re-crowding is the #1 kill signal.** If the crowd is getting BIGGER after we entered contrarian, we're wrong. The unwind isn't happening. Exit immediately regardless of PnL.

---

## Anti-Patterns

1. **NEVER enter flash crowding.** Crowding must persist ≥ 2 hours. A sudden funding spike from one whale is not a crowding event.
2. **NEVER fight a growing crowd.** If OI keeps rising after entry → cut. The crowd can stay irrational longer than you can stay solvent.
3. **NEVER take BTC-correlated alt positions when BTC is the crowded asset.** Trade BTC directly or find independently-crowded assets.
4. **NEVER hold through re-crowding.** If the crowding score increases after entry, the thesis is dead.
5. **NEVER enter without exhaustion.** Crowding alone is not a signal. Crowding + exhaustion is the signal.
6. **NEVER disable the persistence filter.** 2h minimum prevents false signals from single-position funding spikes.
7. **NEVER use greenIn10 for OWL.** Contrarian entries routinely go red initially — this rule would kill every good trade.

---

## Cron Architecture

| # | Job | Interval | Session | Script | Model Tier |
|---|-----|----------|---------|--------|------------|
| 1 | OI Tracker | 5 min | isolated | `oi-tracker.py` | Budget |
| 2 | Crowding Scanner | 5 min | isolated | `crowding-scanner.py` | Budget |
| 3 | Exhaustion Detector | 3 min | isolated | `exhaustion-detector.py` | Budget |
| 4 | Entry Trigger | 3 min | main | `owl-entry.py` | Primary |
| 5 | Risk Guardian | 5 min | isolated | `owl-risk.py` | Mid |
| 6 | DSL v5 | 3 min | isolated | `dsl-v5.py` (shared) | Mid |

**Silence Policy:** When a cron fires and finds no actionable signals → HEARTBEAT_OK. No Telegram notifications, no chat output, no explanation. Only speak when something happens: trade opened, trade closed, crowding alert, risk halt.

**Model tiers:**
- **Budget** (OI Tracker, Crowding Scanner, Exhaustion Detector): Threshold checks, data collection. model configured in OpenClaw.
- **Mid** (Risk Guardian, DSL): Structured judgment. model configured in OpenClaw.
- **Primary** (Entry Trigger): Complex contrarian judgment, multi-signal evaluation. Runs on main session model.

**Stagger offsets:** :00 OI Tracker, :01 Crowding Scanner, :02 Exhaustion Detector, :03 Entry Trigger, :04 Risk Guardian. DSL at :30/:00/:30.

---

## State Schema

### owl-state.json

```json
{
  "version": 1,
  "instanceKey": "owl-{strategyId-prefix}",
  "strategyId": "UUID",
  "wallet": "0x...",
  "budget": 2000,
  "chatId": "12345",
  "maxSlots": 3,
  "maxLeverage": 10,
  "defaultLeverage": 8,
  "activePositions": {},
  "dailyStats": {
    "date": "2026-03-05",
    "tradesOpened": 0,
    "tradesClosed": 0,
    "realizedPnl": 0,
    "fundingIncome": 0,
    "dayStartBalance": 2000
  },
  "safetyFlags": {
    "dailyLossHalted": false,
    "drawdownHalted": false
  },
  "crowdedAssets": {},
  "readyAssets": {},
  "updatedAt": "ISO_TIMESTAMP"
}
```

### crowding-history.json

```json
{
  "version": 1,
  "snapshots": {
    "BTC": [
      {
        "timestamp": "ISO",
        "oi": 1250000000,
        "fundingRate": 0.00065,
        "fundingAnnualized": 57.0,
        "crowdingScore": 0.72,
        "crowdedDirection": "LONG",
        "smConcentration": 0.75,
        "bookImbalance": 3.2
      }
    ]
  },
  "persistenceCount": {
    "BTC": 28,
    "SOL": 3
  },
  "oiBaselines": {
    "BTC": { "avg24h": 1100000000, "avg7d": 950000000 }
  },
  "updatedAt": "ISO_TIMESTAMP"
}
```

**Retention:** Keep 48h of snapshots per asset (576 entries at 5min). Prune older entries on each write.

---

## Expected Behavior

| Metric | Target |
|--------|--------|
| Trades per week | 3-5 |
| Trades per day | 0-2 (often 0) |
| Win rate | 45-55% (lower than momentum, but larger winners) |
| Avg winner : avg loser | 2.5:1+ (unwinds are violent) |
| Best conditions | Extreme funding, late-cycle crowding, exhaustion visible |
| Worst conditions | Low funding, balanced OI, no extremes (few signals) |
| Typical hold time | 1-12 hours (short if cascade is fast, longer if gradual unwind) |

**Key insight:** OWL's edge is not win rate — it's asymmetry. When crowding unwinds, the cascade amplifies the move. A 30% ROE winner covers several 15% ROE losers.

---

## API Dependencies

| Tool | Used By | Purpose |
|------|---------|---------|
| `market_list_instruments` | OI Tracker, Crowding Scanner | OI, funding, volume for all assets |
| `market_get_asset_data` | Crowding Scanner, Exhaustion Detector, Entry Trigger | Candles, order book, funding history |
| `leaderboard_get_markets` | Crowding Scanner, Exhaustion Detector | SM concentration by market |
| `market_get_prices` | DSL v5 | Current prices for trailing stops |
| `create_position` | Entry Trigger (agent) | Open contrarian positions |
| `close_position` | DSL v5, Risk Guardian | Close positions |
| `edit_position` | Risk Guardian (funding floor), DSL v5 | Tighten SL with funding income |
| `strategy_get_clearinghouse_state` | Risk Guardian | Margin check, position verification |
| `account_get_portfolio` | Setup | Balance verification |

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/owl-setup.py` | Setup wizard — creates config from budget/wallet |
| `scripts/owl_config.py` | Shared config loader, mcporter wrapper |
| `scripts/owl_lib.py` | Shared utilities (atomic write, scoring, indicators) |
| `scripts/oi-tracker.py` | OI/funding snapshot collector (every 5min) |
| `scripts/crowding-scanner.py` | Crowding score calculator with persistence tracking |
| `scripts/exhaustion-detector.py` | Exhaustion signal detector for CROWDED assets |
| `scripts/owl-entry.py` | Entry trigger evaluator for READY assets |
| `scripts/owl-risk.py` | Risk guardian: re-crowding exit, funding floor, correlation |

---

## Known Limitations

- **OI history bootstrap:** Need ~2h of snapshots before persistence filter qualifies anything. First 2 hours = no trades.
- **mcporter latency:** ~6s per call. Limits deep-scan to 6-8 assets per cycle.
- **Funding income SL adjustment:** Requires `edit_position` call which has latency. Funding floor tightening happens every 5min, not real-time.
- **BTC correlation detection:** Heuristic-based (same crowded direction + score dependency). Not a statistical correlation coefficient.
- **No liquidation heatmap:** Can't see exact liquidation levels. Infers cascade from OI drop + price acceleration.
- **Crowding can persist:** Markets can stay crowded for days/weeks. OWL may go long periods without signals in balanced markets.

---

## Optimization Levers

| Lever | Config Key | Conservative | Default | Aggressive |
|-------|-----------|-------------|---------|------------|
| Crowding threshold | `minCrowdingScore` | 0.70 | 0.60 | 0.50 |
| Persistence minimum (snapshots) | `minPersistenceCount` | 36 (3h) | 24 (2h) | 12 (1h) |
| Exhaustion threshold | `minExhaustionScore` | 0.60 | 0.50 | 0.40 |
| Entry triggers required | `minEntryTriggers` | 3 | 2 | 2 |
| Phase 1 retrace | `phase1RetraceBase` | 0.025 | 0.03 | 0.035 |
| Phase 1 hard timeout | `phase1HardTimeoutMin` | 60 | 75 | 90 |
| Max leverage | `maxLeverage` | 7 | 8 | 10 |
| Max slots | `maxSlots` | 2 | 3 | 3 |

---

## Lessons from Existing Strategies (Applied)

From WOLF/FOX (14-trade analysis):
- **Direction was right 85% of the time, still lost money** → OWL uses wider Phase 1 to avoid being shaken out of correct calls
- **Tight stops killed winning trades** → 0.03/leverage floor, 75min timeout

From VIPER:
- **Stale state = phantom positions** → OWL reconciles with clearinghouse on every Risk Guardian cycle
- **API transient errors** → All scripts have retry logic and consecutive failure counters

From TIGER:
- **OI history is essential** → OWL builds its own OI tracker from day 1
- **Per-asset error isolation** → Every scanner wraps per-asset loops in try/except
