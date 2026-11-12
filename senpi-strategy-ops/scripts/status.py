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
                     fail-closed verdict), or whose health read failed (its cause is printed; 9 if
                     killed). Not sickness, not health — verify rather than assume.
  no-entry-scanners — runtime is UP but its entry scanners never wired ("running — NO ENTRY SCANNERS"
                     in runtime list): it cannot produce entry signals — broken wiring, not stopped.
  runtime-stopped  — ACTIVE strategy + runtime exists but not running
  no-runtime       — autonomous PACKAGE strategy (skillName, no trader) with NO runtime → funded but not
                     running (likely an interrupted deploy); the only no-runtime case that's an anomaly
  runtime-unknown  — the runtime registry can't be read from THIS host (no openclaw, or the
                     `senpi.listRuntimes` read failed or timed out); NOT a diagnosis (re-run, or run
                     status.py on the runtime host). The run then exits 1, never 0
  copy             — copy-trading strategy (follows a traderAddress) — run by Senpi's copy engine, no runtime
  manual           — manual / app-managed strategy — you manage it in the app, no runtime
Two more facts ride every deep row, because health alone misleads on both:
  paused           — the runtime's OWN risk gate holds entries (daily cap, loss halt, cooldown) — health
                     stays healthy, nothing opens, and the gate's reason + reset are printed verbatim.
                     By design, not a fault; never redeploy to clear it.
  config_drift     — the recipe the runtime is RUNNING (its rendered descriptor) differs from the
                     package on disk: an edit that was never applied. Printed with the in-place fix.
and separately flags orphan runtimes (a runtime with no open strategy). A strategy off the runtime is NOT
broken — it's just not autonomous; status.py says how it's managed. Scanner health is fail-closed: an
external scanner never proven by a tick reads `unknown`, not `healthy` — prove it on a READ-ONLY surface
(`openclaw senpi scanner -r <rt>`, `openclaw senpi status|state -r <rt>`, `python3 deploy.py status`
for the last deploy's verdict, `deploy.py verify <id>` for the per-instance verdict over the same
surfaces). Everything this script prints is read-only. The RESUME — `deploy.py runtime <id>` — is the
one money path near this surface: it runs the deploy verb and can install, start trading, and fund a
wallet. It is named once, with what it does, never per row.

Exit: 0 both lists read · 1 a list could not be read (a strategy list refuses; a runtime list prints
the rows as runtime-unknown) · `_cli.EXIT_OOM` (9) a read was SIGKILLed. Exit 0 is what every
out-of-memory stop names as the sign the box can be used again, so only a read runtime list earns it.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _pkg  # noqa: E402
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


def _openclaw_available(why=None):
    """`openclaw --version` answered. A SIGKILLed probe is out of memory, never "no openclaw": `why`
    receives its `Cause` (see `_cli.openclaw_available`)."""
    return _cli.openclaw_available(why=why)


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


def _config_drift(pkg_id, rt_name, descriptors):
    """The running recipe vs the package on disk — `{"diffs": [...], "dir", "package_dir"}` when
    they differ, None when they agree or nothing can be compared (package not on disk here, no
    descriptor, unreadable YAML). Read-only and fail-open: a status surface may not crash on a
    package it cannot find, and "could not compare" renders as nothing, never as "current"."""
    desc = descriptors.get(rt_name) if (descriptors and rt_name) else None
    if not desc or not pkg_id:
        return None
    try:
        if not (_pkg.resolve_pkg_dir(pkg_id) / "strategy.yaml").is_file():
            return None
        pkg = _pkg.load(pkg_id)
        inst = next((i for i in pkg.instances if i.runtime_name == rt_name), None)
        if inst is None or inst.runtime_doc is None:
            return None
        diffs = _cli.config_drift(desc, inst.runtime_text, inst.runtime_doc)
    except Exception:  # noqa: BLE001 — fail-open on a read-only surface
        return None
    if not diffs:
        return None
    return {"diffs": [{"field": f, "running": a, "on_disk": b} for f, a, b in diffs],
            "dir": str(inst.runtime_path.parent), "package_dir": str(pkg.dir)}


def build(mcp, only_pkg=None, deep=True, rwhy=None, kills=None, hwhy=None):
    """`(rows, orphans, cli_ok)`. `rwhy`, a list, receives why the runtime list could not be read;
    `hwhy`, a list, why the fleet health read failed (a running runtime's health is then unknown).
    `kills`, a list, receives every SIGKILLed read's `Cause` (the probe, the list, the health read):
    the caller turns any of them into the out-of-memory stop."""
    why = []
    strategies = _read_or_refuse(
        _cli.list_strategies_or_none(mcp, statuses=_cli.LIVE_STATUSES, why=why), why)
    opens = [s for s in strategies if _cli.strategy_open(s)]
    # No openclaw here, or a `senpi.listRuntimes` read that failed or timed out: the registry is not
    # visible, and an empty list must NOT read as "no runtimes" (that turns a healthy fleet into false
    # 'interrupted deploy' alarms). Degrade to runtime-unknown instead.
    rwhy = rwhy if rwhy is not None else []
    hwhy = hwhy if hwhy is not None else []
    vwhy = []
    runtimes = _cli.list_runtimes_or_none(why=rwhy) if _openclaw_available(why=vwhy) else None
    if runtimes is None:
        rwhy.extend(vwhy)   # a failed probe IS why the list was not read (killed: not "no openclaw")
    cli_ok = runtimes is not None
    runtimes = runtimes or []
    # ONE `senpi.getHealthStatus` for the whole fleet — only when runtimes actually exist
    health_by_name = _cli.runtime_health_map(why=hwhy) if (deep and runtimes) else {}
    if kills is not None:
        kills.extend(c for c in rwhy + hwhy if _cli.sigkilled(c))
    # The rendered descriptors (what each runtime is actually running) ride the same listRuntimes rows.
    descriptors = _cli.runtime_descriptors(runtimes) if (deep and runtimes) else {}
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
        positions, paused, drift, reason = None, None, None, None
        if rt and _cli.runtime_running(rt):
            health = "running"
            entry = health_by_name.get(_cli.runtime_name(rt))  # from the single fleet-wide status --json
            if entry:  # upgrade process-level "running" to the runtime's own verdict (+ positions)
                health = _cli.health_verdict(entry) or "running"
                positions = _cli.active_positions(entry)
                reason = "; ".join(_cli.scanner_reasons(entry)) or None  # the runtime's words, for quoting
                # The runtime's own entry gate — separate from health, because a paused runtime is
                # healthy: it ticks, it manages what it holds, and it opens nothing.
                paused = _cli.risk_pause(entry)
            elif hwhy:   # the health read failed (killed, timed out, errored): unknown, never "running"
                health = "unknown"
            drift = _config_drift(skill, _cli.runtime_name(rt), descriptors)
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
            # SHOULD have a runtime. Without a readable registry we cannot tell — say so
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
                     "positions": positions, "paused": paused, "config_drift": drift, "reason": reason,
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

    rwhy, kills, hwhy = [], [], []
    rows, orphans, cli_ok = build(MCPClient(), a.package, deep=not a.fast, rwhy=rwhy, kills=kills,
                                  hwhy=hwhy)
    # A SIGKILLed read: what is below is only what was read before it, and this run exits EXIT_OOM.
    # Every kill steer names this script's exit 0 as the sign memory has freed, so 0 is earned only by
    # a runtime list that was actually read: one that could not be read (no openclaw, a failure, a
    # timeout) exits STATUS_INVENTORY_UNREAD, whatever else printed.
    stop = _cli.oom_stop("Nothing was changed. What is shown is only what was read before the kill; "
                         "anything not read is UNKNOWN, not stopped.",
                         "the box is out of memory, so what is running could not be fully read"
                         ) if kills else None
    code = _cli.EXIT_OOM if kills else 0 if cli_ok else _cli.STATUS_INVENTORY_UNREAD

    if a.json:
        print(json.dumps({"strategies": rows, "orphan_runtimes": orphans, "openclaw_available": cli_ok,
                          "runtime_list_error": str(rwhy[0]) if rwhy else None,
                          "health_read_error": str(hwhy[0]) if hwhy else None,
                          **(_cli.oom_fields(stop=stop) if kills else {})}, indent=2))
        return code

    if kills:
        # The fact once (the cause without its own stop), then the one stop; never a "Re-run".
        print(f"\nℹ {_cli.cause_fact(kills[0])}. {stop}")
    elif not cli_ok:
        print("\nℹ couldn't read the runtime list on this host (no openclaw, or `senpi.listRuntimes` "
              "failed or timed out" + (f": {rwhy[0]}" if rwhy else "") + ") — runtime state is UNKNOWN, "
              "not stopped. Re-run, or run status.py on the runtime host.")
    elif hwhy:
        # Scoped to status.py: exit 0 here is the OOM stop's "safe again", so this must not read as
        # "don't run the close/deploy that stop told you to re-run". Only the status re-run is bounded.
        print(f"\nℹ couldn't read runtime health ({hwhy[0]}) — every running runtime's health is "
              "UNKNOWN, not running. This doesn't block other commands: the runtime list was read. "
              "Re-run status.py once; if health still can't be read, tell the user health is unknown "
              "rather than re-running it again.")

    if not rows and not orphans:
        print("No open strategies." + (f" (filter: {a.package})" if a.package else ""))
        return code

    by_pkg = defaultdict(list)
    for r in rows:
        by_pkg[_pkg_label(r)].append(r)
    running = sum(1 for r in rows if r["health"] in _OK)
    idle = [r for r in rows if r["health"] == "no-runtime"]
    unknown = [r for r in rows if r["health"] == "runtime-unknown"]
    unproven = [r for r in rows if r["health"] == "unknown"]
    sick = [r for r in rows if r["health"] in ("degraded", "unhealthy", "runtime-stopped", "no-entry-scanners")]
    off = [r for r in rows if r["health"] in _OFF_RUNTIME]
    paused = [r for r in rows if r.get("paused")]
    drifted = [r for r in rows if r.get("config_drift")]
    bits = [f"{running} autonomous (on runtime)"]
    if sick:
        bits.append(f"{len(sick)} degraded")
    if paused:
        bits.append(f"{len(paused)} paused by a risk gate")
    if unproven:
        bits.append(f"{len(unproven)} unknown ({'health unread' if hwhy else 'not proven live'})")
    if idle:
        bits.append(f"{len(idle)} funded-but-idle")
    if unknown:
        bits.append(f"{len(unknown)} runtime-unknown (runtime list unreadable)")
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
            # The pause rides the row itself: a reader skimming a ✅ row must see that it opens nothing.
            hold = f"  ⏸ {r['paused']['eligibility']}" if r.get("paused") else ""
            print(f"  {_ICON.get(r['health'], ' ')} {r['health']:<15} {_name_cell(r):<22} "
                  f"{r['wallet'][:10]}…  {_funded(r):>8}  [{(r['strategyId'] or '')[:8]}]{pos}{rt}{hold}")
            if r.get("reason"):
                print(f"      ↳ {r['reason']}")
    if any(r.get("name_source") != "strategyName" for r in rows):
        print("\nℹ `*` after a name means the strategy record carried NO name of its own — what's shown "
              "is its package id (every instance of that package renders the same), not a name this "
              "wallet proved. Identify those by wallet / strategyId, never by the starred name.")
    if any(r["funded"] is None for r in rows):
        print("\nℹ `unknown` in the funded column means the strategy record carried no funded amount "
              "(totalFunded/netFunded) — NOT $0, and never the budget that was requested. Read what "
              "actually landed on the wallet in the app before acting on the number.")
    if kills:
        # The stop said run nothing now: no per-row command (resume, delete, update, triage). Drift and
        # orphans have no per-row marker, so their facts still print, without their commands.
        _print_drift(drifted, commands=False)
        _print_orphans(orphans, commands=False)
        return code
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
    if paused:
        print("\n⏸ Entries paused by the strategy's OWN risk gate — by design, not a fault (health above is "
              "unchanged; it manages what it holds and opens nothing until the gate lets go):")
        for r in paused:
            print(f"  - {_pkg_label(r)} {r['runtime'] or ''}: {_cli.describe_pause(r['paused'])}")
        print("  Say it to the user as the strategy's rule. Never close/redeploy to clear it: a fresh wallet "
              "market-exits the book and starts the same gate again from zero.")
    _print_drift(drifted)
    if unproven and hwhy:   # unread, not unproven: no scanner verdict; one generic read, not per row
        print(f"\n❔ {len(unproven)} running runtime(s) unknown: runtime health couldn't be read (see "
              "the health line above). To read one runtime's health: "
              "`openclaw senpi status -r <runtime> --json` (read-only).")
    elif unproven:
        print("\n❔ Unknown (fail-closed — not proven live: scanner not yet proven by a tick, or reporting disabled; verify, don't assume):")
        for r in unproven:
            print(f"  - {_pkg_label(r)} {r['runtime'] or ''} → "
                  f"`openclaw senpi scanner -r {r['runtime']}` / "
                  f"`openclaw senpi status -r {r['runtime']} --json` to check (read-only); "
                  f"`python3 deploy.py status` for the last deploy's verdict")
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
    _print_orphans(orphans)
    return code


def _print_drift(drifted, commands=True):
    if not drifted:
        return
    print("\n✎ Running recipe differs from the package on disk — an edit that was never applied:")
    for r in drifted:
        d = r["config_drift"]
        for x in d["diffs"]:
            print(f"  - {_pkg_label(r)} {r['runtime']}: {x['field']} running {x['running']!r}, "
                  f"on disk {x['on_disk']!r}")
        if commands:
            print(f"    → apply it in place (plans first; nothing is closed): python3 deploy.py update "
                  f"{d['package_dir']} --id {r['runtime']}   (add --apply to commit)")
            if {x["field"] for x in d["diffs"]} == {"recipe"}:
                # Only when `recipe` is the ONLY diff: runtimes before the recipe hash re-formatted
                # the stored recipe but never changed a parsed value, so a `dsl_preset` or
                # `description` diff beside it is always a real edit. Even alone it may be one (an
                # inline-ladder edit), so the plan is the gate. `--apply` stays out of the command.
                print(f"    only the recipe differs. Run the plan (python3 deploy.py update "
                      f"{d['package_dir']} --id {r['runtime']}). If it says 'The recipe is unchanged', "
                      f"this drift is left over from an update made on an older runtime: applying once "
                      f"(add --apply) records the file as it is. If it lists changes, it is an unapplied "
                      f"edit: read the plan to the user first.")


def _print_orphans(orphans, commands=True):
    if not orphans:
        return
    print("\n⚠ Orphan runtimes (no active strategy" + (" — safe to delete):" if commands else "):"))
    for o in orphans:
        cmd = f" → `openclaw senpi runtime delete {o['runtime']}`" if commands else ""
        print(f"  - {o['runtime']} ({str(o['wallet'] or '')[:10]}…){cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
