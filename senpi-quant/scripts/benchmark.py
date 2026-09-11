#!/usr/bin/env python3
"""Recompute references/benchmark.json — the whale medians the desk compares a trader against.

Cohort: the largest profitable accounts on Hyperliquid's public leaderboard (≥ $1M account value, positive
month and all-time PnL, month ROI ≥ 5%), read through the same round-trip engine as any trader. Serial,
with backoff — the public API rate-limits parallel whale reads.

  python3 benchmark.py [--n 40] [--days 90] [--out ../references/benchmark.json]
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
from roundtrips import episodes_from_fills  # noqa: E402

KEYS = ("win_rate", "profit_factor", "hold_winners_h", "hold_losers_h", "hold_ratio", "cost_ratio", "taker_share", "size_cv", "payoff_ratio", "long_share")


def member(hl, addr, days):
    win_start = hl.now_ms - days * hl_api.DAY_MS
    start = hl.now_ms - (days + 60) * hl_api.DAY_MS
    fills = hl_api.merge_fills(hl.fills(addr, start), hl.twap_slices(addr, start))
    closed, opened = episodes_from_fills(fills)
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
    cohort = hl_api.public_cohort(hl.leaderboard(), n=a.n * 3)   # candidates; accounts with no fills are skipped
    members, t0 = [], time.time()
    for i, addr in enumerate(cohort):
        if len(members) >= a.n:
            break
        try:
            m = member(hl, addr, a.days)
            if not m["fills"]:
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
    bench.update(n=len(valid), cohort=f"top-{a.n} profitable accounts ≥ $1M on Hyperliquid's public leaderboard (month & all-time PnL > 0, month ROI ≥ 5%)",
                 computed_at=time.strftime("%Y-%m-%d"), window_days=a.days, engine="senpi-quant roundtrips/metrics", seconds=round(time.time() - t0))
    with open(a.out, "w") as fh:
        json.dump({"benchmark": bench, "members": members}, fh, indent=1)
    print(json.dumps(bench, indent=1))


if __name__ == "__main__":
    main()
