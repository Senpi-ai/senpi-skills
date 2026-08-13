#!/usr/bin/env python3
"""What strategies am I running? — the single source of truth, with runtime health.

  python3 status.py            # every open strategy, grouped by package
  python3 status.py <id>       # only the <id> package
  python3 status.py --json

Reads live truth (MCP strategy_list ∪ openclaw runtime list) — NOT the ephemeral deploy state. If that
strategy read cannot be made, this REFUSES (non-zero) instead of printing "No open strategies." — an
unread list is not an empty one, and this is the surface money decisions are checked against. For each
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
external scanner never proven by a tick reads `unknown`, not `healthy` — prove it on a READ-ONLY surface
(`openclaw senpi scanner -r <rt>`, `openclaw senpi status|state -r <rt>`, `openclaw senpi deploy status`
for the last deploy's verdict, `deploy.py verify <id>` for the per-instance verdict over the same
surfaces). Everything this script prints is read-only. The RESUME — `deploy.py runtime <id>` — is the
one money path near this surface: it runs the deploy verb and can install, start trading, and fund a
wallet. It is named once, with what it does, never per row.
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


def _pkg_label(r):
    """The package cell for display, never a data field. `r["package"]` is `None` in the row data
    for a strategy attributed to no package (unattributed / copy-trading / manual) — only here does
    that become the human-readable placeholder, so `--json` consumers see the honest `null`."""
    return r["package"] or "(not on runtime)"


def _name_cell(r):
    """The strategy's OWN name for the text table, starred when it is a FALLBACK.

    The identity a reader takes away from this surface must be the strategy's, not the runtime's:
    printing only the runtime name is what sent an agent grepping for `owl-main` to the wrong wallet
    (the deployed strategy there is called `owl`). `name_source` is the field that answered, so a name
    the record did not actually carry (`tradingStrategyName` — the PACKAGE id, identical across every
    instance) is marked `*` and explained once below the table, instead of being presented as a name
    this wallet proved."""
    n = str(r.get("name") or "strategy")
    return n if r.get("name_source") == "strategyName" else n + "*"


def _funded(r):
    """The funded cell. `_cli.strategy_funded` is None when the strategy record carried no
    `totalFunded`/`netFunded` — an amount nobody read is `unknown`, never the requested budget
    (which is what `initialBudget` holds) dressed up as money that landed."""
    return r["funded"] or "unknown"


def _openclaw_available():
    rc, _o, _e = _cli.run_cli(["openclaw", "--version"], timeout=15)
    return rc == 0


def _read_or_refuse(rows, why):
    """The strategy inventory, or a refusal — never an empty list standing in for an unread one.

    The same line `close.py`'s `_read_or_refuse` draws, on the surface every money decision now
    routes through: `deploy.py verify`'s collision and ambiguous/PAUSED/no-runtime triage, the
    budget warns and the taxonomy all send the reader HERE before they decide whether to fund
    anything. The fail-OPEN reader degrades a transport error — or a renamed key in the payload —
    to `[]`, which this script prints as "No open strategies." and exits **0**: a positive
    all-clear over wallets that may be live and funded, read immediately before someone funds
    another beside them. A genuinely empty list is an ANSWER and still exits 0; only `None`
    refuses."""
    if rows is not None:
        return rows
    raise SystemExit(
        "error: could not read the strategy list, so NOTHING was read here and nothing about what "
        "you are running is known — this is not 'no open strategies'.\n"
        f"  Cause: {why[0] if why else 'no cause reported'}\n"
        "  Your strategies may be live and funded — do not fund, deploy or close anything off this "
        "run. Fix the cause and re-run:  python3 status.py")


def build(mcp, only_pkg=None, deep=True):
    why = []
    strategies = _read_or_refuse(
        _cli.list_strategies_or_none(mcp, statuses=_cli.LIVE_STATUSES, why=why), why)
    opens = [s for s in strategies if _cli.strategy_open(s)]
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
        # Same producer as the teardown filter (`_cli.strategies_for`): the stamp is written from
        # `pkg.id` verbatim and read back case-normalized, so an exact compare renders NO rows for a
        # package whose wallets are live — and this read is the first triage step every refusal names.
        if only_pkg and not _cli.strategy_skill_match(skill, only_pkg):
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
        # `name`/`name_source`: the strategy's OWN name (never the runtime's — that stays in
        # `runtime` below), read exactly as `senpi-portfolio/scripts/portfolio.py`'s
        # `_strategy_name_and_source` does, so the two surfaces never disagree about what a wallet
        # is called. `package` is a real data value (`None`, not a display string) — the
        # "(not on runtime)" placeholder belongs at the render site, not in the row.
        name, name_source = _cli.strategy_name_and_source(s)
        rows.append({"package": skill or None, "is_pkg": bool(skill),
                     "name": name, "name_source": name_source,
                     "strategyId": _cli.strategy_id_of(s), "wallet": wallet,
                     "status": _cli.strategy_status(s), "funded": _cli.strategy_funded(s),
                     "positions": positions,
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
        by_pkg[_pkg_label(r)].append(r)
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
            pos = f"  {r['positions']} pos" if isinstance(r.get("positions"), int) else ""
            # The runtime name is LABELLED as the runtime's, never left standing in the identity column
            # unmarked. The strategy's own name takes that column: it is the one an agent greps for, and
            # the one that maps to a wallet. Of the two ids the runtime id is the one a human needs least
            # here — the triage lines below hand it back, with the command it belongs to.
            rt = f"  · runtime {r['runtime']}" if r["runtime"] else ""
            print(f"  {_ICON.get(r['health'], ' ')} {r['health']:<15} {_name_cell(r):<22} "
                  f"{r['wallet'][:10]}…  {_funded(r):>8}  [{(r['strategyId'] or '')[:8]}]{pos}{rt}")
    if any(r.get("name_source") != "strategyName" for r in rows):
        print("\nℹ `*` after a name means the strategy record carried NO name of its own — what's shown "
              "is its package id (every instance of that package renders the same), not a name this "
              "wallet proved. Identify those by wallet / strategyId, never by the starred name.")
    if any(r["funded"] is None for r in rows):
        print("\nℹ `unknown` in the funded column means the strategy record carried no funded amount "
              "(totalFunded/netFunded) — NOT $0, and never the budget that was requested. Read what "
              "actually landed on the wallet in the app before acting on the number.")
    # Every command emitted below is READ-ONLY. This is the surface agents are sent to for monitoring,
    # so a per-row `deploy.py runtime <pkg>` here would be a copy-pasteable money path: on a funded
    # wallet with no runtime it installs and starts trading. The resume escape is named once, with
    # what it does. (`deploy.py verify <id>` is read-only and safe to name anywhere — it is the check.)
    if sick:
        print("\n⚠ Degraded (runtime up but not operating cleanly):")
        for r in sick:
            print(f"  - {_pkg_label(r)} {r['runtime'] or ''} → "
                  f"`openclaw senpi status -r {r['runtime']} --json` / "
                  f"`openclaw senpi scanner -r {r['runtime']}` to triage (read-only)")
        print("  Deliberately resuming/reinstalling one? That is `deploy.py runtime <id>` — it runs the "
              "deploy verb and can move money (install + start trading; create+fund with --budget). "
              "Triage first.")
    if unproven:
        print("\n❔ Unknown (fail-closed — not proven live: scanner not yet proven by a tick, or reporting disabled; verify, don't assume):")
        for r in unproven:
            print(f"  - {_pkg_label(r)} {r['runtime'] or ''} → "
                  f"`openclaw senpi scanner -r {r['runtime']}` / "
                  f"`openclaw senpi status -r {r['runtime']} --json` to check (read-only); "
                  f"`openclaw senpi deploy status` for the last deploy's verdict")
    if idle:
        print("\n⚠ Autonomous strategy with NO runtime (funded but not running — likely an interrupted deploy):")
        for r in idle:
            print(f"  - {r['package']} {r['wallet'][:10]}… ({_funded(r)}) → "
                  f"`deploy.py runtime {r['package']}` to start it (runs the deploy verb: it installs and "
                  f"starts trading this funded wallet), or `close.py {r['package']}` to recover funds")
    if off:
        print("\nℹ Not on a runtime — managed outside autonomous trading (this is normal):")
        for r in off:
            print(f"  - {_pkg_label(r)} {r['wallet'][:10]}… ({_funded(r)}): "
                  f"{_MANAGED.get(r['health'], r['health'])}")
    if orphans:
        print("\n⚠ Orphan runtimes (no active strategy — safe to delete):")
        for o in orphans:
            print(f"  - {o['runtime']} ({str(o['wallet'] or '')[:10]}…) → `openclaw senpi runtime delete {o['runtime']}`")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
