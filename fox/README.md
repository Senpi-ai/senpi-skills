# 🦊 FOX v1.4 — Autonomous + Copy Trading for Hyperliquid

The ambush sniper. Catches explosive First Jumps — assets rocketing from obscurity into the top ranks on Hyperliquid's leaderboard — before the crowd confirms the signal. 85% directional accuracy on strong signals.

**Current leader** on the Senpi Predators tracker at +18.1% ROI across 50 trades.

## What FOX Does

FOX scans Hyperliquid's smart money leaderboard every 3 minutes looking for First Jump signals — assets that jump 15+ ranks in a single scan with positive velocity and multiple confirming reasons. When a qualifying signal fires, FOX enters immediately with maker orders and protects the position with DSL v5 trailing stops synced to Hyperliquid.

FOX also runs a copy trading mode that mirrors positions of top-performing traders. Default budget split: **60% copy trading / 40% autonomous** (configurable).

## What's in v1.4

**Bootstrap gate.** On every session, the agent checks for `config/bootstrap-complete.json`. If missing, it silently sets up all crons (copy trading monitor, market regime, autonomous scanners) before responding to the user. This ensures copy trading monitoring is always active — previous versions required manual setup.

**Notification silencing.** All scanner crons run on isolated sessions with strict notification rules. The agent only alerts on position OPENED, position CLOSED, risk guardian triggered, or critical error. No more scanner narration, "systems nominal" messages, or HEARTBEAT_OK spam.

**Dead weight cut removed.** Positions are no longer killed for being flat after 10 minutes. The hard timeout handles positions that genuinely go nowhere. This was the #1 source of unnecessary fee churn.

**NO_REPLY for idle cycles.** All crons use `NO_REPLY` instead of `HEARTBEAT_OK` to prevent the recursive wake-up loop that caused notification spam in earlier versions.

**Clean state.** All user-specific data (wallet addresses, strategy IDs, trade history, Telegram chat IDs) scrubbed from config files, memory files, and cron templates. Safe for fresh deployment.

## Architecture

```
fox-export/
├── AGENTS.md                  # Agent behavior + bootstrap gate + notification rules
├── BOOTSTRAP.md               # First-boot setup (referenced by AGENTS.md)
├── SOUL.md / USER.md          # Agent identity + user profile (set during setup)
├── MEMORY.md                  # Long-term agent memory (starts empty)
├── HEARTBEAT.md               # Heartbeat checklist
├── scripts/
│   ├── emerging-movers.py     # 3min FJ scanner — primary entry signal
│   ├── opportunity-scan-v6.py # 15min deep scanner — secondary signal
│   ├── market-regime.py       # BTC macro regime classifier
│   ├── sm-flip-check.py       # Smart money flip detector
│   ├── wolf-monitor.py        # Liquidation/margin watchdog
│   └── job-health-check.py    # DSL/position reconciliation
├── skills/
│   ├── fox-strategy/          # Entry rules, cron templates, references
│   │   ├── SKILL.md           # Full entry/exit logic + notification policy
│   │   └── references/        # Cron templates, API tools, learnings
│   └── dsl-dynamic-stop-loss/ # DSL v5 trailing stop engine
│       ├── SKILL.md
│       ├── scripts/dsl-v5.py
│       └── references/
├── config/                    # State files (clean — no user data)
│   ├── fox-strategies.json    # Strategy registry
│   ├── fox-trade-counter.json # Daily trade counter (empty)
│   ├── copy-strategies.json   # Copy trading config
│   ├── market-regime-last.json
│   ├── max-leverage.json
│   └── fj-last-seen.json     # FJ persistence tracking
└── docs/
    ├── cron-architecture.md
    ├── entry-rules-v1.0.md
    ├── dsl-rules-v1.0.md
    └── copy-trading-setup.md
```

## Cron Architecture (8 crons, all isolated)

| # | Cron | Interval | Purpose |
|---|---|---|---|
| 1 | Emerging Movers | 3 min | Primary FJ scanner — entry signal |
| 2 | DSL v5 | 3 min | Trailing stops + HL SL sync |
| 3 | SM Flip Detector | 5 min | Instant cut on SM conviction collapse |
| 4 | Watchdog | 5 min | Margin buffer + liquidation distance |
| 5 | Portfolio Summary | 15 min | P&L tracking |
| 6 | Opportunity Scanner | 15 min | Secondary deep scan |
| 7 | Market Regime | 1-4 hr | BTC macro classification |
| 8 | Copy Trading Monitor | 15 min | Copy strategy health + alerts |

All crons run on **isolated sessions** with `agentTurn` payloads. No main session crons.

## DSL Configuration: High Water Mode

FOX uses **DSL High Water Mode** — the trailing stop configuration originally designed for and proven on FOX. Instead of locking fixed ROE amounts at each tier, High Water Mode locks a percentage of the peak. The stop trails at 85% of the highest ROE the trade has ever reached, with no ceiling.

**Spec:** https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

Phase 1 gives trades room to develop with conviction-scaled floors (score 6-7: -20% ROE, score 10+: unrestricted). Once the trade proves itself at +7% ROE, Phase 2 takes over with percentage-of-peak locks that tighten from 40% to 85% of high water. A First Jump that runs +200% ROE has its stop at +170%. No ceiling.

**Current workaround** (until DSL engine supports `lockMode: "pct_of_high_water"`): use legacy fallback tiers with standard `lockPct` fields. Do NOT include `lockMode` or `lockHwPct` in state files. After engine update, switch to High Water tiers with `lockHwPct`. See the spec for full details and the fallback tier table.

## Optional: Trading Strategy Variants

FOX is the best-performing skill in the Senpi zoo as-is. For users who want to experiment with different configurations, two trading strategy variants are available as optional config overrides:

| Strategy | What Changes | When To Consider |
|---|---|---|
| **Feral Fox v2** | Score 7+, 3 reasons min, regime enforced, structural invalidation (1.5% floor), no time exits | After running vanilla FOX profitably and wanting fewer, higher-conviction trades |
| **Ghost Fox** | Feral v2 entries + High Water tiers with `lockHwPct` | After engine supports `pct_of_high_water` and you want infinite trailing on every winner |

**Start with vanilla FOX.** It's proven. The variants are upgrade paths, not requirements.

## Quick Start

1. Deploy to OpenClaw with Senpi MCP configured
2. The agent reads `AGENTS.md` → checks for `config/bootstrap-complete.json` → runs bootstrap silently
3. Bootstrap creates all 8 crons, copy trading monitor, and market regime cron
4. Agent sends one welcome message: "🦊 FOX is online."
5. The FOX hunts

## Key Learnings

- **Signal quality is the edge** — 85% directional accuracy on score 6+ First Jumps
- **Stops were the problem, not direction** — 79% of trades hit floor SL before the move materialized
- **Dead weight cut was killing winners** — removed in v1.1+
- **Fee drag compounds** — every upgrade tightens the same thing: fewer trades, higher conviction, wider stops
- **Copy trading provides baseline income** — 60/40 split covers autonomous losses while the scanner improves

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token
- [mcporter](https://github.com/nichochar/mcporter-cli) CLI
- Python 3.10+
- DSL v5 skill (included)

## License

Apache-2.0 — Built by Senpi (https://senpi.ai). Attribution required for derivative works.
Source: https://github.com/Senpi-ai/senpi-skills
