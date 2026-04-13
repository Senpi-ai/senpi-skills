---
name: analyze-user-trades
description: >-
  On-demand analysis of a Senpi user's trading activity. Given a username,
  user ID, or a request for top N arena agents, fetches their strategies,
  closed positions, and audit logs (including AI reasoning) for a specified
  time range. Supports arena week analysis and arbitrary time ranges.
license: Apache-2.0
compatibility: >-
  Python 3.10+, mcporter, Senpi MCP tools:
  user_resolve_usernames, strategy_list, discovery_get_trader_history,
  audit_get_recent_actions, arena_leaderboard
metadata:
  author: senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# analyze-user-trades

Extracts actionable trading intelligence from a Senpi user's activity — what worked, what didn't, and what to replicate or avoid.

## When to use

Use when asked to:
- Analyze trades, strategies, or orders of a specific user (e.g. "analyze @alice's trades")
- Learn from top arena agents — what gave them their edge this week
- Understand why a strategy made or lost money and extract lessons for future trades

## Quick Start

1. Identify the input: username, user ID, or top N request
2. Run `python3 scripts/analyze_user_trades.py` with the appropriate arguments
3. Parse the JSON output
4. Apply the **Intelligence Extraction** rules below to produce the analysis

## Invocation

| Argument        | When to use                           |
|-----------------|---------------------------------------|
| `--username`    | User provided a @username             |
| `--user-id`     | You already have the internal user ID |
| `--top-n`       | User asked for top N arena agents     |
| `--start-time`  | Custom time range start (ISO 8601)    |
| `--end-time`    | Custom time range end (ISO 8601)      |

`--username`, `--user-id`, and `--top-n` are mutually exclusive — pass exactly one.
Time range defaults to the current arena week if omitted.

```
python3 scripts/analyze_user_trades.py --username alice
python3 scripts/analyze_user_trades.py --user-id M123 --start-time 2026-04-03T00:00:00Z --end-time 2026-04-09T23:59:59Z
python3 scripts/analyze_user_trades.py --top-n 10
```

## Processing Order

1. Determine arguments from the user's request
2. Run `python3 scripts/analyze_user_trades.py`
3. If `success=false`: surface the error to the user, stop
4. If `success=true`: apply Intelligence Extraction below, then present findings

## Intelligence Extraction

This is the core value of the skill. Raw JSON is not the output — structured insight is.

For each user in `results`, reason over their `strategies`, `orders`, and `audit_log` to answer:

### What drove profit?
- Which coins, directions (LONG/SHORT), and leverage levels produced positive `realizedPnl`?
- Were winning trades confirmed by `ai_reasoning` in audit logs? What reasoning patterns appear on winners?
- Were entries well-timed (short hold time, tight entry-to-exit spread)?
- Did the strategy stay consistent (same `skillName`, disciplined sizing)?

### What caused losses?
- Which coins, directions, or leverage levels produced negative `realizedPnl`?
- Did `ai_reasoning` on losing trades show overconfidence, ignored risk signals, or chasing momentum?
- Were losers held too long (wide `openTime`→`closeTime` gap while moving against)?
- Did fees (`totalFees`) eat into marginal winners and turn them into net losers?

### Patterns to replicate
Synthesize 2–4 concrete, actionable takeaways a user could apply to their own strategy:
- e.g. "LONG BTC at 5–10x leverage on confirmed SM momentum — 3 of 3 trades profitable this week"
- e.g. "Short duration trades (<4h) on this strategy had 80% win rate; overnight holds lost"

### Patterns to avoid
Synthesize 2–4 concrete warnings drawn directly from the loss data:
- e.g. "High-leverage SHORT on altcoins during uptrend — 100% loss rate, avg -$340 per trade"
- e.g. "Re-entering the same coin after a stop-out — second entries all lost"

### Cross-user patterns (top-N only)
When analyzing multiple users, compare across results:
- Which coins and directions appear most in top performers' winning trades?
- What leverage ranges do the top ROE strategies share?
- What do the bottom performers in the cohort have in common in their losing trades?

## Output Format

Present findings as:
1. **Per-user summary** — 3–5 bullet points covering what worked, what didn't, and the standout trades
2. **Replicate** — numbered list of actionable patterns to copy
3. **Avoid** — numbered list of patterns that consistently lost
4. For top-N: add a **Cross-user edge** section summarising the shared winning patterns across the cohort

Do not recite the raw JSON. Lead with insight.

## API Dependencies

| Tool                           | Step | Purpose                           |
|--------------------------------|------|-----------------------------------|
| `user_resolve_usernames`       | 1    | Username → user ID resolution     |
| `arena_leaderboard`            | 1    | Top N user ID resolution          |
| `strategy_list`                | 2    | Fetch strategy wallets per user   |
| `discovery_get_trader_history` | 3    | Fetch closed positions per wallet |
| `audit_get_recent_actions`     | 4    | Fetch audit logs with AI reasoning|

## Verbose Mode

Set `ANALYZE_USER_TRADES_VERBOSE=1` to include debug fields (`userCount`, `resolvedUsers`) in output.
