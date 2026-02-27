# WOLF Strategy v6.0

Fully autonomous multi-strategy trading for Hyperliquid perps. The WOLF hunts for its human — scans, enters, exits, and rotates positions without asking permission. Manages multiple strategies simultaneously, each with independent wallets, budgets, slots, and DSL configs.

**Proven:** +$2,000 realized in 24h, 25+ trades, 65% win rate on $6.5k budget.

## What's Included

| File | Purpose |
|------|---------|
| `SKILL.md` | Strategy instructions, rules, multi-strategy architecture |
| `scripts/wolf_config.py` | **Shared config loader** — all scripts import this |
| `scripts/wolf-setup.py` | **Setup wizard** — adds strategy to multi-strategy registry |
| `scripts/emerging-movers.py` | Emerging Movers v4 — primary entry signal (90s scans, FIRST_JUMP priority) |
| `scripts/dsl-combined.py` | DSL v4 combined runner — trailing stops for all positions, all strategies |
| `scripts/opportunity-scan-v6.py` | **Opportunity Scanner v6** — BTC macro, hourly trend, disqualifiers, parallel fetches |
| `scripts/opportunity-scan.py` | Opportunity Scanner v5 (legacy, replaced by v6) |
| `scripts/sm-flip-check.py` | SM conviction flip detector (multi-strategy) |
| `scripts/wolf-monitor.py` | Watchdog — per-strategy margin buffer + position health |
| `scripts/job-health-check.py` | Per-strategy orphan DSL / state validation |
| `references/cron-templates.md` | Cron MANDATE templates with multi-strategy signal routing |
| `references/state-schema.md` | Registry schema, DSL state schema, scanner config |
| `references/learnings.md` | Proven results, known bugs, trading discipline rules |

## What's New in v6

- **Multi-strategy support** — manage 2+ strategies with independent wallets, budgets, slots, DSL configs
- **Strategy registry** (`wolf-strategies.json`) replaces single `wolf-strategy.json`
- **Per-strategy state dirs** — `state/{strategyKey}/dsl-{ASSET}.json` prevents collision when same asset traded in multiple strategies
- **Signal routing** — signals route to best-fit strategy based on available slots and risk profile
- **Opportunity Scanner v6** — fixed with BTC macro context, hourly trend filter, 6 hard disqualifiers, parallel candle fetches, cross-scan momentum
- **One set of crons** — scripts iterate all strategies internally, no per-strategy crons needed
- **Shared config loader** (`wolf_config.py`) — all scripts use same module for config, paths, legacy migration
- **Backward compatible** — auto-migrates legacy `wolf-strategy.json` and old state files on first run

## Quick Start

1. Download all files (or clone this folder)
2. Send `SKILL.md` to your Senpi agent: **"Here are some new superpowers"**
3. Tell the agent your **budget** — it handles everything else
4. To add a second strategy, run `wolf-setup.py` again with a different wallet/budget

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v6.0 | 2026-02-24 | Multi-strategy support, strategy registry, opportunity scanner v6 rewrite, per-strategy state dirs, signal routing, shared config loader |
| v5.0 | 2026-02-24 | FIRST_JUMP signal priority, combined DSL runner, 90s scanner interval, Phase 1 auto-cut, 7x min leverage |
| v4.0 | 2026-02-24 | Complete rewrite — all scripts bundled, setup wizard, cron mandates, tighter DSL tiers, entry filters fixed |
| v3.1 | 2026-02-23 | Budget-scaled parameters, autonomy rules, aggressive rotation |
| v3.0 | 2026-02-23 | Initial release. 2-slot, IMMEDIATE_MOVER entries, +$750 proven |
