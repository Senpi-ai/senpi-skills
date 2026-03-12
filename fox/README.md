# 🦊 FOX v1.5 — Autonomous + Copy Trading for Hyperliquid

The ambush sniper. Catches explosive First Jumps — assets rocketing from obscurity into the top ranks on Hyperliquid's leaderboard — before the crowd confirms the signal. 85% directional accuracy on strong signals.

**Current leader** on the Senpi Predators tracker at +18% ROI.

## What FOX Does

FOX scans Hyperliquid's smart money leaderboard every 3 minutes looking for First Jump signals — assets that jump 15+ ranks in a single scan with positive velocity and multiple confirming reasons. When a qualifying signal fires, FOX enters immediately with maker orders and protects the position with DSL trailing stops synced to Hyperliquid.

FOX also runs a copy trading mode that mirrors positions of top-performing traders. Default budget split: **20% mirror trading / 80% autonomous** (configurable).

## What's New in v1.5

**Clean skill package.** Agent personality files (SOUL.md, USER.md, MEMORY.md, IDENTITY.md, HEARTBEAT.md, TOOLS.md) moved to `templates/` directory as `.template` files. The agent copies them to the workspace root on first boot and customizes them during setup. No personal data ships with the skill.

**DSL High Water Mode.** FOX now uses DSL High Water Mode — the trailing stop configuration originally designed for and proven on FOX. Percentage-of-peak locks that trail infinitely. Phase 1 gives trades room to develop with conviction-scaled floors. Once the trade proves itself at +7% ROE, Phase 2 takes over with locks that tighten from 40% to 85% of peak. A First Jump that runs +200% ROE has its stop at +170%. No ceiling. See the full spec for details and fallback tiers.

**20/80 mirror/autonomous split.** Autonomous trading is now the primary capital allocation — it's the proven edge. Mirror trading provides baseline income with 20% of the budget.

**All v1.4 improvements included:** Bootstrap gate (silent cron setup on first session), notification silencing (all crons isolated, NO_REPLY for idle cycles), dead weight cut removed, clean state files with empty wallet/ID placeholders.

## Architecture

```
fox/
├── README.md                  ← You're here
├── AGENTS.md                  ← Agent behavior + bootstrap gate + notification rules
├── BOOTSTRAP.md               ← First-boot setup sequence
├── config/
│   ├── fox-strategies.json    ← Strategy registry (empty — filled during setup)
│   ├── fox-trade-counter.json ← Daily trade counter (empty)
│   ├── copy-strategies.json   ← Copy trading config (empty)
│   ├── market-regime-last.json
│   ├── max-leverage.json      ← Per-asset max leverage limits
│   └── fj-last-seen.json     ← FJ persistence tracking
├── docs/
│   ├── cron-architecture.md   ← All 8 cron job definitions
│   ├── entry-rules-v1.0.md   ← Current entry filter rules
│   ├── dsl-rules-v1.0.md     ← Current DSL/stop loss rules
│   └── copy-trading-setup.md ← Copy trading deployment guide
├── scripts/
│   ├── emerging-movers.py     ← 3min FJ scanner — primary entry signal
│   ├── opportunity-scan-v6.py ← 15min deep scanner — secondary signal
│   ├── market-regime.py       ← BTC macro regime classifier
│   ├── sm-flip-check.py       ← Smart money flip detector
│   ├── wolf-monitor.py        ← Liquidation/margin watchdog
│   └── job-health-check.py    ← DSL/position reconciliation
├── skills/
│   ├── fox-strategy/          ← Entry rules, cron templates, references
│   │   ├── SKILL.md
│   │   └── references/
│   └── dsl-dynamic-stop-loss/ ← DSL v5 trailing stop engine
│       ├── SKILL.md
│       ├── scripts/dsl-v5.py
│       └── references/
└── templates/                 ← Per-agent files (copied to workspace on first boot)
    ├── SOUL.md.template       ← Agent personality
    ├── USER.md.template       ← User profile (chat ID, etc.)
    ├── MEMORY.md.template     ← Long-term agent memory
    ├── IDENTITY.md.template   ← Agent name/creature/emoji
    ├── HEARTBEAT.md.template  ← Periodic check tasks
    └── TOOLS.md.template      ← Environment-specific notes
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

All crons run on **isolated sessions** with `agentTurn` payloads. No main session crons. NO_REPLY for idle cycles.

## DSL Configuration: High Water Mode

FOX uses **DSL High Water Mode** — the trailing stop configuration originally designed for and proven on FOX.

**Spec:** https://github.com/Senpi-ai/senpi-skills/blob/main/dsl-dynamic-stop-loss/dsl-high-water-spec%201.0.md

Phase 1 conviction-scaled floors give trades room to develop:

| Entry Score | Max ROE Loss | Time Exits |
|---|---|---|
| 6-7 | -20% ROE | All disabled |
| 8-9 | -25% ROE | All disabled |
| 10+ | Unrestricted | All disabled |

Phase 2 percentage-of-peak locks (after +7% ROE):

| Tier | Trigger | Lock (% of peak) | Breaches |
|---|---|---|---|
| 1 | +7% ROE | 40% | 3 |
| 2 | +12% ROE | 55% | 2 |
| 3 | +15% ROE | 75% | 2 |
| 4 | +20% ROE+ | 85% | 1 (infinite trail) |

**Current workaround** (until DSL engine supports `lockMode: "pct_of_high_water"`): use legacy fallback tiers with standard `lockPct` fields. After engine update, switch to High Water tiers with `lockHwPct`. See the spec for the fallback tier table.

## Optional: Trading Strategy Variants

FOX is the best-performing skill in the Senpi zoo as-is. For users who want to experiment, four strategy variants are available as optional config overrides:

| Strategy | What Changes | When To Consider |
|---|---|---|
| **Feral Fox** | Score 7+, 3 reasons min, regime enforced, structural invalidation | After running vanilla FOX profitably and wanting fewer, higher-conviction trades |
| **Ghost Fox** | Feral Fox entries + High Water `lockHwPct` tiers | After engine supports `pct_of_high_water` and you want explicit infinite trailing |
| **Lynx** | Score 10/12, wide stops, no time exits — the patient hunter | When the market is choppy and you want the proven wider-stops approach |
| **Jackal** | Score 12/14, tight Phase 1, fast kills — the defensive variant | When you want to A/B test tight execution vs wide patience in chop |

**Start with vanilla FOX.** It's proven. The variants are upgrade paths, not requirements.

## Quick Start

1. Deploy to OpenClaw with Senpi MCP configured
2. The agent reads AGENTS.md → copies templates to workspace → checks for `config/bootstrap-complete.json` → runs bootstrap
3. Bootstrap creates all 8 crons, copy trading monitor, and market regime cron
4. Agent sends one welcome message: "🦊 FOX is online."
5. The FOX hunts

## Key Learnings

- **Signal quality is the edge** — 85% directional accuracy on score 6+ First Jumps
- **Stops were the problem, not direction** — wide stops + no time exits = the winning pattern
- **Dead weight cut was killing winners** — removed since v1.1
- **Fee drag compounds** — fewer trades, higher conviction, wider stops
- **Mirror trading provides baseline income** — 20/80 split, autonomous is the primary edge

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token
- Python 3.10+
- DSL v5 skill (included)

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
