# 🦊 FOX v1.6 — Autonomous + Copy Trading for Hyperliquid

The ambush sniper. Catches explosive First Jumps — assets rocketing from obscurity into the top ranks on Hyperliquid's leaderboard — before the crowd confirms the signal. 85% directional accuracy on strong signals.

**Current leader** on the Senpi Predators tracker at +18% ROI.

## What FOX Does

FOX scans Hyperliquid's smart money leaderboard every 3 minutes. Every signal must survive a five-layer gauntlet before FOX enters:

1. **First Jump** — asset must jump 10+ ranks from outside the top 25 in one scan. Top-10 assets are blocked (the move already happened).
2. **Score thresholds** — minScore ≥ 9, minReasons ≥ 4, minVelocity > 0.10. The asset must trigger multiple deep signals simultaneously with visible SM acceleration.
3. **Asset trend alignment** — pull the specific asset's 4h/1h chart. If the signal says SHORT but the asset's 4h is trending LONG → trash it. Never trade against the local macro trend.
4. **Leverage floor** — exchange max leverage must be ≥ 7x. Assets capped at 3x or 5x are skipped for capital efficiency.
5. **Time-of-day modifier** — +1 point during 04:00–14:00 UTC (highest win-rate window), -2 points during 18:00–02:00 UTC (worst window). Evening signals need raw score 11+ to pass.

When a signal survives all five layers, FOX enters with a FEE_OPTIMIZED_LIMIT order (maker fees, not taker) and immediately spawns a DSL High Water trailing stop.

FOX also runs a copy trading mode that mirrors positions of top-performing traders. Default budget split: **20% mirror trading / 80% autonomous** (configurable).

## What's New in v1.6

**Five-layer entry gauntlet.** Every signal must survive First Jump detection, score/velocity/reason thresholds, asset-specific trend alignment, leverage floor check, and time-of-day statistical modifier. This is the production-tuned filter set that both the Fox and Feral Fox agents independently converged on through live trading. Kills 95%+ of signals before they reach execution.

**Score raised to 9 (was 6).** The composite momentum score floor is now 9 — only strong multi-signal confirmations pass. Legacy WOLF used 6-7. Previous FOX versions used 6.

**Velocity raised to 0.10 (was 0.01).** SM must be visibly accelerating into the asset, not just drifting. Previous versions accepted any positive velocity.

**Reasons raised to 4 (was 2).** Must trigger FIRST_JUMP + IMMEDIATE_MOVER + CONTRIB_EXPLOSION + at least one more. No single-reason entries.

**Previous rank ≥ 25.** Asset must originate from outside the top 25. Jumps from #20 to #8 are blocked — the easy money is in catching assets rocketing from obscurity.

**Top-10 block.** Never buy an asset already sitting in the top 10. That move already happened.

**Asset-specific trend alignment (hard block).** Global regime is not enough. The specific asset's 4h/1h chart must agree with the signal direction. Counter-trend = trash regardless of score.

**Leverage floor (7x minimum).** Assets capped at 3x or 5x are skipped. Capital efficiency requires at least 7x.

**Time-of-day modifier.** +1 point during 04:00–14:00 UTC, -2 points during 18:00–02:00 UTC. Derived from live trade win-rate analysis. Evening signals need raw score 11+ to pass the 9 threshold.

**All v1.5 improvements included:** Clean template structure, DSL High Water Mode, 20/80 mirror/autonomous split, bootstrap gate, notification silencing, NO_REPLY, dead weight removed.

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
│   └── dsl-dynamic-stop-loss/ ← DSL v5.3.1 trailing stop engine
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
| 2 | DSL v5.3.1 | 3 min | Trailing stops + HL SL sync |
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

FOX v1.6 is the most battle-tested configuration in the zoo — it runs the exact filters that both the Fox and Feral Fox agents independently converged on through live trading. The variants below tune specific aspects for different conditions:

| Strategy | What Changes vs FOX v1.6 | When To Consider |
|---|---|---|
| **Falcon** | Same five-layer gauntlet (FALCON was built from the same live data) | Already built into FOX v1.6 — Falcon IS the new Fox default |
| **Lynx** | Score 10/12, no time-of-day modifier, widest stops | When you want maximum patience in choppy markets |
| **Jackal** | Score 12/14, re-enabled time exits, fast Phase 1 kills | A/B test against Lynx — tight execution vs wide patience |
| **Ghost Fox** | High Water `lockHwPct` tiers explicitly | After engine supports `pct_of_high_water` |

**Start with vanilla FOX v1.6.** The five-layer gauntlet is the proven default.

## Quick Start

1. Deploy to OpenClaw with Senpi MCP configured
2. The agent reads AGENTS.md → copies templates to workspace → checks for `config/bootstrap-complete.json` → runs bootstrap
3. Bootstrap creates all 8 crons, copy trading monitor, and market regime cron
4. Agent sends one welcome message: "🦊 FOX is online."
5. The FOX hunts

## Key Learnings

- **Signal quality is the edge** — 85% directional accuracy on score 9+ First Jumps
- **Five-layer gauntlet kills 95%+ of signals** — the ones that survive are monsters
- **Stops were the problem, not direction** — wide stops + no time exits = the winning pattern
- **Dead weight cut was killing winners** — removed since v1.1
- **Fee drag compounds** — maker orders (FEE_OPTIMIZED_LIMIT) save ~4 bps per entry
- **Time-of-day matters** — 04:00–14:00 UTC wins, 18:00–02:00 UTC bleeds
- **Asset trend > global regime** — checking the specific asset's 4h/1h chart catches counter-trend traps that global regime misses
- **Mirror trading provides baseline income** — 20/80 split, autonomous is the primary edge

## Requirements

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) MCP access token
- Python 3.10+
- DSL v5 skill (included)

## License

MIT — Built by Senpi (https://senpi.ai).
Source: https://github.com/Senpi-ai/senpi-skills
