# TIGER v5.0 — Technical Documentation

Multi-scanner goal-based trading system for Hyperliquid perpetuals via Senpi MCP.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Skill Architecture](#skill-architecture)
3. [Trading Characteristics](#trading-characteristics)
4. [What It Covers](#what-it-covers)
5. [Automated Test Coverage](#automated-test-coverage)

---

## How It Works

TIGER is a fully automated trading system that takes a **budget**, **profit target**, and **deadline**, then executes a multi-scanner strategy to reach the target within the time constraint. It continuously adjusts its aggression based on progress and manages all positions through mechanical entry, trailing-stop management, and pattern-specific exits.

### Execution Lifecycle

```
                    ┌──────────────────┐
                    │   Prescreener    │  Every 5m — scores ~230 assets
                    │  (prescreener.py)│  writes top 30 to prescreened.json
                    └────────┬─────────┘
                             │ candidates
         ┌───────────┬───────┼───────┬────────────┐
         ▼           ▼       ▼       ▼            ▼
    ┌─────────┐ ┌────────┐ ┌────┐ ┌────────┐ ┌────────┐
    │Compress.│ │Correl. │ │Mom.│ │Revers. │ │Funding │  5 scanners score
    │  (5m)   │ │  (3m)  │ │(5m)│ │  (5m)  │ │ (30m)  │  signals by confluence
    └────┬────┘ └───┬────┘ └─┬──┘ └───┬────┘ └───┬────┘
         │          │        │        │           │
         └──────────┴────────┼────────┴───────────┘
                             ▼
                    ┌──────────────────┐
                    │   Agent Decides  │  Confluence ≥ threshold?
                    │  create_position │  Slots available? Not halted?
                    └────────┬─────────┘
                             │ position opened
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌──────────────┐ ┌───────────┐ ┌────────────┐
     │  DSL v4      │ │ Exit      │ │   Risk     │
     │ Trailing Stop│ │ Checker   │ │  Guardian  │
     │   (30s)      │ │  (5m)     │ │   (5m)     │
     └──────────────┘ └───────────┘ └────────────┘
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    ┌──────────────────┐
                    │  close_position  │  Mechanical exits
                    └──────────────────┘
```

### Step-by-Step Flow

1. **Setup**: User runs `tiger-setup.py` with wallet, strategy ID, budget, target, deadline, and chat ID. This creates `tiger-config.json` and initializes state files.

2. **Prescreening** (every 5m): Scores all ~230 Hyperliquid assets in a single API call. Ranks by a composite score (35% momentum + 20% funding rate + 15% OI ratio + 30% volume rank). Outputs top 30 candidates split into `group_a` (higher volatility) and `group_b`.

3. **Scanning** (5 scanners, staggered): Each scanner reads the prescreened candidates and evaluates them against pattern-specific confluence factors. Assets scoring above the aggression-adjusted confluence threshold produce actionable signals.

4. **Entry**: The agent evaluates scanner output. If a signal is actionable (confluence above threshold, slots available, not halted), it calls `create_position` with computed leverage and margin.

5. **Position Management**: Three parallel systems manage open positions:
   - **DSL v4** (every 30s): Trailing stop with 2-phase architecture. Auto-closes on breach.
   - **Exit Checker** (every 5m): Pattern-specific exit rules (false breakout, stagnation, time stop, daily target).
   - **Risk Guardian** (every 5m): Hard risk limits (single-trade loss, daily loss, drawdown, OI collapse, funding reversal).

6. **Goal Recalculation** (hourly): Goal Engine computes required daily return based on current balance vs. target and remaining time. Sets aggression level (CONSERVATIVE/NORMAL/ELEVATED/ABORT) which tunes confluence thresholds, trailing locks, and leverage.

7. **Self-Optimization** (every 8h): ROAR meta-optimizer analyzes the trade log, applies 6 rules to tune execution parameters (confluence thresholds, DSL retrace, scanner thresholds) within bounded ranges. Reverts changes if performance degrades.

### Data Flow

All scripts share state through JSON files in `state/{strategyId}/`, written atomically via `os.replace()`. No database, no IPC — just files. This makes the system debuggable (every state file is human-readable) and crash-safe (partial writes never corrupt state).

---

## Skill Architecture

### Directory Layout

```
tiger/
├── SKILL.md                          # Skill definition (agent-facing)
├── README.md                         # Quick start guide
├── DOCUMENTATION.md                  # This file
├── pending-enforcements.doc.md       # Design doc: advisory→deterministic enforcement
│
├── scripts/                          # 16 Python scripts
│   ├── tiger_lib.py                  # Technical analysis library (pure stdlib)
│   ├── tiger_config.py               # Config, state, MCP helpers, atomic I/O
│   ├── tiger-setup.py                # Setup wizard
│   ├── prescreener.py                # Phase 1: Score ~230 assets → top 30
│   ├── compression-scanner.py        # Scanner: BB squeeze + OI breakout
│   ├── correlation-scanner.py        # Scanner: BTC lag detection
│   ├── momentum-scanner.py           # Scanner: Price move + volume
│   ├── reversion-scanner.py          # Scanner: RSI extremes (mean reversion)
│   ├── funding-scanner.py            # Scanner: Funding rate arbitrage
│   ├── oi-tracker.py                 # Data: OI history sampling
│   ├── goal-engine.py                # Strategy: Aggression recalculation
│   ├── risk-guardian.py              # Safety: Risk limit enforcement
│   ├── tiger-exit.py                 # Exits: Pattern-specific close logic
│   ├── dsl-v4.py                     # Exits: 2-phase trailing stops
│   ├── roar-analyst.py               # Optimizer: Performance-based tuning
│   └── roar_config.py                # Optimizer: Bounds, state, revert logic
│
├── references/                       # Documentation supplements
│   ├── config-schema.md              # All config keys with types and defaults
│   ├── state-schema.md               # State file formats and field descriptions
│   ├── scanner-details.md            # Deep-dive on each scanner's logic
│   ├── setup-guide.md                # Installation and tuning walkthrough
│   └── cron-templates.md             # Ready-to-use OpenClaw cron payloads
│
├── tests/                            # 11 test files, 255 test functions
│   ├── conftest.py                   # Shared fixtures and sample data
│   ├── test_tiger_lib.py             # Technical analysis indicators
│   ├── test_tiger_config.py          # Config loading, normalization, I/O
│   ├── test_compression.py           # Compression scanner scoring
│   ├── test_correlation.py           # BTC correlation lag detection
│   ├── test_dsl_v4.py                # 2-phase trailing stop engine
│   ├── test_prescreener.py           # Asset prescreening pipeline
│   ├── test_risk_guardian.py         # Risk limit enforcement
│   ├── test_roar_analyst.py          # ROAR rules and scorecard
│   ├── test_roar_config.py           # Bounds, protection, revert logic
│   ├── test_tiger_exit.py            # Pattern-specific exit conditions
│   └── (test_momentum, test_reversion — implicitly covered via integration)
│
└── state/                            # Runtime state (per strategy instance)
    └── {strategyId}/
        ├── tiger-state.json          # Core: positions, aggression, safety flags
        ├── dsl-{ASSET}.json          # Per-position trailing stop state
        ├── oi-history.json           # 24h OI time-series (~288 entries/asset)
        ├── trade-log.json            # Complete trade history with outcomes
        ├── roar-state.json           # ROAR optimizer state + previous config
        ├── btc-cache.json            # BTC price cache (correlation optimization)
        └── prescreened.json          # Top 30 prescreened candidates
```

### Component Layers

```
┌──────────────────────────────────────────────────────────┐
│                    12 OpenClaw Crons                      │
│  Prescreener(5m) Compress(5m) Corr(3m) Momentum(5m)     │
│  Reversion(5m) Funding(30m) OI(5m) Goal(1h) Risk(5m)    │
│  Exit(5m) DSL(30s) ROAR(8h)                             │
├──────────────────────────────────────────────────────────┤
│                    Python Scripts                         │
│  Scanners → Goal Engine → Risk/Exit/DSL → ROAR          │
├──────────────────────────────────────────────────────────┤
│                 Shared Libraries                          │
│  tiger_lib.py (TA indicators)  tiger_config.py (infra)  │
├──────────────────────────────────────────────────────────┤
│                 Senpi MCP (via mcporter)                  │
│  Market data, positions, orders, portfolio, leaderboard  │
├──────────────────────────────────────────────────────────┤
│                    State Files                            │
│  tiger-config.json → state/{id}/*.json (atomic writes)   │
└──────────────────────────────────────────────────────────┘
```

### Shared Libraries

#### tiger_lib.py — Technical Analysis (Pure Stdlib)

Zero external dependencies. All indicators implemented with only `math` and `statistics`:

| Function | Purpose |
|----------|---------|
| `sma(data, period)` | Simple moving average |
| `ema(data, period)` | Exponential moving average (Wilder's smoothing) |
| `rsi(closes, period)` | Relative Strength Index |
| `bollinger_bands(closes, period, num_std)` | Upper/mid/lower bands |
| `bb_width(closes, period, num_std)` | Band width (squeeze detection) |
| `bb_width_percentile(widths, current)` | Width percentile rank |
| `atr(highs, lows, closes, period)` | Average True Range |
| `volume_ratio(volumes, short, long)` | Short-term / long-term volume ratio |
| `oi_change_pct(oi_series)` | Open Interest change percentage |
| `detect_rsi_divergence(prices, rsi_values)` | Bullish/bearish divergence |
| `confluence_score(factors, weights)` | Weighted factor summation (0-1) |
| `kelly_fraction(win_rate, avg_win, avg_loss)` | Half-Kelly position sizing (capped 25%) |
| `required_daily_return(current, target, days)` | Compound daily rate needed |
| `aggression_mode(daily_rate)` | Map rate → CONSERVATIVE/NORMAL/ELEVATED/ABORT |
| `parse_candles(raw)` | Extract OHLCV arrays from Senpi candle format |

#### tiger_config.py — Infrastructure

| Component | Purpose |
|-----------|---------|
| `resolve_dependencies()` | Injectable dependency resolution (testable) |
| `atomic_write(path, data)` | Crash-safe JSON writes via `os.replace()` |
| `deep_merge(base, override)` | Recursive config merging with nested defaults |
| `mcporter_call(tool, args)` | 3-attempt retry wrapper for Senpi MCP calls |
| `AliasDict` | Transparent `snake_case` → `camelCase` key normalization |
| `load_config()` | Read config with alias resolution |
| `load_state() / save_state()` | State file I/O with atomic guarantees |
| `get_active_positions()` | Parse clearinghouse for open positions |
| `is_halted()` | Check safety halt flag |

### Cron Architecture

12 crons orchestrated via OpenClaw, split into two model tiers:

| Tier | Models | Used By | Rationale |
|------|--------|---------|-----------|
| Tier 1 (fast/cheap) | claude-haiku-4-5, gpt-4o-mini | Scanners, prescreener, OI tracker, DSL | Math-heavy, high-frequency, no judgment needed |
| Tier 2 (capable) | claude-sonnet-4-6, gpt-4o | Goal engine, risk guardian, exit checker, ROAR | Requires evaluation, context, risk judgment |

**Session types**:
- `main` session + `systemEvent`: Scanners and enforcers that need to interact with the agent (execute trades, update state)
- `isolated` session + `agentTurn`: Prescreener and ROAR that write files independently (no trade execution)

**Stagger schedule** (avoids mcporter rate limits):

| Offset | Job |
|--------|-----|
| :00 | Compression Scanner |
| :01 | Momentum Scanner |
| :02 | Reversion Scanner |
| :03 | OI Tracker |
| :04 | Risk Guardian + Exit Checker |
| own cadence | Correlation (3m), Funding (30m), DSL (30s) |

### Config & State Design

**Config** (`tiger-config.json`): Single source of truth. All keys are canonical `camelCase`, but `snake_case` is accepted and auto-normalized via `AliasDict`. Protected keys (budget, target, risk limits) are never modified by ROAR.

**State** (`state/{strategyId}/*.json`): All files include `version`, `active`, `instanceKey`, `createdAt`, `updatedAt`. All writes use `atomic_write()` — a temp file is written then atomically renamed via `os.replace()`, preventing corruption on crash.

### Execution Model: Deterministic vs. Advisory

| Action | Execution | Script |
|--------|-----------|--------|
| CLOSE | Deterministic — calls `close_position()` directly | risk-guardian, tiger-exit, dsl-v4 |
| CLOSE_ALL | Deterministic — iterates all positions, closes each | risk-guardian |
| TAKE_PROFIT | Deterministic — calls `close_position()` | risk-guardian |
| HALT | Deterministic — sets `halted: true` in state | risk-guardian |
| REDUCE | Advisory — recommends size reduction | risk-guardian |
| PARTIAL_75 | Advisory — recommends 75% close | tiger-exit |

Advisory actions remain so because they require position-size computation that benefits from agent contextual judgment (see `pending-enforcements.doc.md` for the full design rationale).

---

## Trading Characteristics

### 5 Signal Patterns

#### 1. COMPRESSION_BREAKOUT

**Thesis**: Bollinger Band squeeze (low volatility consolidation) with accumulating open interest resolves into a directional breakout.

| Factor | Weight | Condition |
|--------|--------|-----------|
| BB squeeze (4h) | 0.20 | Width below `bbSqueezePercentile` (default: 35th percentile) |
| BB breakout (1h) | 0.20 | Price closes outside 1h Bollinger Bands |
| OI building | 0.15 | OI rising >5% in 1h |
| OI-price divergence | 0.10 | OI rising while price flat |
| Volume surge | 0.15 | Short volume > 1.5x long average |
| RSI not extreme | 0.10 | RSI between 30-70 |
| Funding aligned | 0.05 | Funding rate favors breakout direction |
| ATR expanding | 0.05 | ATR > 2% |

**Key requirement**: Both `breakout: true` AND a `direction` must be present for a signal to be actionable. A high squeeze score alone is not sufficient.

**DSL tuning**: Standard Phase 1 retrace (0.015 = 1.5%).

#### 2. CORRELATION_LAG

**Thesis**: When BTC makes a significant move (>2% in 1-4h), historically correlated altcoins lag behind, creating a catch-up trade opportunity.

| Factor | Weight | Condition |
|--------|--------|-----------|
| BTC significant move | 0.20 | > `btcCorrelationMovePct` (default: 2%) in 1-4h |
| Alt lagging | 0.25 | Lag ratio ≥ 0.5 (alt moved less than half of BTC) |
| Volume quiet | 0.15 | Alt volume hasn't spiked yet |
| RSI safe | 0.10 | Not at extremes |
| SM aligned | 0.15 | Smart money direction matches BTC direction |
| High correlation | 0.10 | Asset in known high-correlation list (23 pairs) |
| Sufficient leverage | 0.05 | Max leverage ≥ `minLeverage` |

**Window quality**: STRONG (lag > 0.7), MODERATE (0.5-0.7), CLOSING (0.4-0.5).

**Optimization**: Scans 23 known high-corr alts first, then extends to 20 additional liquid assets only if no strong signals found. BTC price is cached to reduce API calls.

**DSL tuning**: Tight Phase 1 retrace (0.015). Window closes fast — if the alt catches up, the edge is gone.

#### 3. MOMENTUM_BREAKOUT

**Thesis**: Strong price momentum with volume confirmation indicates directional continuation.

| Factor | Weight | Condition |
|--------|--------|-----------|
| 1h price move | 0.25 | > 1.5% |
| 2h price move | 0.15 | > 2.5% |
| Volume surge | 0.20 | Ratio > 1.5x |
| 4h trend aligned | 0.15 | Move matches 4h candle direction |
| RSI not extreme | 0.10 | Between 30-70 |
| SMA aligned | 0.10 | Price on correct side of SMA20 |
| ATR healthy | 0.05 | > 1.5% |

**Simpler than compression**: No BB squeeze required, just price movement with volume.

**DSL tuning**: Tighter Phase 1 retrace (0.012 = 1.2%) — momentum reversals are fast.

#### 4. MEAN_REVERSION

**Thesis**: Overextended assets with exhaustion signals revert toward the mean.

| Factor | Weight | Condition |
|--------|--------|-----------|
| RSI extreme (4h) | 0.20 | > `rsiOverbought` (75) or < `rsiOversold` (25) — **required** |
| RSI extreme (1h) | 0.10 | Confirms 4h reading |
| RSI divergence | 0.20 | Divergence aligned with reversal direction |
| Price extended | 0.10 | > 10% move in 24h |
| Volume exhaustion | 0.10 | Declining volume on extension |
| At extreme BB | 0.10 | Price beyond Bollinger Bands |
| OI crowded | 0.10 | OI 15%+ above average |
| Funding pays us | 0.10 | Collect funding in counter-trend direction |

**Hard filter**: The 4h RSI extreme is a mandatory precondition. Without it, no reversion signal is generated regardless of other factors.

**DSL tuning**: Standard Phase 1 retrace (0.015). Expects 2-3 ATR moves.

#### 5. FUNDING_ARB

**Thesis**: Extreme funding rates represent crowd overexposure. Going opposite the crowd collects funding income while the position is open.

| Factor | Weight | Condition |
|--------|--------|-----------|
| Extreme funding | 0.25 | Annualized > `minFundingAnnualizedPct` (default: 30%) |
| Trend aligned | 0.20 | SMA20 supports counter-crowd direction |
| RSI safe | 0.15 | Not extreme against our direction |
| OI stable | 0.15 | Funding source (OI) not collapsing |
| SM aligned | 0.10 | Smart money on our side |
| High daily yield | 0.10 | > 5% daily yield on margin |
| Volume healthy | 0.05 | > $10M daily volume |

**Direction logic**: Positive funding → SHORT (longs pay shorts). Negative funding → LONG (shorts pay longs).

**Risk**: Funding can flip suddenly. Risk Guardian auto-exits if funding reverses or weakens below 10% annualized.

**DSL tuning**: Wider Phase 1 retrace (0.020+). Edge is income over time, not price direction.

### Position Sizing

**Half-Kelly formula**: `f = 0.5 * (win_rate * b - (1 - win_rate)) / b` where `b = avg_win / avg_loss`. Capped at 25% of balance per position.

**Per-slot budget**: `current_balance * kelly_fraction`, subject to `maxSlots` (default: 3 concurrent positions).

**Leverage**: Adaptive based on aggression. CONSERVATIVE 5-7x, NORMAL 7-10x, ELEVATED 10-15x, ABORT reduces (no new entries).

### Goal Engine & Aggression

Hourly recalculation of required daily return: `daily_rate = (target / current) ^ (1 / days_remaining) - 1`

| Aggression | Daily Rate | Min Confluence | Trailing Lock | Behavior |
|------------|-----------|----------------|---------------|----------|
| CONSERVATIVE | < 8% | 0.70 | 80% | Take profits early, tight stops |
| NORMAL | 8-15% | 0.40 | 60% | Standard operation |
| ELEVATED | 15-25% | 0.40 | 40% | Wider entries, lower threshold |
| ABORT | > 25% | 999 (never triggers) | 90% | Stop new entries, tighten all stops |

**Halt conditions**: ABORT aggression, max drawdown breached (20%), deadline reached, or target achieved.

### DSL v4 — 2-Phase Trailing Stop

Per-position state file. Combined runner checks all active positions every 30 seconds.

**Phase 1** (entry → first tier):
- Absolute price floor to prevent catastrophic loss
- Trailing floor: `high_water * (1 - retrace_threshold)` (LONG)
- Absolute floor: `entry_price * (1 - phase1.retrace)` — never violated
- 3 consecutive breaches → auto-close
- Max duration: 90 minutes
- Breach decay modes: "soft" (decay by 1 each check) or "hard" (reset to 0)

**Phase 2** (tier 1+ activation at ROE ≥ 5%):

| Tier | ROE Trigger | Lock % of HWM | Retrace | Breaches to Close |
|------|-------------|---------------|---------|-------------------|
| 1 | 5% | 20% | 1.5% | 2 |
| 2 | 10% | 50% | 1.2% | 2 |
| 3 | 20% | 70% | 1.0% | 2 |
| 4 | 35% | 80% | 0.8% | 1 |

**Stagnation take-profit**: ROE ≥ 8% with no new high-water for 1h → auto-close.

### Risk Management

Enforced by `risk-guardian.py` every 5 minutes:

| Rule | Limit | Default | Action |
|------|-------|---------|--------|
| Single trade loss | % of balance | 5% | CLOSE immediately |
| Daily loss | % of day-start balance | 12% | HALT + CLOSE ALL |
| Drawdown from peak | % of peak balance | 20% | HALT |
| OI collapse | OI drops >X% in 1h | 25% | CLOSE affected position |
| OI drop | OI drops >X% in 1h | 10% | REDUCE (advisory) |
| Funding reversal | Funding flips on FUNDING_ARB | Auto | CLOSE |
| Deadline proximity | Final 24h | Auto | Tighten all stops to 90% lock |
| Max concurrent positions | Number of slots | 3 | Block new entries |

### Exit Checker — Pattern-Specific Rules

| Exit Rule | Applies To | Condition | Action |
|-----------|-----------|-----------|--------|
| False breakout | Compression only | Re-enters BB range within 2 candles + ROE <3% | CLOSE |
| Daily target hit | All patterns | Unrealized PnL ≥ daily target USD | CLOSE |
| Trailing lock | All patterns | ROE below lock% of peak HWM | CLOSE |
| Conservative TP | All (CONSERVATIVE) | PnL > 50% daily target | PARTIAL_75 |
| Stagnation | All patterns | ROE >3% but flat for 2+ hours | CLOSE |
| Time stop | All patterns | Losing >30min + ROE <0% | CLOSE |
| Deadline | All patterns | Days remaining ≤ 0 | CLOSE ALL |

### ROAR Meta-Optimizer

Runs every 8h (+ ad-hoc every 5th trade). Analyzes trade log, applies 6 rules:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | Win rate < 40% (10+ trades) | Raise pattern confluence +0.05 |
| 2 | Win rate > 70% (10+ trades) | Lower threshold -0.03 |
| 3 | Avg DSL exit tier < 2 | Loosen phase1 retrace +0.002 |
| 4 | Avg DSL exit tier ≥ 4 | Tighten phase1 retrace -0.001 |
| 5 | No entries in 48h (5+ trades) | Lower threshold -0.02 |
| 6 | Negative expectancy (20+ trades) | Disable pattern for 48h |

**Protected keys** (never modified): budget, target, deadlineDays, maxSlots, maxLeverage, maxDrawdownPct, maxDailyLossPct, maxSingleLossPct, strategyWallet.

**Safety net**: If both win rate AND avg PnL degraded since last adjustment, ROAR auto-reverts to the previous config snapshot.

**Tunable bounds** (hard min/max per parameter):

| Parameter | Min | Max |
|-----------|-----|-----|
| Confluence scores (all aggression) | 0.25 | 0.85 |
| BB squeeze percentile | 15 | 45 |
| BTC correlation move % | 1.5 | 5.0 |
| Funding annualized % | 15.0 | 60.0 |
| DSL retrace (phase 1 & 2) | 0.008 | 0.03 |

### Expected Performance

| Metric | Target Range |
|--------|-------------|
| Win rate | 55-65% |
| Profit factor | 1.8-2.5 |
| Trades per day | 2-8 |
| Best market conditions | Volatile with clean setups (squeeze → breakout) |
| Worst market conditions | Low-vol grind (few signals), choppy (false breakouts) |

### Known Limitations

- **OI history bootstrap**: Scanners need ~1h of OI data before OI-dependent signals are reliable.
- **mcporter latency**: ~4-6s per API call. Scanners limited to ~8 assets per 55s scan window.
- **DSL is per-position**: Each position requires its own DSL state file with `active: true`.
- **Correlation scanner assumes BTC leads**: Doesn't capture scenarios where alts lead BTC.
- **Funding arb needs patience**: Income-based edge; DSL must be wide enough to avoid premature exits.
- **Goal engine recalculates hourly**: Aggression can shift mid-trade.

---

## What It Covers

### Market Coverage

- **Exchange**: Hyperliquid (perpetual contracts)
- **Universe**: All ~230 Hyperliquid perpetual assets
- **Prescreened to**: Top 30 per cycle (split into group_a and group_b)
- **Per-scanner**: 8-12 assets evaluated per scan cycle (API latency limited)

### Signal Coverage

| Pattern | Market Regime | Direction | Timeframe |
|---------|--------------|-----------|-----------|
| Compression Breakout | Low vol → breakout | Long or Short | 1h/4h |
| Correlation Lag | BTC-driven moves | Long or Short (follows BTC) | 1-4h |
| Momentum Breakout | Trending/volatile | Long or Short | 1-2h |
| Mean Reversion | Overextended | Counter-trend | 4h |
| Funding Arb | Extreme funding | Opposite crowd | Ongoing |

### Risk Coverage

- **Per-trade**: Max single loss 5%, Half-Kelly sizing capped at 25%, leverage limits
- **Per-day**: Max daily loss 12% halt, daily target take-profit
- **Per-strategy**: Max drawdown 20% halt, deadline enforcement, max 3 concurrent slots
- **Per-pattern**: Pattern-specific DSL retrace, false breakout detection, funding reversal exit
- **Microstructure**: OI collapse detection (25%), OI drop warning (10%), volume health checks

### Operational Coverage

- **Entry automation**: 5 scanners × prescreened candidates, staggered crons
- **Exit automation**: DSL trailing stops (30s), exit checker (5m), risk guardian (5m)
- **Self-optimization**: ROAR tunes thresholds within bounds, reverts if worse
- **Notifications**: Telegram alerts for entries, exits, risk events, and ROAR adjustments
- **Crash safety**: Atomic state writes, graceful handling of `CLOSE_NO_POSITION`
- **Rate limit management**: Staggered cron offsets, 55s timeouts, BTC price caching

### API Integration

| MCP Tool | Used By | Purpose |
|----------|---------|---------|
| `market_list_instruments` | All scanners, OI tracker | Asset discovery, OI, funding, volume |
| `market_get_asset_data` | All scanners | Candles (1h, 4h), funding rates |
| `market_get_prices` | Correlation scanner, risk guardian | Current prices |
| `leaderboard_get_markets` | Correlation, funding scanners | Smart money alignment |
| `account_get_portfolio` | Goal engine | Portfolio balance |
| `strategy_get_clearinghouse_state` | Goal engine, risk guardian | Margin, positions, PnL |
| `create_position` | Agent (from scanner output) | Open positions |
| `close_position` | DSL, risk guardian, exit checker | Close positions |
| `edit_position` | Risk guardian | Resize positions |

---

## Automated Test Coverage

### Summary

| File | Test Count | Component |
|------|-----------|-----------|
| `test_tiger_lib.py` | 65 | Technical analysis indicators |
| `test_roar_config.py` | 34 | ROAR bounds, protection, revert logic |
| `test_dsl_v4.py` | 31 | 2-phase trailing stop engine |
| `test_risk_guardian.py` | 29 | Risk limit enforcement |
| `test_tiger_config.py` | 29 | Config loading, normalization, I/O |
| `test_roar_analyst.py` | 19 | ROAR rules engine and scorecard |
| `test_tiger_exit.py` | 15 | Pattern-specific exit conditions |
| `test_correlation.py` | 13 | BTC correlation lag detection |
| `test_prescreener.py` | 12 | Asset prescreening pipeline |
| `test_compression.py` | 8 | Compression scanner scoring |
| **Total** | **255** | |

### Design Principle

All expected values in tests are derived from **mathematical definitions and business rules**, not from running the code. This ensures tests verify correctness against the specification, not just consistency with the implementation.

### What Each Test Suite Covers

#### test_tiger_lib.py (65 tests) — Technical Analysis

Core indicator correctness:
- **SMA**: Basic computation, constant input, length preservation, empty/short input
- **EMA**: Seed calculation (first value = SMA), subsequent values, constant input, insufficient data
- **RSI**: All gains (100), all losses (0), equal gains/losses, known computation, range validation [0-100]
- **Bollinger Bands**: Output length, first valid index, constant series (zero width), band symmetry, upper > lower
- **BB Width**: Output length, constant (zero), wider with more volatility
- **BB Width Percentile**: Range [0-100], constant (zero), insufficient data
- **ATR**: True range uses previous close, output length, insufficient data
- **Volume Ratio**: Equal volumes, increasing volume, insufficient data, zero long average
- **OI Change**: Basic change, decrease, zero base, insufficient data
- **Confluence Score**: All true, all false, partial, empty, weights sum correctly
- **Kelly Fraction**: Positive edge, no edge (returns 0), negative edge (returns 0), capped at 25%
- **Required Daily Return**: Double in 7 days, already at target, zero days remaining, zero current
- **Aggression Mode**: CONSERVATIVE (<8%), NORMAL (8-15%), ELEVATED (15-25%), ABORT (>25%), None → ABORT
- **Parse Candles**: Basic extraction, string-to-float conversion, empty input
- **RSI Divergence**: Bullish divergence, bearish divergence, no divergence, insufficient data

#### test_tiger_config.py (29 tests) — Config & State Infrastructure

Key normalization:
- AliasDict: camelCase direct access, snake_case → camelCase resolution, `in` operator with snake_case, get() with snake_case, write to camelCase when key exists, unknown key raises error

Config operations:
- `deep_merge`: Flat override, nested merge, does not mutate base
- `normalize_key`: snake_to_camel conversion, camelCase passthrough, DSL retrace normalization

State I/O:
- `save_state` / `load_state`: Roundtrip fidelity, halt flag preservation
- `load_scan_state`: Fields persisted, preserved on update, file location
- Instance directory: Uses strategyId, default fallback
- Trade log: Instance directory location, empty when no file
- Disabled patterns: Reads correctly, expired patterns filtered
- Halted state: is_halted true/false detection
- Active positions: Empty portfolio handling
- Pattern confluence: Override from config, aggression-level fallback

#### test_dsl_v4.py (31 tests) — Trailing Stop Engine

Unit normalization:
- `normalize_trigger_pct`: Whole number 5 → 0.05, whole number 100 → 1.0, already decimal passthrough, boundary at exactly 1, boundary just under 1, string input, zero, negative, valid number, invalid string, None, zero string

DSL override application:
- Applies phase1 override, applies phase2 override, no override when empty, creates phase dict if missing

Phase 1 logic:
- uPnL calculation (long and short), trailing floor (long), absolute floor used when higher
- Breach counting: hard reset mode, soft decay mode
- Tier upgrade triggers Phase 2

Phase 2 logic:
- Close on breach threshold, close fails gracefully (CLOSE_NO_POSITION handling)
- High-water mark update (long and short), tier floor calculation (long)
- Pending close triggers close action

General:
- Inactive state returns inactive status, fetch failure counting

#### test_risk_guardian.py (29 tests) — Risk Enforcement

Daily loss:
- No breach (within limits), breach (exceeds limit), at exact limit (no breach)
- Uses budget if no dayStartBalance, zero dayStartBalance falls back to budget, no budget and no dayStartBalance

Drawdown:
- No breach, breach, at peak (no breach), peak is zero

Deadline:
- Reached (0 days), imminent (1 day), approaching (2 days), no alert (many days), negative days

OI checks:
- OI collapse (>25% drop), OI drop (>10% drop), OI building (positive), no alert (small change)
- Reduce threshold guard, insufficient history

Funding reversal (FUNDING_ARB pattern):
- Funding flipped (short position, funding goes negative), funding weak, non-FUNDING_ARB ignored, healthy funding

Position limits:
- Single loss limit, daily target hit, no alert (small loss), zero balance guard

#### test_roar_analyst.py (19 tests) — Meta-Optimizer

Scorecard aggregation:
- Basic aggregation (single pattern), multiple patterns, empty trades, zero PnL counts as loss
- Hold duration calculated, DSL exit tier averaged, confluence score averaged

Rules engine:
- Rule 1: Low win rate raises threshold
- Rule 2: High win rate lowers threshold
- Rule 3: Low exit tier loosens retrace
- Rule 4: High exit tier tightens retrace
- Rule 5: Stale signal lowers threshold
- Rule 6: Negative expectancy disables pattern
- No changes with insufficient trades
- Bounds enforcement (clamping)

Revert logic:
- Not triggered with few post-adjustment trades
- Triggered when enough trades and both metrics worse
- No revert when only win rate worse
- No revert when only PnL worse

#### test_roar_config.py (34 tests) — ROAR Bounds & Protection

Bounds clamping:
- Within bounds (passthrough), above max (clamped), below min (clamped)
- Pattern confluence override, unknown key passthrough

Protected keys:
- Budget protected, maxLeverage protected, strategyWallet protected
- Scanner thresholds NOT protected, confluence NOT protected

Nested config access:
- `get_nested`: Flat key, nested dot path, missing key
- `set_nested`: Creates path, overwrites existing

Revert logic:
- Both worse triggers revert, only win rate worse → no revert, only PnL worse → no revert
- Insufficient trades → no revert, no previous stats → no revert, exactly min trades

Pattern disable:
- Disable sets 48h expiry, is_pattern_disabled check, re-enable when expired, not expired

Config application:
- Applies changes, skips protected keys, clamps to bounds, saves previous config snapshot

#### test_tiger_exit.py (15 tests) — Pattern-Specific Exits

- Daily target hit: Triggers when PnL exceeds target, no trigger below target
- Trailing lock: Triggers when ROE below lock%, no trigger above lock, no trigger with insufficient HWM
- Deadline tightening: Lock tightened in final 24h
- Stagnation: Triggers after 24 flat checks, no stagnation if price moving
- Deadline exit: Triggers when reached, no trigger with time remaining
- False breakout (compression): Triggers on BB re-entry, no trigger with high ROE
- Time stop: Triggers after 30min negative, zero margin returns None
- Priority: Critical outranks high severity

#### test_correlation.py (13 tests) — BTC Lag Detection

Trigger detection:
- No trigger on flat BTC, trigger on 4h BTC move, trigger on short direction
- 1h fast trigger, insufficient candles, fetch failure

Lag scoring:
- Strong lag (long direction), no lag (alt already moved), short direction lag
- Insufficient candles for lag

Window quality:
- STRONG (lag >0.7), MODERATE (0.5-0.7), CLOSING (0.4-0.5)

Correlation factor:
- High-corr alt gets factor bonus, non-corr alt does not

#### test_prescreener.py (12 tests) — Asset Screening

Score calculation:
- Full score calculation (all components), zero change, momentum caps at 1
- Partial momentum, negative price change

Filtering:
- Delisted assets return None, blacklisted assets return None
- Low leverage returns None, zero volume returns None, zero price returns None
- Uses config `minLeverage`

Output:
- Writes to correct instance directory

#### test_compression.py (8 tests) — BB Squeeze Detection

- Insufficient 1h candles, insufficient 4h candles
- Fetch failure returns None
- Returns correct pattern type (COMPRESSION_BREAKOUT)
- Squeeze percentile above 40 returns None (not squeezed)
- Halted state triggers early exit
- Disabled pattern triggers early exit
- Active positions filtered (no duplicate signals)

### What Is NOT Tested

For completeness, these components rely on integration rather than isolated unit tests:

- **Momentum scanner** and **reversion scanner**: Not individually unit-tested. Their logic is covered indirectly through `test_tiger_lib.py` (indicator correctness) and the shared scoring infrastructure tested in compression/correlation.
- **Funding scanner**: Same coverage model as momentum/reversion — indicator math is tested via `tiger_lib`, pattern-specific logic follows the same structure as tested scanners.
- **OI tracker**: Data collection script with minimal logic beyond API calls and file writes.
- **Goal engine**: Aggression calculation is tested in `test_tiger_lib.py` (`aggression_mode`, `required_daily_return`, `kelly_fraction`). The script itself orchestrates these primitives.
- **Setup wizard**: Interactive script, not suitable for automated testing.
- **End-to-end integration**: No tests simulate a full scan → entry → management → exit lifecycle across multiple crons.
- **MCP API calls**: All external calls go through `mcporter_call()` which is mocked in tests. No live API integration tests.

### Test Infrastructure

**conftest.py** provides:
- Sample instrument data (realistic Hyperliquid asset format)
- 1h and 4h candle data (OHLCV arrays)
- Config and state fixtures with proper defaults
- Dependency injection for isolating external calls
- Temporary directory fixtures for state file tests

All tests are structured for `pytest` with no external dependencies beyond the standard library and pytest itself.
