#!/usr/bin/env python3
"""Close a strategy PACKAGE — stop the runtime(s), TRIGGER strategy_close, return immediately.

  python3 close.py <id> [--instance <name>] [--dry-run] [--json]

Does NOT wait for the on-chain flatten — it hands off to the agent to poll. Per strategy (discovered
from strategy_list by attribution skillName == manifest id; sid + wallet read from the strategy record,
not the runtime, so orphans close too):
  1. STOP    — openclaw senpi runtime delete (if a runtime is live), confirm gone.
  2. TRIGGER — submit MCP strategy_close(<strategyId>) — flattens ALL positions + closes the strategy
               (returns funds). Submit only; no poll.
  → reports `closing`. strategy_close is async, so POLL by re-running `close.py <id>`: it's idempotent
    (runtime already gone → skip; status already closing/closed → skip re-submit) and reports `closed`
    once the strategy leaves the active set.

--instance scopes which leg(s) to close; --dry-run prints the plan with no side effects.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _fetch  # noqa: E402
import _pkg  # noqa: E402
from _mcp import MCPClient, MCPError  # noqa: E402

SUBMIT_TIMEOUT = 60        # HTTP timeout for the strategy_close submit
_CLOSED = ("CLOSED", "INACTIVE", "CLOSING_DONE", "TERMINATED")


def confirm_runtime_gone(name, tries=6):
    for _ in range(tries):
        if _cli.find_runtime(name) is None:
            return True
        time.sleep(2)
    return _cli.find_runtime(name) is None


def close_one(mcp, label, strat, dry_run, log):
    """Stop the runtime + TRIGGER strategy_close, then return immediately — NO waiting for on-chain
    flatten. Idempotent: re-running close.py is the poll (runtime already gone → skip; status already
    closing/closed → skip re-submit). sid + wallet come from the strategy record, not the runtime."""
    sid = _cli.strategy_id_of(strat)
    wallet = _cli.strategy_wallet(strat)
    status0 = str(_cli.strategy_status(strat) or "").upper()
    rt = _cli.find_runtime_by_wallet(wallet)
    rname = _cli.runtime_name(rt) if rt else None
    rec = {"instance": label, "strategy_id": sid, "wallet": wallet, "runtime_id": rname,
           "strategy_status": status0 or None}

    if dry_run:
        rec["plan"] = ([f"runtime delete --id {rname} --address {wallet}"] if rt else ["(no live runtime)"])
        rec["plan"] += ([f"strategy_close({sid})  (trigger, no wait)"] if status0 not in _CLOSED
                        else ["(already closed)"])
        rec["status"] = "planned"
        return rec

    if not sid:
        rec["status"] = "failed"
        rec["error"] = "no strategyId on strategy record"
        return rec
    if status0 in _CLOSED:
        rec["status"] = "closed"
        return rec

    # 1. stop the runtime if one is live (so nothing re-opens) — idempotent across re-runs
    if rt:
        log(f"  [{label}] stopping runtime {rname!r}…")
        rc, _o, err = _cli.run_cli(["openclaw", "senpi", "runtime", "delete", "--id", rname,
                                    "--address", wallet or ""], timeout=60)
        if rc != 0 or not confirm_runtime_gone(rname):
            rec["status"] = "failed"
            rec["error"] = (err or "runtime delete failed / still present").strip()[:300]
            return rec
        rec["runtime"] = "stopped"
    else:
        rec["runtime"] = "not_found"

    # 2. TRIGGER close (no wait). Only submit if the strategy is still ACTIVE — once triggered it leaves
    #    ACTIVE, so a re-run won't re-submit. Tolerate a benign "already closing" error.
    if status0 == "ACTIVE" or not status0:
        log(f"  [{label}] strategy_close({sid}) — triggered, not waiting")
        try:
            mcp.mcp_call("strategy_close", timeout=SUBMIT_TIMEOUT, strategyId=sid)
        except MCPError as e:
            rec["status"] = "failed"
            rec["error"] = f"strategy_close submit: {e}"
            return rec
    rec["status"] = "closing"   # handed off — agent polls (re-run close.py, or strategy_list) until closed
    return rec


def main(argv):
    ap = argparse.ArgumentParser(description="Close a strategy package (stop runtimes → close strategies).")
    ap.add_argument("package", help="Strategy id (e.g. spider) or package dir (strategies/spider).")
    ap.add_argument("--instance", default=None, help="Close only this leg.")
    ap.add_argument("--ref", default=None, help="Branch/ref to fetch the package manifest from if not on disk.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    msgs = []
    log = (lambda m: msgs.append(m)) if a.json else (lambda m: print(m))

    try:
        pkg = _pkg.load(a.package)
    except _pkg.BadPackage as e:
        # package not on local disk — fetch the manifest from the remote (same as deploy)
        sid = Path(a.package).name
        try:
            _fetch.fetch_package(sid, "strategies", ref=a.ref)
            pkg = _pkg.load(sid)
        except (_fetch.FetchError, _pkg.BadPackage):
            raise SystemExit(f"error: {e}")

    if a.instance and a.instance not in {i.name for i in pkg.instances}:
        raise SystemExit(f"error: no instance {a.instance!r} in {pkg.id} (have: {', '.join(i.name for i in pkg.instances)})")

    mcp = MCPClient()
    # Discover the package's strategies DIRECTLY from strategy_list (by attribution skillName==id).
    # sid + wallet come from the strategy record, NOT from a runtime — so close works even when the
    # runtimes are gone (e.g. orphaned wallets from a deploy that failed before runtime create).
    all_for_skill = _cli.strategies_for(mcp, skill_name=pkg.id)
    strategies = [s for s in all_for_skill if _cli.strategy_open(s)]   # ignore CLOSED/FAILED history
    closed_n = len(all_for_skill) - len(strategies)

    if a.instance:
        # Per-instance scoping needs the live runtime to know which strategy is this leg (the strategy
        # record carries no leg label). Map by the runtime's wallet; if that runtime is gone, we can't
        # disambiguate — tell the user to omit --instance to close the whole strategy.
        rt = _cli.find_runtime(f"{pkg.id}-{a.instance}")
        w = str(_cli.runtime_wallet(rt) or "").lower() if rt else None
        targets = [(a.instance, s) for s in strategies if w and str(_cli.strategy_wallet(s) or "").lower() == w]
        if not targets:
            raise SystemExit(f"error: can't map instance {a.instance!r} to a strategy without its live "
                             f"runtime ({pkg.id}-{a.instance}). Omit --instance to close all of {pkg.id}.")
    else:
        targets = [(f"{pkg.id}[{i + 1}]", s) for i, s in enumerate(strategies)]

    if not targets and not a.dry_run:
        print(f"{pkg.id}: no OPEN strategies for skillName=={pkg.id} "
              f"(nothing to close; {closed_n} already closed/failed in history).")
        sys.exit(0)

    results = [close_one(mcp, label, strat, a.dry_run, log) for label, strat in targets]

    statuses = {r["status"] for r in results}
    overall = ("planned" if a.dry_run else
               "failed" if "failed" in statuses else
               "closing" if "closing" in statuses else
               "closed" if statuses <= {"closed"} else "partial")
    report = {"strategy": pkg.id, "version": pkg.version, "status": overall, "instances": results}

    if a.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{pkg.id} v{pkg.version}: {overall}")
        for r in results:
            extra = f"  ({r['error']})" if r.get("error") else ""
            print(f"  - {r['instance']}: {r['status']}{extra}")
        if overall == "closing":
            print(f"\nClose triggered (runtimes stopped). strategy_close is async — positions flatten "
                  f"on-chain over time. Poll until closed by re-running: close.py {pkg.id}  "
                  f"(reports `closed` when done; or check strategy_list status).")
    sys.exit(2 if overall in ("failed", "partial") else 0)


if __name__ == "__main__":
    main(sys.argv)
