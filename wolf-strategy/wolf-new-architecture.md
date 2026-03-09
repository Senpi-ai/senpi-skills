# Migrating WOLF v6.1.1 to the Plugin Architecture

**Version 2.0 · March 2026**

---

## 1. Current WOLF v6.1.1 Inventory

WOLF v6.1.1 is a 6-cron, 9-script multi-strategy system running on OpenClaw isolated `agentTurn` crons with a 2-tier model approach.

### Scripts

| Script | Lines (est) | Purpose |
|---|---|---|
| `wolf_config.py` | ~200 | Shared config loader. Registry loading, legacy migration, state paths, `fcntl` locking, `mcporter_call` wrapper |
| `wolf-setup.py` | ~300 | Setup wizard. Adds strategy to registry, calculates params, outputs cron templates |
| `emerging-movers.py` | ~200 | Scanner. Leaderboard fetch, signal classification (FIRST_JUMP, IMMEDIATE_MOVER, DEEP_CLIMBER, etc.), scan history, `topPicks` pre-selection, slot availability per strategy |
| `dsl-combined.py` | ~300 | DSL runner. Iterates all strategies, all positions. Price fetch, tier math, close on breach, stagnation |
| `sm-flip-check.py` | ~150 | SM flip detector. Per-strategy conviction checks, FLIP_NOW alerts |
| `wolf-monitor.py` | ~180 | Watchdog. Per-strategy margin buffer, liq distances, `action_required` for emergency closes |
| `open-position.py` | ~200 | Atomic opener. Opens position + creates DSL state in one step. Gate check (refuses if gate != OPEN). Concurrency lock |
| `job-health-check.py` | ~150 | Health check. Per-strategy state validation, orphan DSL detection, auto-repair, `notifications` array |
| `risk-guardian.py` | ~200 | Risk guardian. Guard rails: daily loss halt (G1), max entries (G3), consecutive loss cooldown (G4). Gate state management (OPEN/COOLDOWN/CLOSED) |

**Total: ~1,880 lines of Python across 9 scripts.**

### Crons (6)

| # | Job | Interval | Model Tier | Script | Purpose |
|---|---|---|---|---|---|
| 1 | Emerging Movers | 3min | Mid | `emerging-movers.py` | Signal detection + entry via `open-position.py` |
| 2 | DSL Combined | 3min | Mid | `dsl-combined.py` | Trailing stop exits, all positions, all strategies |
| 3 | SM Flip Detector | 5min | Budget | `sm-flip-check.py` | Cut positions on conviction collapse |
| 4 | Watchdog | 5min | Budget | `wolf-monitor.py` | Margin buffer, emergency closes |
| 5 | Health Check | 10min | Mid | `job-health-check.py` | State validation, auto-repair |
| 6 | Risk Guardian | 5min | Budget | `risk-guardian.py` | Guard rails, gate management |

All crons use isolated `agentTurn` sessions. Each runs in its own context with no pollution between runs.

### Key Architecture Features

**Multi-strategy registry** (`wolf-strategies.json`): Holds all strategies with independent wallets, budgets, slots, DSL presets, and `tradingRisk` levels. Scripts iterate all enabled strategies internally — one set of crons regardless of strategy count.

**Per-strategy state directories**: `state/{strategyKey}/dsl-{ASSET}.json`. Same asset can be traded in different strategies simultaneously without collision.

**Signal routing**: When emerging movers detects a signal, it checks which strategies have empty slots, whether the asset is already held within a strategy (skip) vs cross-strategy (allow), and which strategy's risk profile matches (aggressive gets FIRST_JUMPs, conservative gets DEEP_CLIMBERs).

**Guard rails**: Risk guardian manages gate states per strategy (OPEN/COOLDOWN/CLOSED). `open-position.py` refuses entries when gate != OPEN. Daily loss halt, max entries per day, consecutive loss cooldown with auto-reset.

**2-tier model approach**: Crons that need judgment (Emerging Movers, DSL, Health Check) use Mid tier. Crons that do binary threshold checks (SM Flip, Watchdog, Risk Guardian) use Budget tier. Provider-aware (`--provider anthropic/openai/google`).

**Notification policy**: Only notify on actual trade actions (open, close, auto-fix). Never notify on informational warnings, pending retries, or transient errors. HEARTBEAT_OK if no action taken.

**Strategy locking**: `fcntl` file-based locking in `wolf_config.py` prevents race conditions between scanner and guardian operating on the same strategy.

**Atomic position opening**: `open-position.py` opens position + creates DSL state file in one step. No manual DSL JSON creation. Takes `--strategy`, `--asset`, `--signal-index`. Leverage auto-calculated from `tradingRisk` * asset max leverage * signal conviction.

---

## 2. Migration Map

Every WOLF v6.1.1 component maps to the plugin architecture:

### Component-by-Component

#### 1. `wolf_config.py` (shared config loader) → Plugin core

**Current:** Every script imports `wolf_config` to load registry, resolve paths, handle locking, wrap mcporter calls.

**New:** The plugin IS the shared config loader. Registry management, state paths, locking, and MCP client are all plugin-internal. This module disappears entirely — its functionality is the plugin's Layer 1.

#### 2. `wolf-setup.py` (setup wizard) → `skill.yaml` + install flow

**Current:** CLI tool that calculates params from budget, writes to registry, outputs cron templates.

**New:** Budget-based calculations (margin per slot, auto-delever threshold, daily loss limit) move into the plugin's risk guard module. DSL presets (aggressive/conservative) become config options in `skill.yaml`. The setup wizard is replaced by `clawhub install` reading `skill.yaml` and prompting for variables.

Adding a second strategy = dropping a second strategy block in `skill.yaml` or a second `skill.yaml`. No setup script.

#### 3. `emerging-movers.py` (scanner) → Plugin background service + LLM decision

**Current:** 3min cron wakes agent → agent runs script → parses JSON → reads mandate → follows `topPicks` priority → calls `open-position.py`. Agent involvement even when no signals (HEARTBEAT_OK).

**New:** Plugin runs scanner internally every 3min. Signal classification (FIRST_JUMP, IMMEDIATE_MOVER, etc.) lives in the `emerging_movers` primitive. Scan history stays in memory. When signals detected → plugin calls `llm-task` with the skill's decision prompt for routing/entry judgment. No signal → nothing happens (zero tokens).

**What moves to plugin:** Timer, leaderboard API call, signal classification, scan history, slot availability checks, signal routing to best-fit strategy.

**What becomes the LLM decision prompt:** "Given this FIRST_JUMP signal and 2 strategies with available slots, which strategy should it route to? Should we rotate out a weak position?" — the nuanced routing judgment.

#### 4. `open-position.py` (atomic opener) → Plugin internal function

**Current:** Called by agent after scanner decision. Opens position via mcporter, creates DSL state file, checks gate state, acquires strategy lock.

**New:** Becomes an internal plugin function called directly after LLM entry decision. Gate check, strategy lock, position opening, DSL state creation — all handled in-process. No subprocess, no agent mediation.

The `{skill}_override` agent tool wraps this for manual user commands ("open BTC long on my aggressive strategy").

#### 5. `dsl-combined.py` (DSL runner) → Plugin background service (pure code)

**Current:** 3min cron wakes agent → agent runs script → script iterates all strategies, all positions → fetches prices → runs DSL math → closes breached positions → agent parses output.

**New:** Plugin DSL runner ticks every 3min internally. Iterates all registered skills (not just WOLF — any skill using DSL). Gets prices from shared cache. Runs DSL engine. Closes positions directly via MCP client. Fires hooks.

**Everything moves to plugin.** No LLM needed. DSL is pure math. This is the single biggest token saving.

Multi-strategy iteration comes for free: the plugin's DSL runner already scans all state files across all skill state directories.

#### 6. `sm-flip-check.py` (SM flip detector) → Plugin background service

**Current:** 5min cron wakes agent → agent runs script → if FLIP_NOW with conviction >= 4 + 100 traders → agent closes position.

**New:** Plugin service checks SM conviction every 5min per strategy. Binary threshold logic — doesn't need LLM. Already runs on Budget tier, confirming it's simple. Fires `on_position_closed` hook.

#### 7. `wolf-monitor.py` (watchdog) → Plugin background service

**Current:** 5min cron wakes agent → agent runs script → agent acts only on `action_required` items.

**New:** Plugin watchdog service. Checks margin buffer and liq distances per strategy. Acts on `action_required` directly (close position, send Telegram). Already Budget tier.

#### 8. `job-health-check.py` (health check) → Plugin internal

**Current:** 10min cron wakes agent → agent runs script → agent sends `notifications` to Telegram + auto-fixes issues.

**New:** Plugin self-validates state internally. Orphan DSL detection is simpler in-process (plugin knows which positions it opened). Auto-repair handled directly.

#### 9. `risk-guardian.py` (risk guardian) → Plugin risk guard module

**Current:** 5min cron wakes agent → agent runs script → gate state changes.

**New:** Plugin's risk guard is a persistent module with continuous state. Daily PnL tracking, entry counting, consecutive loss tracking happen in-memory. Gate management is internal. Checks happen on every `on_position_closed` hook — not every 5min.

**This is actually better:** The 5min cron means there's a window where a loss isn't detected. The plugin catches it instantly.

---

## 3. The WOLF skill.yaml

```yaml
name: wolf
version: 7.0.0
description: >
  Autonomous aggressive multi-strategy trading. Enter early on emerging movers,
  protect with tight DSL, cut dead weight fast, rotate into strength.

# ── STRATEGIES ──
strategies:
  aggressive-momentum:
    wallet: "${WALLET_1}"
    strategy_id: "${STRATEGY_ID_1}"
    budget: "${BUDGET_1}"
    slots: 3
    trading_risk: aggressive
    dsl_preset: aggressive

  # Second strategy (optional)
  # conservative-xyz:
  #   wallet: "${WALLET_2}"
  #   strategy_id: "${STRATEGY_ID_2}"
  #   budget: "${BUDGET_2}"
  #   slots: 2
  #   trading_risk: conservative
  #   dsl_preset: conservative

# ── SCANNER ──
scanner:
  type: emerging_movers
  interval: 3m
  filters:
    min_reasons: 2
    max_rank: 30
    require_immediate: true
    exclude_erratic: true
    min_traders: 10
  blocked_assets: []

# ── ENTRY DECISIONS ──
entry:
  decision_mode: llm
  decision_model: sonnet
  decision_prompt: |
    You evaluate emerging mover signals for the WOLF multi-strategy system.
    WOLF enters early on first jumps — before the peak, not at it. Speed is edge.

    SIGNAL PRIORITY (strict — act on highest priority first):
    1. FIRST_JUMP: Jumped 10+ ranks from #25+ in one scan, wasn't in top 50 before.
       ENTER IMMEDIATELY. 2 reasons is enough. vel > 0 is enough.
    2. CONTRIB_EXPLOSION: 3x+ contribution spike from rank #20+. ENTER.
       Never downgrade for erratic history.
    3. IMMEDIATE_MOVER: 10+ rank jump from #25+ but not first jump.
       ENTER if not erratic and not low velocity.
    4. DEEP_CLIMBER: Steady climb from #30+, vel >= 0.03, 3+ reasons.
       Route to conservative strategy if available.

    ROUTING:
    - FIRST_JUMP/CONTRIB_EXPLOSION → aggressive strategy
    - DEEP_CLIMBER → conservative if available, else aggressive
    - Never duplicate same asset within one strategy (cross-strategy OK)

    ANTI-PATTERNS:
    - Rank #1-10 for 2+ scans = SKIP
    - 4+ reasons at rank #5 = SKIP
    - Counter to BTC hourly trend = SKIP
    - Burned asset today = SKIP

    ROTATION (slots full + FIRST_JUMP):
    - Weakest has negative ROE + SM conviction <= 1 → CUT, ENTER new
    - Weakest is Tier 2+ or SM conviction 3+ → HOLD
    - Positions younger than 45min → never rotate out

    XYZ EQUITIES: Ignore trader count. Use reasons + rank velocity.

    Respond JSON:
    {
      "enter": true/false,
      "target_strategy": "aggressive-momentum",
      "direction": "LONG"/"SHORT",
      "confidence": 1-10,
      "reasoning": "one sentence",
      "rotate_out": null or "ASSET_NAME"
    }
  context: [signal, portfolio, recent_trades, btc_trend, burned_assets, strategy_gates]
  min_confidence: 6

# ── EXIT ──
exit:
  engine: dsl
  dsl_presets:
    aggressive:
      tiers:
        - { trigger_pct: 5, lock_pct: 50, breaches: 3 }
        - { trigger_pct: 10, lock_pct: 65, breaches: 2 }
        - { trigger_pct: 15, lock_pct: 75, breaches: 2 }
        - { trigger_pct: 20, lock_pct: 85, breaches: 1 }
      max_loss_pct: 3.0
      stagnation: { enabled: true, min_roe: 8, timeout_minutes: 60 }
    conservative:
      tiers:
        - { trigger_pct: 3, lock_pct: 60, breaches: 4 }
        - { trigger_pct: 7, lock_pct: 75, breaches: 3 }
        - { trigger_pct: 12, lock_pct: 85, breaches: 2 }
        - { trigger_pct: 18, lock_pct: 90, breaches: 1 }
      max_loss_pct: 4.0
      stagnation: { enabled: true, min_roe: 10, timeout_minutes: 90 }
  sm_flip:
    enabled: true
    interval: 5m
    conviction_collapse: { from_min: 4, to_max: 1, window_minutes: 10, action: cut_immediately }
    dead_weight: { conviction_zero_action: cut_immediately }

# ── RISK ──
risk:
  per_strategy:
    daily_loss_limit_pct: 8
    margin_buffer_pct: 15
    auto_delever: { enabled: true, threshold_pct: 70, reduce_to: 2 }
  guard_rails:
    max_entries_per_day: 8
    bypass_on_profit: true
    max_consecutive_losses: 3
    cooldown_minutes: 60
  leverage:
    aggressive_cap_pct: 75
    moderate_cap_pct: 50
    conservative_cap_pct: 25
  directional_guard: { max_same_direction: 3 }
  rotation_cooldown_minutes: 45

# ── HOOKS ──
hooks:
  on_position_closed:
    action: check_rotation
    decision_mode: llm
    decision_prompt: |
      A WOLF position just closed. Should we fill the empty slot or wait?
      Consider signal quality, win/loss streak, BTC trend, strategy gate state.
      Respond JSON: {"rotate": true/false, "reasoning": "brief"}
    context: [portfolio, recent_trades, btc_trend, pending_signals, strategy_gates]
    notify: true
  on_tier_changed:
    notify: true
  on_daily_limit_hit:
    action: pause_scanning
    decision_mode: agent
    wake_agent: true
    notify: true
  on_drawdown_cap_hit:
    action: emergency_close_all
    decision_mode: agent
    wake_agent: true
    notify: true
  on_consecutive_losses:
    threshold: 3
    action: cooldown
    decision_mode: agent
    wake_agent: true
    notify: true
  on_schedule:
    cron: "0 20 * * *"
    action: notify_only
    context: [portfolio, daily_trades, daily_pnl]
    notify: true

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

---

## 4. What Gets Deleted

| Artifact | Reason |
|---|---|
| `scripts/wolf_config.py` (~200 lines) | Plugin IS the config loader |
| `scripts/wolf-setup.py` (~300 lines) | Replaced by `skill.yaml` install flow |
| `scripts/emerging-movers.py` (~200 lines) | Replaced by `emerging_movers` scanner primitive |
| `scripts/dsl-combined.py` (~300 lines) | Replaced by DSL engine + runner service |
| `scripts/sm-flip-check.py` (~150 lines) | Replaced by `sm_flip` exit primitive |
| `scripts/wolf-monitor.py` (~180 lines) | Replaced by plugin watchdog service |
| `scripts/open-position.py` (~200 lines) | Replaced by plugin internal position opener |
| `scripts/job-health-check.py` (~150 lines) | Plugin self-validates state in-process |
| `scripts/risk-guardian.py` (~200 lines) | Replaced by plugin risk guard module (hook-triggered, not polled) |
| `wolf-strategies.json` | Plugin manages strategy config from `skill.yaml` |
| `references/cron-templates.md` | No crons in the new model |
| All 6 OpenClaw cron jobs | Plugin owns all scheduling |

**What survives:** `SKILL.md` as optional human-readable strategy docs.

**Net reduction:** ~1,880 lines of Python eliminated. 6 crons eliminated. Replaced by ~120-line `skill.yaml`.

---

## 5. What Gets Better (Not Just Equivalent)

| Current Limitation | Plugin Improvement |
|---|---|
| Risk guardian checks every 5min — losses in between go undetected | Risk guard triggers on every `on_position_closed` hook — instant |
| Gate state changes take up to 5min to propagate | Gate state is in-memory, checked on every tick |
| `fcntl` file locks — fragile across restarts | In-process concurrency — no file locks needed |
| Budget tier crons still burn tokens for no-ops | Background services cost zero tokens for no-ops |
| Notification policy enforced by mandate text | Enforced by plugin code — can't be violated |
| Signal routing judgment in ~500 words of mandate | Focused ~200 word LLM decision prompt |
| `open-position.py` called as subprocess | Internal function call — no subprocess overhead |
| Multi-strategy iteration per-script | Shared iteration across all services |

---

## 6. Migration Steps

### Step 1: Plugin Must Exist First

Plugin needs: `skill.yaml` loader with multi-strategy support, `emerging_movers` primitive, DSL engine + runner, risk guard with guard rails + gate states, SM flip checker, watchdog, price cache, Senpi MCP client, `llm-task` integration, hook system, Telegram notifications, strategy-scoped state management.

### Step 2: Port DSL Logic (highest value)

Validate: TypeScript DSL engine vs Python `dsl-combined.py` tick-by-tick. Must cover both presets (aggressive + conservative), all tier transitions, breach counting, stagnation, Phase 1 autocut, `pendingClose` recovery, XYZ isolated, multi-strategy iteration.

### Step 3: Port Emerging Movers Scanner

Port signal classification + `topPicks` pre-selection + per-strategy slot availability. Validate against identical leaderboard snapshots.

### Step 4: Port Risk Guardian Logic

Guard rails (G1, G3, G4) + gate states into plugin risk guard. Now hook-triggered instead of polled.

### Step 5: Write + Test Decision Prompt

Test against 50+ historical signals. Compare LLM decisions to actual agent decisions. Tune to >90% agreement.

### Step 6: Parallel Run (48h read-only)

### Step 7: Go Live + Delete Crons

---

## 7. Expected Outcome

| Metric | v6.1.1 | v7 (plugin) | Change |
|---|---|---|---|
| Cron jobs | 6 | 0 | -100% |
| Python scripts | 9 (1,880 lines) | 0 | -100% |
| Config | SKILL.md + registry + cron templates | skill.yaml (~120 lines) | -80% |
| Daily tokens (est.) | ~2.5M (isolated + 2-tier) | ~25K (LLM decisions only) | -99% |
| Agent wakes/day | ~1,500 (every cron tick) | ~5-10 (crises + user questions) | -99.7% |
| Risk reaction time | Up to 5min (cron interval) | Instant (hook-triggered) | Faster |
| Decision quality | Same | Same (LLM judges every entry + rotation) | Maintained |

---

*End of migration guide.*