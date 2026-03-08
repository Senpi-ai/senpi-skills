# TIGER v4 — Multi-Scanner Goal-Based Trading System

Autonomous trading strategy for Hyperliquid perpetuals via [Senpi](https://senpi.ai) MCP.

## What's Included

```
tiger-v4/
├── README.md                    ← You're here
├── tiger-strategy/              ← The skill (trading logic)
│   ├── SKILL.md                 ← Main skill instructions for the agent
│   ├── OPTIMIZATION-PLAN.md     ← v4 data-driven optimization rationale
│   ├── tiger-config.json        ← Strategy configuration (edit this)
│   ├── scripts/                 ← All Python scanners & utilities
│   │   ├── tiger_config.py      ← Shared config/state/MCP helpers
│   │   ├── tiger_lib.py         ← Technical analysis library (pure stdlib)
│   │   ├── prescreener.py       ← Phase 1: scores all 230+ assets cheaply
│   │   ├── compression-scanner.py  ← BB squeeze + OI breakout detector
│   │   ├── momentum-scanner.py  ← Price move + volume spike detector
│   │   ├── reversion-scanner.py ← RSI extreme + divergence detector
│   │   ├── correlation-scanner.py  ← BTC/ETH leader → alt lag detector
│   │   ├── funding-scanner.py   ← Extreme funding rate arb detector
│   │   ├── oi-tracker.py        ← OI history accumulator
│   │   ├── goal-engine.py       ← Adaptive aggression calculator
│   │   ├── risk-guardian.py     ← Drawdown & daily loss enforcer
│   │   ├── tiger-exit.py        ← Smart exit logic (time stops, stagnation)
│   │   ├── dsl-v4.py            ← Dynamic trailing stop loss (10 tiers)
│   │   ├── roar-analyst.py      ← Auto-optimizer (adjusts params over time)
│   │   ├── roar_config.py       ← ROAR configuration
│   │   ├── tiger-setup.py       ← Strategy initialization
│   │   └── create-dsl-state.py  ← DSL state bootstrapper
│   └── references/              ← Detailed docs for the agent
│       ├── config-schema.md
│       ├── cron-templates.md
│       ├── scanner-details.md
│       ├── setup-guide.md
│       └── state-schema.md
└── workspace/                   ← Agent personality & bootstrap files
    ├── AGENTS.md                ← Agent behavior rules
    ├── SOUL.md                  ← Trading persona
    ├── IDENTITY.md              ← Name, creature, emoji
    ├── TOOLS.md                 ← Environment-specific notes
    ├── BOOTSTRAP.md             ← Startup sequence
    ├── HEARTBEAT.md             ← Periodic check tasks
    ├── MEMORY.md                ← Long-term memory (template)
    └── USER.md.template         ← User config template
```

## Requirements

- **OpenClaw** — AI agent runtime with cron system
- **Senpi MCP** — Hyperliquid trading API (auth token required)
- **mcporter CLI** — MCP tool caller (comes with OpenClaw)
- **Python 3.8+** — No external dependencies (stdlib only)

## Setup

1. **Deploy OpenClaw** and configure Senpi MCP auth token
2. **Copy `workspace/` files** to your OpenClaw workspace root (`/data/workspace/`)
3. **Copy `tiger-strategy/`** to `/data/workspace/skills/tiger-strategy/`
4. **Edit `tiger-config.json`** — set your strategy_id, strategy_wallet, telegram_chat_id, budget, target, deadline
5. **Edit `USER.md`** — fill in your Telegram chat ID and wallet addresses
6. **Start the agent** — it reads SKILL.md and creates all 12 cron jobs automatically

## Architecture

12 cron jobs running in parallel:

| Cron | Frequency | Purpose |
|------|-----------|---------|
| Prescreener | 5min | Scores all assets, produces top-30 candidate list |
| Compression Scanner | 5min | BB squeeze + OI breakout detection |
| Momentum Scanner | 5min | Price momentum + volume spike detection |
| Reversion Scanner | 5min | RSI extreme + divergence detection |
| Correlation Scanner | 3min | BTC/ETH leader → alt lag detection |
| Funding Scanner | 30min | Extreme funding rate arbitrage |
| OI Tracker | 5min | Open interest history accumulation |
| Goal Engine | 1h | Adaptive aggression recalculation |
| Risk Guardian | 5min | Drawdown & daily loss enforcement |
| Exit Checker | 5min | Smart exit logic (time stops, stagnation) |
| DSL Trailing Stop | 30s | 10-tier dynamic stop loss management |
| ROAR Analyst | 8h | Auto-parameter optimization |

## Key v4 Improvements (vs v3)

Based on analysis of 24 trades (12W/12L, -$41 net → projected +$400-600):

- **Realistic targets**: 1.5%/day over 14 days (was 8%/day moonshots)
- **Tighter risk**: 3% max loss per trade, 8% daily, 15% drawdown
- **Higher confluence**: Score ≥0.75-0.85 required (was 0.55-0.65)
- **10-tier DSL**: Smooth profit locking from 5% → 100% ROE
- **Wall-clock budget**: Scanners stop at 40s to avoid cron timeouts
- **Volume gates**: Momentum entries need volume_ratio ≥ 1.2
- **ALO entries**: FEE_OPTIMIZED_LIMIT for entries, MARKET for emergency exits
- **Margin sizing**: 30% of balance per slot as margin (fixed prior bug)

## Performance History

| Mission | Result | Notes |
|---------|--------|-------|
| Mission 1 | +18.5% in 2.5 days (7W/1L) | Best run |
| Mission 2-4 | Flat/losses | Too aggressive targets |
| Mission 5 | Flat | Reset after transfer |
| Mission 6 (v4) | Active | $3,048 → $3,660 target |

## License

Apache-2.0
