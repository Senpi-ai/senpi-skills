# Senpi Skills Platform — System Design

**Version 2.0 · March 2026 · DRAFT**

---

## 1. Overview

The Senpi Skills Platform enables non-developers to build production-ready autonomous trading strategies for Hyperliquid perpetuals. The platform is an OpenClaw plugin that provides shared trading infrastructure, so skill authors focus only on strategy logic — expressed as LLM decision prompts and configuration.

**Core principle:** The plugin is the gatekeeper and executor. The LLM is the judge. The plugin decides *when* to ask the judge. The judge decides *what* to do. The plugin executes the decision.

**Target outcome:** A non-developer ships a production-ready trading skill in under a day by writing a single `skill.yaml` file with no Python, no infrastructure code, and no OpenClaw crons.

---

## 2. Problem Statement

Today, Senpi has 2 production-ready skills out of a target of 10–20. The bottleneck is architectural.

**Current model:** A skill is a monolithic SKILL.md (300+ lines of mandate logic) paired with 5–10 Python scripts (200+ lines each). Each script independently handles price fetching, state management, mcporter calls, error handling, and output formatting. The agent is invoked on every cron tick — including the 95% of ticks where nothing happens.

**What breaks:**

- Every skill reinvents the same infrastructure. No code sharing.
- The agent burns ~3000 tokens per cron tick even for no-ops. A WOLF strategy running 7 crons over 24 hours consumes ~4M+ tokens/day, mostly wasted.
- Non-developers vibe-coding through OpenClaw produce unstructured scripts with anti-patterns (sequential API calls, non-atomic state writes, hardcoded config, missing retries).
- Mandate text conflates scheduling logic with business logic in 800-word blobs of natural language.

**Root cause:** There is no shared runtime. Every skill is an island. The concept of a reusable plugin does not exist in the skill ecosystem.

---

## 3. Architecture

### 3.1 Three-Layer Model

```
┌───────────────────────────────────────────────────────┐
│  Layer 3: Skills                                      │
│  skill.yaml + optional strategy.py                    │
│  Decision prompts, strategy config, hook preferences  │
│  Built by: non-developers                             │
├───────────────────────────────────────────────────────┤
│  Layer 2: Trading Primitives                          │
│  DSL engine, signal detectors, risk math,             │
│  scoring frameworks, market data helpers              │
│  Built by: core team                                  │
├───────────────────────────────────────────────────────┤
│  Layer 1: Core Runtime (the plugin)                   │
│  Scheduling, state mgmt, MCP client, price cache,     │
│  config loader, hook system, LLM integration          │
│  Built by: core team                                  │
└───────────────────────────────────────────────────────┘
```

**Layer 1 (Core Runtime)** is the OpenClaw plugin itself. It owns all scheduling, infrastructure, and the hook system. Skills never touch this layer.

**Layer 2 (Trading Primitives)** are composable building blocks shipped with the plugin. DSL as a library function, signal detectors as reusable modules, risk math as utilities. Skills reference these by name in their config.

**Layer 3 (Skills)** are what non-developers write. A `skill.yaml` file that declares which primitives to use, how to configure them, and LLM decision prompts that capture the author's trading intuition. No infrastructure code.

### 3.2 Why OpenClaw Plugin

The shared runtime lives as an OpenClaw plugin (TypeScript, in-process with Gateway) rather than a Python package. Three reasons:

**Zero-token scheduling.** The plugin runs its own internal timers — no OpenClaw crons. The scanner, DSL runner, and watchdog all tick inside the plugin process without ever waking the agent. A Python package would still require crons to invoke scripts, which means agent wakes on every tick.

**Persistent state.** The plugin process is long-lived. Price cache stays warm in memory. State files stay loaded. A Python package is stateless per invocation — every script run loads config, fetches prices, reads state, writes state.

**Native integration.** The plugin registers typed agent tools, background services, hooks, and CLI commands using OpenClaw's plugin API. Skills get auto-generated status and override tools for free.

### 3.3 The Hybrid Model

Not everything moves to TypeScript. Heavy computation stays in Python and the plugin calls it when needed.

**Plugin owns (TypeScript):** scheduling, state management, MCP calls, price cache, DSL math, risk guards, hook dispatch, LLM invocations, notifications.

**Python handles (called by plugin):** opportunity scoring with complex TA, candle analysis, RSI computation, custom signal detection algorithms, any logic that benefits from numpy/pandas.

The plugin shells out to Python scripts via exec, passes structured JSON as stdin, and parses structured JSON from stdout. The Python script is a pure function — data in, decision out. No side effects, no API calls, no state management.

---

## 4. Skill Anatomy

A skill is a directory containing:

| File | Required | Purpose |
|---|---|---|
| `skill.yaml` | Yes | Strategy config, scanner, entry/exit, risk, hooks, decision prompts |
| `SKILL.md` | No | Human-readable docs. Strategy philosophy, author notes. Not read by plugin. |
| `strategy.py` | No | Custom decision logic for `decision_mode: script` hooks |
| `scripts/*.py` | No | Additional Python for complex computation |

The `skill.yaml` is the single source of truth. The plugin reads it at install time, validates it against the schema, prompts the user for any template variables (`${BUDGET}`, `${WALLET}`), stores the resolved config, and starts services.

### 4.1 skill.yaml Sections

**`strategy`** — Wallet, budget, slots, leverage. The identity and sizing of the strategy.

**`scanner`** — Which signal detector to use, how often to run, and what filters to apply. The plugin maps `type: emerging_movers` to the built-in emerging movers primitive and starts an internal timer at the specified interval. No cron created.

**`entry`** — How to decide whether a signal warrants opening a position. This is where the LLM decision layer lives. The `decision_prompt` is the skill author's trading intuition expressed in natural language. The `context` list tells the plugin what data to auto-inject into each LLM call (signal, portfolio, recent trades, BTC trend).

**`exit`** — Which exit engine(s) to use. DSL config, SM flip detection, time decay, stagnation rules. All pure code — no LLM needed for math.

**`risk`** — Daily loss limits, drawdown cap, margin buffer, auto-delever threshold. Enforced by the plugin's risk guard on every position open and monitored continuously.

**`hooks`** — Per-event configuration: what action to take, which decision mode to use, whether to notify via Telegram, whether to wake the full agent. This is where skills declare their reaction to trading events.

**`notifications`** — Telegram chat ID and toggle for which events generate notifications.

### 4.2 Example: Complete Skill

```yaml
name: momentum-scalper
version: 1.0.0

strategy:
  budget: "${BUDGET}"
  slots: 3
  leverage: 10

scanner:
  type: emerging_movers
  interval: 90s
  filters:
    min_reasons: 2
    max_rank: 30
    require_immediate: true
  blocked_assets: ["PEPE", "DOGE"]

entry:
  decision_mode: llm
  decision_model: sonnet
  decision_prompt: |
    You evaluate emerging mover signals for a momentum scalping strategy.
    - 2 reasons at rank ≤25 with big jump = ENTER
    - 4+ reasons at rank ≤5 = SKIP (already peaked)
    - Never enter counter to BTC hourly trend
    - Prefer 15+ SM traders for crypto
    Respond JSON: {"enter": true/false, "direction": "LONG"/"SHORT",
                   "confidence": 1-10, "reasoning": "one sentence"}
  context: [signal, portfolio, recent_trades, btc_trend]
  min_confidence: 6

exit:
  engine: dsl
  dsl:
    tiers: [5, 10, 15, 20]
    retrace: [0.50, 0.35, 0.25, 0.15]
    breach_counts: [3, 2, 2, 1]
    max_loss_pct: 3.0
    stagnation: { enabled: true, min_roe: 8, timeout_minutes: 60 }
  sm_flip:
    enabled: true
    interval: 5m
    min_trader_drop_pct: 60

risk:
  daily_loss_limit: 500
  drawdown_cap: 1500
  margin_buffer_pct: 15
  auto_delever_threshold: 3500

hooks:
  on_position_closed:
    action: check_rotation
    decision_mode: llm
    decision_prompt: |
      A position just closed. Should we enter a new one or wait?
      Respond JSON: {"rotate": true/false, "reasoning": "brief"}
    notify: true
  on_daily_limit_hit:
    action: pause_scanning
    decision_mode: agent
    wake_agent: true
  on_drawdown_cap_hit:
    action: emergency_close_all
    decision_mode: agent
    wake_agent: true

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
```

No Python. No scripts. No crons. This is a complete, production-ready trading strategy.

---

## 5. Decision Modes

The platform provides three ways for skills to make decisions, configurable per-hook:

### 5.1 LLM Mode (`decision_mode: llm`)

The plugin calls a one-shot LLM completion (via OpenClaw's `llm-task`) with the skill's decision prompt and auto-injected context. No agent session. No conversation history. Just a focused prompt → structured JSON response.

**Cost:** ~200–500 tokens per invocation.

**When to use:** Nuanced entry/exit decisions, pattern recognition, ambiguous signals, any situation where rigid rules would miss good trades or enter bad ones.

**Context injection:** The plugin automatically builds context based on the `context` list in skill.yaml. Available context types:

| Context | What the plugin provides |
|---|---|
| `signal` | The detected signal (scanner output) |
| `portfolio` | Current positions, PnL, slot usage, margin |
| `recent_trades` | Last N closed trades with PnL and signal quality |
| `btc_trend` | BTC hourly trend direction and strength |
| `market_data` | Candles, volume, funding rate for the asset |
| `sm_conviction` | Smart money trader count and conviction score |

The skill author never fetches this data. They just list what they need and write the prompt.

### 5.2 Script Mode (`decision_mode: script`)

The plugin runs a Python script, passing context as stdin JSON, and reads a JSON decision from stdout. Zero LLM tokens.

**Cost:** Zero tokens.

**When to use:** Simple deterministic rules that don't benefit from LLM reasoning. High-frequency checks where even ~400 tokens per invocation adds up.

### 5.3 Agent Mode (`decision_mode: agent`)

The plugin wakes the full OpenClaw agent with structured event data and an instruction. The agent gets a full conversation session with access to all tools, memory, and multi-step reasoning.

**Cost:** ~2000–5000 tokens per invocation.

**When to use:** User communication (explaining a drawdown), crisis management, multi-step reasoning (3 signals competing for 1 slot), situations that benefit from conversation history.

### 5.4 Mixing Modes

A skill can use different modes for different hooks:

| Hook | Typical Mode | Why |
|---|---|---|
| `on_signal_detected` | llm | Entry decisions need judgment |
| `on_position_closed` | llm | Rotation decisions need context |
| `on_tier_changed` | none | Pure notification, no decision |
| `on_position_at_risk` | llm | "Cut early or let DSL handle it?" |
| `on_daily_limit_hit` | agent | Explain to user, suggest next steps |
| `on_drawdown_cap_hit` | agent | Crisis communication |
| `on_consecutive_losses` | agent | Strategy reassessment |

---

## 6. Hook System

Hooks are typed events fired by the plugin when something happens. They replace the mandate-based cron model entirely.

### 6.1 Why Hooks

The current mandate model conflates scheduling with business logic. An 800-word mandate fires every 90 seconds, the agent reads all of it, runs a script, reads the output, re-reads the mandate, and decides what to do — even when there's nothing to do (95% of ticks).

Hooks separate concerns. The plugin handles scheduling internally. When an event occurs, the plugin fires a typed hook. The skill's hook config determines what happens: run a built-in action, invoke the LLM for a decision, or wake the full agent.

### 6.2 Hook Types

**Signal hooks:** `on_signal_detected`, `on_composite_signal`

**Position hooks:** `on_position_opened`, `on_position_closed`, `on_tier_changed`, `on_position_at_risk`

**Risk hooks:** `on_daily_limit_approaching`, `on_daily_limit_hit`, `on_drawdown_cap_hit`, `on_auto_delever`, `on_consecutive_losses`

**System hooks:** `on_skill_started`, `on_skill_error`, `on_health_check`

**Scheduled hooks:** `on_schedule` with cron expression (for periodic reports like daily summary)

Each hook carries structured data (asset, PnL, signal quality, etc.) that gets auto-injected into LLM prompts or passed to script handlers.

### 6.3 Hook Configuration

Per-hook in `skill.yaml`:

```yaml
hooks:
  on_signal_detected:
    action: evaluate_entry          # built-in action
    decision_mode: llm              # how to decide
    decision_prompt: "..."          # the LLM prompt
    notify: true                    # send Telegram
    wake_agent: false               # don't start agent session
```

**Built-in actions:** `evaluate_entry`, `check_rotation`, `notify_only`, `pause_scanning`, `resume_scanning`, `emergency_close_all`, `log_and_notify`, `custom`.

**`wake_agent`** is a per-hook toggle. Most hooks run entirely in the plugin (zero agent tokens). The agent only wakes for situations requiring human communication or complex reasoning.

### 6.4 Cross-Skill Hooks

Skills can subscribe to events from other skills using `source: "*"`. This enables patterns like:

**HOWL (nightly analysis):** Subscribes to `on_position_closed` from all skills, records every trade in real-time. At night, generates an analysis report with exact data instead of reconstructing from stale state files.

**Risk Guardian:** Subscribes to `on_position_opened` from all skills, checks total portfolio leverage and directional exposure across all strategies.

### 6.5 Agent Wake Budget

To prevent chatty hooks from burning tokens, the plugin enforces limits:

- **max_agent_wakes_per_hour:** Hard cap on full agent sessions.
- **cooldown_seconds:** Minimum gap between agent wakes.
- **batch_window:** Events within N seconds are batched into a single agent wake with all events included. If 3 signals fire within 10 seconds, the agent gets one wake with 3 signals, not 3 separate wakes.

---

## 7. Plugin Components

### 7.1 Package Identity

| Field | Value |
|---|---|
| npm package | `@senpi/trading-core` |
| OpenClaw plugin ID | `senpi-trading` |
| Install | `clawhub install @senpi/trading-core` |
| Config namespace | `plugins.entries.senpi-trading.config` |

### 7.2 Background Services

These run inside the plugin process on internal timers. No OpenClaw crons. No agent involvement. Zero tokens.

**Price Cache** — Fetches Hyperliquid `allMids` every 5 seconds, XYZ prices on demand. Shared across all skills. Eliminates the per-script `fetch_all_mids()` duplication that currently exists in every Python script.

**Scanner Loops** — One per skill, running at the skill's configured interval. Calls the specified signal primitive, applies filters, and fires `on_signal_detected` when signals pass. The 95% of ticks with no signals cost nothing.

**DSL Runner** — Ticks all active DSL state files every 3 minutes. Gets prices from the shared cache. Runs DSL engine logic (pure function). Closes breached positions directly via the MCP client. Fires `on_position_closed` or `on_tier_changed` hooks. The 95% of ticks where positions are healthy cost nothing.

**SM Flip Checker** — Per-skill if enabled. Monitors smart money conviction for open positions. Cuts positions where conviction collapses.

**Watchdog** — Monitors margin buffer, liquidation distances, and overall system health across all skills.

### 7.3 Agent Tools

Auto-generated per skill at registration:

**`{skill}_status`** — Returns current positions, PnL, slot usage, daily P&L, scanner health, DSL state for each position.

**`{skill}_override`** — Manual controls: pause scanning, resume scanning, close specific position, close all, update config parameter.

Shared across all skills:

**`trading_list_skills`** — Lists all registered skills with status.

**`trading_install_skill`** — Installs a skill from a skill.yaml path (alternative to `clawhub install`).

### 7.4 Trading Primitives (Layer 2)

**Signal Detectors:**

| Primitive | Description |
|---|---|
| `emerging_movers` | Leaderboard rank climb, contribution velocity, FIRST_JUMP/IMMEDIATE_MOVER classification |
| `funding_rate` | Extreme funding rate detection with reversal signals |
| `smart_money_flow` | SM concentration changes across assets |
| `volume_spike` | Unusual volume detection |
| `custom` | Runs skill's Python script |

**Exit Engines:**

| Primitive | Description |
|---|---|
| `dsl` | ROE-based trailing stop with configurable tiers, retrace, breach counts, stagnation |
| `fixed_target` | Close at target ROE |
| `time_decay` | Close after max hold duration unless ROE exceeds threshold |
| `sm_flip` | Close when SM conviction collapses |

**Risk Module:** Position sizing, margin math, leverage cap lookup, daily loss tracking, drawdown monitoring, auto-delever logic, directional exposure guard.

### 7.5 State Management

All state persists to disk as JSON files with atomic writes and file locking:

- `skill-config-{name}.json` — resolved skill config (survives gateway restart)
- `dsl-state-{skill}-{asset}.json` — per-position DSL state
- `trade-log-{skill}.json` — trade history for analytics
- `skill-runtime-{name}.json` — daily PnL, scan history, burned assets

On gateway restart, the plugin reads all `skill-config-*.json` files and re-registers services. No agent involvement needed for recovery.

---

## 8. Execution Flow

### 8.1 Skill Installation

```
User installs skill (clawhub install or sends skill.yaml to agent)
  → Plugin reads skill.yaml
  → Validates against schema
  → Prompts for template variables (${BUDGET}, etc.)
  → Stores resolved config to disk
  → Creates primitive instances (scanner, DSL engine, risk guard)
  → Starts background services (scanner timer, DSL runner)
  → Registers agent tools ({skill}_status, {skill}_override)
  → Skill is live. Agent goes dormant.
```

### 8.2 Scanner Tick (95% — No Signal)

```
Plugin timer fires (e.g. every 90s)
  → Scanner primitive runs (1 API call to Hyperliquid)
  → No signals detected
  → return
  → Zero tokens. Agent not involved.
```

### 8.3 Scanner Tick (5% — Signal Detected)

```
Plugin timer fires
  → Scanner detects signal (FIRST_JUMP on BTC)
  → Plugin fires on_signal_detected hook
  → Hook config says: decision_mode: llm
  → Plugin builds context (signal + portfolio + recent_trades + btc_trend)
  → Plugin calls llm-task with skill's decision_prompt + context (~400 tokens)
  → LLM responds: { enter: true, direction: "LONG", confidence: 8 }
  → confidence ≥ min_confidence? Yes
  → Plugin runs risk guard checks (slots, daily limit, drawdown)
  → Plugin calculates position size (margin, leverage)
  → Plugin opens position via Senpi MCP client
  → Plugin creates DSL state file
  → Plugin sends Telegram notification
  → Plugin fires on_position_opened hook
  → Agent not involved. Total cost: ~400 tokens for LLM decision.
```

### 8.4 DSL Tick (95% — Hold)

```
Plugin DSL runner fires (every 3 min)
  → Reads all active DSL state files for this skill
  → Gets prices from shared price cache (in memory)
  → Runs DSL engine: tick(state, price) → { action: "hold" }
  → continue to next position
  → Zero tokens. Agent not involved.
```

### 8.5 DSL Tick (5% — Close)

```
DSL engine returns { action: "close", reason: "tier_2_breach" }
  → Plugin closes position via Senpi MCP client
  → Plugin updates DSL state file (active: false)
  → Plugin fires on_position_closed hook
  → Hook config says: action: check_rotation, decision_mode: llm
  → Plugin calls llm-task: "Slot freed. Enter a new position or wait?"
  → LLM decides
  → If rotate: plugin opens new position (same flow as 8.3)
  → Plugin sends Telegram notification
  → Total cost: ~400 tokens for rotation decision.
```

### 8.6 Crisis Event

```
Risk guard detects drawdown cap breached
  → Plugin fires on_drawdown_cap_hit hook
  → Hook config says: action: emergency_close_all, decision_mode: agent
  → Plugin closes all positions immediately
  → Plugin wakes full agent with structured event data
  → Agent explains situation to user via Telegram
  → Total cost: ~2000-3000 tokens. Justified — user needs communication.
```

---

## 9. Token Impact

| Operation | Current (tokens/tick) | With Plugin |
|---|---|---|
| Scanner tick — no signal (95%) | ~2,000–4,000 | 0 |
| Scanner tick — signal found (5%) | ~4,000–6,000 | ~400 (llm-task) |
| DSL tick — hold (95%) | ~2,000–4,000 | 0 |
| DSL tick — close (5%) | ~3,000–5,000 | ~400 (rotation llm-task) |
| SM flip check — no flip | ~1,500–2,500 | 0 |
| Watchdog — no issues | ~1,500–2,500 | 0 |
| Crisis event | ~3,000–5,000 | ~2,500 (agent wake, justified) |

**Daily estimate for a WOLF-like strategy:**

| | Current | With Plugin |
|---|---|---|
| Scanner no-ops (912 ticks) | ~2.9M tokens | 0 |
| Scanner signals (48 ticks) | included above | ~19K tokens |
| DSL no-ops (480 ticks) | ~1.4M tokens | 0 |
| DSL closes (~5 events) | included above | ~2K tokens |
| Other crons (SM, watchdog, health) | ~800K tokens | 0 |
| **Total** | **~4.2M tokens** | **~21K tokens** |

**~99.5% reduction.** The LLM still makes every entry and rotation decision. No intelligence lost. The reduction comes entirely from eliminating agent involvement in no-op ticks.

---

## 10. Implementation Phases

### Phase 1: Core Platform (Weeks 1–3)

Ship the minimum viable plugin that can run a skill.yaml end-to-end.

**Deliverables:**
- Plugin skeleton with `openclaw.plugin.json` and config schema
- `skill.yaml` schema, parser, validator, variable prompting
- Skill discovery and lifecycle (install, start, stop, restart, uninstall)
- Price cache background service
- Senpi MCP client (typed wrappers, retry logic)
- State manager (atomic JSON, file locking, schema validation)
- Risk guard (sizing, loss limits, drawdown, auto-delever)
- DSL engine (pure function, all v4 tier logic)
- DSL background runner service
- Emerging movers scanner as built-in primitive
- LLM decision layer (`llm-task` integration)
- Hook system (signal, position, risk hooks)
- Telegram notifications
- Auto-generated agent tools per skill
- Config persistence and gateway restart recovery

**Milestone:** `clawhub install @senpi/trading-core`, drop a `skill.yaml` in the skills directory, and have an autonomous trading strategy running with LLM-powered entry decisions and zero-token DSL protection.

### Phase 2: Primitives + Cross-Skill (Weeks 4–5)

Expand the primitive library and enable cross-skill patterns.

**Deliverables:**
- Additional scanner types: `funding_rate`, `smart_money_flow`, `volume_spike`
- Additional exit engines: `fixed_target`, `time_decay`
- Composite scanner (multiple signal sources with weighted scoring)
- Cross-skill hooks (`source: "*"` subscriptions)
- SM flip checker as background service
- Watchdog service
- `on_schedule` hook for periodic tasks (daily summaries, HOWL-style analysis)

**Milestone:** Skills can compose multiple signal sources and exit strategies. HOWL can subscribe to all skills' events for real-time trade journaling.

### Phase 3: Tooling + Ecosystem (Weeks 6–7)

Make it trivially easy to create, test, and share skills.

**Deliverables:**
- `senpi init` — interactive CLI that scaffolds a `skill.yaml` from a questionnaire
- `senpi validate` — validates skill.yaml against schema, checks for common errors
- `senpi dry-run` — simulates skill against historical data
- Backport WOLF and DSL-Tight as reference skill.yaml files
- Skill author documentation and guide
- Community contribution process for new primitives

**Milestone:** Non-developers can create, validate, and ship a production-ready skill in under a day.

---

## 11. Risks and Mitigations

**TypeScript rewrite cost.** Porting DSL and scanner logic from Python to TypeScript introduces risk. *Mitigation:* The hybrid model keeps complex TA in Python. Only hot-path code (DSL math, price cache, state management) moves to TypeScript. Port incrementally and test against existing state file schemas.

**Plugin process stability.** Background services running in the Gateway process means a bug could affect all strategies. *Mitigation:* Services are isolated with independent try/catch. One skill's scanner crashing doesn't affect another skill's DSL runner. Watchdog monitors all services. Health check CLI via `openclaw plugins doctor`.

**LLM decision quality.** Moving from full agent context to focused `llm-task` prompts could miss nuance. *Mitigation:* The `decision_mode: agent` escape hatch exists for complex situations. Skills can escalate any hook to a full agent session. The `min_confidence` threshold prevents low-conviction entries.

**Adoption friction.** Existing skill authors must learn the new model. *Mitigation:* Backward compatible. Old-style SKILL.md + Python skills continue working alongside new skill.yaml skills. Migration is optional and incremental.

**Scope creep.** The plugin tries to do too much in Phase 1. *Mitigation:* Phase 1 scopes to one scanner type (emerging_movers), one exit engine (DSL), and one decision mode (llm). The architecture supports extensibility but doesn't require it on day one.

---

## 12. Success Metrics

| Metric | Current | Target (Post Phase 3) |
|---|---|---|
| Production-ready skills | 2 | 10–20 |
| Time to ship new skill | Days to weeks | Hours to 1 day |
| Custom code per skill | 500–1500 lines | 0–50 lines (most skills are pure YAML) |
| Token usage per strategy/day | ~4.2M (baseline) | ~21K (99.5% reduction) |
| Non-dev ships without code review | No | Yes (plugin validates config + guards runtime) |
| Agent involvement in routine operations | Every tick | Installation only |

---

## 13. Open Questions

1. **Multi-strategy isolation.** Should one plugin installation support multiple strategies on different wallets simultaneously? If yes, how do we namespace state files, risk guards, and agent tools?

2. **LLM model selection.** Should skills be able to specify different models for different hooks? (e.g., Opus for complex rotation decisions, Haiku for simple signal filtering). What's the default?

3. **Decision prompt versioning.** When a skill author updates their decision prompt, do existing positions continue with the old prompt or switch to the new one? How do we handle mid-trade prompt changes?

4. **Community primitives.** Should Layer 2 primitives be open for community contribution? If yes, what's the review/testing bar? If no, core team becomes a bottleneck for new scanner types.

5. **Cross-skill hook permissions.** Can any skill subscribe to any other skill's events (`source: "*"`)? Or do skills need to explicitly allow subscribers? A misbehaving subscriber could slow the hook pipeline.

6. **Backtesting.** How does `senpi dry-run` work? Does it replay historical leaderboard data through the scanner → LLM decision → simulated entry flow? How do we handle LLM non-determinism in backtests?

7. **Backward compatibility.** When do old-style SKILL.md + Python skills get deprecated? Suggestion: never hard-deprecated, but new skills are expected to use skill.yaml after Phase 1.

---

*End of design document.*