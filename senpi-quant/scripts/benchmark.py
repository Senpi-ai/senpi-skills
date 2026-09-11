#!/usr/bin/env python3
"""Recompute references/benchmark.json — the whale medians the desk compares a trader against.

With `SENPI_AUTH_TOKEN` (the only source that works for whales): the cohort is Senpi discovery's ≥ $1M-realized
ranking and each member's closed positions come from `discovery_get_trader_history` — complete. Without a
token the script falls back to the public leaderboard's large accounts read through the public engine, but
whales are TWAP-heavy and the public endpoints keep only the most recent slices, so their round trips come
back 0–1 per wallet: that run is diagnostic only, and the desk hides the whale-median table when the
benchmark has fewer than 5 members with ≥ 10 trades.

  SENPI_AUTH_TOKEN=… python3 benchmark.py [--n 40] [--days 90] [--out ../references/benchmark.json]
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hl_api  # noqa: E402
import metrics  # noqa: E402
import senpi_history  # noqa: E402
import smart_money  # noqa: E402
from roundtrips import episodes_from_fills  # noqa: E402

KEYS = ("win_rate", "profit_factor", "hold_winners_h", "hold_losers_h", "hold_ratio", "cost_ratio", "taker_share", "size_cv", "payoff_ratio", "long_share")


def member(hl, addr, days, mcp=None):
    win_start = hl.now_ms - days * hl_api.DAY_MS
    start = hl.now_ms - (days + 60) * hl_api.DAY_MS
    fills = hl_api.merge_fills(hl.fills(addr, start), hl.twap_slices(addr, start))
    closed, opened = episodes_from_fills(fills)
    if mcp is not None:
        rows = senpi_history.fetch(mcp, addr, win_start, {})
        if rows:
            closed = rows
    tr = metrics.track_record(closed, opened, hl.funding(addr, win_start), hl.info({"type": "userFees", "user": addr}), win_start)
    out = {k: tr.get(k) for k in KEYS}
    out.update(trades=tr["trades"], complete_trades=tr["complete_trades"], liquidations=tr["liquidations"], fills=len(fills))
    out["coverage"] = metrics.coverage(closed, opened)["overall"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40); ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "references", "benchmark.json"))
    a = ap.parse_args(argv)
    hl = hl_api.HL(timeout=90)
    mcp = None
    if os.environ.get("SENPI_AUTH_TOKEN"):
        from mcp_client import MCPClient
        mcp = MCPClient()
        cohort = smart_money.senpi_cohort(mcp, {})[: a.n * 2]
        print(f"cohort: Senpi discovery, {len(cohort)} wallets with ≥ $1M realized", file=sys.stderr)
    else:
        cohort = hl_api.public_cohort(hl.leaderboard(), n=a.n * 3)   # candidates; accounts with no fills are skipped
        print("cohort: public leaderboard — diagnostic only for whales (see the docstring)", file=sys.stderr)
    members, t0 = [], time.time()
    for i, addr in enumerate(cohort):
        if len(members) >= a.n:
            break
        try:
            m = member(hl, addr, a.days, mcp)
            if not m["fills"] and not m["trades"]:
                print(f"{i + 1:2d} no fills — skipped", file=sys.stderr, flush=True)
                continue
            members.append(m)
            print(f"{len(members):2d}/{a.n} trades={m['trades']:3d} complete={m['complete_trades']:3d} coverage={m['coverage']}", file=sys.stderr, flush=True)
        except hl_api.HLError as e:
            print(f"{i + 1:2d} failed: {e}", file=sys.stderr, flush=True)
        time.sleep(0.5)
    valid = [m for m in members if m["trades"] >= 10]
    def med(k):
        xs = [m[k] for m in valid if m[k] is not None and m[k] != float("inf")]
        return statistics.median(xs) if xs else None
    bench = {k: med(k) for k in KEYS}
    bench.update(n=len(valid), cohort=("Senpi discovery — wallets with ≥ $1M realized" if mcp is not None else f"top-{a.n} profitable accounts ≥ $1M on Hyperliquid's public leaderboard"),
                 computed_at=time.strftime("%Y-%m-%d"), window_days=a.days, engine="senpi-quant roundtrips/metrics", seconds=round(time.time() - t0))
    with open(a.out, "w") as fh:
        json.dump({"benchmark": bench, "members": members}, fh, indent=1)
    print(json.dumps(bench, indent=1))


if __name__ == "__main__":
    main()
