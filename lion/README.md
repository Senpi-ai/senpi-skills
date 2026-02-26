# 🦁 LION v1 — Liquidation Intelligence & Order-flow Network

**Wait for the market to break. Trade the repair.**

LION detects liquidation cascades, order book imbalances, and squeeze setups on Hyperliquid — then enters the counter-trade at the dislocation and rides the snapback. Patience-first: 0-4 trades per day, often zero.

## Quick Start

```bash
python3 scripts/lion-setup.py --wallet 0x... --strategy-id UUID \
  --budget 5000 --chat-id 12345
```

Then create 6 crons from `references/cron-templates.md`. First hour builds OI baseline.

## 3 Hunt Patterns

| Pattern | Signal | Hold Time | Leverage |
|---------|--------|-----------|----------|
| Cascade Reversal | OI cliff + price velocity + volume spike → counter-trade at stabilization | 15min - 2h | 5-7× |
| Book Imbalance Fade | 3:1+ bid/ask ratio → fade after sweep | 5-30min | 5× |
| Squeeze Detection | Extreme funding + rising OI → trade the unwind | 2-12h | 7-10× |

## Architecture

| Cron | Interval | Tier | Purpose |
|------|----------|------|---------|
| OI Monitor | 60s | Tier 1 | Sample OI across all liquid assets |
| Cascade Detector | 90s | Tier 2 | Combine OI + price + volume + funding |
| Book Scanner | 30s | Tier 1 | L2 order book imbalance |
| Squeeze Monitor | 15 min | Tier 2 | Funding + OI buildup |
| Risk & Exit | 60s | Tier 2 | Pattern-specific exits |
| Health | 10 min | Tier 1 | Margin, reporting |

## Performance Targets

| Metric | Target |
|--------|--------|
| Win rate | 65-75% |
| Profit factor | 2.0-3.0 |
| Trades/day | 0-4 (avg 1-2) |
| Best conditions | Volatile days with leverage flushes |

## File Structure

```
lion/
├── SKILL.md
├── README.md
├── scripts/
│   ├── lion_lib.py
│   ├── lion_config.py
│   ├── lion-setup.py
│   ├── oi-monitor.py
│   ├── cascade-detector.py
│   ├── book-scanner.py
│   ├── squeeze-monitor.py
│   ├── lion-exit.py
│   └── lion-health.py
├── references/
│   ├── state-schema.md
│   └── cron-templates.md
├── state/{instanceKey}/
│   ├── lion-state.json
│   └── trade-log.json
└── history/
    └── oi-history.json        # Shared OI time-series
```

## Changelog

### v1.0
- Initial release
- Conforms to Senpi Skill Development Guide
- atomic_write(), deep_merge(), call_mcp() with retry
- Model tiering per cron (Tier 1/Tier 2)
- HEARTBEAT_OK early exit pattern
- Verbose mode via LION_VERBOSE=1
- Percentage convention: whole numbers throughout
- OI history as shared signal (history/), positions as instance-scoped (state/)

## License

Apache-2.0
