[README (3).md](https://github.com/user-attachments/files/25506639/README.3.md)
# Senpi AI Skills

Skills give your Senpi agent superpowers — pre-built trading strategies and tools that work out of the box. Built on the open [Agent Skills](https://agentskills.io) standard.

---

## Install a skill (Senpi users)

1. Download the `SKILL.md` file (click the link in the table below)
2. Send it to your agent in Telegram with the message: **"Here are some new superpowers"**
3. Your agent will prompt you for next steps

> **Tip:** Use top-tier AI models (Claude Opus or equivalent). Trading requires precision. Skills are optimized to use far fewer tokens than training your agent from scratch.

## Install a skill (OpenClaw / ClawHub)

```bash
clawhub install dsl-dynamic-stop-loss
clawhub install dsl-tight
clawhub install opportunity-scanner
clawhub install autonomous-trading
clawhub install emerging-movers
clawhub install whale-index
clawhub install wolf-strategy
clawhub install wolf-howl
```

## Install a skill (agents)

🤖 Grab the raw URL and go:

```
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dsl-dynamic-stop-loss/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/dsl-tight/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/opportunity-scanner/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/autonomous-trading/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/emerging-movers/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/whale-index/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolf-strategy/SKILL.md
https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/wolf-howl/SKILL.md
```

Each skill folder is self-contained — SKILL.md has the core instructions, `scripts/` has executable code, `references/` has detailed schemas and examples. Everything an agent needs.

---

## Skills

| Skill | What it does | Install |
|-------|-------------|---------|
| **[DSL (Dynamic Stop Loss)](#dsl-dynamic-stop-loss)** | 2-phase trailing stop loss with per-tier retrace tightening. ROE-based tier triggers, auto-closes on breach with retry. Works for LONG and SHORT. | [`SKILL.md`](dsl-dynamic-stop-loss/SKILL.md) |
| **[DSL-Tight](#dsl-tight)** | Opinionated DSL preset with tighter defaults. 4 tiers with per-tier breach counts that tighten as profit grows, auto-calculated price floors, stagnation take-profit. | [`SKILL.md`](dsl-tight/SKILL.md) |
| **[Opportunity Scanner](#opportunity-scanner)** | 4-stage funnel screening all 500+ Hyperliquid perps. Scores 0–400 across smart money, market structure, technicals, and funding. Hourly trend gate, BTC macro filter. | [`SKILL.md`](opportunity-scanner/SKILL.md) |
| **[Autonomous Trading](#autonomous-trading)** | Give your agent a budget, target, and deadline — it does the rest. Orchestrates DSL + Scanner + Emerging Movers with race condition prevention and conviction collapse cuts. | [`SKILL.md`](autonomous-trading/SKILL.md) |
| **[WOLF Strategy](#wolf-strategy)** | Fully autonomous 2-3 slot trading. All 7 scripts bundled, setup wizard, cron mandates. Proven: +$2,100 in 24h across 25+ trades, 65% win rate. | [`SKILL.md`](wolf-strategy/SKILL.md) |
| **[HOWL](#howl)** | 🐺🌙 Hunt, Optimize, Win, Learn. Nightly self-improvement loop — analyzes every trade, finds patterns, suggests strategy improvements. | [`SKILL.md`](wolf-howl/SKILL.md) |
| **[Emerging Movers Detector](#emerging-movers-detector)** | Tracks SM concentration across all Hyperliquid assets. Quality-filtered IMMEDIATE signals, runs every 60 seconds. One API call per scan. | [`SKILL.md`](emerging-movers/SKILL.md) |
| **[Whale Index](#whale-index)** | Auto-mirror top Discovery traders. Scores on PnL rank, win rate, consistency, drawdown. 2–5 mirror strategies, daily rebalance with 2-day watch period. | [`SKILL.md`](whale-index/SKILL.md) |

---

## Skill details

### DSL (Dynamic Stop Loss)

Automated trailing stop loss for leveraged perp positions on Hyperliquid. All tier triggers are ROE-based (return on margin), so they automatically account for leverage.

**How it works:** Two phases — Phase 1 ("Let it breathe") uses a wide retrace with multiple breach checks to avoid early shakeouts. Phase 2 ("Lock the bag") triggers on the first profit tier, tightens the retrace (optionally per-tier), and ratchets a profit floor upward that can never go back down. The script closes positions directly via mcporter with retry — no agent intervention needed for the critical path.

**Key features:** ROE-based tier ratcheting, per-tier retrace, breach decay modes, error handling with close retry and `pendingClose` recovery. Self-contained: one Python script + one JSON state file.

📥 **[Download SKILL.md](dsl-dynamic-stop-loss/SKILL.md)**

---

### DSL-Tight

A tighter, more opinionated variant of DSL for aggressive profit protection. Same ROE-based engine — different defaults.

**How it works:** Same core as DSL v4. DSL-Tight ships with aggressive defaults and fewer knobs. 4 tiers that lock 50→65→75→85% of the high-water move, with breach counts that tighten as profit grows (3→2→2→1). Auto-calculates all price floors. Stagnation take-profit auto-closes if ROE ≥ 8% but the high-water mark hasn't improved for 1 hour.

📥 **[Download SKILL.md](dsl-tight/SKILL.md)**

---

### Opportunity Scanner

Screens all Hyperliquid perps to find the highest-conviction trading setups.

**How it works:** 4-stage funnel — BTC macro context, bulk volume screen, smart money overlay, multi-timeframe deep dive with parallel candle fetches. Scores 0–400 across 4 equal pillars, gated by hourly trend alignment. Counter-trend on hourly = hard skip. Near-zero LLM tokens.

📥 **[Download SKILL.md](opportunity-scanner/SKILL.md)**

---

### Autonomous Trading

Give your agent a budget, a target, and a deadline. It does the rest.

**How it works:** Creates a strategy wallet, scans for opportunities, opens positions, protects profits with DSL, and enforces risk controls. Race condition prevention across crons, conviction collapse = instant cut, cross-margin buffer math, speed filter.

**Requires:** DSL + Opportunity Scanner + Emerging Movers skills
**Minimum budget:** $500 (recommend $1k+)

📥 **[Download SKILL.md](autonomous-trading/SKILL.md)**

---

### WOLF Strategy

Fully autonomous trading — the WOLF hunts for its human. All 7 Python scripts bundled, setup wizard, cron mandate templates.

**How it works:** 2-3 slot concentrated position management. IMMEDIATE_MOVER as primary entry trigger. 7 cron jobs (60s scanner, 180s DSL per position, 5min SM flip detector, 5min watchdog, 15min scanner, 15min portfolio, 10min health check). Budget-scaled sizing — tell it your budget, it calculates slots, margin, leverage, loss limits, and auto-delevers if account drops. Tighter 4-tier DSL (5/10/15/20% ROE) locks profit earlier than the old 6-tier system.

**Proven:** +$1,500 realized across 25+ trades, 65% win rate on $6.5k budget.

**Self-contained:** Includes DSL v4, Emerging Movers, Opportunity Scanner, SM flip detector, watchdog, health check, and setup wizard. No companion skill installs needed.

📥 **[Download SKILL.md](wolf-strategy/SKILL.md)**

---

### HOWL

🐺🌙 **Hunt, Optimize, Win, Learn.** The WOLF hunts all day. At night, it HOWLs — reviewing every trade, finding patterns, and sharpening itself for tomorrow.

**How it works:** A nightly cron spawns an isolated sub-agent that gathers trade history, DSL state files, and memory logs. It computes win rates, profit factor, signal quality correlation, DSL tier distribution, slot utilization, and missed opportunities. Produces a structured report with data-backed improvement suggestions at high/medium/low confidence, saves to memory, and sends a Telegram summary.

**Requires:** WOLF Strategy skill (this analyzes WOLF's trades)

📥 **[Download SKILL.md](wolf-howl/SKILL.md)**

---

### Emerging Movers Detector

Catches Smart Money rotations before they hit the top of the leaderboard.

**How it works:** Tracks SM profit concentration leaderboard every 60 seconds using a single API call. Scans top 50 markets. v3.1 adds quality filters — erratic rank history, velocity gate, trader count floor, max leverage check — to prevent false IMMEDIATE signals.

📥 **[Download SKILL.md](emerging-movers/SKILL.md)**

---

### Whale Index

Auto-mirror the highest-conviction traders from Senpi's 30-day Discovery leaderboard.

**How it works:** Scans top 50 Discovery traders, scores on PnL rank (35%), win rate (25%), consistency (20%), hold time (10%), drawdown (10%). Creates 2–5 mirror strategies with overlap checks. Daily rebalance with 2-day watch period before swaps.

**Minimum budget:** $500 (recommend $1k+)

📥 **[Download SKILL.md](whale-index/SKILL.md)**

---

## What are Agent Skills?

[Agent Skills](https://agentskills.io) is an open standard for giving AI agents new capabilities. Skills are folders of instructions, scripts, and resources that any compatible agent can discover and use. The standard is adopted by Claude, Claude Code, OpenAI Codex, Gemini CLI, GitHub Copilot, Cursor, VS Code, OpenClaw, and 20+ other platforms.

These skills are built for Senpi on Hyperliquid, but the format is portable — any agent that supports the Agent Skills standard can read and execute them.

---

## Notes

- **Skills are a work in progress.** Updates and new skills are announced in the [Senpi Telegram group](https://t.me/+0Z_77HSTD6EwYzhh).
- **Feedback welcome.** [Share how skills are working for you](https://t.me/+0Z_77HSTD6EwYzhh).
