---
name: tiger-strategy
description: >-
  TIGER v2 — Multi-scanner trading system for Hyperliquid perps via Senpi MCP.
  5 signal patterns (BB compression breakout, BTC correlation lag, momentum breakout,
  mean reversion, funding rate arb), DSL v4 trailing stops, goal-based aggression engine,
  and risk guardrails. Configurable profit target over deadline. 11-cron architecture (10 TIGER + 1 ROAR meta-optimizer).
  Pure Python analysis. Requires Senpi MCP, python3, mcporter CLI, and OpenClaw cron system.
license: Apache-2.0
compatibility: >-
  Python 3.8+, no external deps (stdlib only). Requires mcporter
  (configured with Senpi auth) and OpenClaw cron system.
metadata:
  author: jason-goldberg
  version: "3.0"
  platform: senpi
  exchange: hyperliquid
---

# TIGER v2 — Multi-Scanner Goal-Based Trading

5 scanners. 1 goal. Configurable aggression. Mechanical exits.

**Philosophy:** WOLF hunts on instinct. TIGER calculates what it needs, then hunts exactly that much. Give it a budget, a target, and a deadline — it adjusts aggression automatically.

---

## Architecture

```
┌──────────────────────────────────────────┐
│           10 OpenClaw Crons              │
│  Compress(5m) Corr(3m) Momentum(5m)     │
│  Reversion(5m) Funding(30m) OI(5m)      │
│  Goal(1h) Risk(5m) Exit(5m) DSL(30s)    │
├──────────────────────────────────────────┤
│           Python Scripts                  │
│  tiger_lib.py  tiger_config.py           │
│  5 scanners / goal-engine / risk /       │
│  exit / oi-tracker / dsl-v4              │
├──────────────────────────────────────────┤
│           Senpi MCP (via mcporter)        │
│  market_list_instruments                  │
│  market_get_asset_data / market_get_prices│
│  create_position / close_position         │
│  edit_position / cancel_order             │
│  strategy_get_clearinghouse_state         │
│  leaderboard_get_markets                  │
│  account_get_portfolio                    │
├──────────────────────────────────────────┤
│           State Files                     │
│  tiger-config.json → tiger_config.py      │
│  state/{instance}/*.json (atomic writes)  │
└──────────────────────────────────────────┘
```

**State flow:** OI Tracker samples all assets → Scanners score signals by confluence → Goal Engine sets aggression → Agent enters via `create_position` → DSL manages trailing stops → Risk Guardian enforces limits → Exit Checker handles pattern-specific exits.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` shows `senpi`)
2. Create a custom strategy: `strategy_create_custom_strategy`
3. Fund the wallet: `strategy_top_up`
4. Run setup:
   ```bash
   python3 scripts/tiger-setup.py --wallet 0x... --strategy-id UUID \
     --budget 1000 --target 2000 --deadline-days 7 --chat-id 12345
   ```
5. Create 10 OpenClaw crons from `references/cron-templates.md`

**First hour:** OI Tracker needs ~1h of history before compression/reversion scanners can use OI data. Goal engine and risk guardian work immediately.

---

## 5 Signal Patterns

### 1. Compression Breakout (Primary)

BB squeeze with OI accumulation → price breaks bands.

| Factor | Weight | Threshold |
|--------|--------|-----------|
| BB squeeze (4h) | 0.25 | Width < `bbSqueezePercentile` (default: 35th) |
| BB breakout (1h) | 0.25 | Price closes outside 1h BB |
| OI building | 0.20 | OI rising > 5% in 1h |
| OI-price divergence | 0.15 | OI rising, price flat |
| Volume surge | 0.15 | Short vol > 1.5× long avg |
| RSI not extreme | 0.10 | RSI 30-70 |
| Funding aligned | 0.10 | Funding favors direction |
| ATR expanding | 0.05 | ATR > 2% |

### 2. BTC Correlation Lag

BTC moves significantly → high-corr alts haven't caught up.

| Factor | Weight | Threshold |
|--------|--------|-----------|
| BTC significant move | 0.20 | > `btcCorrelationMovePct` (default: 2%) in 1-4h |
| Alt lagging | 0.25 | Lag ratio ≥ 0.5 |
| Volume quiet | 0.15 | Alt volume not spiked yet |
| RSI safe | 0.10 | Not at extremes |
| SM aligned | 0.15 | Smart money direction matches |
| High correlation | 0.10 | Asset in known high-corr list |
| Sufficient leverage | 0.05 | Max leverage ≥ `minLeverage` |

Window quality: STRONG (lag > 0.7), MODERATE (0.5-0.7), CLOSING (0.4-0.5).

### 3. Momentum Breakout

Strong price move with volume confirmation.

| Factor | Weight | Threshold |
|--------|--------|-----------|
| 1h move | 0.25 | > 1.5% |
| 2h move | 0.15 | > 2.5% |
| Volume surge | 0.20 | Ratio > 1.5× |
| 4h trend aligned | 0.15 | Move matches 4h direction |
| RSI not extreme | 0.10 | 30-70 |
| SMA aligned | 0.10 | Price correct side of SMA20 |
| ATR healthy | 0.05 | > 1.5% |

**DSL note:** Tighter Phase 1 retrace (0.012) — momentum reversals are fast.

### 4. Mean Reversion

Overextended asset with exhaustion signals → counter-trend.

| Factor | Weight | Threshold |
|--------|--------|-----------|
| RSI extreme (4h) | 0.20 | > `rsiOverbought` or < `rsiOversold` (required) |
| RSI extreme (1h) | 0.15 | Confirms 4h |
| RSI divergence | 0.20 | Divergence aligned with reversal |
| Price extended | 0.10 | > 10% move in 24h |
| Volume exhaustion | 0.15 | Declining volume on extension |
| At extreme BB | 0.10 | Price beyond BB bands |
| OI crowded | 0.15 | OI 15%+ above avg |
| Funding pays us | 0.10 | Collect funding in our direction |

### 5. Funding Rate Arb

Extreme funding → go opposite the crowd, collect income.

| Factor | Weight | Threshold |
|--------|--------|-----------|
| Extreme funding | 0.25 | Annualized > `minFundingAnnualizedPct` (default: 30%) |
| Trend aligned | 0.20 | SMA20 supports direction |
| RSI safe | 0.15 | Not extreme against us |
| OI stable | 0.15 | Funding source not collapsing |
| SM aligned | 0.10 | Smart money on our side |
| High daily yield | 0.10 | > 5% daily yield on margin |
| Volume healthy | 0.05 | > $10M daily |

**DSL note:** Wider retrace tiers (0.02+) — edge is income, not price direction. Risk Guardian auto-exits if funding flips.

---

## Goal Engine & Aggression

`goal-engine.py` runs hourly. Calculates required daily return and sets aggression:

| Aggression | Daily Rate Needed | Min Confluence | Trailing Lock | Behavior |
|------------|-------------------|----------------|---------------|----------|
| CONSERVATIVE | < 8% | 0.70 | 80% | Take profits early |
| NORMAL | 8-15% | 0.40 | 60% | Standard operation |
| ELEVATED | 15-25% | 0.40 | 40% | Wider entries, lower threshold |
| ABORT | > 25% | 999 (never) | 90% | Stop new entries, tighten all |

---

## DSL v4 — Trailing Stop System

Per-position DSL state file. Combined runner (`dsl-v4.py`) checks all active positions every 30s.

**IMPORTANT**: The DSL cron must first check `activePositions` in TIGER state. If no positions are open, output `HEARTBEAT_OK` immediately and do NOT invoke `dsl-v4.py`. This prevents unnecessary session spam when TIGER is idle.

**Phase 1** (pre-Tier 1): Absolute floor. 3 consecutive breaches → close. Max duration: 90 minutes.

**Phase 2** (Tier 1+): Trailing tiers.

| Tier | ROE Trigger | Lock % of High-Water | Retrace | Breaches |
|------|-------------|---------------------|---------|----------|
| 1 | 5% | 20% | 1.5% | 2 |
| 2 | 10% | 50% | 1.2% | 2 |
| 3 | 20% | 70% | 1.0% | 2 |
| 4 | 35% | 80% | 0.8% | 1 |

**Stagnation TP:** ROE ≥ 8% + high-water stale 1h → auto-close.

### DSL Tuning by Pattern

| Pattern | Phase 1 Retrace | Tier Widths | Notes |
|---------|----------------|-------------|-------|
| COMPRESSION | 0.015 (standard) | Standard | Watch for false breakouts |
| CORRELATION_LAG | 0.015 | Standard | Tight absolute floor — window closes fast |
| MOMENTUM | 0.012 (tighter) | Standard | Fast reversals |
| MEAN_REVERSION | 0.015 | Medium | Expect 2-3 ATR move |
| FUNDING_ARB | 0.020+ (wider) | Wider | Income-based, needs room |

---

## Risk Management

| Rule | Limit | Config Key | Default |
|------|-------|-----------|---------|
| Max single trade loss | 5% of balance | `maxSingleLossPct` | 5 |
| Max daily loss | 12% of day-start balance | `maxDailyLossPct` | 12 |
| Max drawdown from peak | 20% | `maxDrawdownPct` | 20 |
| Max concurrent positions | 3 | `maxSlots` | 3 |
| OI collapse exit | OI drops > 25% in 1h | `oiCollapseThresholdPct` | 25 |
| Funding reversal exit | Funding flips on FUNDING_ARB | — | Auto |
| Deadline proximity | Final 24h → tighten all stops | — | Auto |

All percentage values are whole numbers (5 = 5%).

---

## Anti-Patterns

1. **NEVER enter in ABORT aggression.** Goal engine set ABORT for a reason.
2. **NEVER override DSL.** DSL auto-closes. Don't re-enter after DSL exit.
3. **NEVER hold FUNDING_ARB after funding flips.** The thesis is dead.
4. **NEVER chase momentum after 2h.** If you missed the 1h move, wait for the next one.
5. **NEVER enter reversion without 4h RSI extreme.** That's the required filter, not optional.
6. **NEVER run scanners without timeout wrapper.** `timeout 55` prevents overlap.

---

## API Dependencies

| Tool | Used By | Purpose |
|------|---------|---------|
| `market_list_instruments` | all scanners, oi-tracker | Asset discovery, OI, funding, volume |
| `market_get_asset_data` | all scanners | Candles (1h, 4h), funding |
| `market_get_prices` | correlation-scanner, risk-guardian | BTC price, alt prices |
| `leaderboard_get_markets` | correlation, funding scanners | SM alignment |
| `account_get_portfolio` | goal-engine | Portfolio balance |
| `strategy_get_clearinghouse_state` | goal-engine, risk-guardian | Margin, positions |
| `create_position` | agent (from scanner output) | Open positions |
| `close_position` | dsl-v4, risk-guardian, tiger-exit | Close positions |
| `edit_position` | risk-guardian | Resize positions |

---

## State Schema

See `references/state-schema.md` for full schema with field descriptions.

Key state files:
```
state/{instanceKey}/
├── tiger-state.json              # Positions, aggression, safety, daily stats
├── dsl-{ASSET}.json              # Per-position DSL trailing stop state
├── oi-history.json               # 24h OI time-series
├── trade-log.json                # All trades with outcomes
└── scan-history/                 # Scanner output history
```

All state files include `version`, `active`, `instanceKey`, `createdAt`, `updatedAt`. All writes use `atomic_write()`.

---

## Cron Setup

See `references/cron-templates.md` for ready-to-use OpenClaw cron payloads.

**Silence Policy — CRITICAL**: When a cron fires and the result is HEARTBEAT_OK, NO_POSITIONS, or no actionable signals:
- Do NOT notify Telegram
- Do NOT reply in chat
- Do NOT explain what the cron did
- Do NOT summarize the scan results
- Produce NO output of any kind — complete silence
Only speak (chat or Telegram) when something actionable happens: trade opened, trade closed, aggression changed, risk halt triggered, or an error that needs attention. Idle cycles = total silence.

| # | Job | Interval | Script | Model Tier |
|---|-----|----------|--------|------------|
| 1 | Compression Scanner | 5 min | `compression-scanner.py` | Tier 1 |
| 2 | Correlation Scanner | 3 min | `correlation-scanner.py` | Tier 1 |
| 3 | Momentum Scanner | 5 min | `momentum-scanner.py` | Tier 1 |
| 4 | Reversion Scanner | 5 min | `reversion-scanner.py` | Tier 1 |
| 5 | Funding Scanner | 30 min | `funding-scanner.py` | Tier 1 |
| 6 | OI Tracker | 5 min | `oi-tracker.py` | Tier 1 |
| 7 | Goal Engine | 1 hour | `goal-engine.py` | Tier 2 |
| 8 | Risk Guardian | 5 min | `risk-guardian.py` | Tier 2 |
| 9 | Exit Checker | 5 min | `tiger-exit.py` | Tier 2 |
| 10 | DSL Combined | 30 sec | `dsl-v4.py` | Tier 1 |
| 11 | ROAR Analyst | 8 hour | `roar-analyst.py` | Tier 2 |

**Tier 1** (fast/cheap): threshold checks, data collection, DSL math. Runs `isolated` with `delivery.mode: "none"` and explicit model (`claude-haiku-4-5`).
**Tier 2** (capable): aggression decisions, risk judgment, exit evaluation. Runs `isolated` with `delivery.mode: "announce"` and explicit model (`claude-sonnet-4-5`). OpenClaw auto-suppresses HEARTBEAT_OK — only real content gets delivered.
**DSL** (Cron 10): Runs in `main` session (`systemEvent`) — needs position state context.

Scanners are staggered by 1-2 minutes to avoid mcporter rate limits (see cron-templates.md).

---

## ROAR — Recursive Optimization & Adjustment Runtime

ROAR is TIGER's meta-optimizer. It runs every 8 hours (+ ad-hoc every 5th trade), analyzes TIGER's trade log, and tunes execution parameters within bounded ranges. User intent (budget, target, risk limits) is never touched.

**What ROAR tunes** (within hard min/max bounds):
- Per-pattern confluence thresholds (0.25–0.85)
- Scanner thresholds (BB squeeze percentile, BTC correlation move, funding annualized)
- DSL retrace thresholds per phase (0.008–0.03)
- Trailing lock percentages per aggression level

**What ROAR never touches** (protected): budget, target, deadline, max_slots, max_leverage, maxDrawdownPct, maxDailyLossPct, maxSingleLossPct.

**Rules engine** (6 rules):
1. Win rate < 40% over 10+ trades → raise pattern confluence threshold by 0.05
2. Win rate > 70% over 10+ trades → lower threshold by 0.03 to catch more signals
3. Avg DSL exit tier < 2 → loosen phase1 retrace by 0.002 (let positions run)
4. Avg DSL exit tier ≥ 4 → tighten phase1 retrace by 0.001 (lock gains)
5. No entries in 48h for a pattern with 5+ trades → lower threshold by 0.02
6. Negative expectancy over 20+ trades → disable pattern for 48h (auto-re-enables)

**Safety**: revert-if-worse checks every cycle. If both win rate AND avg PnL degraded since last adjustment, auto-reverts to previous config.

Scripts: `roar-analyst.py` (engine), `roar_config.py` (bounds, state, revert logic).

---

## Expected Performance

| Metric | Target |
|--------|--------|
| Trades per day | 2-8 |
| Win rate | 55-65% |
| Profit factor | 1.8-2.5 |
| Best conditions | Volatile with clear setups (squeeze→breakout) |
| Worst conditions | Low-vol grind (few signals), choppy (false breakouts) |

---

## Known Limitations

- **OI history bootstrap.** Scanners need ~1h of OI data before OI-dependent signals are reliable.
- **mcporter latency.** ~6s per call. Scanners limited to 8 assets per cycle.
- **DSL is per-position.** Each position needs its own DSL state file.
- **Correlation scanner assumes BTC leads.** Doesn't work when alts lead BTC.
- **Funding arb needs patience.** Edge is income over time; DSL must be wide enough.
- **Goal engine recalculates hourly.** Aggression can shift mid-trade.

---

## Optimization Levers

| Lever | Config Key | Conservative | Default | Aggressive |
|-------|-----------|-------------|---------|------------|
| Confluence threshold (NORMAL) | `minConfluenceScore.NORMAL` | 0.55 | 0.40 | 0.35 |
| BB squeeze percentile | `bbSqueezePercentile` | 25 | 35 | 45 |
| BTC corr move % | `btcCorrelationMovePct` | 3 | 2 | 1.5 |
| Max leverage | `maxLeverage` | 7 | 10 | 15 |
| Max slots | `maxSlots` | 2 | 3 | 4 |
| Daily loss halt % | `maxDailyLossPct` | 8 | 12 | 15 |
| Trailing lock (NORMAL) | `trailingLockPct.NORMAL` | 0.80 | 0.60 | 0.40 |

---

## Gotchas

- `maxSingleLossPct` is a whole number: `5` = 5%.
- `minConfluenceScore` values are decimals (0.40 = 40%), NOT whole numbers — this is a weighted score 0-1.
- `trailingLockPct` values are decimals (0.60 = lock 60%).
- `triggerPct` in DSL tiers is ROE % (5 = 5% ROE), not price %.
- `lockPct` in DSL is % of high-water move to lock, not a retrace threshold.
- DSL reads `DSL_STATE_FILE` env var ONLY — positional args are silently ignored.
- `timeout 55` on all scanner scripts to prevent cron overlap.
- Cron stagger offsets: :00 compression, :01 momentum, :02 reversion, :03 OI, :04 risk+exit.

---

## Lessons from Live Trading

### Operational

- **DSL state file `active` field**: MUST include `active: true` or `dsl-v4.py` returns `{"status": "inactive"}` (line 22 check). This is the #1 gotcha when setting up new positions.
- **DSL invocation syntax**: `DSL_STATE_FILE=/path/to/file.json python3 scripts/dsl-v4.py COIN`
- **API latency**: `market_get_asset_data` ~4s/call, `market_list_instruments` ~6s. Max 8 assets per 55s scan window.
- **Correlation scanner timeouts**: Frequently times out — skip after consecutive timeouts rather than waste 55s per attempt.
- **Compression scanner signals**: Requires `breakout: true` AND a `direction` to be actionable — a high compression score alone is not enough.

### Trading

- **Don't short compressed assets with building OI** — compression often resolves upward.
- **No duplicate positions**: Skip signals for assets already in `active_positions`.
- **Re-entry in opposite direction IS valid**: When signals are strong, entering the same asset in the opposite direction works.
- **DSL trailing stops >> fixed TP**: Every winning trade ran past where a fixed TP would have closed. Let winners run.
- **High-score signals (0.85+) justify overriding blacklists**: If original loss was small and new direction differs, take the trade.
- **`create_position` format**: Requires `orders` array with `coin`, `direction`, `leverage`, `marginAmount`, `orderType` fields.
- **`close_position` syntax**: `mcporter call 'senpi.close_position(...)'`
- **CLOSE_NO_POSITION pattern**: Position may already be closed on-chain before DSL's close call — handle gracefully (not an error).
