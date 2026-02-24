[SKILL.md](https://github.com/user-attachments/files/25506588/SKILL.md)
---
name: wolf-howl
description: >-
  HOWL — Hunt, Optimize, Win, Learn. Nightly self-improvement loop for the
  WOLF autonomous trading strategy. Runs once per day (via cron) to review
  all trades from the last 24 hours, compute win rates, analyze signal quality
  correlation, evaluate DSL tier performance, identify missed opportunities,
  and produce concrete improvement suggestions for the wolf-strategy skill.
  Use when setting up daily trade review automation, analyzing trading
  performance, or improving an autonomous trading strategy through
  data-driven feedback loops.
  Requires Senpi MCP connection, mcporter CLI, and OpenClaw cron system.
---

# HOWL — Hunt, Optimize, Win, Learn

The WOLF hunts all day. At night, it HOWLs — reviewing every kill and miss, sharpening its instincts, and waking up sharper tomorrow.

Automated daily retrospective with data-driven self-improvement suggestions for the WOLF strategy.

## Setup

Run the setup script to configure the nightly HOWL:

```bash
python3 scripts/howl-setup.py --wallet {WALLET} --chat-id {CHAT_ID}
```

The agent already knows wallet and chat ID — it just needs to create the cron. Optionally set run time (default: 23:55 local) and timezone.

## How It Works

The cron fires daily and spawns an isolated sub-agent that:

### 1. Gathers Data
- Reads `memory/YYYY-MM-DD.md` (today + yesterday)
- Reads `MEMORY.md` for cumulative context
- Reads all `dsl-state-WOLF-*.json` files (active = current positions, inactive = closed trades)
- Reads `wolf-strategy.json` for current config
- Queries Senpi trade history via mcporter
- Reads scanner script for current filter thresholds

### 2. Analyzes Each Closed Trade
For every trade: asset, direction, entry/exit price, PnL, ROE, duration, max ROE (high water), DSL tier reached, entry signal type and quality (reason count, rank jump, contrib velocity, trader count), SM conviction at entry vs exit, close trigger (DSL/manual/stagnation).

### 3. Computes Metrics
- Win rate, avg winner vs avg loser PnL, profit factor
- Signal quality correlation (do higher reason counts → better outcomes?)
- DSL tier distribution (how many reached Tier 1/2/3/4 vs Phase 1 closes?)
- Slot utilization (% time filled vs empty)
- Dead weight duration (how long losers sat before cut)
- Missed opportunities (top movers we didn't trade)

### 4. Identifies Patterns
- Entry patterns distinguishing winners from losers
- DSL effectiveness (too tight? too loose?)
- Stagnation thresholds (cutting dead weight fast enough?)
- Scanner filter accuracy (catching right signals?)
- Position sizing optimization
- Timing patterns

### 5. Produces Report
Saves full report to `memory/howl-YYYY-MM-DD.md` with:
- Summary stats (trades, win rate, net PnL, profit factor)
- Trade log table
- What worked / what didn't (data-backed)
- Pattern insights
- Recommended improvements (high/medium/low confidence)
- Config change suggestions

### 6. Updates Memory & Delivers
- Appends distilled summary to `MEMORY.md`
- Sends concise Telegram summary to user

## Report Format

See `references/report-template.md` for the exact output format.

## Rules
- Every recommendation must be backed by data from the last 24h
- Don't change things that are working — if win rate is high, don't fix what isn't broken
- Small incremental improvements > big overhauls
- If < 3 trades in 24h, skip pattern analysis (not enough data)
- Compare today vs cumulative stats to spot trends
- Be brutally honest — no sugarcoating losses

## Customization

Edit `references/analysis-prompt.md` to adjust what the sub-agent analyzes. The prompt is read by the sub-agent at runtime, so changes take effect on the next HOWL without restarting crons.

## Files

| File | Purpose |
|------|---------|
| `scripts/howl-setup.py` | Setup wizard — creates the nightly HOWL cron |
| `references/analysis-prompt.md` | Full sub-agent analysis prompt (editable) |
| `references/report-template.md` | Output report format |
