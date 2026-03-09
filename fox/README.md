# 🦊 FOX Trading System

**Autonomous + Copy Trading for Hyperliquid perps via Senpi MCP**

Current version: **FOX v1.0** (autonomous) + **Copy Trading Mode** (active as of 2026-03-08)

## Architecture

FOX runs inside [OpenClaw](https://openclaw.ai) as a set of cron jobs that trigger an AI agent to scan markets, enter positions, and manage trailing stops — all autonomously.

### Two Operating Modes

#### 1. Autonomous Trading (FOX v1.0) — Currently DISABLED
Scans Hyperliquid's smart money leaderboard every 3 minutes for "First Jump" signals — assets rapidly climbing the contribution ranks. Enters positions with scoring-based filters and manages them via Dynamic Stop Loss (DSL).

#### 2. Copy Trading — Currently ACTIVE
Mirrors positions of top-performing Hyperliquid traders identified via Senpi's discovery API. Hands-off: Senpi's copy engine handles entry/exit timing.

---

## Prerequisites

- [OpenClaw](https://openclaw.ai) agent with cron support
- [Senpi](https://senpi.ai) account with MCP access token
- [mcporter](https://github.com/nichochar/mcporter-cli) CLI configured with Senpi server
- Python 3.10+
- Node.js 18+ (for mcporter)

## Directory Structure

```
fox-export/
├── README.md                  # This file
├── AGENTS.md                  # Agent behavior instructions
├── SOUL.md                    # Agent personality/tone
├── TOOLS.md                   # Environment-specific tool notes
├── IDENTITY.md                # Agent identity
├── USER.md                    # User profile
├── MEMORY.md                  # Long-term agent memory
├── HEARTBEAT.md               # Heartbeat task list
├── scripts/                   # Python scanner/monitor scripts
│   ├── emerging-movers.py     # 3min FJ scanner (main entry signal)
│   ├── opportunity-scan-v6.py # 15min deep scanner
│   ├── market-regime.py       # BTC macro regime classifier
│   ├── sm-flip-check.py       # Smart money flip detector
│   ├── wolf-monitor.py        # Liquidation/margin watchdog
│   └── job-health-check.py    # DSL/position reconciliation
├── skills/
│   ├── fox-strategy/          # FOX strategy skill (instructions + references)
│   │   ├── SKILL.md
│   │   └── references/
│   └── dsl-dynamic-stop-loss/ # Dynamic Stop Loss skill
│       ├── SKILL.md
│       ├── scripts/dsl-v5.py  # DSL engine (trailing stops + HL SL sync)
│       └── references/
├── config/                    # State files (current snapshots)
│   ├── fox-strategies.json    # Strategy registry
│   ├── fox-trade-counter.json # Daily trade counter + history
│   ├── copy-strategies.json   # Active copy trading strategies
│   ├── market-regime-last.json# Latest market regime classification
│   ├── max-leverage.json      # Per-asset max leverage limits
│   └── fj-last-seen.json      # FJ persistence tracking
└── docs/
    ├── cron-architecture.md   # All cron job definitions
    ├── entry-rules-v1.0.md    # Current entry filter rules
    ├── dsl-rules-v1.0.md      # Current DSL/stop loss rules
    └── copy-trading-setup.md  # Copy trading deployment guide
```

## Quick Start

### Copy Trading Mode (Recommended)
1. Configure Senpi MCP via mcporter
2. Use `discovery_get_top_traders` and `discovery_get_top_strategies` to find targets
3. Deploy with `strategy_create(traderAddress, initialBudget, mirrorMultiplier, slippage)`
4. Set up the 15min Copy Trading Monitor cron
5. Profit (hopefully)

### Autonomous Mode
1. Configure Senpi MCP via mcporter
2. Create strategy wallet: `strategy_create_custom_strategy`
3. Update `fox-strategies.json` with wallet/strategyId
4. Create 8 OpenClaw crons (see `docs/cron-architecture.md`)
5. The FOX hunts

## Key Learnings (7 Days of Autonomous Trading)

- **Signal quality was excellent** — 85% directional accuracy
- **Stop losses killed profitability** — 79% of trades hit floor SL before the move materialized
- **Fees compound on frequent losers** — $15-50/trade × 3-6 trades/day
- **Monster trades make all the money** — 2-3 Tier 3+ trades = entire day's profit
- **Copy trading > autonomous for now** — delegate exit timing to proven profitable traders

## License

Apache-2.0
