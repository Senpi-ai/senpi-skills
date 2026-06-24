#!/usr/bin/env python3
"""What strategies am I running? — the single source of truth, with runtime health.

  python3 status.py            # every open strategy, grouped by package
  python3 status.py <id>       # only the <id> package
  python3 status.py --json

Reads live truth (MCP strategy_list ∪ openclaw runtime list) — NOT the ephemeral deploy state. For each
OPEN strategy it classifies the runtime:
  running          — ACTIVE strategy + a live runtime (actually trading)
  runtime-stopped  — ACTIVE strategy + runtime exists but not running
  no-runtime       — ACTIVE strategy with NO runtime → funded but IDLE (orphaned, or never `runtime`'d)
and separately flags orphan runtimes (a runtime with no open strategy). `running` ≠ ticking — use
`deploy.py verify <id>` to confirm scanners have fired.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
from _mcp import MCPClient  # noqa: E402

_ICON = {"running": "✅", "runtime-stopped": "⚠", "no-runtime": "⚠", "external": "·"}


def _funded(strat):
    v = _cli.dig(_cli.strategy_obj(strat), "totalFunded", "netFunded", "initialBudget")
    return f"${float(v):g}" if isinstance(v, (int, float)) else "?"


def build(mcp, only_pkg=None):
    opens = [s for s in _cli.list_strategies(mcp) if _cli.strategy_open(s)]
    runtimes = _cli.list_runtimes()
    rt_by_wallet = {str(_cli.runtime_wallet(r) or "").lower(): r for r in runtimes}
    matched = set()
    rows = []
    for s in opens:
        skill = _cli.strategy_skill(s)
        if only_pkg and skill != only_pkg:
            continue
        wallet = str(_cli.strategy_wallet(s) or "")
        rt = rt_by_wallet.get(wallet.lower())
        if rt:
            matched.add(wallet.lower())
        # Only PACKAGE strategies (skillName set) run on a Senpi runtime; a copy/manual strategy with no
        # runtime is normal ("external"), NOT idle/broken.
        if not skill:
            health = "external"
        elif not rt:
            health = "no-runtime"
        elif _cli.runtime_running(rt):
            health = "running"
        else:
            health = "runtime-stopped"
        rows.append({"package": skill or "(copy / manual)", "is_pkg": bool(skill),
                     "strategyId": _cli.strategy_id_of(s), "wallet": wallet,
                     "status": _cli.strategy_status(s), "funded": _funded(s),
                     "runtime": _cli.runtime_name(rt) if rt else None, "health": health})
    # runtimes with no matching OPEN strategy (orphans — trading nothing / on a gone wallet)
    orphans = [{"runtime": _cli.runtime_name(r), "wallet": _cli.runtime_wallet(r),
                "running": _cli.runtime_running(r)}
               for r in runtimes if str(_cli.runtime_wallet(r) or "").lower() not in matched
               and (not only_pkg or str(_cli.runtime_name(r) or "").startswith(only_pkg + "-"))]
    return rows, orphans


def main(argv):
    ap = argparse.ArgumentParser(description="List the strategies you're running + their runtime health.")
    ap.add_argument("package", nargs="?", help="Filter to one strategy id (e.g. spider).")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    rows, orphans = build(MCPClient(), a.package)

    if a.json:
        print(json.dumps({"strategies": rows, "orphan_runtimes": orphans}, indent=2))
        return 0

    if not rows and not orphans:
        print("No open strategies." + (f" (filter: {a.package})" if a.package else ""))
        return 0

    by_pkg = defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    running = sum(1 for r in rows if r["health"] == "running")
    idle = [r for r in rows if r["health"] == "no-runtime"]
    ext = sum(1 for r in rows if r["health"] == "external")
    bits = [f"{running} running"]
    if idle:
        bits.append(f"{len(idle)} funded-but-idle")
    if ext:
        bits.append(f"{ext} copy/manual")
    print(f"\nYou have {len(rows)} open strateg{'y' if len(rows) == 1 else 'ies'} ({', '.join(bits)}):")
    for pkg in sorted(by_pkg):
        print(f"\n{pkg}")
        for r in by_pkg[pkg]:
            rt = r["runtime"] or "(none)"
            print(f"  {_ICON.get(r['health'], ' ')} {r['health']:<15} {rt:<16} "
                  f"{r['wallet'][:10]}…  {r['funded']:>8}  [{(r['strategyId'] or '')[:8]}]")
    if idle:
        print("\n⚠ Funded but NOT trading (ACTIVE strategy, no runtime):")
        for r in idle:
            print(f"  - {r['package']} {r['wallet'][:10]}… ({r['funded']}) → "
                  f"`deploy.py runtime {r['package']}` to start it, or `close.py {r['package']}` to recover funds")
    if orphans:
        print("\n⚠ Orphan runtimes (no active strategy — safe to delete):")
        for o in orphans:
            print(f"  - {o['runtime']} ({str(o['wallet'] or '')[:10]}…) → `openclaw senpi runtime delete {o['runtime']}`")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
