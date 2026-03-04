# WOLF Strategy v6.1

Fully autonomous multi-strategy trading for Hyperliquid perps. The WOLF hunts for its human — scans, enters, exits, and rotates positions without asking permission. Manages multiple strategies simultaneously, each with independent wallets, budgets, slots, and DSL configs.

**Proven:** +$2,000 realized in 24h, 25+ trades, 65% win rate on $6.5k budget.

## What's Included

| File | Purpose |
|------|---------|
| `SKILL.md` | Strategy instructions, rules, multi-strategy architecture |
| `scripts/wolf_config.py` | **Shared config loader** — all scripts import this |
| `scripts/wolf-setup.py` | **Setup wizard** — adds strategy to multi-strategy registry |
| `scripts/emerging-movers.py` | Emerging Movers v4 — primary entry signal (3min scans, FIRST_JUMP priority) |
| `scripts/dsl-combined.py` | DSL v4 combined runner — trailing stops for all positions, all strategies |
| `scripts/sm-flip-check.py` | SM conviction flip detector (multi-strategy) |
| `scripts/wolf-monitor.py` | Watchdog — per-strategy margin buffer + position health |
| `scripts/open-position.py` | **Atomic position opener** — opens position + creates DSL state in one step |
| `scripts/job-health-check.py` | Per-strategy orphan DSL / state validation |
| `references/cron-templates.md` | Cron MANDATE templates with multi-strategy signal routing |
| `references/state-schema.md` | Registry schema, DSL state schema, scanner config |
| `references/learnings.md` | Proven results, known bugs, trading discipline rules |

## What's New in v6

- **Multi-strategy support** — manage 2+ strategies with independent wallets, budgets, slots, DSL configs
- **Strategy registry** (`wolf-strategies.json`) replaces single `wolf-strategy.json`
- **Per-strategy state dirs** — `state/{strategyKey}/dsl-{ASSET}.json` prevents collision when same asset traded in multiple strategies
- **Signal routing** — signals route to best-fit strategy based on available slots and risk profile
- **One set of crons** — scripts iterate all strategies internally, no per-strategy crons needed
- **Shared config loader** (`wolf_config.py`) — all scripts use same module for config, paths, legacy migration
- **Backward compatible** — auto-migrates legacy `wolf-strategy.json` and old state files on first run

## What's New in v6.1

- **Reduced leverage ranges** — aggressive caps at 75% of max leverage (was 100%), moderate at 50% (was 75%), conservative at 25% (was 50%). Prevents over-leveraging on high-max-leverage assets.
- **Risk-based leverage** — dynamic per-position leverage computed from `tradingRisk` × `maxLeverage` × signal `conviction` (replaces hardcoded leverage)
- **Rotation cooldown** — positions younger than 45 min can't be rotated out, preventing churning brand-new entries
- **Atomic position opening** — `open-position.py` opens position + creates DSL state in one step; no manual DSL JSON creation needed

## Quick Start

1. Download all files (or clone this folder)
2. Send `SKILL.md` to your Senpi agent: **"Here are some new superpowers"**
3. Tell the agent your **budget** — it handles everything else
4. To add a second strategy, run `wolf-setup.py` again with a different wallet/budget

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v6.1 | 2026-03-03 | Reduced leverage ranges, risk-based dynamic leverage, rotation cooldown (45min), atomic open-position.py |
| v6.0 | 2026-02-24 | Multi-strategy support, strategy registry, per-strategy state dirs, signal routing, shared config loader |
| v5.0 | 2026-02-24 | FIRST_JUMP signal priority, combined DSL runner, 90s scanner interval, Phase 1 auto-cut, 7x min leverage |
| v4.0 | 2026-02-24 | Complete rewrite — all scripts bundled, setup wizard, cron mandates, tighter DSL tiers, entry filters fixed |
| v3.1 | 2026-02-23 | Budget-scaled parameters, autonomy rules, aggressive rotation |
| v3.0 | 2026-02-23 | Initial release. 2-slot, IMMEDIATE_MOVER entries, +$750 proven |
