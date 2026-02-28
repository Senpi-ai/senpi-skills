# 🐯 TIGER v4 — Multi-Scanner Goal-Based Trading with Prescreener + ROAR

**5 scanners. 1 prescreener. 1 goal. Configurable aggression. Mechanical exits. Self-optimizing.**

TIGER targets a configurable profit over a deadline using 5 signal patterns, DSL v4 trailing stops, and automatic aggression adjustment. The prescreener scores all ~230 assets in one API call and feeds the top 30 to scanners. ROAR watches TIGER trade and continuously tunes execution parameters. Give it a budget, a target, and a timeframe — it calculates how hard to hunt.

## Quick Start

```bash
python3 scripts/tiger-setup.py --wallet 0x... --strategy-id UUID \
  --budget 1000 --target 2000 --deadline-days 7 --chat-id 12345
```

Then create 12 crons from `references/cron-templates.md`. OI tracker needs ~1h to build history.

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
| Prescreener | 5 min | Tier 1 | Score all assets, feed top 30 to scanners |
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
| ROAR Analyst | 8 hour | Tier 2 | Meta-optimizer |

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
│   ├── prescreener.py
│   ├── compression-scanner.py
│   ├── correlation-scanner.py
│   ├── momentum-scanner.py
│   ├── reversion-scanner.py
│   ├── funding-scanner.py
│   ├── oi-tracker.py
│   ├── goal-engine.py
│   ├── risk-guardian.py
│   ├── tiger-exit.py
│   ├── dsl-v4.py
│   ├── roar-analyst.py
│   └── roar_config.py
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
    ├── roar-state.json
    ├── prescreened.json
    └── scan-history/
```

## Changelog

### v4.0 (current)

**Two-Phase Prescreener**
- New `prescreener.py` scores all ~230 Hyperliquid assets in one API call (~6s).
- Ranks by momentum + volume + funding + OI activity, outputs top 30 candidates.
- Splits into two groups of 15 (group_a = higher volume, group_b = next 15).
- Scanners read `SCAN_GROUP` env var (a/b) to pick their assigned group.
- Without prescreener or SCAN_GROUP, scanners fall back to original top-12 behavior (fully backward compatible).
- Cron 0 added. 12-cron architecture.
- `load_prescreened_candidates()` extracted to tiger_config as shared utility (no duplication across scanners).

**Scanner Updates**
- `compression-scanner.py`, `momentum-scanner.py`, `reversion-scanner.py`: Updated to read prescreened.json when fresh (<10min), with graceful fallback.
- `tiger_config.py`: Added `STATE_DIR` export and `load_prescreened_candidates()` shared utility.

### v3.0

**ROAR Meta-Optimizer**
- New `roar-analyst.py` and `roar_config.py`. Runs every 8h + ad-hoc every 5th trade.
- Builds per-pattern scorecard from trade log. 6 rule-based adjustment engine.
- Tunes: confluence thresholds (per-pattern), DSL retrace, scanner params — all within hard-bounded ranges.
- Auto-reverts if both win rate and avg PnL degrade after an adjustment.
- Disables patterns with negative expectancy over 20+ trades, auto-re-enables after 48h.
- Never touches user risk limits (budget, target, drawdown, daily loss).
- Cron 11 added to cron-templates.md. 11-cron architecture.

**Critical Bug Fixes**
- **dsl-v4.py**: Fixed units mismatch — `triggerPct` (decimal 0.05) was compared directly to `upnl_pct` (whole 2.1%), causing instant tier escalation and premature closes. Now multiplies by 100. Fixed `lockPct` floor calc double-dividing by 100.
- **tiger_config.py**: Fixed zombie process leak — `subprocess.run(timeout)` → `Popen + communicate + proc.kill()`. Added `load_trade_log()` and `get_trade_log_path()` for ROAR.

**Scanner Fixes**
- **correlation-scanner.py**: Reduced alt scan from 20 to max 6. Prevents API timeouts.
- **funding-scanner.py**: Added retry on instruments fetch failure. Reduced candidates 15→8.

**Cron Architecture** (from [OpenClaw best practices](https://docs.openclaw.ai/automation/cron-jobs))
- Tier 1 scanners → isolated sessions, `delivery.mode: "none"`, explicit `claude-haiku-4-5`
- Tier 2 decision-makers → isolated sessions, `delivery.mode: "announce"`, `claude-sonnet-4-5`
- DSL stays main session (needs position state context)
- HEARTBEAT_OK auto-suppressed. Notification policy: silent when idle.
- Eliminates session lock contention and notification spam.

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
