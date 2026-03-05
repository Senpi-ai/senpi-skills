---
name: wolf-strategy
description: >-
  WOLF v6.1 — Fully autonomous multi-strategy trading for Hyperliquid perps via Senpi MCP.
  Manages multiple strategies simultaneously, each with independent wallets, budgets, slots,
  and DSL configs. 6-cron architecture with Emerging Movers scanner (3min, FIRST_JUMP + IMMEDIATE_MOVER),
  DSL v4 trailing stops (combined runner every 3min, 4-tier at 5/10/15/20% ROE),
  SM flip detector (5min), watchdog (5min), risk guardian (5min, account-level guard rails),
  and health checks (10min). Same asset can be traded in different strategies simultaneously.
  Enter early on first jumps, not at confirmed peaks. Dynamic risk-based leverage per strategy.
  Requires Senpi MCP connection, python3, mcporter CLI, and OpenClaw cron system.

---

# WOLF v6.1 — Autonomous Multi-Strategy Trading

The WOLF hunts for its human. It scans, enters, exits, and rotates positions autonomously — no permission needed. When criteria are met, it acts. Speed is edge.

**Proven:** +$1,500 realized, 25+ trades, 65% win rate, single session on $6.5k budget.

**v6: Multi-strategy support.** Each strategy has independent wallet, budget, slots, and DSL config. Same asset can be held in different strategies simultaneously (e.g., Strategy A LONG HYPE + Strategy B SHORT HYPE).

**v6.1: Reduced leverage ranges.** All risk tiers lowered — aggressive now caps at 75% of max leverage (was 100%), moderate at 50% (was 75%), conservative at 25% (was 50%). Prevents over-leveraging on high-max-leverage assets.

---

## Multi-Strategy Architecture

### Strategy Registry (`wolf-strategies.json`)
Central config holding all strategies. Created/updated by `wolf-setup.py`.

```
wolf-strategies.json
├── strategies
│   ├── wolf-abc123 (Aggressive Momentum, 3 slots, tradingRisk=aggressive)
│   └── wolf-xyz789 (Conservative XYZ, 2 slots, tradingRisk=conservative)
└── global (telegram, workspace)
```

### Per-Strategy State
Each strategy gets its own state directory:
```
state/
├── wolf-abc123/
│   ├── dsl-HYPE.json
│   └── dsl-SOL.json
└── wolf-xyz789/
    ├── dsl-HYPE.json    # Same asset, different strategy, no collision
    └── dsl-GOLD.json
```

### Signal Routing
When a signal fires, it's routed to the best-fit strategy:
1. Which strategies have empty slots?
2. Does any strategy already hold this asset? (skip within strategy, allow cross-strategy)
3. Which strategy's risk profile matches? (aggressive gets FIRST_JUMPs, conservative gets DEEP_CLIMBERs)
4. Route to best-fit -> open on that wallet -> create DSL state in that strategy's dir

### Adding a Strategy
```bash
python3 scripts/wolf-setup.py --wallet 0x... --strategy-id UUID --budget 2000 \
    --chat-id 12345 --name "Conservative XYZ" --dsl-preset conservative --provider anthropic
```
This adds to the registry without disrupting running strategies. Disable with `enabled: false` in the registry.

---

## Entry Philosophy — THE Most Important Section

**Enter before the peak, not at the top.**

Leaderboard rank confirmation LAGS price. When an asset jumps from #31->#16 in one scan, the price is moving NOW. By the time it reaches #7 with clean history, the move is over. Speed is edge.

**Core principle:** 2 reasons at rank #25 with a big jump = ENTER. 4+ reasons at rank #5 = SKIP (already peaked).

---

## Quick Start

1. Ensure Senpi MCP is connected (`mcporter list` should show `senpi`)
2. Create a custom strategy wallet: use `strategy_create_custom_strategy` via mcporter
3. Fund the wallet via `strategy_top_up` with your budget
4. **Determine the user's AI provider** — which provider is configured in OpenClaw? (`anthropic`, `openai`, or `google`)
5. Run setup: `python3 scripts/wolf-setup.py --wallet 0x... --strategy-id UUID --budget 6500 --chat-id 12345 --provider anthropic`
6. Create the 6 OpenClaw crons using templates from `references/cron-templates.md`
7. The WOLF is hunting

To add a second strategy, run `wolf-setup.py` again with a different wallet/budget. It adds to the registry.

---

## Architecture — 6 Cron Jobs

| # | Job | Interval | Session | Script | Purpose |
|---|-----|----------|---------|--------|---------|
| 1 | Emerging Movers | **3min** | isolated | `scripts/emerging-movers.py` | Hunt FIRST_JUMP + IMMEDIATE_MOVER signals — primary entry trigger |
| 2 | DSL Combined | **3min** | isolated | `scripts/dsl-combined.py` | Trailing stop exits for ALL open positions across ALL strategies |
| 3 | SM Flip Detector | 5min | isolated | `scripts/sm-flip-check.py` | Cut positions where SM conviction collapses |
| 4 | Watchdog | 5min | isolated | `scripts/wolf-monitor.py` | Per-strategy margin buffer, liq distances, rotation candidates |
| 5 | Health Check | 10min | isolated | `scripts/job-health-check.py` | Per-strategy orphan DSL detection, state validation |
| 6 | Risk Guardian | 5min | isolated | `scripts/risk-guardian.py` | Account-level guard rails: daily loss halt, max entries, consecutive loss cooldown |

**v6 change:** One set of crons for all strategies. Each script reads `wolf-strategies.json` and iterates all enabled strategies internally.

### Model Selection Per Cron — 2-Tier Approach

> **IMPORTANT:** Determine the user's configured AI provider BEFORE running `wolf-setup.py`. Pass `--provider` to auto-select correct model IDs. Do NOT pick models from an unconfigured provider — crons will fail silently.

`wolf-setup.py --provider <name>` auto-configures model IDs for all cron templates. Step down to Budget tier for simple threshold crons to save ~60-70% on those runs.

**Provider defaults** (auto-selected by `--provider`):

| Provider | Mid Model | Budget Model |
|----------|-----------|--------------|
| `anthropic` | `anthropic/claude-sonnet-4-5` | `anthropic/claude-haiku-4-5` |
| `openai` | `openai/gpt-4o` | `openai/gpt-4o-mini` |
| `google` | `google/gemini-2.0-flash` | `google/gemini-2.0-flash-lite` |

| Cron | Session | Model Tier | Reason |
|------|---------|-----------|--------|
| Emerging Movers | isolated | Mid | Multi-strategy routing judgment, entry decisions |
| DSL Combined | isolated | Mid | Script output parsing, rule-based close/alert |
| Health Check | isolated | Mid | Rule-based file repair, action routing |
| SM Flip Detector | isolated | Budget | Binary: conviction≥4 + 100 traders → close |
| Watchdog | isolated | Budget | Threshold checks → alert |
| Risk Guardian | isolated | Budget | Guard rail evaluation, send notifications |

**Single-model option:** All 6 crons can run on one model. Simpler but costs more for the crons that do simple threshold/binary work.

**Model ID gotchas:**
- `--provider` auto-selects models. Only use `--mid-model`/`--budget-model` to override specific tiers.
- Budget should be the cheapest model that can follow explicit if/then rules. Mid should handle structured JSON parsing and multi-strategy routing reliably.
- Agents are often not model-aware — they may suggest deprecated IDs (e.g. `claude-3-5-haiku-20241022`) or hallucinate model names. Always use `--provider` instead of manually specifying model IDs.
- If a cron fails to create or run due to an invalid model ID, fall back to your Mid model for that cron. A working cron on the "wrong" tier is better than a broken cron.
- When in doubt, use your Mid model for all 6 crons (single-model option) and optimize tiers later.

## Cron Setup

**Critical:** Crons are **OpenClaw crons**, NOT senpi crons. All 6 crons run in **isolated sessions** (`agentTurn`) — each runs in its own session with no context pollution, enabling cheaper model tiers.

Create each cron using the OpenClaw cron tool. The exact mandate text for each cron is in **`references/cron-templates.md`**. Read that file, replace the placeholders (`{TELEGRAM}`, `{SCRIPTS}`, and `{WORKSPACE}` in v6), and create all 6 crons.

**v6 simplification:** No more per-wallet/per-strategy placeholders in cron mandates. Scripts read all strategy info from the registry.

---

## Autonomy Rules

The WOLF operates autonomously by default. The agent does NOT ask permission to:
- Open a position when entry checklist passes
- Close a position when DSL triggers or conviction collapses
- Rotate out of weak positions into stronger signals
- Cut dead weight (SM conv 0, negative ROE, 30+ min)

The agent DOES notify the user (via Telegram) after every action.

---

## Entry Signals — Priority Order

### 1. FIRST_JUMP (Highest Priority)

**What:** Asset jumps 10+ ranks from #25+ in ONE scan AND was not in previous scan's top 50 (or was at rank >= #30).

**Action:** Enter IMMEDIATELY. This is the money signal. Route to best-fit strategy with available slots.

**Checklist:**
- `isFirstJump: true` in scanner output
- **2+ reasons is enough** (don't require 4+)
- **vel > 0 is sufficient** (velocity hasn't had time to build on a first jump)
- Leverage auto-calculated from `tradingRisk` + asset `maxLeverage` + signal `conviction`
- Slot available in target strategy (or rotation justified)
- >= 10 SM traders (crypto); for XYZ equities, ignore trader count

**What to ignore:**
- Erratic rank history — the scanner excludes the current jump from erratic checks.
- Low velocity — first jumps haven't had time to build velocity.

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

---

## Anti-Patterns — When NOT to Enter

- **NEVER enter assets already at #1-10.** That's the top, not the entry. Rank = what already happened.
- **NEVER wait for a signal to "clean up."** By the time rank history is smooth and velocity is high, the move is priced in.
- **4+ reasons at rank #5 = SKIP.** The asset already peaked. You'd be buying the top.
- **2 reasons at rank #25 with a big jump = ENTER.** The move is just starting.
- **Leaderboard rank != future price direction.** Rank reflects past trader concentration. Price moves first, rank follows.
- **Negative velocity + no jump = skip.** Slow bleeders going nowhere.
- **Oversold shorts** (RSI < 30 + extended 24h move) = skip.

---

## Late Entry Anti-Pattern

This deserves its own section because it's the #1 way to lose money with WOLF.

**The pattern:** Scanner fires FIRST_JUMP for ASSET at #25->#14. You hesitate. Next scan it's #10. Next scan #7 with 5 reasons and clean history. NOW it looks "safe." You enter. It reverses from #5.

**The fix:** Enter on the FIRST signal or don't enter at all. If you missed it, wait for the next asset. There's always another FIRST_JUMP coming.

**Rule:** If an asset has been in the top 10 for 2+ scans already, it's too late. Move on.

---

## Phase 1 Auto-Cut

Positions that never gain momentum get cut automatically.

**Rules:**
- **90-minute maximum** in Phase 1 (pre-Tier 1 DSL). If ROE never hits 5% in 90 minutes, close.
- **Weak peak early cut:** If peak ROE was < 3% and ROE is now declining -> close after 45 minutes. Don't wait 90.
- **Dead weight:** SM conviction = 0, negative ROE, position open 30+ minutes -> instant cut regardless of phase.

**Why:** Phase 1 positions have no trailing stop protection. They're running on faith. If SM conviction doesn't materialize in 90 min, the thesis is wrong.

---

## Exit Rules

### 1. DSL v4 Mechanical Exit (Trailing Stops)

All trailing stops handled automatically by `dsl-combined.py` across all strategies.

### 2. SM Conviction Collapse
Conv drops to 0 or 4->1 with mass trader exodus -> instant cut.

### 3. Dead Weight
Conv 0, negative ROE, 30+ min -> instant cut.

### 4. SM Flip
Conviction 4+ in the OPPOSITE direction with 100+ traders -> cut immediately.

### 5. Race Condition Prevention
When ANY job closes a position -> immediately:
1. Set DSL state `active: false` in `state/{strategyKey}/dsl-{ASSET}.json`
2. Alert user
3. Evaluate: empty slot in that strategy for next signal?

**v6 note:** Since DSL is a combined runner iterating all strategies, no per-position crons to manage. Just set `active: false` in the state file.

---

## DSL v4 — Trailing Stop System

### Phase 1 (Pre-Tier 1): Absolute floor
- LONG floor = entry x (1 - 10%/leverage)
- SHORT floor = entry x (1 + 10%/leverage)
- 3 consecutive breaches -> close
- **Max duration: 90 minutes** (see Phase 1 Auto-Cut above)

### Phase 2 (Tier 1+): Trailing tiers

| Tier | ROE Trigger | Lock % of High-Water | Breaches to Close |
|------|-------------|---------------------|-------------------|
| 1 | 5% | 50% | 2 |
| 2 | 10% | 65% | 2 |
| 3 | 15% | 75% | 2 |
| 4 | 20% | 85% | 1 |

### Stagnation Take-Profit
Auto-close if ROE >= 8% and high-water stale for 1 hour.

### DSL State File
Each position gets `state/{strategyKey}/dsl-{ASSET}.json`. The combined runner iterates all active state files across all strategies. See `references/state-schema.md` for the full schema and critical gotchas (triggerPct not threshold, lockPct not retracePct, etc.).

---

## Rotation Rules

When slots are full in a strategy and a new FIRST_JUMP or IMMEDIATE fires:
- **Cross-strategy first:** If one strategy is full but another has slots, route to the available strategy instead of rotating
- **Rotation cooldown (mandatory):** Only rotate a position listed in `rotationEligibleCoins` from the scanner output. Positions younger than `rotationCooldownMinutes` (default 45 min) are ineligible — they have flat/negative ROE by design. Do NOT override this with judgment.
- **Rotate if:** new signal is FIRST_JUMP or has 3+ reasons + positive velocity AND weakest **eligible** position (from `rotationEligibleCoins`) is flat/negative ROE with SM conv 0-1
- **Hold if:** current position in Tier 2+ or trending up with SM conv 3+
- **If `hasRotationCandidate: false`:** all positions are in cooldown. Do not rotate. Output HEARTBEAT_OK.

---

## Budget Scaling

All sizing is calculated from budget (30% per slot):

| Budget | Slots | Margin/Slot | Daily Loss Limit |
|--------|-------|-------------|------------------|
| $500 | 2 | $150 | -$75 |
| $2,000 | 2 | $600 | -$300 |
| $6,500 | 3 | $1,950 | -$975 |
| $10,000+ | 3-4 | $3,000 | -$1,500 |

Leverage is computed dynamically per position from `tradingRisk` + asset `maxLeverage` + signal `conviction`. See "Risk-Based Leverage" section below.

**Auto-Delever:** If a strategy's account drops below its `autoDeleverThreshold` -> reduce max slots by 1, close weakest in that strategy.

---

## Position Lifecycle

### Opening
1. Signal fires -> validate checklist -> route to best-fit strategy
2. Enter via `python3 scripts/open-position.py --strategy {strategyKey} --asset {ASSET} --direction {DIR} --conviction {CONVICTION}`
   Leverage is auto-calculated from `tradingRisk` + asset `maxLeverage` + `conviction`. This atomically opens the position AND creates the DSL state file. Do NOT manually create DSL JSON.
3. Alert user

### Closing
1. Close via `close_position` (or DSL auto-closes)
2. **Immediately** set DSL state `active: false`
3. Alert user with strategy name for context
4. Evaluate: empty slot in that strategy for next signal?

---

## Margin Types

- **Cross-margin** for crypto (BTC, ETH, SOL, etc.)
- **Isolated margin** for XYZ DEX (GOLD, SILVER, TSLA, etc.) — set `leverageType: "ISOLATED"` and `dex: "xyz"`
- Same wallet holds both cross crypto + isolated XYZ side by side

---

## XYZ Equities

XYZ DEX assets (GOLD, SILVER, TSLA, AAPL, etc.) behave differently:

- **Ignore trader count.** XYZ equities often have low SM trader counts — this doesn't invalidate the signal.
- **Use reason count + rank velocity** as primary quality filter instead.
- **Always use isolated margin** (`leverageType: "ISOLATED"`, `dex: "xyz"`).
- **Leverage auto-calculated** — many XYZ assets cap at 3-5x. No skip needed; leverage is computed dynamically from `tradingRisk`.

---

## Token Optimization & Context Management

**Model tiers:** See "Model Selection Per Cron" table. Mid for complex crons, Budget for simple threshold crons. Configure per-cron in OpenClaw.

**Heartbeat policy:** If script output contains no actionable signals, output HEARTBEAT_OK immediately. Do not reason about what wasn't found.

**Context isolation (multi-signal runs):** Read `wolf-strategies.json` ONCE per cron run. Build a complete action plan before executing any tool calls. Send ONE consolidated Telegram per run, not one per signal.

**Skip rules:** Skip redundant checks when data < 3 min old. If all slots full and no FIRST_JUMPs → skip scanner processing. If SM check shows no flips and < 5 min old → skip.

---

## Risk-Based Leverage

Leverage is computed dynamically per position instead of being hardcoded. The formula uses the **strategy's risk tier**, the **asset's max leverage**, and **signal conviction**.

### Formula

```
leverage = maxLeverage × (rangeLow + (rangeHigh - rangeLow) × conviction)
clamped to [1, maxLeverage]
```

### Risk Tiers

| Tier | Range of Max Leverage | Example (40x max, mid conviction) | Example (3x max, mid conviction) |
|------|----------------------|----------------------------------|----------------------------------|
| `conservative` | 15% – 25% | 8x | 1x |
| `moderate` | 25% – 50% | 15x | 1x |
| `aggressive` | 50% – 75% | 25x | 2x |

### Conviction

Conviction (0.0–1.0) determines where within a tier's range to land. It's **auto-derived** from scanner output:

- **Emerging Movers**: mapped from signal type (FIRST_JUMP=0.9, CONTRIB_EXPLOSION=0.8, IMMEDIATE_MOVER=0.7, NEW_ENTRY_DEEP=0.7, DEEP_CLIMBER=0.5)

### Override

Pass `--leverage N` to `open-position.py` to bypass auto-calculation (capped against max leverage as before).

### Backward Compatibility

- Existing strategies without `tradingRisk` default to `"moderate"`
- `defaultLeverage` in the registry is used as fallback when `max-leverage.json` data is unavailable

---

## Guard Rails — Risk Guardian

The Risk Guardian (6th cron, 5min, Budget tier) enforces account-level guard rails that protect against runaway losses across all positions in a strategy. Per-position DSL handles individual trailing stops; guard rails handle the portfolio.

### Gate States

| Gate | Meaning | Resets |
|------|---------|--------|
| `OPEN` | Normal trading | — |
| `COOLDOWN` | Temporary pause after consecutive losses | Auto-expires after `cooldownMinutes` |
| `CLOSED` | Halted for the day | Midnight UTC |

When gate != OPEN, `open-position.py` refuses new entries and `emerging-movers.py` shows `available: 0` for that strategy.

### Guard Rail Rules

| Rule | Trigger | Action |
|------|---------|--------|
| **G1** Daily Loss Halt | `accountValue - accountValueStart <= -dailyLossLimit` | Gate → CLOSED |
| **G3** Max Entries | `entries >= maxEntriesPerDay` (bypass if profitable day + `bypassOnProfit`) | Gate → CLOSED |
| **G4** Consecutive Losses | Last N results all "L" (N = `maxConsecutiveLosses`) | Gate → COOLDOWN for `cooldownMinutes` |

### Config (`guardRails` in strategy registry)

```json
{
  "guardRails": {
    "maxEntriesPerDay": 8,
    "bypassOnProfit": true,
    "maxConsecutiveLosses": 3,
    "cooldownMinutes": 60
  }
}
```

All parameters are optional — defaults are used for any missing key. Set per strategy in `wolf-strategies.json`.

---

## Known Limitations

- **Watchdog blind spot for XYZ isolated:** The watchdog monitors cross-margin buffer but can't see isolated position liquidation distances in the same way. XYZ positions rely on DSL for protection.
- **Health check only sees crypto wallet:** The health check can't see XYZ positions for margin calculations. Total equity may differ.

---

## Backward Compatibility

- `wolf_config.py` auto-migrates legacy `wolf-strategy.json` to registry format on first load
- Old `dsl-state-WOLF-*.json` files detected and migrated to `state/wolf-{id}/dsl-*.json`
- All scripts work with both layouts during transition
- All DSL logic is handled by `dsl-combined.py` (multi-strategy runner)

---

## Troubleshooting

See `references/learnings.md` for known bugs, gotchas, and trading discipline rules. Key ones:
- **`dryRun: true` actually executes** — NEVER use dryRun
- **Max leverage varies per asset** — always check `max-leverage.json`
- **`close_position` is the close tool** — not `edit_position`
- **Tier 1 lock != guaranteed profit** — lock is from high-water, not entry

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/wolf-setup.py` | Setup wizard — adds strategy to registry from budget |
| `scripts/wolf_config.py` | Shared config loader — all scripts import this |
| `scripts/emerging-movers.py` | Emerging Movers v4 scanner (FIRST_JUMP, IMMEDIATE, CONTRIB_EXPLOSION) |
| `scripts/dsl-combined.py` | DSL v4 combined trailing stop engine (all positions, all strategies) |
| `scripts/sm-flip-check.py` | SM conviction flip detector (multi-strategy) |
| `scripts/wolf-monitor.py` | Watchdog — per-strategy margin buffer + position health |
| `scripts/job-health-check.py` | Per-strategy orphan DSL / state validation |
| `scripts/risk-guardian.py` | Risk Guardian — account-level guard rails (daily loss, max entries, consecutive loss cooldown) |
