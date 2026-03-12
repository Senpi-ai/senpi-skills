---
name: wolf-strategy
description: >-
  WOLF v6.3 — Fully autonomous multi-strategy trading for Hyperliquid perps via Senpi MCP.
  Manages multiple strategies simultaneously, each with independent wallets, budgets, slots,
  and DSL configs. 5+N cron architecture: 5 shared wolf crons (Emerging Movers 3min, SM Flip 5min,
  Watchdog 5min, Risk Guardian 5min, Health Check 10min) plus one DSL v5.2 cron per strategy
  (native Hyperliquid SL sync via dsl-dynamic-stop-loss skill v5.3). Same asset can be traded in
  different strategies simultaneously. Enter early on first jumps, not at confirmed peaks.
  Dynamic risk-based leverage per strategy.
  Requires Senpi MCP connection, python3, mcporter CLI, OpenClaw cron system, and
  dsl-dynamic-stop-loss skill (provides dsl-cli.py + dsl-v5.py).

---

# WOLF v6.3 — Autonomous Multi-Strategy Trading

The WOLF hunts for its human. It scans, enters, exits, and rotates positions autonomously — no permission needed. When criteria are met, it acts. Speed is edge.

**Proven:** +$1,500 realized, 25+ trades, 65% win rate, single session on $6.5k budget.

**v6: Multi-strategy support.** Each strategy has independent wallet, budget, slots, and DSL config. Same asset can be held in different strategies simultaneously (e.g., Strategy A LONG HYPE + Strategy B SHORT HYPE).

**v6.1.1: Risk Guardian & strategy lock.** 6th cron (5min, Budget tier) enforcing account-level guard rails — daily loss halt, max entries per day, consecutive loss cooldown. Strategy lock for concurrency protection. Gate check in `open-position.py` refuses new entries when gate != OPEN.

**v6.3: DSL v5.3 High Water.** Default DSL profile and strategy config use High Water Mode (`lockMode: pct_of_high_water`) with `lockHwPct` tiers and per-tier `consecutiveBreachesRequired`. One DSL cron per strategy runs `dsl-v5.py` (v5.3); state files in `{workspace}/dsl/{strategyId_UUID}/`. Migration script: `wolf-migrate-dsl.py`.

**v6.2: DSL v5.2 integration.** Replaced internal `dsl-combined.py` with the `dsl-dynamic-stop-loss` skill. Native Hyperliquid stop-loss sync via `dsl-v5.py`.

---

## Skill Attribution

When creating a strategy, include `skill_name` and `skill_version` in the call. See `references/skill-attribution.md` for details.

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
DSL state files live under the DSL skill's directory (by strategy UUID). Wolf-specific state (trade counters) stays in `state/`:
```
dsl/                              # owned by dsl-dynamic-stop-loss skill
├── abc12345-.../                 # Strategy A UUID
│   ├── strategy-abc12345....json # DSL strategy config
│   ├── HYPE.json                 # Position state
│   └── xyz--SILVER.json          # XYZ position
└── xyz78901-.../                 # Strategy B UUID
    └── HYPE.json                 # Same asset, different UUID dir, no collision

state/                            # wolf-only state (no DSL files here)
├── wolf-abc123/
│   └── trade-counter.json
└── wolf-xyz789/
    └── trade-counter.json
```

### Signal Routing
When a signal fires, it's routed to the best-fit strategy:
1. Which strategies have empty slots?
2. Does any strategy already hold this asset? (skip within strategy, allow cross-strategy)
3. Which strategy's risk profile matches? (aggressive gets FIRST_JUMPs, conservative gets DEEP_CLIMBERs)
4. Route to best-fit -> open on that wallet -> create DSL state in that strategy's dir

**DSL default:** Use this skill's **`dsl-profile.json`** as the default when setting up DSL (e.g. `dsl-cli.py add-dsl` / `update-dsl` with `--configuration @<path-to-wolf-strategy>/dsl-profile.json`). Use it unless the user explicitly provides a custom DSL configuration via the agent.

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
2. Ensure the `dsl-dynamic-stop-loss` skill is installed alongside this skill (provides `dsl-cli.py` and `dsl-v5.py`)
3. Create a custom strategy wallet: use `strategy_create_custom_strategy` via mcporter
4. Fund the wallet via `strategy_top_up` with your budget
5. **Determine the user's AI provider** — which provider is configured in OpenClaw? (`anthropic`, `openai`, or `google`)
6. Run setup: `python3 scripts/wolf-setup.py --wallet 0x... --strategy-id UUID --budget 6500 --chat-id 12345 --provider anthropic`
   - Setup auto-discovers `dsl-cli.py` and `dsl-v5.py` paths and stores them in `wolf-strategies.json` global config
   - If auto-discovery fails, set `global.dslCliPath` and `global.dslScriptPath` manually in the registry (or set env `DSL_CLI_PATH`)
7. **Upgrading from v6.1.x?** Migrate existing DSL state files: `python3 scripts/wolf-migrate-dsl.py`
   - Copies active `state/{strategyKey}/dsl-{ASSET}.json` → `dsl/{UUID}/{ASSET}.json`
   - Tombstones old files (sets `active: false`). Run once before switching crons.
8. Create the 5 wolf crons + 1 DSL cron per strategy using templates from `references/cron-templates.md`
   - The DSL cron mandate is in `cronTemplates.dsl_per_strategy` from setup output — use the `message` field directly
9. The WOLF is hunting

To add a second strategy, run `wolf-setup.py` again with a different wallet/budget. It adds to the registry and creates the DSL cron for the new strategy.

---

## Architecture — 5+N Cron Jobs

| # | Job | Interval | Session | Script | Purpose |
|---|-----|----------|---------|--------|---------|
| 1 | Emerging Movers | **3min** | isolated | `scripts/emerging-movers.py` | Hunt FIRST_JUMP + IMMEDIATE_MOVER signals — primary entry trigger |
| 2 | DSL *(per strategy)* | **3min** | isolated | `dsl-v5.py` (DSL skill) | Trailing stops + native HL SL sync for ONE strategy's positions |
| 3 | SM Flip Detector | 5min | isolated | `scripts/sm-flip-check.py` | Cut positions where SM conviction collapses |
| 4 | Watchdog | 5min | isolated | `scripts/wolf-monitor.py` | Per-strategy margin buffer, liq distances (Phase 1 auto-cut is handled by DSL cron when configured) |
| 5 | Health Check | 10min | isolated | `scripts/job-health-check.py` | Per-strategy orphan DSL detection, state validation |
| 6 | Risk Guardian | 5min | isolated | `scripts/risk-guardian.py` | Account-level guard rails: daily loss halt, max entries, consecutive loss cooldown |

**v6.2 change:** DSL is no longer a combined runner. Each strategy has its own `dsl-v5.py` cron (from the `dsl-dynamic-stop-loss` skill), run with `--strategy-id {strategyId_UUID} --state-dir {DSL_STATE_DIR}` (env vars are fallback). Wolf scripts call `dsl-cli.py` (add-dsl / delete-dsl) to create and archive DSL state; they never write state directly.

With 2 strategies: **7 crons total** (5 wolf + 2 DSL). DSL cron IDs are stored in `dslCronJobId` in the strategy registry.

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
| DSL (per strategy) | isolated | Mid | ndjson parsing, rule-based close/alert, cron lifecycle |
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

**Critical:** Crons are **OpenClaw crons**, NOT senpi crons. All crons run in **isolated sessions** (`agentTurn`) — each runs in its own session with no context pollution, enabling cheaper model tiers.

Create each cron using the OpenClaw cron tool. The exact mandate text for each cron is in **`references/cron-templates.md`**. Replace placeholders (`{TELEGRAM}`, `{SCRIPTS}`, `{WORKSPACE}`).

**DSL cron:** The DSL per-strategy cron mandate is generated by `wolf-setup.py` in `cronTemplates.dsl_per_strategy.payload.message`. Use it directly — `wolf-setup.py` auto-fills the `dsl-v5.py` path from `global.dslScriptPath` in the registry. If the path could not be auto-discovered (placeholder `{DSL_SCRIPTS}` still present), read `global.dslScriptPath` from `wolf-strategies.json` after installing the DSL skill and substitute it manually.

**v6.2:** 5 shared wolf crons + 1 DSL cron per strategy. No more single DSL Combined cron.

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

Phase 1 time-based cuts (hard timeout, weak peak, dead weight) are **managed by the DSL cron** when the skill supplies `phase1.hardTimeout`, `phase1.weakPeakCut`, and/or `phase1.deadWeightCut` in its DSL config. Wolf-strategy does not implement these in the Watchdog; include them in the skill's dsl-profile (or defaultConfig) if desired.

**When configured in DSL:**
- **Hard timeout:** Close when elapsed in Phase 1 ≥ intervalInMinutes (e.g. 90).
- **Weak peak early cut:** After intervalInMinutes (e.g. 45), close if peak ROE &lt; minValue and current ROE &lt; peak ROE.
- **Dead weight cut:** Close when ROE was never positive and elapsed ≥ intervalInMinutes (e.g. 30).

**Why:** Phase 1 positions have no trailing stop protection. If the skill enables these in DSL config, the DSL cron enforces them; wolf-strategy no longer needs to manage them.

---

## Exit Rules

### 1. DSL v4 Mechanical Exit (Trailing Stops)

All trailing stops handled automatically by `dsl-combined.py` across all strategies.

### 2. SM Conviction Collapse
Conv drops to 0 or 4->1 with mass trader exodus -> instant cut.

### 3. Dead Weight
When DSL config includes `phase1.deadWeightCut`, the DSL cron closes positions that have never gone positive (ROE negative entire time) after the configured minutes. Other dead-weight logic (e.g. conv 0, negative ROE) can remain in agent mandate if desired.

### 4. SM Flip
Conviction 4+ in the OPPOSITE direction with 100+ traders -> cut immediately.

### 5. Race Condition Prevention
When ANY wolf script closes a position:
1. Call `dsl-cli.py delete-dsl {strategyId} {asset} {main|xyz} --state-dir {DSL_STATE_DIR}` to archive the DSL state
2. If CLI output has `cron_to_remove` → remove that DSL cron (it was the last position for that strategy)
3. Alert user via Telegram
4. Evaluate: empty slot in that strategy for next signal?

**Never set `active: false` in place.** DSL v5.2 archives files by rename; an in-place deactivation will confuse `dsl-v5.py`.

---

## DSL v5.2 — Trailing Stop System (via dsl-dynamic-stop-loss skill)

Wolf delegates all DSL logic to the `dsl-dynamic-stop-loss` skill. Wolf's role:
- **Create:** call `dsl-cli.py add-dsl {strategyId} {asset} {dex} --skill wolf-strategy --configuration {...} --state-dir {DSL_STATE_DIR}` after opening a position
- **Delete:** call `dsl-cli.py delete-dsl {strategyId} {asset} {dex} --state-dir {DSL_STATE_DIR}` after closing a position
- **Run:** `dsl-v5.py` runs as a per-strategy cron with `--strategy-id {UUID} --state-dir {DSL_STATE_DIR}` (env vars are fallback)

Wolf never writes DSL state files directly.

### Phase 1 (Pre-Tier 1): Absolute floor
- LONG floor = entry × (1 − retraceThreshold/leverage)  where retraceThreshold=0.10 (10% ROE)
- SHORT floor = entry × (1 + retraceThreshold/leverage)
- 3 consecutive breaches → close
- **Max duration: 90 minutes** (enforced by Watchdog, not DSL v5 — see Phase 1 Auto-Cut)
- **Native HL SL:** dsl-v5.py sets a real stop-loss order on HL via `edit_position`. Between cron runs, HL's engine protects the position.

### Phase 2 (Tier 1+): Trailing tiers

| Tier | ROE Trigger | Lock % of High-Water | Breaches (shared) |
|------|-------------|---------------------|-------------------|
| 1 | 5% | 50% | 2 (majority from wolf tier config) |
| 2 | 10% | 65% | 2 |
| 3 | 15% | 75% | 2 |
| 4 | 20% | 85% | 2 |

Note: DSL v5.2 uses a single `consecutiveBreachesRequired` for all tiers. `build_wolf_dsl_config()` derives it from the majority breach count in wolf's tier config.

### DSL State Files
Location: `dsl/{strategyId_UUID}/{ASSET}.json` (e.g. `dsl/6a23783a-.../HYPE.json`).
See `references/state-schema.md` for schema. Key difference from v4: `phase1.retraceThreshold` is a fraction (0.10), not a percentage (10).

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
1. Signal fires → validate checklist → route to best-fit strategy
2. Enter via `python3 scripts/open-position.py --strategy {strategyKey} --asset {ASSET} --direction {DIR} --conviction {CONVICTION}`
   - Atomically opens position AND calls `dsl-cli.py add-dsl` to create DSL state
   - DSL CLI fetches fill data (entryPx, size, leverage) directly from clearinghouse
   - Do NOT manually create DSL JSON
3. Check output for `dsl_cron_needed: true` — if present, create the DSL cron for this strategy
4. Alert user

### Closing
1. Close via `close_position` MCP call (or DSL auto-closes via its own cron)
2. Call `dsl-cli.py delete-dsl {strategyId} {asset} {dex}` to archive DSL state — **never set `active: false` directly**
3. If CLI output has `cron_to_remove` → remove that DSL cron
4. Alert user with strategy name for context
5. Evaluate: empty slot in that strategy for next signal?

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
- **DSL v4 → v5.2 migration:** Run `python3 scripts/wolf-migrate-dsl.py` once. Moves active `state/{strategyKey}/dsl-{ASSET}.json` files to `dsl/{UUID}/{ASSET}.json`. Old files tombstoned (`active: false`). Must run before switching to DSL v5 crons.
- Old DSL Combined cron should be removed after migration and per-strategy DSL crons are running

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
| `scripts/wolf-setup.py` | Setup wizard — adds strategy to registry, creates DSL strategy config + cron |
| `scripts/wolf_config.py` | Shared config loader — all scripts import this; provides `resolve_dsl_cli_path()`, `build_wolf_dsl_config()` |
| `scripts/emerging-movers.py` | Emerging Movers v4 scanner (FIRST_JUMP, IMMEDIATE, CONTRIB_EXPLOSION) |
| `scripts/open-position.py` | Opens position + calls `dsl-cli.py add-dsl` atomically |
| `scripts/sm-flip-check.py` | SM conviction flip detector (multi-strategy) |
| `scripts/wolf-monitor.py` | Watchdog — per-strategy margin buffer, phase1 auto-cut + `delete-dsl` |
| `scripts/job-health-check.py` | Per-strategy orphan DSL / state validation — auto-heals via CLI |
| `scripts/risk-guardian.py` | Risk Guardian — account-level guard rails (daily loss, max entries, consecutive loss cooldown) |
| `scripts/wolf-migrate-dsl.py` | One-time migration: moves DSL state from v4 path to v5.2 path |
| *(DSL skill)* `dsl-cli.py` | DSL lifecycle CLI: `add-dsl`, `delete-dsl`, `pause-dsl`, `resume-dsl` |
| *(DSL skill)* `dsl-v5.py` | DSL cron runner: trailing stops + native HL SL sync, one per strategy |
