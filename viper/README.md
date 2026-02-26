# 🐍 VIPER v1 — Range Trading for Choppy Markets

**Detect the range. Map the boundaries. Trade the bounces. Exit when the range breaks.**

VIPER profits from the 60-70% of market time that destroys everything else — sideways, choppy, range-bound conditions. Maker limit orders at pre-determined support/resistance levels. Immediate exit on range break.

## Quick Start

```bash
python3 scripts/viper-setup.py --wallet 0x... --strategy-id UUID \
  --budget 5000 --chat-id 12345
```

Then create 6 crons from `references/cron-templates.md`. VIPER needs 1-4 hours to confirm its first range.

## Architecture

| Cron | Interval | Tier | Purpose |
|------|----------|------|---------|
| Range Scanner | 15 min | Tier 1 | Detect range-bound assets |
| Boundary Mapper | 1 hour | Tier 1 | Map support/resistance zones |
| Bounce Trader | 2 min | Tier 2 | Limit orders at boundaries |
| Break Detector | 2 min | Tier 1 | Range break → emergency exit |
| Risk & Exit | 5 min | Tier 2 | Trailing stops, aging, PnL guards |
| Health | 10 min | Tier 1 | Margin, reporting |

## Performance Targets

| Metric | Target |
|--------|--------|
| Win rate | 65-75% |
| Profit factor | 2.5-3.5 |
| Maker fill rate | 85-95% |
| Trades/day | 2-6 |
| Bounces/range | 4-8 |

## File Structure

```
viper/
├── SKILL.md                          # Agent playbook
├── README.md                         # This file
├── scripts/
│   ├── viper_lib.py                  # ADX, BB, range detection math
│   ├── viper_config.py               # Config/state/MCP (single source of truth)
│   ├── viper-setup.py                # Setup wizard
│   ├── range-scanner.py
│   ├── boundary-mapper.py
│   ├── bounce-trader.py
│   ├── break-detector.py
│   ├── viper-exit.py
│   └── viper-health.py
├── references/
│   ├── state-schema.md               # Full state file documentation
│   └── cron-templates.md             # Ready-to-use cron payloads
└── state/{instanceKey}/              # Created at runtime
    ├── viper-state.json
    ├── range-{ASSET}.json
    └── trade-log.json
```

## Changelog

### v1.0
- Initial release
- Conforms to Senpi Skill Development Guide
- atomic_write(), deep_merge(), call_mcp() with retry
- Model tiering per cron (Tier 1/Tier 2)
- HEARTBEAT_OK early exit pattern
- Verbose mode via VIPER_VERBOSE=1
- Percentage convention: whole numbers throughout

## License

Apache-2.0
