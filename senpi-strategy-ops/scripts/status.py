#!/usr/bin/env python3
"""What strategies am I running? — the single source of truth, with runtime health.

  python3 status.py            # every open strategy, grouped by package
  python3 status.py <id>       # only the <id> package
  python3 status.py --json

Reads live truth (MCP strategy_list ∪ openclaw runtime list) — NOT the ephemeral deploy state. For each
OPEN strategy it classifies the runtime:
  healthy/degraded/unhealthy — ACTIVE strategy + live runtime, upgraded from process-level "running" to
                               the runtime's OWN verdict via `openclaw senpi status -r <id>` (+ position
                               count). `--fast` skips this per-runtime call and just reports `running`.
  runtime-stopped  — ACTIVE strategy + runtime exists but not running
  no-runtime       — autonomous PACKAGE strategy (skillName, no trader) with NO runtime → funded but not
                     running (likely an interrupted deploy); the only no-runtime case that's an anomaly
  copy             — copy-trading strategy (follows a traderAddress) — run by Senpi's copy engine, no runtime
  manual           — manual / app-managed strategy — you manage it in the app, no runtime
and separately flags orphan runtimes (a runtime with no open strategy). A strategy off the runtime is NOT
broken — it's just not autonomous; status.py says how it's managed. `healthy` ≠ a confirmed scanner tick —
use `deploy.py verify <id>` for that.
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

_ICON = {"healthy": "✅", "running": "✅", "degraded": "⚠", "unhealthy": "❌",
         "runtime-stopped": "⚠", "no-runtime": "⚠", "copy": "·", "manual": "·"}
_OK = ("healthy", "running")
_OFF_RUNTIME = ("copy", "manual")  # managed outside the runtime — not autonomous, not flagged
_MANAGED = {"copy": "copy-trading — followed by Senpi's copy engine (no runtime)",
            "manual": "manual — positions you manage in the app (no runtime)"}


def _funded(strat):
    v = _cli.dig(_cli.strategy_obj(strat), "totalFunded", "netFunded", "initialBudget")
    return f"${float(v):g}" if isinstance(v, (int, float)) else "?"


def build(mcp, only_pkg=None, deep=True):
    opens = [s for s in _cli.list_strategies(mcp) if _cli.strategy_open(s)]
    runtimes = _cli.list_runtimes()
    matched_rt = set()  # runtime names already matched to a strategy
    rows = []
    for s in opens:
        skill = _cli.strategy_skill(s)
        if only_pkg and skill != only_pkg:
            continue
        wallet = str(_cli.strategy_wallet(s) or "")
        rt = next((r for r in runtimes if _cli.wallet_match(_cli.runtime_wallet(r), wallet)), None)
        if rt:
            matched_rt.add(_cli.runtime_name(rt))
        # A strategy with no runtime is NOT inherently broken — it's just not an autonomous runtime
        # strategy. Explain it by type: copy-trading (follows a trader, run by the copy engine) or manual
        # (managed in the app). Only an autonomous PACKAGE strategy (skillName, no trader) is expected to
        # have a runtime — a missing one there is the real anomaly.
        positions = None
        if rt and _cli.runtime_running(rt):
            health = "running"
            if deep:  # upgrade process-level "running" to the runtime's own verdict (+ positions)
                sj = _cli.runtime_status(_cli.runtime_name(rt))
                health = _cli.health_verdict(sj) or "running"
                positions = _cli.active_positions(sj)
        elif rt:
            health = "runtime-stopped"
        elif _cli.strategy_trader(s):
            health = "copy"           # copy-trading: managed by the copy engine, no runtime expected
        elif skill:
            health = "no-runtime"     # autonomous package strategy that SHOULD have a runtime but doesn't
        else:
            health = "manual"         # manual / app-managed position, no runtime expected
        rows.append({"package": skill or "(not on runtime)", "is_pkg": bool(skill),
                     "strategyId": _cli.strategy_id_of(s), "wallet": wallet,
                     "status": _cli.strategy_status(s), "funded": _funded(s), "positions": positions,
                     "runtime": _cli.runtime_name(rt) if rt else None, "health": health})
    # runtimes with no matching OPEN strategy (orphans — trading nothing / on a gone wallet)
    orphans = [{"runtime": _cli.runtime_name(r), "wallet": _cli.runtime_wallet(r),
                "running": _cli.runtime_running(r)}
               for r in runtimes if _cli.runtime_name(r) not in matched_rt
               and (not only_pkg or str(_cli.runtime_name(r) or "").startswith(only_pkg + "-"))]
    return rows, orphans


def main(argv):
    ap = argparse.ArgumentParser(description="List the strategies you're running + their runtime health.")
    ap.add_argument("package", nargs="?", help="Filter to one strategy id (e.g. spider).")
    ap.add_argument("--fast", action="store_true",
                    help="Skip the per-runtime health check (status -r) — just running/stopped from the list.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    rows, orphans = build(MCPClient(), a.package, deep=not a.fast)

    if a.json:
        print(json.dumps({"strategies": rows, "orphan_runtimes": orphans}, indent=2))
        return 0

    if not rows and not orphans:
        print("No open strategies." + (f" (filter: {a.package})" if a.package else ""))
        return 0

    by_pkg = defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    running = sum(1 for r in rows if r["health"] in _OK)
    idle = [r for r in rows if r["health"] == "no-runtime"]
    sick = [r for r in rows if r["health"] in ("degraded", "unhealthy", "runtime-stopped")]
    off = [r for r in rows if r["health"] in _OFF_RUNTIME]
    bits = [f"{running} autonomous (on runtime)"]
    if sick:
        bits.append(f"{len(sick)} degraded")
    if idle:
        bits.append(f"{len(idle)} funded-but-idle")
    if off:
        bits.append(f"{len(off)} managed off-runtime")
    print(f"\nYou have {len(rows)} open strateg{'y' if len(rows) == 1 else 'ies'} ({', '.join(bits)}):")
    for pkg in sorted(by_pkg):
        print(f"\n{pkg}")
        for r in by_pkg[pkg]:
            rt = r["runtime"] or "(none)"
            pos = f"  {r['positions']} pos" if isinstance(r.get("positions"), int) else ""
            print(f"  {_ICON.get(r['health'], ' ')} {r['health']:<15} {rt:<16} "
                  f"{r['wallet'][:10]}…  {r['funded']:>8}  [{(r['strategyId'] or '')[:8]}]{pos}")
    if sick:
        print("\n⚠ Degraded (runtime up but not operating cleanly):")
        for r in sick:
            print(f"  - {r['package']} {r['runtime'] or ''} → "
                  f"`openclaw senpi status -r {r['runtime']}` / `deploy.py verify {r['package']}` to triage")
    if idle:
        print("\n⚠ Autonomous strategy with NO runtime (funded but not running — likely an interrupted deploy):")
        for r in idle:
            print(f"  - {r['package']} {r['wallet'][:10]}… ({r['funded']}) → "
                  f"`deploy.py runtime {r['package']}` to start it, or `close.py {r['package']}` to recover funds")
    if off:
        print("\nℹ Not on a runtime — managed outside autonomous trading (this is normal):")
        for r in off:
            print(f"  - {r['package']} {r['wallet'][:10]}… ({r['funded']}): {_MANAGED.get(r['health'], r['health'])}")
    if orphans:
        print("\n⚠ Orphan runtimes (no active strategy — safe to delete):")
        for o in orphans:
            print(f"  - {o['runtime']} ({str(o['wallet'] or '')[:10]}…) → `openclaw senpi runtime delete {o['runtime']}`")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
