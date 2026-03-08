---
name: fox-strategy
description: >-
  FOX v0.1 — Fully autonomous multi-strategy trading for Hyperliquid perps via Senpi MCP.
  Forked from Wolf v7 + v7.1 data-driven optimizations (14-trade analysis: 2W/12L).
  Tighter absolute floor (0.02/lev, ~20% max ROE loss), aggressive Phase 1 timing
  (30min hard timeout, 15min weak peak, 10min dead weight), green-in-10 floor tightening,
  time-of-day scoring (+1 for 04-14 UTC, -2 for 18-02 UTC), rank jump minimum (≥15 OR vel>15).
  Scoring system (6+ pts), NEUTRAL regime support, tiered margin (6 entries max),
  BTC 1h bias alignment, market regime refresh 4h.
  8-cron architecture. Independent from Wolf.
  Requires Senpi MCP, python3, mcporter CLI, OpenClaw cron system.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and OpenClaw cron system.
  Hyperliquid perp positions only (main dex and xyz dex).
  Depends on dsl-dynamic-stop-loss skill for trailing stops.
metadata:
  author: jason-goldberg
  version: "0.1"
  platform: senpi
  exchange: hyperliquid

---

# FOX v0.1 — Autonomous Multi-Strategy Trading

The FOX hunts for its human. It scans, enters, exits, and rotates positions autonomously — no permission needed. When criteria are met, it acts. Speed is edge.

**Forked from Wolf v7 + v7.1 data-driven optimizations.** Independent strategies, state files, scripts, and cron jobs. Runs in parallel with Wolf without interference.

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` should show `senpi`)
2. Create a custom strategy wallet: use `strategy_create_custom_strategy` via mcporter
3. Fund the wallet via `strategy_top_up` with your budget
4. Run setup: `python3 scripts/fox-setup.py --wallet 0x... --strategy-id UUID --budget 6500 --chat-id 12345`
5. Create the 8 OpenClaw crons using templates from `references/cron-templates.md`
6. The FOX is hunting

To add a second strategy, run `fox-setup.py` again with a different wallet/budget. It adds to the registry.

---

## Reference Files

| File | Contents |
|------|----------|
| [references/learnings.md](references/learnings.md) | Proven results, key learnings, known bugs, trading discipline |
| [references/cron-setup.md](references/cron-setup.md) | Cron config, race condition prevention, time-aware scheduling |
| [references/cron-templates.md](references/cron-templates.md) | Ready-to-use cron mandates with proper systemEvent/agentTurn format |
| [references/state-schema.md](references/state-schema.md) | JSON state file schemas (registry, trade counter, DSL state, scanner config) |
| [references/api-tools.md](references/api-tools.md) | Senpi API tool quick reference |
| [references/senpi-skill-guide.md](references/senpi-skill-guide.md) | Senpi Skill Development Guide (conformance reference) |

---

## Multi-Strategy Architecture

### Strategy Registry (`fox-strategies.json`)
Central config holding all strategies. Created/updated by `fox-setup.py`.

```
fox-strategies.json
├── strategies
│   ├── fox-abc123 (Aggressive Momentum, 3 slots, 10x)
│   └── fox-xyz789 (Conservative XYZ, 2 slots, 7x)
└── global (telegram, workspace)
```

### Per-Strategy State
Each strategy gets its own state directory:
```
state/
├── fox-abc123/
│   ├── dsl-HYPE.json
│   └── dsl-SOL.json
└── fox-xyz789/
    ├── dsl-HYPE.json    # Same asset, different strategy, no collision
    └── dsl-GOLD.json
```

### Signal Routing
When a signal fires, it's routed to the best-fit strategy:
1. Which strategies have empty slots?
2. Does any strategy already hold this asset? (skip within strategy, allow cross-strategy)
3. Which strategy's risk profile matches? (aggressive gets FIRST_JUMPs, conservative gets DEEP_CLIMBERs)
4. Route to best-fit → open on that wallet → create DSL state in that strategy's dir

### Adding a Strategy
```bash
python3 scripts/fox-setup.py --wallet 0x... --strategy-id UUID --budget 6500 \
    --chat-id 12345 --name "Aggressive Momentum" --dsl-preset aggressive
```
This adds to the registry without disrupting running strategies. Disable with `enabled: false` in the registry.

---

## FOX v7 Changes Summary (from Wolf v7)

### 1. Market Regime Refresh (4h)
- New cron job runs `fox-market-regime.py` every 4 hours
- Saves output to `market-regime-last.json`
- Provides fresh BEARISH/BULLISH/NEUTRAL classification

### 2. NEUTRAL Regime Support
- **BEARISH**: SHORT only (unchanged)
- **BULLISH**: LONG only (unchanged)
- **NEUTRAL**: Both LONG and SHORT allowed if signal score ≥ 8 (higher bar)
- **NEUTRAL definition**: market_bull < 60 AND market_bear < 60, OR overall confidence < 50

### 3. Scoring System (replaces "min 4 reasons")
- **FIRST_JUMP**: +3 pts (still mandatory)
- **IMMEDIATE_MOVER**: +2 pts
- **contribVelocity > 10**: +2 pts
- **CONTRIB_EXPLOSION**: +2 pts
- **DEEP_CLIMBER**: +1 pt
- **Other reasons** (RANK_UP, CLIMBING, ACCEL, STREAK): +1 pt each
- **BTC 1h bias alignment**: +1 bonus pt
- **v0.1 TIME BONUS** (04:00–14:00 UTC): **+1 pt**
- **v0.1 TIME PENALTY** (18:00–02:00 UTC): **-2 pts**
- **Minimum 6 points** to enter (8+ for NEUTRAL regime)

### 4. Tiered Margin System
- **Max entries**: 6 (flat, no dynamic slots)
- **Entries 1-2**: $1450 margin, $1500 budget
- **Entries 3-4**: $950 margin, $1000 budget
- **Entries 5-6**: $450 margin, $500 budget
- Scanner reads tier based on current entry count from `fox-trade-counter.json`

### 5. BTC 1h Bias Alignment
- Check BTC hourly trend from regime data before entry
- **Aligned signals**: +1 bonus point (BTC 1h BULLISH + signal LONG, or BTC 1h BEARISH + signal SHORT)
- **Conflicting signals**: no penalty, no bonus (regime filter handles hard blocks)

---

## v0.1 Optimizations (Data-Driven from 14 Live Trades: 2W/12L)

These changes are derived from analyzing Wolf v7's live trading results. Every optimization has evidence from real trades.

### 1. Tighter Absolute Floor (0.02/leverage → ~20% max ROE loss)

| | Old (v7) | New (v0.1) |
|---|---|---|
| Formula (LONG) | `entry × (1 - 0.03/leverage)` | `entry × (1 - 0.02/leverage)` |
| Formula (SHORT) | `entry × (1 + 0.03/leverage)` | `entry × (1 + 0.02/leverage)` |
| Max ROE loss | ~30% | ~20% |

**Evidence:** NEAR lost 23.7% ROE, FARTCOIN 18.4% — both would have been capped earlier at 20%.

**Config field:** `phase1.retraceThreshold` (default: `0.02`)

### 2. Aggressive Phase 1 Timing

| Rule | Old (v7) | New (v0.1) |
|---|---|---|
| Hard timeout (never hits Tier 1) | 90 min | **30 min** |
| Weak peak cut (peak ROE < 3%, declining) | 45 min | **15 min** |
| Dead weight (never positive ROE) | 30 min | **10 min** |

**Evidence:** 100% of winners went positive within minutes. 100% of losers that started negative stayed negative.

**Config fields:** `phase1.hardTimeoutMin` (default: `30`), `phase1.weakPeakCutMin` (default: `15`), `phase1.deadWeightCutMin` (default: `10`)

**Enforcement:** These timing rules are checked by the DSL cron mandate (agent-level), NOT by the dsl-v5.py script. The agent reads `createdAt` from the DSL state file and applies timing logic after parsing dsl-v5.py output.

### 3. Green-in-10 Rule

**New field in DSL state:** `greenIn10` (default: `false`)

If a position **never** shows positive ROE within the first 10 minutes → tighten `absoluteFloor` to **50% of original distance** from entry. This is a "tighten, don't close" rule — the 10min dead weight cut handles the actual close if it stays negative.

For positions that briefly went green then reversed, this rule does NOT apply.

**Config field:** `phase1.greenIn10TightenPct` (default: `50` — tighten floor to 50% of original distance)

### 4. Time-of-Day Scoring

| Time Window (UTC) | Modifier | Rationale |
|---|---|---|
| 04:00–14:00 | **+1 pt** | Historically where winners occur |
| 14:00–18:00 | 0 pts | Neutral window |
| 18:00–02:00 | **-2 pts** | 0% win rate across 6 evening trades |
| 02:00–04:00 | 0 pts | Neutral window |

**Effect:** Evening entries effectively need score ≥ 8 to pass the 6-point threshold.

**Config fields:** `scoring.timeBonusHoursUTC` (default: `[4,14]`), `scoring.timePenaltyHoursUTC` (default: `[18,2]`), `scoring.timeBonusPts` (default: `1`), `scoring.timePenaltyPts` (default: `-2`)

### 5. Rank Jump Minimum

**New filter:** `rankJumpThisScan ≥ 15` OR `contribVelocity > 15`

Old rule: just needed `isFirstJump=true` (any 10+ rank jump from #25+). Now requires either a massive jump (≥15 ranks) or strong velocity (>15).

**Evidence:** Both winners had massive jumps. Small jumps (#30→#20, #35→#21) all lost.

**Config fields:** `filters.minRankJump` (default: `15`), `filters.minVelocityOverride` (default: `15`)

### 6. Conviction-Scaled Phase 1 Tolerance (v7.2)

**Finding:** Direction was right 85% of the time (11/13 trades) but we still lost $785 on correct-direction trades and left $8,000+ on the table. The problem is timing/stops, not signal quality.

**Solution:** Score at entry determines how much room Phase 1 gets. High-conviction trades survive initial volatility; low-conviction trades get cut fast.

| Score | Absolute Floor | Hard Timeout | Weak Peak | Dead Weight |
|-------|---------------|-------------|-----------|-------------|
| 6-7 | 0.02/lev (~20%) | 30 min | 15 min | 10 min |
| 8-9 | 0.025/lev (~25%) | 45 min | 20 min | 15 min |
| 10+ | 0.03/lev (~30%) | 60 min | 30 min | 20 min |

**Implementation:** Entry score is stored in DSL state (`score` field). The DSL cron mandate reads the score and selects the matching tolerance tier from `phase1.convictionTiers` config. If no score is present, defaults to the 6-7 tier (tightest).

**Config field:** `phase1.convictionTiers` (array of `{minScore, retraceThreshold, hardTimeoutMin, weakPeakCutMin, deadWeightCutMin}`)

### 7. Re-Entry Rule (v7.2)

When we exit a Phase 1 trade and the asset continues in our original direction, we can re-enter with guardrails.

**Re-entry checklist (ALL must pass):**
1. Asset still in top 20 with same direction
2. `contribVelocity > 5` (momentum continuing)
3. Price has moved FURTHER in our original direction since exit
4. Within 2 hours of original exit
5. First attempt did NOT lose > 15% ROE (too volatile = skip)
6. Score minimum: **5 pts** (relaxed from 6 — direction already validated)
7. `isFirstJump` NOT required (it already jumped once)

**Sizing:** 75% of normal margin tier (reduced risk on 2nd attempt)

**State tracking:** DSL state gets `isReentry: true` and `reentryOf: "<original_trade_id>"`. Trade counter logs `isReentry: true`.

**Config fields:** `reentry.enabled` (default: `true`), `reentry.marginPct` (default: `75`), `reentry.minScore` (default: `5`), `reentry.maxOriginalLossROE` (default: `15`), `reentry.windowMin` (default: `120`), `reentry.minContribVelocity` (default: `5`)

---

## Entry Philosophy — THE Most Important Section

**Enter before the peak, not at the top.**

Leaderboard rank confirmation LAGS price. When an asset jumps from #31→#16 in one scan, the price is moving NOW. By the time it reaches #7 with clean history, the move is over. Speed is edge.

**Core principle:** 2 reasons at rank #25 with a big jump = ENTER. 4+ reasons at rank #5 = SKIP (already peaked).

---

## Entry Signals — Priority Order

### 1. FIRST_JUMP (Highest Priority)

**What:** Asset jumps 10+ ranks from #25+ in ONE scan AND was not in previous scan's top 50 (or was at rank >= #30).

**Action:** Enter IMMEDIATELY. This is the money signal. Route to best-fit strategy with available slots.

**Checklist:**
- `isFirstJump: true` in scanner output
- **v0.1 Rank jump minimum:** `rankJumpThisScan ≥ 15` OR `contribVelocity > 15` (small jumps like #30→#20 all lost)
- **2+ reasons is enough** (don't require 4+)
- **vel > 0 is sufficient** (velocity hasn't had time to build on a first jump)
- Max leverage >= 7x (check `max-leverage.json`)
- Slot available in target strategy (or rotation justified)
- >= 10 SM traders (crypto); for XYZ equities, ignore trader count

**What to ignore:**
- Erratic rank history — the scanner excludes the current jump from erratic checks
- Low velocity — first jumps haven't had time to build velocity

**If CONTRIB_EXPLOSION accompanies it:** Double confirmation. Even stronger entry.

### 2. CONTRIB_EXPLOSION

**What:** 3x+ contribution increase in one scan from asset at rank #20+.

**Action:** Enter even if rank history looks "erratic." The contrib spike IS the signal regardless of prior rank bouncing.

**Never downgraded for erratic history.** Often accompanies FIRST_JUMP for double confirmation.

### 3. DEEP_CLIMBER

**What:** Steady climb from #30+, positive velocity (>= 0.03), 3+ reasons, clean rank history.

**Action:** Enter when it crosses into top 20. Route to conservative strategy if available.

### 4. NEW_ENTRY_DEEP

**What:** Appears in top 20 from nowhere (wasn't in top 50 last scan).

**Action:** Instant entry.

### 5. Opportunity Scanner (Score 175+)

Runs every 15min. v6 scanner with BTC macro context, hourly trend classification, and hard disqualifiers. Complements Emerging Movers as a secondary signal source for deeper technical analysis.

---

## Anti-Patterns — When NOT to Enter

- **NEVER enter assets already at #1-10.** That's the top, not the entry. Rank = what already happened.
- **NEVER wait for a signal to "clean up."** By the time rank history is smooth and velocity is high, the move is priced in.
- **4+ reasons at rank #5 = SKIP.** The asset already peaked.
- **2 reasons at rank #25 with a big jump = ENTER.** The move is just starting.
- **Leaderboard rank != future price direction.** Rank reflects past trader concentration. Price moves first, rank follows.
- **Negative velocity + no jump = skip.** Slow bleeders going nowhere.
- **Oversold shorts** (RSI < 30 + extended 24h move) = skip.

---

## Late Entry Anti-Pattern

**The pattern:** Scanner fires FIRST_JUMP for ASSET at #25→#14. You hesitate. Next scan it's #10. Next scan #7 with 5 reasons. NOW it looks "safe." You enter. It reverses from #5.

**The fix:** Enter on the FIRST signal or don't enter at all. If you missed it, wait for the next asset. There's always another FIRST_JUMP coming.

**Rule:** If an asset has been in the top 10 for 2+ scans already, it's too late. Move on.

---

## Architecture — 8 Cron Jobs

| # | Job | Interval | Session | Script | Purpose |
|---|-----|----------|---------|--------|---------|
| 1 | Emerging Movers | **3min** | **main** | `scripts/fox-emerging-movers.py` | Hunt FIRST_JUMP signals with v7 scoring |
| 2 | DSL v5 | **3min** | isolated | `dsl-v5.py` (per-strategy cron) | Trailing stop exits, HL SL sync |
| 3 | SM Flip Detector | 5min | isolated | `scripts/fox-sm-flip-check.py` | Conviction collapse cuts |
| 4 | Watchdog | 5min | isolated | `scripts/fox-monitor.py` | Per-strategy margin buffer, liq distances |
| 5 | Portfolio Update | 15min | isolated | (agent-driven) | Per-strategy PnL reporting |
| 6 | Opportunity Scanner | 15min | **main** | `scripts/fox-opportunity-scan-v6.py` | 4-pillar scoring, BTC macro, hourly trend |
| 7 | Market Regime | **4h** | isolated | `scripts/fox-market-regime.py` | Regime classification |
| 8 | Health Check | 10min | isolated | `scripts/fox-health-check.py` | Orphan DSL, state validation |

**All scripts read `fox-strategies.json` and iterate all enabled Fox strategies.**

See [references/cron-setup.md](references/cron-setup.md) for detailed cron configuration, race condition prevention, and time-aware scheduling.

### Model Selection Per Cron — 3-Tier Approach

| Tier | Role | Crons | Example Model IDs |
|------|------|-------|--------------------|
| **Primary** | Complex judgment, multi-strategy routing | Emerging Movers, Opportunity Scanner | Your configured model (runs on main session) |
| **Mid** | Structured tasks, script output parsing | DSL v5, Portfolio Update, Health Check, Market Regime | model configured in OpenClaw |
| **Budget** | Simple threshold checks, binary decisions | SM Flip, Watchdog | model configured in OpenClaw |

**Do NOT create crons yet** — the main agent will set these up when activating the strategy.

---

## Cron Setup

**Critical:** Crons are **OpenClaw crons**, NOT senpi crons. FOX uses two session types:
- **Main session** (`systemEvent`): Emerging Movers + Opportunity Scanner. These share the primary session context for accumulated routing knowledge.
- **Isolated session** (`agentTurn`): All others. Each runs in its own session — no context pollution, enables cheaper model tiers.

**Key rules (per Senpi Skill Guide §7):**
- `systemEvent` uses `"text"` key; `agentTurn` uses `"message"` key — wrong key = silent failure
- Budget/Mid mandates have explicit `if/then` per output field — never "apply rules from SKILL.md"
- Slot guard pattern: check `anySlotsAvailable` BEFORE any entry
- One set of crons — scripts iterate all strategies internally

See [references/cron-templates.md](references/cron-templates.md) for exact payloads.

---

## Autonomy Rules

The FOX operates autonomously by default. The agent does NOT ask permission to:
- Open a position when entry checklist passes
- Close a position when DSL triggers or conviction collapses
- Rotate out of weak positions into stronger signals
- Cut dead weight (SM conv 0, negative ROE, 10+ min)

The agent DOES notify the user (via Telegram) after every action.

---

## Phase 1 Auto-Cut (v0.1 — Conviction-Scaled Timing)

Positions that never gain momentum get cut automatically. Timing is **scaled by entry score** (v7.2): high-conviction trades get more room, low-conviction trades get cut fast.

**Default rules (score 6-7, configurable via `phase1` fields in DSL state):**
- **30-minute hard timeout** (`hardTimeoutMin: 30`). If ROE never hits Tier 1 (5%) in 30 min, close.
- **15-minute weak peak cut** (`weakPeakCutMin: 15`). If peak ROE was < 3% and declining → close after 15 min.
- **10-minute dead weight cut** (`deadWeightCutMin: 10`). If position never shows positive ROE after 10 min → close.
- **Green-in-10 floor tightening** (`greenIn10TightenPct: 50`). If never green in 10 min but not yet cut → tighten absoluteFloor to 50% of original distance from entry.

**Conviction scaling (v7.2):**

| Score | Floor | Hard Timeout | Weak Peak | Dead Weight |
|-------|-------|-------------|-----------|-------------|
| 6-7 | 0.02/lev | 30 min | 15 min | 10 min |
| 8-9 | 0.025/lev | 45 min | 20 min | 15 min |
| 10+ | 0.03/lev | 60 min | 30 min | 20 min |

The DSL cron mandate reads `score` from the DSL state file and applies the matching tier. No score = defaults to 6-7 tier.

**Enforcement:** These timing rules are applied by the DSL cron mandate (agent reads `createdAt` and `score` from state file), NOT by dsl-v5.py itself.

**Why:** Direction was right 85% of the time. We lost money because stops were too tight on high-conviction trades. Score-scaling gives good trades room to breathe while still cutting bad ones fast.

---

## Exit Rules

### 1. DSL v5 Mechanical Exit (Trailing Stops)
All trailing stops handled automatically by DSL v5 per-strategy crons. SL synced to Hyperliquid for instant execution.

### 2. SM Conviction Collapse
Conv drops to 0 or 4→1 with mass trader exodus → instant cut.

### 3. Dead Weight
Conv 0, negative ROE, 10+ min → instant cut (v0.1: tightened from 30min).

### 4. SM Flip
Conviction 4+ in the OPPOSITE direction with 100+ traders → cut immediately.

### 5. Race Condition Prevention
When ANY job closes a position → immediately:
1. Set DSL state `active: false` in `dsl/{strategyId}/{ASSET}.json` (or DSL v5 auto-reconciles via clearinghouse)
2. Alert user
3. Evaluate: empty slot in that strategy for next signal?

---

## DSL v5 — Trailing Stop System

**Uses the official DSL v5 skill at `/data/workspace/skills/dsl-dynamic-stop-loss/`.** See that skill's SKILL.md for full details.

### Phase 1 (Pre-Tier 1): Absolute floor (v0.1 tightened)
- LONG floor = `entry × (1 - 0.02/leverage)` — caps max loss at **~20% ROE** (v0.1: was 0.03/~30%)
- SHORT floor = `entry × (1 + 0.02/leverage)`
- 2% retrace threshold, 3 consecutive breaches → close
- **Max duration: 30 minutes** (v0.1: was 90min — see Phase 1 Auto-Cut above)
- **Green-in-10:** If never positive ROE in 10min → floor tightens to 50% of original distance

### Phase 2 (Tier 1+): 9-Tier Aggressive Trailing

| Tier | ROE Trigger | Lock % | Breaches |
|------|-------------|--------|----------|
| 1 | 5% | 2% | 2 |
| 2 | 10% | 5% | 2 |
| 3 | 20% | 14% | 2 |
| 4 | 30% | 24% | 2 |
| 5 | 40% | 34% | 1 |
| 6 | 50% | 44% | 1 |
| 7 | 65% | 56% | 1 |
| 8 | 80% | 72% | 1 |
| 9 | 100% | 90% | 1 |

Phase 2: 1.5% retrace threshold, 2 consecutive breaches required.

### DSL v5 State Creation (on position entry)
Create directory `dsl/{strategyId}/` if needed. Write state file with ALL required fields:
```json
{
  "active": true,
  "asset": "ASSET",
  "direction": "SHORT",
  "leverage": 10,
  "entryPrice": "<entry>",
  "size": "<size>",
  "wallet": "<strategy_wallet>",
  "strategyId": "<strategy_uuid>",
  "phase": 1,
  "phase1": {
    "retraceThreshold": 0.02,
    "consecutiveBreachesRequired": 3,
    "absoluteFloor": "<calculated>",
    "hardTimeoutMin": 30,
    "weakPeakCutMin": 15,
    "deadWeightCutMin": 10,
    "greenIn10TightenPct": 50
  },
  "greenIn10": false,
  "score": "<entry_score>",
  "isReentry": false,
  "reentryOf": null,
  "phase2TriggerTier": 0,
  "phase2": {
    "retraceThreshold": 0.015,
    "consecutiveBreachesRequired": 2
  },
  "tiers": [
    {"triggerPct": 5, "lockPct": 2},
    {"triggerPct": 10, "lockPct": 5},
    {"triggerPct": 20, "lockPct": 14},
    {"triggerPct": 30, "lockPct": 24},
    {"triggerPct": 40, "lockPct": 34},
    {"triggerPct": 50, "lockPct": 44},
    {"triggerPct": 65, "lockPct": 56},
    {"triggerPct": 80, "lockPct": 72},
    {"triggerPct": 100, "lockPct": 90}
  ],
  "currentTierIndex": -1,
  "tierFloorPrice": null,
  "highWaterPrice": "<entryPrice>",
  "floorPrice": "<absoluteFloor>",
  "currentBreachCount": 0,
  "createdAt": "<ISO 8601>"
}
```

**absoluteFloor:** LONG: `entry × (1 - 0.02/leverage)`, SHORT: `entry × (1 + 0.02/leverage)`. Caps max loss at ~20% ROE (v0.1: tightened from 0.03/~30%).

**Filename:** Main dex: `{ASSET}.json`. XYZ dex: `xyz--SYMBOL.json` (colon → double-dash).

### DSL v5 Cron Management
One cron per strategy. Created when first position opens. Removed on `strategy_inactive`.
```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID={strategyId} PYTHONUNBUFFERED=1 python3 /data/workspace/skills/dsl-dynamic-stop-loss/scripts/dsl-v5.py
```
Schedule: `*/3 * * * *` (every 3 min), isolated session, mid-tier model.

---

## Opportunity Scanner v6

4-stage funnel with 6 hard disqualifiers:
1. Counter-trend on hourly (the "$346 lesson")
2. Extreme RSI (<20 shorts, >80 longs)
3. Counter-trend on 4h with strength >50
4. Volume dying (<0.5x on both timeframes)
5. Heavy unfavorable funding (>50% annualized)
6. BTC macro headwind >30 points

Disqualified assets appear in output with `reason` and `wouldHaveScored` for transparency.

---

## Budget Scaling v7 — Tiered Margin System

**v7 uses tiered margin based on entry count (not budget-based slots):**

| Entry Count | Margin per Trade | Budget per Strategy | Leverage | Notes |
|-------------|-----------------|-------------------|----------|--------|
| 1-2 | $1,450 | $1,500 | 10x | Higher margin for early entries |
| 3-4 | $950 | $1,000 | 10x | Medium margin for mid entries |
| 5-6 | $450 | $500 | 10x | Lower margin for final entries |

**Max entries per day: 6 (flat limit, no dynamic slots)**

**How it works:**
1. Scanner reads current entry count from `fox-trade-counter.json`
2. Finds matching tier from `marginTiers` array
3. Uses that tier's margin/budget for position sizing

**Minimum leverage: 7x.** If max leverage for an asset is below 7x, skip it.

**Auto-Delever:** If a strategy's account drops below its `autoDeleverThreshold` → reduce max slots by 1, close weakest in that strategy.

---

## Rotation Rules

When slots are full in a strategy and a new FIRST_JUMP or IMMEDIATE fires:
- **Rotate if:** new signal is FIRST_JUMP or has 3+ reasons + positive velocity AND weakest position in that strategy is flat/negative ROE with SM conv 0-1
- **Hold if:** current position in Tier 2+ or trending up with SM conv 3+
- **Cross-strategy:** If one strategy is full but another has slots, route to the available strategy instead of rotating

---

## Position Lifecycle

### Opening
1. Signal fires → validate checklist → route to best-fit strategy
2. `create_position` on that strategy's wallet (use `leverageType: "ISOLATED"` for XYZ assets)
3. **Create DSL v5 state file** in `dsl/{strategyId}/{ASSET}.json`
4. **Ensure DSL v5 cron exists** for this strategy (create if first position)
5. Update `fox-trade-counter.json`
6. Alert user

### Closing
1. Close via `close_position` (or DSL v5 auto-closes on breach/SL hit)
2. DSL v5 script auto-deletes state file and reconciles via clearinghouse
3. Alert user with strategy name for context
4. On `strategy_inactive` output → remove the DSL v5 cron for that strategy
5. Evaluate: empty slot in that strategy for next signal?
6. **Re-entry evaluation (v7.2):** If closed from Phase 1, log exit details (asset, direction, exit price, ROE, exit time) for re-entry window tracking

### Re-Entry (v7.2)
1. Emerging Movers detects asset still in top 20 with same direction, `contribVelocity > 5`
2. Validate: within 2h of original exit, original loss ≤ 15% ROE, price moved further in original direction
3. Score ≥ 5 pts (relaxed threshold — direction already validated)
4. Size at 75% of normal margin tier
5. Create DSL state with `isReentry: true`, `reentryOf: "<original_trade_id>"`
6. Alert user: "RE-ENTRY: {ASSET} {DIR} — direction confirmed, 2nd attempt at 75% size"

---

## Margin Types

- **Cross-margin** for crypto (BTC, ETH, SOL, etc.)
- **Isolated margin** for XYZ DEX (GOLD, SILVER, TSLA, etc.) — set `leverageType: "ISOLATED"` and `dex: "xyz"`
- Same wallet holds both cross crypto + isolated XYZ side by side

---

## XYZ Equities

- **Ignore trader count.** XYZ equities often have low SM trader counts — this doesn't invalidate the signal.
- **Use reason count + rank velocity** as primary quality filter instead.
- **Always use isolated margin** (`leverageType: "ISOLATED"`, `dex: "xyz"`).
- **Check max leverage** — many XYZ assets cap at 5x or 3x. If below 7x, skip.

---

## Token Optimization & Context Management

**Model tiers:** See Architecture table. Primary for main-session crons, Mid/Budget for isolated.

**Heartbeat policy:** If script output contains no actionable signals, output HEARTBEAT_OK immediately. Do not reason about what wasn't found.

**Context isolation (multi-signal runs):** Read `fox-strategies.json` ONCE per cron run. Build complete action plan before executing. Send ONE consolidated Telegram per run.

**Skip rules:** Skip redundant checks when data < 3 min old. If all slots full and no FIRST_JUMPs → skip scanner processing.

---

## API Dependencies

All MCP calls go through `fox_config.mcporter_call()` — no direct subprocess invocations. See [references/api-tools.md](references/api-tools.md) for the full tool reference.

| Action | Tool | Used By |
|--------|------|---------|
| Create strategy wallet | `strategy_create_custom_strategy` | Setup |
| Fund wallet | `strategy_top_up` | Setup |
| Open position | `create_position` | Emerging Movers, Opp Scanner |
| Close position | `close_position` | DSL v5, SM Flip, Watchdog |
| Sync stop loss to HL | `edit_position` | DSL v5 |
| Check positions/PnL | `strategy_get_clearinghouse_state` | Watchdog, Portfolio, Health Check |
| Check strategy status | `strategy_get` | DSL v5 |
| Check open orders | `strategy_get_open_orders` | DSL v5 |
| Smart money data | `leaderboard_get_markets` | Emerging Movers, SM Flip, Opp Scanner |
| Top traders | `leaderboard_get_top` | Opp Scanner |
| Asset candles | `market_get_asset_data` | Opp Scanner, Market Regime |
| Market prices | `market_get_prices` | DSL v5 |
| All instruments | `market_list_instruments` | Opp Scanner, Setup |

**Never use:** `dryRun: true` (actually executes), `strategy_close_strategy` (closes entire strategy irreversibly).

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/fox-setup.py` | Setup wizard — adds strategy to registry from budget |
| `scripts/fox_config.py` | Shared config loader — all Fox scripts import this |
| `scripts/fox-emerging-movers.py` | Emerging Movers v4 scanner (FIRST_JUMP, IMMEDIATE, CONTRIB_EXPLOSION) |
| `scripts/fox-sm-flip-check.py` | SM conviction flip detector (multi-strategy) |
| `scripts/fox-monitor.py` | Watchdog — per-strategy margin buffer + position health |
| `scripts/fox-opportunity-scan-v6.py` | Opportunity Scanner v6 (BTC macro, hourly trend, disqualifiers) |
| `scripts/fox-health-check.py` | Per-strategy orphan DSL / state validation |
| `scripts/fox-market-regime.py` | Market regime detector |

## State Files Reference

| File | Purpose |
|------|---------|
| `fox-strategies.json` | Strategy registry (wallets, budgets, DSL config) |
| `fox-trade-counter.json` | Daily trade counter with tiered margin |
| `fox-emerging-movers-history.json` | Emerging movers scan history |
| `market-regime-last.json` | Latest market regime (shared, read-only) |
| `max-leverage.json` | Per-asset max leverage (shared) |
| `dsl/{strategyId}/{ASSET}.json` | DSL v5 per-position state |
| `history/scan-history.json` | Cross-scan momentum tracking |
| `history/fox-scanner-config.json` | Scanner threshold overrides |

See [references/state-schema.md](references/state-schema.md) for complete schemas.

---

## Known Limitations

- **Watchdog blind spot for XYZ isolated:** Can't see isolated position liquidation distances. XYZ positions rely on DSL.
- **Health check only sees crypto wallet:** Total equity may differ from actual.
- **Scanner needs history for momentum:** Cross-scan momentum requires at least 2 scans. First scan produces scored results without momentum data.
