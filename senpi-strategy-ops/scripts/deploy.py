#!/usr/bin/env python3
"""Deploy a strategy PACKAGE in three short, resumable steps (so no single call blocks past the
tool/session timeout). The SCRIPT does the work deterministically — the agent just runs the steps
in order and re-runs a step until it reports done.

  python3 deploy.py create  <id> --budget <usd> [--max-wait S] [--dry-run] [--json]
  python3 deploy.py runtime  <id> [--decision-model M] [--dry-run] [--json]
  python3 deploy.py verify   <id> [--max-wait S] [--json]
  python3 deploy.py status   <id> [--json]

Step 1 `create`  — per instance: strategy_create_custom_strategy (records strategyId IMMEDIATELY),
                   then poll strategy_list until ACTIVE, BOUNDED by --max-wait. Not all ACTIVE yet →
                   exits `creating`; re-run `create` to RESUME (never re-creates). Refuses if it finds
                   skillName==<id> strategies not in the state file (interrupted run → close first).
Step 2 `runtime` — render each runtime.yaml with its wallet → openclaw senpi runtime create. Fast,
                   idempotent (skips a runtime that already exists).
Step 3 `verify`  — bounded poll until each external_scanner has ticked; resumable.

State lives in <pkg>/.deploy-state.json: per instance {strategyId, wallet, status}. Every sub-action
is persisted, so a kill mid-step just means re-run that step. The package is fetched from the remote
on first use if it isn't on disk.
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
import validate_universe  # noqa: E402
from _mcp import MCPClient, MCPError  # noqa: E402

SUBMIT_TIMEOUT = 60     # HTTP timeout for the async create submit
POLL_HTTP_TIMEOUT = 15  # HTTP timeout for fast read polls
DEFAULT_MAX_WAIT = 150  # per-call poll budget (s) — stays under the ~180s tool timeout
VERIFY_BUFFER = 120
POLL_EVERY = 10
ORDER = ("pending", "creating", "active", "registered", "live")


# ---------- package + state ----------

def ensure_pkg(arg, ref, log):
    try:
        return _pkg.load(arg)
    except _pkg.BadPackage as e:
        sid = Path(arg).name
        log(f"package not on disk — fetching {sid} from remote…")
        try:
            _fetch.fetch_package(sid, "strategies", ref=ref)
            return _pkg.load(sid)
        except (_fetch.FetchError, _pkg.BadPackage):
            raise SystemExit(f"error: {e}")


def _state_path(pkg):
    return pkg.dir / ".deploy-state.json"


def load_state(pkg):
    p = _state_path(pkg)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"id": pkg.id, "version": pkg.version, "instances": {}}


def save_state(pkg, st):
    _state_path(pkg).write_text(json.dumps(st, indent=2) + "\n")


def inst_state(st, name):
    return st["instances"].setdefault(name, {"status": "pending"})


def wallet_amount(budget, share):
    return max(100.0, round((budget or 0) * (share if share is not None else 1.0), 2))


def report(pkg, st, overall, note=None, as_json=False):
    insts = [{"instance": i, **st["instances"].get(i, {"status": "pending"})}
             for i in [x for x in st["instances"]]]
    out = {"strategy": pkg.id, "version": pkg.version, "status": overall, "instances": insts}
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"\n{pkg.id} v{pkg.version}: {overall}")
        for r in insts:
            print(f"  - {r['instance']}: {r['status']}"
                  + (f"  wallet={r['wallet']}" if r.get("wallet") else "")
                  + (f"  ({r['error']})" if r.get("error") else ""))
        if note:
            print(f"\n{note}")
    return out


# ---------- step 1: create wallets ----------

def cmd_create(pkg, a, log):
    st = load_state(pkg)
    st["budget"] = a.budget
    # Preflight: every hardcoded ticker must be a LIVE Hyperliquid instrument. A fake
    # ticker silently no-trades (market_get_asset_data 500s, the scan skips it) — so we
    # refuse to fund a package with an unverifiable universe. Confirmed-missing → abort;
    # transient fetch failure → warn + proceed (deploy needs HL anyway). Runs in dry-run too.
    try:
        bad = validate_universe.unknown_tickers(pkg.dir)
        if bad:
            raise SystemExit(
                "error: these universe tickers are NOT live Hyperliquid instruments: "
                + ", ".join(bad) + f"\n  Fix {pkg.id}'s runtime.yaml/strategy.yaml universe, "
                "then re-run. (check: validate_universe.py strategies/" + pkg.id + ")")
    except validate_universe.FetchError as e:
        log(f"  [warn] could not verify universe vs live HL instruments ({e}); proceeding")
    if a.dry_run:
        for inst in pkg.instances:
            s = inst_state(st, inst.name)
            s.setdefault("status", "pending")
            s["plan"] = f"strategy_create_custom_strategy(${wallet_amount(a.budget, inst.funding_share):g}, skillName={pkg.id}, skillVersion={pkg.version})"
        return report(pkg, st, "planned", as_json=a.json)
    if a.budget is None:
        raise SystemExit("error: --budget <total> is required for `create`")

    mcp = MCPClient()
    # Safety: refuse if there are skillName==id strategies we never recorded (interrupted run) — do
    # NOT blindly fund duplicates. The operator must close the strays first.
    recorded = {inst_state(st, i.name).get("strategyId") for i in pkg.instances}
    recorded.discard(None)
    existing = _cli.strategies_for(mcp, skill_name=pkg.id)
    # only OPEN, unrecorded strategies indicate an interrupted run — closed/failed history is harmless
    untracked = [s for s in existing if _cli.strategy_open(s) and _cli.strategy_id_of(s) not in recorded]
    need = [i for i in pkg.instances if not inst_state(st, i.name).get("strategyId")]
    if untracked and need:
        raise SystemExit(
            f"error: found {len(untracked)} existing {pkg.id} strateg(y/ies) not in this deploy's state "
            f"(likely an interrupted run). Close them first to avoid duplicate funded wallets:\n"
            f"  python3 {Path(__file__).with_name('close.py')} {pkg.id}")

    # create any instance that has no strategyId yet — record the id IMMEDIATELY (before polling),
    # so an interrupted run resumes instead of re-creating.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        if s.get("strategyId"):
            continue
        amt = wallet_amount(a.budget, inst.funding_share)
        log(f"  [{inst.name}] creating wallet (initialBudget=${amt:g})…")
        try:
            res = mcp.mcp_call("strategy_create_custom_strategy", timeout=SUBMIT_TIMEOUT,
                               initialBudget=amt, positions=[], skillName=pkg.id, skillVersion=pkg.version)
        except MCPError as e:
            s["status"] = "pending"
            s["error"] = f"create submit: {e}"
            save_state(pkg, st)
            return report(pkg, st, "failed", as_json=a.json)
        sid = _cli.strategy_id_of(res)
        if not sid:
            s["error"] = f"create returned no strategyId: {res!r}"
            save_state(pkg, st)
            return report(pkg, st, "failed", as_json=a.json)
        s.update(strategyId=sid, status="creating", error=None)
        save_state(pkg, st)  # ← persist before any polling

    # poll to ACTIVE, bounded by --max-wait (resume on re-run)
    deadline = time.time() + a.max_wait
    while True:
        pending = []
        for inst in pkg.instances:
            s = inst_state(st, inst.name)
            if s.get("status") in ("active", "registered", "live"):
                continue
            m = _cli.strategies_for(mcp, strategy_id=s["strategyId"], timeout=POLL_HTTP_TIMEOUT)
            status = str(_cli.strategy_status(m[0]) if m else "").upper()
            addr = _cli.strategy_wallet(m[0]) if m else None
            if status == "ACTIVE" and addr:
                s.update(wallet=addr, status="active")
                save_state(pkg, st)
            else:
                pending.append(f"{inst.name}={status or '…'}")
        if not pending:
            return report(pkg, st, "wallets-ready", note="Next: deploy.py runtime " + pkg.id, as_json=a.json)
        if time.time() >= deadline:
            return report(pkg, st, "creating",
                          note="Wallets still funding. Re-run `deploy.py create " + pkg.id + "` to resume.",
                          as_json=a.json)
        log(f"  waiting on {', '.join(pending)}…")
        time.sleep(POLL_EVERY)


# ---------- step 2: deploy runtimes ----------

def cmd_runtime(pkg, a, log):
    st = load_state(pkg)
    not_ready = [i.name for i in pkg.instances
                 if not inst_state(st, i.name).get("wallet")]
    if not_ready:
        raise SystemExit(f"error: wallets not ready for {', '.join(not_ready)} — run `deploy.py create {pkg.id}` first")
    if pkg.any_needs_model and not a.decision_model and not a.dry_run:
        raise SystemExit("error: a runtime has a decision_mode: llm action — pass --decision-model <bare-model>")

    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        wallet = s["wallet"]
        build = inst.runtime_path.with_name(f"{inst.name}.deploy.runtime.yaml")
        try:
            text = inst.render(wallet, model_env=pkg.model_env, model=a.decision_model)
        except _pkg.BadPackage as e:
            s.update(status="active", error=str(e))
            save_state(pkg, st)
            continue
        s["error"] = None  # render succeeded — clear any stale error from a prior run
        if a.dry_run:
            s["plan"] = f"openclaw senpi runtime create -p {build} --runtime-id {inst.runtime_name}"
            save_state(pkg, st)
            continue
        if _cli.find_runtime(inst.runtime_name):           # idempotent — already deployed
            s.update(status="registered", error=None)
            save_state(pkg, st)
            continue
        build.write_text(text)
        log(f"  [{inst.name}] runtime create…")
        rc, _o, err = _cli.run_cli(["openclaw", "senpi", "runtime", "create", "-p", str(build),
                                    "--runtime-id", inst.runtime_name], timeout=120)
        if rc != 0:
            s.update(error=(err or "runtime create failed").strip()[:300])
            save_state(pkg, st)
            continue
        s.update(status="registered", error=None)
        save_state(pkg, st)

    if a.dry_run:
        return report(pkg, st, "planned", as_json=a.json)
    failed = [i.name for i in pkg.instances if inst_state(st, i.name).get("error")]
    overall = "failed" if failed else "registered"
    return report(pkg, st, overall, note="Next: deploy.py verify " + pkg.id
                  + "   (`registered` ≠ ticking yet)", as_json=a.json)


# ---------- step 3: verify ticking ----------

def _deep_find_scanner(obj, name):
    if isinstance(obj, dict):
        if _cli.dig(obj, "name", "scanner", "scannerName") == name and any(
                k.lower() in ("runcount", "lastrunfinishedat", "lastrunstartedat", "ticks") for k in obj):
            return obj
        for v in obj.values():
            r = _deep_find_scanner(v, name)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find_scanner(v, name)
            if r:
                return r
    return None


def _check_ticks(pkg, st):
    """One pass: mark each instance live if its external_scanner has ticked. Returns the list of
    instances not yet live as (name, reason)."""
    pending = []
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        if s.get("status") == "live":
            continue
        rt = _cli.find_runtime(inst.runtime_name)
        if not rt or not _cli.runtime_running(rt):
            pending.append((inst.name, "no live runtime"))
            continue
        state = _cli.cli_json(["openclaw", "senpi", "state", "-r", inst.runtime_name, "--json"], POLL_HTTP_TIMEOUT)
        sc = _deep_find_scanner(state, inst.external_scanner.get("name")) if state else None
        runs = _cli.dig(sc or {}, "runCount", "ticks", "runs", default=0) or 0
        if isinstance(runs, (int, float)) and runs > 0:
            s["status"] = "live"
            save_state(pkg, st)
        else:
            pending.append((inst.name, f"awaiting first tick (~{inst.interval_seconds or '?'}s cadence)"))
    return pending


def cmd_verify(pkg, a, log):
    # Single fast check by default — the first scan() tick is gated by the scanner's interval_seconds
    # (e.g. spider swing = 300s), so blocking here would just burn the tool budget. Report liveness or
    # the expected cadence and let the agent re-run when it's worth re-checking. `--max-wait S` opts
    # into a bounded poll for fast legs.
    st = load_state(pkg)
    deadline = time.time() + a.max_wait
    while True:
        pending = _check_ticks(pkg, st)
        if not pending:
            return report(pkg, st, "live", as_json=a.json)
        if time.time() >= deadline:
            note = "Not ticking yet — each leg's first scan() fires on its interval_seconds:\n  " + \
                   "\n  ".join(f"{n}: {why}" for n, why in pending) + \
                   f"\nRe-run `deploy.py verify {pkg.id}` after that to confirm `live`."
            return report(pkg, st, "registered", note=note, as_json=a.json)
        log(f"  waiting on {', '.join(n for n, _ in pending)}…")
        time.sleep(POLL_EVERY)


# ---------- cli ----------

def main(argv):
    ap = argparse.ArgumentParser(description="Deploy a strategy package in resumable steps.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("package", help="Strategy id (e.g. spider) or package dir (strategies/spider).")
        p.add_argument("--ref", default=None, help="Branch/ref to fetch the package from if not on disk.")
        p.add_argument("--json", action="store_true")

    pc = sub.add_parser("create", help="Step 1: create + fund the strategy wallet(s) (resumable).")
    common(pc)
    pc.add_argument("--budget", type=float, default=None, help="Total USDC split across wallets by funding_share.")
    pc.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="Poll budget for this call (s).")
    pc.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("runtime", help="Step 2: render + create the runtime(s) on the ready wallet(s).")
    common(pr)
    pr.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pr.add_argument("--dry-run", action="store_true")

    pv = sub.add_parser("verify", help="Step 3: confirm each scanner is ticking (fast single check; re-run as needed).")
    common(pv)
    pv.add_argument("--max-wait", type=int, default=0,
                    help="Default 0 = one fast check (first tick is gated by interval_seconds; re-run later). "
                         ">0 keeps polling up to S seconds (useful for fast legs).")

    ps = sub.add_parser("status", help="Show the deploy state.")
    common(ps)

    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    pkg = ensure_pkg(a.package, a.ref, log)
    errs = _pkg.validate(pkg)
    if errs:
        print(f"✗ {pkg.id}: invalid package", file=sys.stderr)
        for e in errs:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    if a.cmd == "create":
        out = cmd_create(pkg, a, log)
    elif a.cmd == "runtime":
        out = cmd_runtime(pkg, a, log)
    elif a.cmd == "verify":
        out = cmd_verify(pkg, a, log)
    else:  # status
        out = report(pkg, load_state(pkg), "status", as_json=a.json)

    sys.exit(2 if out.get("status") == "failed" else 0)


if __name__ == "__main__":
    main(sys.argv)
