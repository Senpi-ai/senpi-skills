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
  unknown          — live runtime whose scanner has not yet PROVEN itself with a tick (the runtime's
                     fail-closed verdict). Not sickness, not health — verify rather than assume.
  no-entry-scanners — runtime is UP but its entry scanners never wired ("running — NO ENTRY SCANNERS"
                     in runtime list): it cannot produce entry signals — broken wiring, not stopped.
  runtime-stopped  — ACTIVE strategy + runtime exists but not running
  no-runtime       — autonomous PACKAGE strategy (skillName, no trader) with NO runtime → funded but not
                     running (likely an interrupted deploy); the only no-runtime case that's an anomaly
  runtime-unknown  — openclaw is not on THIS host, so the runtime registry isn't visible from here;
                     NOT a diagnosis (run status.py on the runtime host for the real verdict)
  copy             — copy-trading strategy (follows a traderAddress) — run by Senpi's copy engine, no runtime
  manual           — manual / app-managed strategy — you manage it in the app, no runtime
and separately flags orphan runtimes (a runtime with no open strategy). A strategy off the runtime is NOT
broken — it's just not autonomous; status.py says how it's managed. Scanner health is fail-closed: an
external scanner never proven by a tick reads `unknown`, not `healthy` — `deploy.py verify <id>` remains
the deploy-time liveness gate.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
from mcp_client import MCPClient  # noqa: E402

_ICON = {"healthy": "✅", "running": "✅", "degraded": "⚠", "unhealthy": "❌", "unknown": "❔",
         "no-entry-scanners": "❌", "runtime-stopped": "⚠", "no-runtime": "⚠", "runtime-unknown": "·",
         "copy": "·", "manual": "·"}
_OK = ("healthy", "running")
_OFF_RUNTIME = ("copy", "manual")  # managed outside the runtime — not autonomous, not flagged
_MANAGED = {"copy": "copy-trading — followed by Senpi's copy engine (no runtime)",
            "manual": "manual — positions you manage in the app (no runtime)"}


def _funded(strat):
    v = _cli.dig(_cli.strategy_obj(strat), "totalFunded", "netFunded", "initialBudget")
    return f"${float(v):g}" if isinstance(v, (int, float)) else "?"


# Live (non-terminal) statuses — filtered server-side so we don't pull a long closed/failed history.
_LIVE_STATUSES = ["ACTIVE", "PAUSED", "CREATE_WALLET", "FUND_WALLET", "INITIALIZE_POSITIONS",
                  "SUBSCRIBE_TRADER", "CLOSING_POSITIONS"]


def _openclaw_available():
    rc, _o, _e = _cli.run_cli(["openclaw", "--version"], timeout=15)
    return rc == 0


def build(mcp, only_pkg=None, deep=True):
    opens = [s for s in _cli.list_strategies(mcp, statuses=_LIVE_STATUSES) if _cli.strategy_open(s)]
    # If openclaw isn't on THIS host, the runtime registry is simply not visible from here —
    # an empty list must NOT read as "no runtimes" (that turns a healthy remote-hosted fleet
    # into false 'interrupted deploy' alarms). Degrade to runtime-unknown instead.
    cli_ok = _openclaw_available()
    runtimes = _cli.list_runtimes() if cli_ok else []
    # ONE status --json for the whole fleet — only when runtimes actually exist (skip the flaky call otherwise)
    health_by_name = _cli.runtime_health_map() if (deep and runtimes) else {}
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
            entry = health_by_name.get(_cli.runtime_name(rt))  # from the single fleet-wide status --json
            if entry:  # upgrade process-level "running" to the runtime's own verdict (+ positions)
                health = _cli.health_verdict(entry) or "running"
                positions = _cli.active_positions(entry)
            if _cli.runtime_no_entry_scanners(rt):
                # positive wiring-failure evidence from the inventory itself ("running — NO ENTRY
                # SCANNERS"): the runtime is up but cannot produce entry signals — own class, not
                # "running" and not "runtime-stopped".
                health = "no-entry-scanners"
        elif rt:
            health = "runtime-stopped"
        elif _cli.strategy_trader(s):
            health = "copy"           # copy-trading: managed by the copy engine, no runtime expected
        elif skill:
            # SHOULD have a runtime. Without openclaw here we cannot see the registry — say so
            # honestly instead of diagnosing an interrupted deploy we can't verify.
            health = "no-runtime" if cli_ok else "runtime-unknown"
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
    return rows, orphans, cli_ok


def main(argv):
    ap = argparse.ArgumentParser(description="List the strategies you're running + their runtime health.")
    ap.add_argument("package", nargs="?", help="Filter to one strategy id (e.g. spider).")
    ap.add_argument("--fast", action="store_true",
                    help="Skip the per-runtime health check (status -r) — just running/stopped from the list.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    rows, orphans, cli_ok = build(MCPClient(), a.package, deep=not a.fast)

    if a.json:
        print(json.dumps({"strategies": rows, "orphan_runtimes": orphans,
                          "openclaw_available": cli_ok}, indent=2))
        return 0

    if not cli_ok:
        print("\nℹ openclaw is not available on this host — runtime state is UNKNOWN from here "
              "(run status.py on the runtime host for runtime health).")

    if not rows and not orphans:
        print("No open strategies." + (f" (filter: {a.package})" if a.package else ""))
        return 0

    by_pkg = defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    running = sum(1 for r in rows if r["health"] in _OK)
    idle = [r for r in rows if r["health"] == "no-runtime"]
    unknown = [r for r in rows if r["health"] == "runtime-unknown"]
    unproven = [r for r in rows if r["health"] == "unknown"]
    sick = [r for r in rows if r["health"] in ("degraded", "unhealthy", "runtime-stopped", "no-entry-scanners")]
    off = [r for r in rows if r["health"] in _OFF_RUNTIME]
    bits = [f"{running} autonomous (on runtime)"]
    if sick:
        bits.append(f"{len(sick)} degraded")
    if unproven:
        bits.append(f"{len(unproven)} unknown (not proven live)")
    if idle:
        bits.append(f"{len(idle)} funded-but-idle")
    if unknown:
        bits.append(f"{len(unknown)} runtime-unknown (no openclaw here)")
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
    if unproven:
        print("\n❔ Unknown (fail-closed — not proven live: scanner not yet proven by a tick, or reporting disabled; verify, don't assume):")
        for r in unproven:
            print(f"  - {r['package']} {r['runtime'] or ''} → "
                  f"`openclaw senpi status -r {r['runtime']}` / `deploy.py verify {r['package']}` to check")
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
