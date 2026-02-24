# 🐺🌙 HOWL — Hunt, Optimize, Win, Learn

The WOLF hunts all day. At night, it HOWLs — reviewing every trade, finding patterns, and sharpening itself for tomorrow.

Automated nightly self-improvement loop for the [WOLF strategy](../wolf-strategy/SKILL.md). A sub-agent analyzes every trade from the last 24 hours and produces data-driven improvement suggestions.

## What It Does

Every night at 23:55 (configurable), an isolated sub-agent:

1. **Gathers** — reads trade history, DSL state files, memory logs, scanner output, FDR counter
2. **Analyzes** — per-trade breakdown: entry signal quality, DSL tier reached, close trigger, PnL (gross and net), fees, holding duration
3. **Computes** — win rate, profit factor (gross and net), FDR, signal quality correlation, holding period buckets, direction breakdown, slot utilization
4. **Identifies** — patterns distinguishing winners from losers, fee drag, regime mismatches, monster trade dependency, rotation costs, DSL calibration
5. **Reports** — structured report saved to `memory/howl-YYYY-MM-DD.md` + Telegram summary
6. **Learns** — appends distilled insights to `MEMORY.md`, checks for recurring suggestions across consecutive HOWLs (drift detection)

## What's Included

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill instructions and architecture |
| `scripts/howl-setup.py` | Setup wizard — creates the nightly HOWL cron |
| `references/analysis-prompt.md` | Full sub-agent analysis prompt (editable at runtime) |
| `references/report-template.md` | Output report format |

## Setup

Tell your agent to set up the nightly HOWL — it already knows your wallet and chat ID:

```bash
python3 scripts/howl-setup.py --wallet {WALLET} --chat-id {CHAT_ID}
```

## Requires

- [WOLF Strategy](../wolf-strategy/SKILL.md) — the strategy being analyzed
- Senpi MCP connection
- OpenClaw cron system

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v2.0 | 2026-02-24 | FDR analysis, holding period buckets, direction regime detection, monster trade dependency, rotation cost tracking, cumulative drift detection, gross vs net profit factor separation |
| v1.0 | 2026-02-24 | Initial release — nightly retro with pattern analysis and improvement suggestions |
