#!/usr/bin/env python3
"""Close a strategy PACKAGE — stop the runtime(s), TRIGGER strategy_close, return immediately.

  python3 close.py <id> [--instance <name>] [--dry-run] [--json]
  python3 close.py --all                                     # close EVERY open strategy + delete their runtimes
  python3 close.py --strategy-id <id> | --address <wallet>    # a wallet with NO package (unattributed
                                                                # or app-created) — <id>/--all only resolve a package

Does NOT wait for the on-chain flatten — it hands off to the agent to poll. Per strategy (discovered
from strategy_list by attribution skillName == manifest id, or directly by strategyId/wallet for
--strategy-id/--address; sid + wallet read from the strategy record, not the runtime, so orphans
close too):
  1. STOP    — openclaw senpi runtime delete (if a runtime is live), confirm gone.
  2. TRIGGER — submit MCP strategy_close(<strategyId>) — flattens ALL positions + closes the strategy
               (returns funds). Submit only; no poll.
  → reports `closing`. strategy_close is async, so POLL by re-running `close.py <id>`: it's idempotent
    (runtime already gone → skip; status already closing/closed → skip re-submit) and reports `closed`
    once the strategy leaves the active set.

--instance scopes which instance(s) to close; --dry-run prints the plan with no side effects.
--strategy-id/--address are mutually exclusive with <id>/--all/--instance/--ref and with each other.
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
# Alias, not a local copy: a separately-maintained tuple here once omitted FAILED, so a FAILED row
# matched neither this early-return nor the ACTIVE submit condition below and fell through to
# reporting "closing" having done nothing — a terminal strategy misreported as freshly triggered.
_CLOSED = _cli.DEAD_STATUSES
# Live (non-terminal) statuses — filter server-side so discovery doesn't pull a long closed history.
LIVE_STATUSES = _cli.LIVE_STATUSES


def _read_or_refuse(rows, why, what):
    """The strategy inventory, or a refusal — never an empty list standing in for an unread one.

    `strategy_list` degrading to `[]` on an unreadable read is not a silent no-op here: teardown then
    finds no targets, prints "no OPEN strategies to close" and exits **0**. That is a positive
    all-clear the surface never earned, answering the one question where being wrong is worst — the
    user asked to close everything and return their funds, and got told there was nothing to close
    while their wallets may be live and funded. Same rule `_runtime_gone` holds one call later: on the
    teardown money path, "couldn't read it" must never render as "there is nothing there"."""
    if rows is not None:
        return rows
    raise SystemExit(
        f"error: could not read the strategy list, so NOTHING was closed and nothing about {what} is "
        f"known here — this is not 'there is nothing to close'.\n"
        f"  Cause: {why[0] if why else 'no cause reported'}\n"
        f"  Your strategies may be live and funded. Re-run this command, or read what is actually "
        f"open first (read-only):  python3 status.py")


def _select_direct_target(rows, target):
    """From an UNFILTERED-by-status `strategies_for_or_none()` read, pick the one row addressed by
    `--strategy-id`/`--address`. Refuses on 0 or >1 matches rather than guessing: `--all`/`<package>`
    answer a SET question ("what's open for X"), where zero rows is a legitimate true fact; this
    answers an EXISTENCE question about one named target, where zero rows must never be read as the
    generic 'no OPEN strategies to close' all-clear — that would report success on a typo'd id or
    address over a live, funded wallet. `strategy_id` is a backend primary key so >1 should not
    happen for `--strategy-id`; wallets are documented elsewhere in this codebase as only ever
    duplicated across DIFFERENT addresses (never one address on two strategy rows), so >1 here would
    mean an unmodeled backend state — refuse and name every match rather than pick one silently."""
    if not rows:
        raise SystemExit(f"error: no strategy found for {target!r} on this account — nothing to close.")
    if len(rows) > 1:
        ids = ", ".join(str(_cli.strategy_id_of(s) or "?")[:8] for s in rows)
        raise SystemExit(f"error: {target!r} matched {len(rows)} strategies ({ids}) — refusing to "
                         f"guess which to close. Re-run with --strategy-id for the exact one.")
    return rows[0]


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
    ap.add_argument("--strategy-id", default=None,
                    help="Close by strategy id directly — for a wallet with NO package (unattributed "
                         "or app-created). Mutually exclusive with <package>/--all/--instance/--ref "
                         "and with --address.")
    ap.add_argument("--address", default=None,
                    help="Close by wallet address directly — same use as --strategy-id.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    direct = a.strategy_id or a.address
    if a.strategy_id and a.address:
        raise SystemExit("error: pass --strategy-id or --address, not both.")
    if direct and (a.package or a.all or a.instance or a.ref):
        raise SystemExit("error: --strategy-id/--address addresses one strategy directly — drop the "
                         "package id, --all, --instance, and --ref.")
    if not a.package and not a.all and not direct:
        raise SystemExit("error: pass a strategy id, --strategy-id/--address for a wallet with no "
                         "package, or --all to close every open strategy.")

    msgs = []
    log = (lambda m: msgs.append(m)) if a.json else (lambda m: print(m))
    mcp = MCPClient()
    pkg = None
    why = []

    if a.all:
        # Close every OPEN strategy across all packages — for "close all strategies / return funds".
        # sid + wallet come from each strategy record; close_one stops the matching runtime (by wallet)
        # and triggers strategy_close, so package runtimes are never stranded.
        rows = _read_or_refuse(_cli.list_strategies_or_none(mcp, statuses=LIVE_STATUSES, why=why),
                               why, "every open strategy")
        opens = [s for s in rows if _cli.strategy_open(s)]
        targets = [((_cli.strategy_skill(s) or "strategy") + ":" + str(_cli.strategy_id_of(s))[:8], s)
                   for s in opens]
        hdr = "all open strategies"
        strategy_label = "ALL"
    elif direct:
        # A wallet with NO package — unattributed, or app/manual-created — has no <id> to resolve
        # against. Read UNFILTERED by status, unlike --all/<package> above: a status-filtered read
        # cannot tell "no such id" from "already closed" (both return zero rows), and either would
        # otherwise fall through to the "no OPEN strategies to close" + exit 0 below — a false
        # all-clear on a target named explicitly. `_select_direct_target` refuses on 0 or >1 matches.
        rows = _read_or_refuse(
            _cli.strategies_for_or_none(mcp, strategy_id=a.strategy_id, wallet=a.address, why=why),
            why, direct)
        strat = _select_direct_target(rows, direct)
        sid = str(_cli.strategy_id_of(strat) or "")
        targets = [(f"strategy:{sid[:8]}", strat)]
        hdr = f"strategy {sid[:8]} (no package attribution)"
        strategy_label = sid
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
                raise SystemExit(
                    f"error: {e}\n  If this wallet has no package — unattributed, or created "
                    f"directly in the app — address it instead: --strategy-id <id> or "
                    f"--address <wallet> (read either from status.py).")
        if a.instance and a.instance not in {i.name for i in pkg.instances}:
            raise SystemExit(f"error: no instance {a.instance!r} in {pkg.id} (have: {', '.join(i.name for i in pkg.instances)})")
        rows = _read_or_refuse(_cli.strategies_for_or_none(mcp, skill_name=pkg.id,
                                                           statuses=LIVE_STATUSES, why=why),
                               why, pkg.id)
        opens = [s for s in rows if _cli.strategy_open(s)]
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
        strategy_label = pkg.id

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
    out = {"strategy": strategy_label, "status": overall, "instances": results}

    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{hdr}: {overall}")
        for r in results:
            extra = f"  ({r['error']})" if r.get("error") else ""
            print(f"  - {r['instance']}: {r['status']}{extra}")
        if overall == "closing":
            if pkg:
                poll = f"close.py {pkg.id}"
            elif direct:
                poll = f"close.py --strategy-id {a.strategy_id}" if a.strategy_id else f"close.py --address {a.address}"
            else:
                poll = "close.py --all"
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
