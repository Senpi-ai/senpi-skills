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
  audit_get_recent_actions, audit_get_strategy_history, audit_query,
  arena_leaderboard
metadata:
  author: senpi
  version: "1.0.0"
  platform: senpi
  exchange: hyperliquid
---

# analyze-user-trades

Analyzes the trading activity of any Senpi user or arena participant on demand.

## When to use

Use when asked to:
- Analyze trades, strategies, or orders of a specific user (e.g. "analyze @alice's trades")
- Review what the top N arena agents are doing this week
- Understand what strategies a user is running and why

## Architecture

```
Agent receives user request
        │
        ▼
SKILL.md loaded → agent invokes analyze_user_trades.py with resolved params
        │
        ▼
Step 1: Resolve user ID(s)
   ├── username provided → user_resolve_usernames(username)
   ├── userId provided   → use directly, skip resolution
   └── topN provided     → arena_leaderboard(limit=topN) → extract senpiUserIds
        │
        ▼
Step 2: Fetch strategies per user (parallelized)
        strategy_list(userIds=[senpiUserId])
        → extract strategyWalletAddress, status, skillName, createdAt
        │
        ▼
Step 3: Fetch closed positions per strategy wallet (parallelized)
        discovery_get_trader_history(
          trader_address=strategyWalletAddress,
          sort_by=CLOSED_TIME,
          sort_direction=DESC,
          latest=true,
          limit=50
        )
        → filter client-side by startTime/endTime
        │
        ▼
Step 4: Fetch audit logs per strategy (parallelized)
        audit_get_recent_actions
        user_ids=[senpiUserId]
        → filter by startTime/endTime
        → extract tool, ai_reasoning, timestamp
        │
        ▼
Output: structured JSON per user → agent presents analysis
```

## Quick Start

1. Identify the input type: username, user ID, or top N request
2. Determine the time range (default: current arena week)
3. Run `python3 scripts/analyze_user_trades.py` with the appropriate arguments
4. Parse the JSON output and present the analysis

## Invocation

Pass parameters as command-line arguments:

| Argument        | When to use                                      |
|-----------------|--------------------------------------------------|
| `--username`    | User provided a @username                        |
| `--user-id`     | You already have the internal user ID            |
| `--top-n`       | User asked for top N arena agents                |
| `--start-time`  | Custom time range start (ISO 8601)               |
| `--end-time`    | Custom time range end (ISO 8601)                 |

`--username`, `--user-id`, and `--top-n` are mutually exclusive — pass exactly one.
If `--start-time`/`--end-time` are omitted, the script defaults to the current arena week.
See `references/arena-week-cycle.md` for the week cycle.

**Examples:**
```
python3 scripts/analyze_user_trades.py --username alice
python3 scripts/analyze_user_trades.py --user-id M123 --start-time 2026-04-03T00:00:00Z --end-time 2026-04-09T23:59:59Z
python3 scripts/analyze_user_trades.py --top-n 10
```

## Processing Order

1. Determine the correct arguments from the user's request
2. Run `python3 scripts/analyze_user_trades.py` with those arguments
3. Parse the JSON output
4. If `success=false`: surface the error message to the user
5. If `success=true`: present the results — strategies, orders, skillName, and ai_reasoning from audit logs

## API Dependencies

| Tool                           | Step | Purpose                              |
|--------------------------------|------|--------------------------------------|
| `user_resolve_usernames`       | 1    | Username → user ID resolution        |
| `arena_leaderboard`            | 1    | Top N user ID resolution             |
| `strategy_list`                | 2    | Fetch strategy wallets per user      |
| `discovery_get_trader_history` | 3    | Fetch closed positions per wallet    |
| `audit_get_recent_actions`     | 4    | Fetch audit logs per user            |

## Known Limitations

- `rank`, `roePct`, and `totalPnl` are only available when entry point is top N
  (resolved via arena_leaderboard). They are null for username/userId lookups.
- Order history is limited to the 50 most recent closed positions per strategy
  wallet before client-side time filtering.
- Audit log access requires the target user's `senpiUserId` — not available
  for users with no strategies.

## Verbose Mode

Set `ANALYZE_USER_TRADES_VERBOSE=1` to include debug fields (`userCount`, `resolvedUsers`) in output.
