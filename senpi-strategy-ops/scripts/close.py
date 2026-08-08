#!/usr/bin/env python3
"""Close a strategy PACKAGE — stop the runtime(s), TRIGGER strategy_close, return immediately.

  python3 close.py <id> [--instance <name>] [--dry-run] [--json]
  python3 close.py --all                       # close EVERY open strategy + delete their runtimes

Does NOT wait for the on-chain flatten — it hands off to the agent to poll. Per strategy (discovered
from strategy_list by attribution skillName == manifest id; sid + wallet read from the strategy record,
not the runtime, so orphans close too):
  1. STOP    — openclaw senpi runtime delete (if a runtime is live), confirm gone.
  2. TRIGGER — submit MCP strategy_close(<strategyId>) — flattens ALL positions + closes the strategy
               (returns funds). Submit only; no poll.
  → reports `closing`. strategy_close is async, so POLL by re-running `close.py <id>`: it's idempotent
    (runtime already gone → skip; status already closing/closed → skip re-submit) and reports `closed`
    once the strategy leaves the active set.

--instance scopes which instance(s) to close; --dry-run prints the plan with no side effects.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _fetch  # noqa: E402
import _pkg  # noqa: E402
from mcp_client import MCPClient, MCPError  # noqa: E402

SUBMIT_TIMEOUT = 60        # HTTP timeout for the strategy_close submit
_CLOSED = ("CLOSED", "INACTIVE", "CLOSING_DONE", "TERMINATED")
# Live (non-terminal) statuses — filter server-side so discovery doesn't pull a long closed history.
LIVE_STATUSES = _cli.LIVE_STATUSES


def _runtime_gone(name):
    """True ONLY when `runtime list` was read successfully AND `name` is absent from it. An UNREADABLE
    inventory (rc!=0 / garbled → None) returns False: on teardown's money path we must never mistake
    'couldn't read the inventory' for 'the runtime is gone' and then strategy_close a strategy whose
    runtime is still live and could re-enter positions. Fail CLOSED here — the caller retries / reports."""
    rts = _cli.list_runtimes_or_none()
    if rts is None:
        return False
    return not any(_cli.runtime_name(r) == name for r in rts)


def close_one(label, strat, runtimes, dry_run, log):
    """Stop the runtime (FIRE — no confirm-wait) + TRIGGER strategy_close, then return immediately. The
    agent polls by re-running close.py (idempotent: runtime already gone → skip; status closing/closed →
    skip re-submit). Thread-safe: uses the pre-fetched `runtimes` list and its OWN MCP client, so instances
    run in parallel. sid + wallet come from the strategy record, not the runtime."""
    sid = _cli.strategy_id_of(strat)
    wallet = _cli.strategy_wallet(strat)
    status0 = str(_cli.strategy_status(strat) or "").upper()
    rt = next((r for r in runtimes if _cli.wallet_match(_cli.runtime_wallet(r), wallet)), None)
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

    # 1. stop the runtime if one is live. Trust `runtime list` (the authoritative inventory), NOT the
    #    delete's exit code: `runtime delete` rides the flaky gateway (its OTel/HyperDX banner leaks into
    #    our captured output) AND returns NOT_FOUND / non-zero when the runtime is ALREADY gone — so a
    #    non-zero is NOT proof of failure. Trusting it both broke idempotent re-runs (a poll re-run hit
    #    NOT_FOUND → false 'failed') and false-aborted the money-critical strategy_close below, stranding
    #    the strategy open (seen live). The delete succeeded iff the runtime is gone from `runtime list`.
    if rt:
        log(f"  [{label}] stopping runtime {rname!r}…")
        _cli.run_cli(["openclaw", "senpi", "runtime", "delete", "--id", rname,
                      "--address", wallet or ""], timeout=60)
        if not _runtime_gone(rname):   # still listed OR inventory unreadable → one retry, then treat as stuck
            _cli.run_cli(["openclaw", "senpi", "runtime", "delete", "--id", rname,
                          "--address", wallet or ""], timeout=60)
        if not _runtime_gone(rname):
            rec["status"] = "failed"
            rec["error"] = (f"runtime {rname!r} still in (or unreadable from) `runtime list` after delete — "
                            f"it may re-enter positions; delete it "
                            f"(`openclaw senpi runtime delete --id {rname} --address {wallet or '<wallet>'}`) "
                            f"then re-run close")
            return rec
        rec["runtime"] = "stopped"
    else:
        rec["runtime"] = "not_found"

    # 2. TRIGGER close (no wait). Only submit while still ACTIVE — once triggered it leaves ACTIVE, so a
    #    re-run won't re-submit. Own MCP client → safe to run instances concurrently.
    if status0 == "ACTIVE" or not status0:
        log(f"  [{label}] strategy_close({sid}) — triggered, not waiting")
        try:
            MCPClient().mcp_call("strategy_close", timeout=SUBMIT_TIMEOUT, strategyId=sid)
        except MCPError as e:
            rec["status"] = "failed"
            rec["error"] = f"strategy_close submit: {e}"
            return rec
    rec["status"] = "closing"   # handed off — agent polls (re-run close.py, or strategy_list) until closed
    return rec


def main(argv):
    ap = argparse.ArgumentParser(description="Close strategies: stop runtime(s) → trigger strategy_close (no wait).")
    ap.add_argument("package", nargs="?", help="Strategy id (e.g. spider). Omit with --all.")
    ap.add_argument("--all", action="store_true",
                    help="Close EVERY open strategy (all packages) and delete their runtimes.")
    ap.add_argument("--instance", default=None, help="Close only this instance of <package>.")
    ap.add_argument("--ref", default=None, help="Branch/ref to fetch the package manifest from if not on disk.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    if not a.package and not a.all:
        raise SystemExit("error: pass a strategy id, or --all to close every open strategy.")

    msgs = []
    log = (lambda m: msgs.append(m)) if a.json else (lambda m: print(m))
    mcp = MCPClient()
    pkg = None

    if a.all:
        # Close every OPEN strategy across all packages — for "close all strategies / return funds".
        # sid + wallet come from each strategy record; close_one stops the matching runtime (by wallet)
        # and triggers strategy_close, so package runtimes are never stranded.
        opens = [s for s in _cli.list_strategies(mcp, statuses=LIVE_STATUSES) if _cli.strategy_open(s)]
        targets = [((_cli.strategy_skill(s) or "strategy") + ":" + str(_cli.strategy_id_of(s))[:8], s)
                   for s in opens]
        hdr = "all open strategies"
    else:
        try:
            pkg = _pkg.load(a.package)
        except _pkg.BadPackage as e:
            sid = Path(a.package).name
            try:
                # Same durable, CWD-independent fetch root as deploy.py (see _pkg.strategies_root).
                dest_root = _pkg.strategies_root()
                _fetch.fetch_package(sid, dest_root, ref=a.ref)
                pkg = _pkg.load(dest_root / sid)
            except (_fetch.FetchError, _pkg.BadPackage):
                raise SystemExit(f"error: {e}")
        if a.instance and a.instance not in {i.name for i in pkg.instances}:
            raise SystemExit(f"error: no instance {a.instance!r} in {pkg.id} (have: {', '.join(i.name for i in pkg.instances)})")
        opens = [s for s in _cli.strategies_for(mcp, skill_name=pkg.id, statuses=LIVE_STATUSES)
                 if _cli.strategy_open(s)]
        if a.instance:
            rt = _cli.find_runtime(f"{pkg.id}-{a.instance}")
            w = _cli.runtime_wallet(rt) if rt else None
            targets = [(a.instance, s) for s in opens if w and _cli.wallet_match(w, _cli.strategy_wallet(s))]
            if not targets:
                raise SystemExit(f"error: can't map instance {a.instance!r} to a strategy without its live "
                                 f"runtime ({pkg.id}-{a.instance}). Omit --instance to close all of {pkg.id}.")
        else:
            targets = [(f"{pkg.id}[{i + 1}]", s) for i, s in enumerate(opens)]
        hdr = f"{pkg.id} v{pkg.version}"

    if not targets and not a.dry_run:
        print(f"{hdr}: no OPEN strategies to close.")
        sys.exit(0)

    # stop runtime + trigger strategy_close per instance — in PARALLEL (each instance uses its own MCP client),
    # then hand off to the agent to poll. runtime list fetched ONCE and shared (instances target distinct ids).
    rc0, _o0, _e0 = _cli.run_cli(["openclaw", "--version"], timeout=15)
    if rc0 != 0:
        log("⚠ openclaw is not available on this host — runtimes (if any, on the runtime host) will "
            "NOT be stopped by this run and would be left orphaned. Run close.py on the runtime host "
            "too, or `openclaw senpi runtime delete <id>` there.")
    runtimes = _cli.list_runtimes()
    if a.dry_run or len(targets) == 1:
        results = [close_one(label, strat, runtimes, a.dry_run, log) for label, strat in targets]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as ex:
            results = list(ex.map(lambda t: close_one(t[0], t[1], runtimes, a.dry_run, log), targets))

    statuses = {r["status"] for r in results}
    overall = ("planned" if a.dry_run else
               "failed" if "failed" in statuses else
               "closing" if "closing" in statuses else
               "closed" if statuses <= {"closed"} else "partial")
    out = {"strategy": (pkg.id if pkg else "ALL"), "status": overall, "instances": results}

    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{hdr}: {overall}")
        for r in results:
            extra = f"  ({r['error']})" if r.get("error") else ""
            print(f"  - {r['instance']}: {r['status']}{extra}")
        if overall == "closing":
            poll = f"close.py {pkg.id}" if pkg else "close.py --all"
            print(f"\nClose triggered (runtimes stopped). strategy_close is async — positions flatten "
                  f"on-chain. Poll until closed by re-running: {poll}  (or check strategy_list status).")

    # state is ephemeral: once a package is torn down, clear its deploy state so the next deploy is clean
    if pkg and not a.dry_run:
        try:
            (pkg.dir / ".deploy-state.json").unlink()
        except OSError:
            pass
    sys.exit(2 if overall in ("failed", "partial") else 0)


if __name__ == "__main__":
    main(sys.argv)
