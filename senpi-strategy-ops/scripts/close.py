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


def close_one(mcp, leg, sid, wallet, runtime_name, deadline, dry_run, log):
    rec = {"instance": leg, "runtime_id": runtime_name, "wallet": wallet, "strategy_id": sid}
    rt = _cli.find_runtime(runtime_name) if runtime_name else None

    if dry_run:
        rec["plan"] = ([f"runtime delete --id {runtime_name} --address {wallet}"] if rt else ["(no live runtime)"])
        rec["plan"] += [f"strategy_close({sid})  → poll until CLOSED"]
        rec["status"] = "planned"
        return rec

    # 1. stop runtime
    if rt:
        log(f"  [{leg}] stopping runtime {runtime_name!r}…")
        rc, _o, err = _cli.run_cli(["openclaw", "senpi", "runtime", "delete", "--id", runtime_name,
                                    "--address", wallet or ""], timeout=60)
        if rc != 0 or not confirm_runtime_gone(runtime_name):
            rec["status"] = "failed"
            rec["error"] = (err or "runtime delete failed / still present").strip()[:300]
            return rec
        rec["runtime"] = "stopped"
    else:
        rec["runtime"] = "not_found"

    if not sid:
        rec["status"] = "failed"
        rec["error"] = "no strategyId resolved (runtime gone? close manually via strategy_list)"
        return rec

    # 2. submit close (async)
    log(f"  [{leg}] strategy_close({sid})…")
    try:
        mcp.mcp_call("strategy_close", timeout=SUBMIT_TIMEOUT, strategyId=sid)
    except MCPError as e:
        rec["status"] = "failed"
        rec["error"] = f"strategy_close submit: {e}"
        return rec
    # 3. poll to confirmation
    ok, status = wait_closed(mcp, sid, deadline, log, leg)
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

    legs = [i for i in pkg.instances if (a.instance is None or i.name == a.instance)]
    if a.instance and not legs:
        raise SystemExit(f"error: no instance {a.instance!r} in {pkg.id} (have: {', '.join(i.name for i in pkg.instances)})")

    mcp = MCPClient()
    # the package's strategies, by attribution; index by wallet (lowercased).
    # Read-only lookups run even in --dry-run so the plan shows the real wallets/strategyIds.
    strategies = _cli.strategies_for(mcp, skill_name=pkg.id)
    by_wallet = {str(_cli.strategy_wallet(s) or "").lower(): s for s in strategies}

    results = []
    for inst in legs:
        rt = _cli.find_runtime(inst.runtime_name)
        wallet = _cli.runtime_wallet(rt) if rt else None
        strat = by_wallet.get(str(wallet or "").lower()) if wallet else None
        sid = _cli.strategy_id_of(strat) if strat else None
        results.append(close_one(mcp, inst.name, sid, wallet, inst.runtime_name,
                                 time.time() + a.timeout, a.dry_run, log))

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
