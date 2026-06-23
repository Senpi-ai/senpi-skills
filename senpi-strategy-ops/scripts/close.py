#!/usr/bin/env python3
"""Close a strategy PACKAGE — one shot: stop runtimes → close strategies (async-safe).

  python3 close.py <package-dir> [--instance <name>] [--timeout <s>] [--dry-run] [--json]

Full teardown of the package's strategies (identified by MCP attribution skillName == manifest id):
  1. STOP   — openclaw senpi runtime delete --id <id>-<instance> --address <wallet>, confirm gone
              (so nothing re-opens positions while we close).
  2. CLOSE  — submit MCP strategy_close(<strategyId>). strategy_close flattens ALL positions AND
              closes the strategy (returns funds) — there is no separate close-positions step.
  3. CONFIRM — strategy_close is ASYNC (positions flatten on-chain over time). Submit with a raised
              HTTP timeout, then poll strategy_list by strategyId until status CLOSED (or it drops out
              of the list) under a bounded deadline. Report `closed` only when confirmed; `closing`
              (positions still open) if the deadline elapses — never hang, never claim success early.

Close always closes the strategy (it never just stops the runtime). --instance scopes which leg(s)
to close; --dry-run prints the plan with no side effects.
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

SUBMIT_TIMEOUT = 60        # HTTP timeout for the async strategy_close submit
POLL_HTTP_TIMEOUT = 15
DEFAULT_DEADLINE = 300     # max seconds to wait for CLOSED
POLL_EVERY = 10
_CLOSED = ("CLOSED", "INACTIVE", "CLOSING_DONE", "TERMINATED")


def confirm_runtime_gone(name, tries=6):
    for _ in range(tries):
        if _cli.find_runtime(name) is None:
            return True
        time.sleep(2)
    return _cli.find_runtime(name) is None


def wait_closed(mcp, sid, deadline, log, leg):
    """Poll until the strategy reports CLOSED or drops out of strategy_list. Returns (ok, status)."""
    last = None
    while time.time() < deadline:
        matches = _cli.strategies_for(mcp, strategy_id=sid, timeout=POLL_HTTP_TIMEOUT)
        if not matches:                       # gone from the list → fully closed
            return True, "CLOSED"
        last = str(_cli.strategy_status(matches[0]) or "").upper()
        if last in _CLOSED:
            return True, last
        log(f"  [{leg}] {last or '…'} — closing")
        time.sleep(POLL_EVERY)
    return False, last or "UNKNOWN"


def close_one(mcp, label, strat, deadline, dry_run, log):
    # Resolve sid + wallet DIRECTLY from the strategy record (strategy_list metadata) — NOT via the
    # runtime. The runtime is used only to STOP it, and only if one is live (orphans have none).
    sid = _cli.strategy_id_of(strat)
    wallet = _cli.strategy_wallet(strat)
    rt = _cli.find_runtime_by_wallet(wallet)
    rname = _cli.runtime_name(rt) if rt else None
    rec = {"instance": label, "strategy_id": sid, "wallet": wallet, "runtime_id": rname}

    if dry_run:
        rec["plan"] = ([f"runtime delete --id {rname} --address {wallet}"] if rt else ["(no live runtime)"])
        rec["plan"] += [f"strategy_close({sid})  → poll until CLOSED"]
        rec["status"] = "planned"
        return rec

    if not sid:
        rec["status"] = "failed"
        rec["error"] = "no strategyId on strategy record"
        return rec

    # 1. stop the runtime if one is live (so nothing re-opens while closing)
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

    # 2. submit close (async)
    log(f"  [{label}] strategy_close({sid})…")
    try:
        mcp.mcp_call("strategy_close", timeout=SUBMIT_TIMEOUT, strategyId=sid)
    except MCPError as e:
        rec["status"] = "failed"
        rec["error"] = f"strategy_close submit: {e}"
        return rec
    # 3. poll to confirmation
    ok, status = wait_closed(mcp, sid, deadline, log, label)
    rec["status"] = "closed" if ok else "closing"
    rec["strategy_status"] = status
    return rec


def main(argv):
    ap = argparse.ArgumentParser(description="Close a strategy package (stop runtimes → close strategies).")
    ap.add_argument("package", help="Strategy id (e.g. spider) or package dir (strategies/spider).")
    ap.add_argument("--instance", default=None, help="Close only this leg.")
    ap.add_argument("--ref", default=None, help="Branch/ref to fetch the package manifest from if not on disk.")
    ap.add_argument("--timeout", type=int, default=DEFAULT_DEADLINE, help="Overall per-leg deadline to confirm CLOSED (s).")
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
    strategies = _cli.strategies_for(mcp, skill_name=pkg.id)

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
        print(f"{pkg.id}: no strategies found for skillName=={pkg.id} (already closed, or not yours).")
        sys.exit(0)

    results = [close_one(mcp, label, strat, time.time() + a.timeout, a.dry_run, log)
               for label, strat in targets]

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
            print("\nNote: close submitted but not yet confirmed CLOSED — positions still flattening. "
                  "Re-check: openclaw senpi runtime list  +  strategy_list (status).")
    sys.exit(2 if overall in ("failed", "partial") else 0)


if __name__ == "__main__":
    main(sys.argv)
