# 🦅 HAWK — Multi-Asset Momentum Trading Bot

AI-powered trading bot for Hyperliquid via [Senpi](https://senpi.ai), running on [OpenClaw](https://openclaw.ai).

## What It Does

Scans BTC, ETH, SOL, and HYPE for momentum signals every 30 seconds, enters with ALO fee-optimized orders, and manages positions with a trailing stop loss system (DSL) that pyramids into winners and cuts losers fast.

## Architecture

```
Scanner v3 (30s)  →  Scores all 4 markets, picks strongest signal
                     Uses smart money consensus from top traders
                     6 filters: momentum, 15m trend, 1h trend, volume, chop, funding

DSL v5.2 (3min)  →  Trailing stop loss with 7 tiers
                     Time decay kills stale trades at 45min
                     Pyramids into winners (+15% margin per tier upgrade)
                     Partial TP at 40% ROE (25% of position)
                     Native SL/TP on Hyperliquid via edit_position
                     Auto-adopts orphan positions from clearinghouse

Hedge v2 (60s)   →  Funding-based + market-risk hedging
                     ALO entries for fee savings

Health (10min)    →  Portfolio reporting, margin alerts
```

## File Structure

```
├── AGENTS.md              # Agent behavior instructions
├── BOOTSTRAP.md           # Startup sequence (silent boot)
├── SOUL.md                # Personality & boundaries
├── IDENTITY.md            # Name, emoji, vibe
├── TOOLS.md               # MCP config, cron templates, shell notes
├── USER.md                # User info (chat ID, username)
├── MEMORY.md              # Long-term memory across sessions
├── HEARTBEAT.md           # Heartbeat checklist (empty = skip)
├── memory/                # Daily session logs
│   └── *.md
└── recipes/
    └── hype-sniper/
        ├── SKILL.md           # Strategy documentation
        ├── hype-config.json   # All tunable parameters
        ├── alo-guide.md       # ALO fee optimization reference
        ├── scripts/
        │   ├── hype_lib.py        # Shared library (MCP calls, state I/O)
        │   ├── scanner_v3.py      # Multi-asset momentum scanner
        │   ├── dsl-v52.py         # Trailing stop loss engine
        │   ├── hedge-monitor-v2.py # Hedge monitor
        │   ├── health.py          # Health & reporting
        │   ├── risk-guardian.py    # Account-level guard rails
        │   └── dsl-cleanup.py     # Manual cleanup utility
        └── state/                 # Runtime state (auto-created)
            └── .gitkeep
```

## Setup

1. Deploy OpenClaw with Telegram channel
2. Configure Senpi MCP with auth token
3. Create a CUSTOM strategy on Senpi ($999+ budget recommended)
4. Update `hype-config.json` with your strategy ID and wallet
5. Update `USER.md` with your Telegram chat ID
6. Set up 5 cron jobs (see TOOLS.md for templates)

## Cron Jobs

| Name | Schedule | Script | Env Vars |
|------|----------|--------|----------|
| Multi-Asset Scanner v3 | every 30s | `scanner_v3.py` | `OPENCLAW_WORKSPACE` |
| DSL v5.2 | `*/3 * * * *` | `dsl-v52.py` | `DSL_STATE_DIR`, `DSL_STRATEGY_ID` |
| Hedge v2 | every 60s | `hedge-monitor-v2.py` | `OPENCLAW_WORKSPACE` |
| Risk Guardian | every 5min | `risk-guardian.py` | `OPENCLAW_WORKSPACE` |
| Health | every 10min | `health.py` | `OPENCLAW_WORKSPACE` |

## Key Design Decisions

- **No native TP in Phase 1** — let winners run, trailing stops handle exits
- **5 minute SL sync delay** — prevents instant death on entry noise
- **1h trend filter** — blocks counter-trend entries (LONGs into downtrend = death)
- **Time decay at 45min** — kills trades that go nowhere, capital efficiency
- **Smart money alignment** — boosts signal score when aligned with top trader consensus
- **Partial TP at Tier 4 (40% ROE)** — don't cap winners too early
- **Auto-adopt orphans** — any position without DSL state gets one automatically

## Requires

- OpenClaw (with exec, cron, Telegram)
- Senpi MCP server (configured via mcporter)
- Python 3 (node available for JSON processing)
- `mcporter` CLI for Senpi tool calls
