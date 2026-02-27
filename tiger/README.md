# 🐯 TIGER v2 — Multi-Scanner Goal-Based Trading

**5 scanners. 1 goal. Configurable aggression. Mechanical exits.**

TIGER targets a configurable profit over a deadline using 5 signal patterns, DSL v4 trailing stops, and automatic aggression adjustment. Give it a budget, a target, and a timeframe — it calculates how hard to hunt.

## Quick Start

```bash
python3 scripts/tiger-setup.py --wallet 0x... --strategy-id UUID \
  --budget 1000 --target 2000 --deadline-days 7 --chat-id 12345
```

Then create 10 crons from `references/cron-templates.md`. OI tracker needs ~1h to build history.

## 5 Signal Patterns

| Pattern | Scanner | Signal |
|---------|---------|--------|
| Compression Breakout | `compression-scanner.py` | BB squeeze + OI accumulation → breakout |
| BTC Correlation Lag | `correlation-scanner.py` | BTC moves, alt hasn't caught up |
| Momentum Breakout | `momentum-scanner.py` | Strong move + volume confirmation |
| Mean Reversion | `reversion-scanner.py` | RSI extreme + exhaustion signals |
| Funding Rate Arb | `funding-scanner.py` | Extreme funding → collect income |

## Architecture

| Cron | Interval | Tier | Purpose |
|------|----------|------|---------|
| Compression Scanner | 5 min | Tier 1 | BB squeeze breakout |
| Correlation Scanner | 3 min | Tier 1 | BTC lag detection |
| Momentum Scanner | 5 min | Tier 1 | Price + volume |
| Reversion Scanner | 5 min | Tier 1 | Overextension fade |
| Funding Scanner | 30 min | Tier 1 | Funding arb |
| OI Tracker | 5 min | Tier 1 | Data collection |
| Goal Engine | 1 hour | Tier 2 | Aggression |
| Risk Guardian | 5 min | Tier 2 | Risk limits |
| Exit Checker | 5 min | Tier 2 | Pattern exits |
| DSL Combined | 30 sec | Tier 1 | Trailing stops |

## Performance Targets

| Metric | Target |
|--------|--------|
| Win rate | 55-65% |
| Profit factor | 1.8-2.5 |
| Trades/day | 2-8 |
| Best conditions | Volatile with clear setups |

## File Structure

```
tiger-strategy/
├── SKILL.md
├── README.md
├── scripts/
│   ├── tiger_lib.py
│   ├── tiger_config.py
│   ├── tiger-setup.py
│   ├── compression-scanner.py
│   ├── correlation-scanner.py
│   ├── momentum-scanner.py
│   ├── reversion-scanner.py
│   ├── funding-scanner.py
│   ├── oi-tracker.py
│   ├── goal-engine.py
│   ├── risk-guardian.py
│   ├── tiger-exit.py
│   └── dsl-v4.py
├── references/
│   ├── state-schema.md
│   ├── cron-templates.md
│   ├── config-schema.md
│   ├── scanner-details.md
│   └── setup-guide.md
└── state/{instanceKey}/
    ├── tiger-state.json
    ├── dsl-{ASSET}.json
    ├── oi-history.json
    ├── trade-log.json
    └── scan-history/
```

## Changelog

### v2.4 (current)
- **correlation-scanner.py**: Reduced alt scan from 10+10 to 6+2 (max 8 alts). Prevents API timeouts.
- **funding-scanner.py**: Added retry on instruments fetch failure. Reduced candidates from 15→8. Prevents timeouts.
- **cron-templates.md**: Complete rewrite following [OpenClaw cron best practices](https://docs.openclaw.ai/automation/cron-jobs):
  - Tier 1 scanners → isolated sessions with `delivery.mode: "none"` (no main session pollution)
  - Tier 2 decision-makers → isolated sessions with `delivery.mode: "announce"` (HEARTBEAT_OK auto-suppressed)
  - DSL stays main session (needs position state context)
  - Model overrides per job (`model` field in payload)
  - `agentTurn` payload for isolated jobs, `systemEvent` for main only
  - Eliminates session lock contention and notification spam
- **DSL cron mandate**: Checks activePositions before invoking dsl-v4.py. No positions = HEARTBEAT_OK, no session spam.

### v2.2
- **AliasDict**: snake_case config/state key access now works transparently alongside camelCase (fixes all KeyError crashes)
- **Function signatures**: `load_state()`, `save_state(state)`, `load_oi_history()`, `append_oi_snapshot()` now work without explicit config arg
- **dsl-v4.py**: migrated to shared infra (atomic_write, mcporter_call, get_prices) — no more raw curl or non-atomic writes
- **Confluence weights**: compression (1.25→1.00) and reversion (1.15→1.00) scanner weights now sum correctly
- **min_leverage**: unified default to 5 across tiger_config.py, tiger-setup.py, and oi-tracker.py
- **Bare except** fixed to `except Exception` in tiger-exit.py
- **Doc fix**: setup-guide.md reference corrected from cron-setup.md to cron-templates.md

### v2.1
- Merged live trading lessons & gotchas from production usage
- DSL `active: true` gotcha documented (the #1 setup mistake)
- API latency notes (6s/call, 8 asset max per scan window)
- Correlation scanner timeout handling guidance
- Trading rules from real P&L: don't short compressed+OI-building, re-entry opposite direction valid, high-score overrides blacklists
- `create_position` order format and `CLOSE_NO_POSITION` handling documented
- Updated setup-guide.md with DSL state file format

### v2.0
- Conforms to Senpi Skill Development Guide
- atomic_write(), deep_merge(), mcporter_call() with 3-retry
- Model tiering per cron (Tier 1/Tier 2)
- HEARTBEAT_OK early exit pattern
- Verbose mode via TIGER_VERBOSE=1
- OpenClaw cron templates (systemEvent format)
- State schema reference with full field documentation
- Instance-scoped state (state/{instanceKey}/)
- Race condition guard on state writes
- Correct MCP tool names (create_position, close_position)

### v1.0
- Initial release with 5 scanners, goal engine, DSL v4

## License

Apache-2.0
