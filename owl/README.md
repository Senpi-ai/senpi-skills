# OWL v4 — Multi-Scanner Trading Strategy for Hyperliquid

**Version:** 4.0 (March 8, 2026)
**Platform:** Hyperliquid perps via Senpi MCP + OpenClaw
**Type:** Autonomous multi-scanner (contrarian + momentum + correlation lag)

## Overview

OWL is a fully autonomous trading strategy that runs 3 parallel scanners sharing a single wallet, 3-slot limit, and DSL trailing stop system:

1. **Contrarian Hunt** (owl-hunt-v4.py) — Point-scored entries against extreme crowding
2. **Momentum** (owl-momentum.py) — Follows smart money concentration via Hyperfeed
3. **Correlation Lag** (owl-correlation.py) — BTC/ETH lag → enter lagging alts

## Architecture

```
6 Crons (OpenClaw scheduler)
├── Hunt Scanner         15m   main session     Contrarian entries
├── Momentum Scanner      5m   main session     SM-following entries
├── Correlation Scanner   5m   main session     BTC/ETH lag entries
├── DSL v5                3m   isolated session  Trailing stops + Phase 1 cuts
├── OI Tracker            5m   isolated session  Open interest data collection
└── Risk Guardian         5m   isolated session  Kill switches
```

## File Structure

```
owl-v4/
├── README.md                    # This file
├── SKILL.md                     # Original skill definition
├── scripts/                     # Python scanners (stdlib only, no pip)
│   ├── owl_config.py            # Shared utilities
│   ├── owl-hunt-v4.py           # Contrarian scanner
│   ├── owl-momentum.py          # Momentum scanner
│   ├── owl-correlation.py       # Correlation lag scanner
│   ├── owl-risk.py              # Risk guardian
│   └── oi-tracker.py            # OI data collector
├── dsl/
│   └── dsl-v5.py                # Trailing stop engine
├── config/
│   └── owl-config.json          # Strategy config (thresholds, tiers, scoring)
├── crons/
│   └── cron-setup.md            # Full cron definitions with payloads
├── workspace/                   # OpenClaw workspace files
│   ├── AGENTS.md, SOUL.md, IDENTITY.md, USER.md
│   ├── TOOLS.md, HEARTBEAT.md, BOOTSTRAP.md
│   └── MEMORY.md
└── references/
    ├── config-schema.md
    └── cron-templates.md
```

## Requirements

- OpenClaw with cron scheduler
- Senpi MCP connection (JWT token)
- mcporter CLI
- Python 3.10+ (stdlib only)
- Strategy created on Senpi (`strategy_create_custom_strategy`)

## Setup

1. Copy scripts to workspace skill directory
2. Edit `owl-config.json` with your strategyId, wallet, chatId, budget
3. Create `owl-state.json` (see cron-setup.md)
4. Create DSL and state directories
5. Add crons per `crons/cron-setup.md`

## Key Parameters

### Entry Scoring
- **Contrarian:** min 8 points (crowding, structural signals, 4h divergence)
- **Momentum:** min 6 points (SM concentration, contribution trend, technicals)
- **Correlation:** min 6 points (lag ratio, volume, SM alignment)

### DSL Trailing Stops (9-Tier)
| Tier | ROE Trigger | Lock % |
|------|-------------|--------|
| 1 | 5% | 2% |
| 2 | 10% | 5% |
| 3 | 20% | 14% |
| 4 | 30% | 24% |
| 5 | 40% | 34% |
| 6 | 50% | 44% |
| 7 | 65% | 56% |
| 8 | 80% | 72% |
| 9 | 100% | 90% |

### Phase 1 (Conviction-Scaled)
| Score | Hard Timeout | Weak Peak | Dead Weight |
|-------|-------------|-----------|-------------|
| 8-9   | 45 min      | 20 min    | 12 min      |
| 10-11 | 60 min      | 25 min    | 15 min      |
| 12+   | 75 min      | 30 min    | 20 min      |

- **Floor:** 0.06/leverage (6% ROE max loss)
- **weakPeakCut threshold:** 5% ROE (close if peak below this after timeout)
- **T1 warmup:** 5min delay before setting exchange SL
- **greenIn10:** Tighten floor 50% if never positive in 10min

### Risk Limits
| Rule | Value |
|------|-------|
| Max slots | 3 |
| Max same direction | 2 |
| Loss cooldown | 4 hours per asset |
| Max daily loss | 8% |
| Max drawdown | 18% |

### ALO (Fee Optimization)
- Entries: FEE_OPTIMIZED_LIMIT + ensureExecutionAsTaker
- Profit exits: ALO
- Stop losses: MARKET

## Lessons Learned

1. 0.04/lev floor at 8x = 0.5% from entry = noise. Use 0.06 minimum.
2. Exchange SL on open kills positions. T1 warmup delay is mandatory.
3. weakPeakCut at 3% kills slow builders. Use 5%.
4. BTC correlation filter prevents 3 identical shorts.
5. owl-state.json can desync — verify clearinghouse after cron actions.
