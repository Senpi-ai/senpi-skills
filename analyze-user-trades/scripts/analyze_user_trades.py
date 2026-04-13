#!/usr/bin/env python3
"""
analyze_user_trades.py

Fetches strategies, closed positions, and audit logs for one or more Senpi users,
then outputs a structured JSON analysis to stdout.

Usage:
  python3 scripts/analyze_user_trades.py --username alice
  python3 scripts/analyze_user_trades.py --user-id M123
  python3 scripts/analyze_user_trades.py --top-n 10
  python3 scripts/analyze_user_trades.py --username alice --start-time 2026-04-03T00:00:00Z --end-time 2026-04-09T23:59:59Z

Arguments:
  --username    Senpi username to resolve (mutually exclusive with --user-id / --top-n)
  --user-id     Senpi user ID, skips username resolution (mutually exclusive with the others)
  --top-n       Analyze top N arena agents by ROE (mutually exclusive with the others)
  --start-time  ISO 8601 start of analysis window (optional, defaults to current arena week)
  --end-time    ISO 8601 end of analysis window (optional, defaults to current arena week)

Environment:
  ANALYZE_USER_TRADES_VERBOSE=1  Include debug fields in output
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from analyze_user_trades_config import (
    mcporter_call,
    mcporter_call_safe,
    compute_week_boundaries,
    VERBOSE,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze Senpi user trades")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--username", type=str, help="Senpi username")
    group.add_argument("--user-id",  type=str, help="Senpi user ID (skips resolution)")
    group.add_argument("--top-n",    type=int, help="Analyze top N arena agents by ROE")
    parser.add_argument("--start-time", type=str, default=None)
    parser.add_argument("--end-time",   type=str, default=None)
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Step 1 — resolve user IDs
# ---------------------------------------------------------------------------

def resolve_users(args):
    """Return (users_list, error_str).

    users_list: [{ senpiUserId, senpiUserName, rank, roePct, totalPnl }]
    error_str:  non-None on failure
    """
    if args.user_id:
        return [
            {
                "senpiUserId":   args.user_id,
                "senpiUserName": None,
                "rank":          None,
                "roePct":        None,
                "totalPnl":      None,
            }
        ], None

    if args.username:
        data = mcporter_call("user_resolve_usernames", username=args.username)
        if not data:
            return None, f"No Senpi user found with username '{args.username}'"
        resolved_id = data.get("userId")
        if not resolved_id:
            return None, f"No Senpi user found with username '{args.username}'"
        return [
            {
                "senpiUserId":   resolved_id,
                "senpiUserName": args.username,
                "rank":          None,
                "roePct":        None,
                "totalPnl":      None,
            }
        ], None

    # top-n branch
    data = mcporter_call("arena_leaderboard", limit=args.top_n)
    if not data:
        return None, "arena_leaderboard returned no data"
    entries = data.get("leaderboard", [])
    users = [
        {
            "senpiUserId":   entry["senpiUserId"],
            "senpiUserName": entry.get("senpiUserName"),
            "rank":          entry.get("rank"),
            "roePct":        entry.get("roePct"),
            "totalPnl":      entry.get("totalPnl"),
        }
        for entry in entries
    ]
    return users, None


# ---------------------------------------------------------------------------
# Step 2 — fetch strategies
# ---------------------------------------------------------------------------

def fetch_strategies(user_id):
    data = mcporter_call_safe("strategy_list", userIds=[user_id])
    if not data:
        return []
    strategies = data.get("strategies", [])
    return [
        {
            "strategyId": s.get("strategyWalletId"),
            "address":    s.get("strategyWalletAddress"),
            "status":     s.get("status"),
            "skillName":  s.get("skillName"),
            "createdAt":  s.get("createdAt"),
        }
        for s in strategies
    ]


# ---------------------------------------------------------------------------
# Step 3 — fetch closed positions
# ---------------------------------------------------------------------------

def fetch_orders(address, start_time, end_time):
    data = mcporter_call_safe(
        "discovery_get_trader_history",
        trader_address=address,
        sort_by="CLOSED_TIME",
        sort_direction="DESC",
        latest=True,
        limit=50,
    )
    if not data:
        return []
    positions = data.get("closedPositions", [])
    filtered = [
        p for p in positions
        if p.get("closeTime") and start_time <= p["closeTime"] <= end_time
    ]
    return [
        {
            "coin":        p.get("coin"),
            "entryPx":     p.get("entryPx"),
            "exitPx":      p.get("exitPx"),
            "leverage":    p.get("leverage"),
            "openTime":    p.get("openTime"),
            "closeTime":   p.get("closeTime"),
            "szi":         p.get("szi"),
            "realizedPnl": p.get("realizedPnl"),
            "totalFees":   p.get("totalFees"),
        }
        for p in filtered
    ]


# ---------------------------------------------------------------------------
# Step 4 — fetch audit logs
# ---------------------------------------------------------------------------

def fetch_audit_logs(user_id, start_time, end_time):
    data = mcporter_call_safe(
        "audit_get_recent_actions",
        user_ids=[user_id],
    )
    if not data:
        return []
    logs = data.get("auditLogs", [])
    # Filter client-side — tool does not accept time range params
    filtered = [
        log for log in logs
        if log.get("timestamp") and start_time <= log["timestamp"] <= end_time
    ]
    return [
        {
            "tool":         log.get("toolName"),
            "ai_reasoning": log.get("aiReasoning"),
            "timestamp":    log.get("timestamp"),
        }
        for log in filtered
    ]


# ---------------------------------------------------------------------------
# Enrich one user
# ---------------------------------------------------------------------------

def analyze_user(user, start_time, end_time):
    user_id    = user["senpiUserId"]
    strategies = fetch_strategies(user_id)

    def enrich_strategy(strategy):
        address = strategy["address"]
        with ThreadPoolExecutor(max_workers=2) as ex:
            orders_f = ex.submit(fetch_orders,     address, start_time, end_time)
            audit_f  = ex.submit(fetch_audit_logs, user_id, start_time, end_time)
        strategy["orders"]    = orders_f.result()
        strategy["audit_log"] = audit_f.result()
        return strategy

    with ThreadPoolExecutor(max_workers=5) as ex:
        enriched = list(ex.map(enrich_strategy, strategies))

    return {
        "senpiUserName": user["senpiUserName"],
        "senpiUserId":   user_id,
        "rank":          user["rank"],
        "roePct":        user["roePct"],
        "totalPnl":      user["totalPnl"],
        "strategies":    enriched,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    start_time = args.start_time
    end_time   = args.end_time
    if not start_time or not end_time:
        start_time, end_time = compute_week_boundaries(week_offset=0)

    try:
        users, err = resolve_users(args)
    except RuntimeError as e:
        print(json.dumps({"success": False, "error": str(e), "actionable": False}))
        sys.exit(0)

    if err:
        print(json.dumps({"success": False, "error": err, "actionable": False}))
        sys.exit(0)

    if len(users) == 1:
        results = [analyze_user(users[0], start_time, end_time)]
    else:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(analyze_user, u, start_time, end_time): u for u in users}
            results = [f.result() for f in as_completed(futures)]
        results.sort(key=lambda r: r["rank"] if r["rank"] is not None else 9999)

    output = {
        "success":   True,
        "startTime": start_time,
        "endTime":   end_time,
        "results":   results,
    }

    if VERBOSE:
        output["debug"] = {"userCount": len(results), "resolvedUsers": users}

    print(json.dumps(output))


if __name__ == "__main__":
    main()
