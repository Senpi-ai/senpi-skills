#!/usr/bin/env python3
"""Deploy a strategy PACKAGE — one shot: create wallets → render → runtime create → cross-verify.

  python3 deploy.py <package-dir> --budget <total-usd>
                    [--decision-model <bare-model>] [--dry-run] [--json]

Lifecycle (per instance in strategy.yaml). Deploy ALWAYS creates fresh wallets:
  0. PRE-CHECK — if any leg's runtime (<id>-<instance>) is already live, refuse and create NO wallets
     ("already deployed — run close.py first"). Redeploy = close then deploy.
  1. WALLET — create a NEW strategy wallet via MCP strategy_create_custom_strategy
     (initialBudget = max($100, budget x funding_share), positions=[], skillName/skillVersion from
     the manifest), then poll strategy_list by strategyId until ACTIVE → strategyWalletAddress.
  2. RENDER — substitute ${wallet_env} (+ the decision-model env iff a runtime has a decision_mode:
     llm action) into the leg's runtime.yaml → <pkg>/.build/<instance>.runtime.yaml. No telegram.
  3. CREATE — openclaw senpi runtime create -p <rendered>. The runtime supervises scan() itself —
     there is NO scanner daemon to launch.
  4. VERIFY — poll runtime list/state until the external_scanner has actually ticked.
     Report `live` only then; `registered` if the runtime is up but no tick confirmed before timeout.

--dry-run validates + plans (renders in memory) with NO side effects: no wallet creation, no create.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _pkg  # noqa: E402
from _mcp import MCPClient, MCPError  # noqa: E402

SUBMIT_TIMEOUT = 60        # HTTP timeout for the async create-wallet submit
POLL_HTTP_TIMEOUT = 15     # HTTP timeout for fast read polls
CREATE_DEADLINE = 600      # max seconds to wait for a new wallet to reach ACTIVE
VERIFY_BUFFER = 120        # seconds added to interval_seconds for the first-tick wait
POLL_EVERY = 10


def wallet_amount(budget, share):
    return max(100.0, round((budget or 0) * (share if share is not None else 1.0), 2))


def create_wallet(mcp, pkg, inst, amount, log):
    """Create a new strategy wallet via MCP, poll to ACTIVE. Returns (address, strategy_id)."""
    log(f"  [{inst.name}] creating wallet (initialBudget=${amount:g}, skill={pkg.id}/{pkg.version})…")
    res = mcp.mcp_call("strategy_create_custom_strategy", timeout=SUBMIT_TIMEOUT,
                       initialBudget=amount, positions=[],
                       skillName=pkg.id, skillVersion=pkg.version)
    sid = _cli.strategy_id_of(res) or _cli.strategy_id_of(_cli.dig(res, "data") or {})
    if not sid:
        raise RuntimeError(f"strategy_create_custom_strategy returned no strategyId (got: {res!r})")
    deadline = time.time() + CREATE_DEADLINE
    while time.time() < deadline:
        matches = _cli.strategies_for(mcp, strategy_id=sid, timeout=POLL_HTTP_TIMEOUT)
        s = matches[0] if matches else None
        status = str(_cli.strategy_status(s) or "").upper()
        addr = _cli.strategy_wallet(s)
        if status == "ACTIVE" and addr:
            log(f"  [{inst.name}] wallet ACTIVE: {addr}")
            return addr, sid
        log(f"  [{inst.name}] wallet {status or '…'} — waiting")
        time.sleep(POLL_EVERY)
    raise TimeoutError(f"wallet for {inst.name} (strategyId {sid}) not ACTIVE within {CREATE_DEADLINE}s")


def _deep_find_scanner(obj, name):
    """Walk a state/status JSON for a dict describing the named scanner."""
    if isinstance(obj, dict):
        nm = _cli.dig(obj, "name", "scanner", "scannerName")
        if nm == name and any(k.lower() in ("runcount", "lastrunfinishedat", "lastrunstartedat", "ticks")
                               for k in obj):
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


def verify(inst, deadline, log):
    """Poll until the runtime is running AND its external_scanner has ticked. Returns a status str."""
    name = inst.runtime_name
    scanner = inst.external_scanner.get("name")
    seen_running = False
    while time.time() < deadline:
        rt = _cli.find_runtime(name)
        if rt and _cli.runtime_running(rt):
            seen_running = True
            state = _cli.cli_json(["openclaw", "senpi", "state", "-r", name, "--json"], POLL_HTTP_TIMEOUT)
            sc = _deep_find_scanner(state, scanner) if state else None
            runs = _cli.dig(sc or {}, "runCount", "ticks", "runs", default=0) or 0
            if isinstance(runs, (int, float)) and runs > 0:
                log(f"  [{inst.name}] live — scanner {scanner!r} ticked (runCount={runs})")
                return "live"
        log(f"  [{inst.name}] {'running, awaiting first tick' if seen_running else 'awaiting runtime'}…")
        time.sleep(POLL_EVERY)
    return "registered" if seen_running else "failed"


def deploy_instance(pkg, inst, wallet, model_env, model, dry_run, log):
    rec = {"instance": inst.name, "runtime_id": inst.runtime_name, "wallet": wallet}
    # render (in memory always; write only for a real run)
    text = inst.render(wallet, model_env=model_env, model=model)
    build = pkg.dir / ".build" / f"{inst.name}.runtime.yaml"
    if dry_run:
        rec["status"] = "planned"
        rec["create_cmd"] = f"openclaw senpi runtime create -p {build} --runtime-id {inst.runtime_name}"
        return rec
    build.parent.mkdir(parents=True, exist_ok=True)
    build.write_text(text)

    # Safety guard — the all-legs pre-check should have refused already, but never clobber a live runtime.
    if _cli.find_runtime(inst.runtime_name):
        rec["status"] = "failed"
        rec["error"] = f"runtime {inst.runtime_name!r} already exists — close it first"
        return rec

    log(f"  [{inst.name}] runtime create…")
    # pin --runtime-id to the runtime.yaml `name` so it matches verify/close lookups
    # (otherwise it derives from the build filename, e.g. "swing.runtime").
    rc, _out, err = _cli.run_cli(["openclaw", "senpi", "runtime", "create", "-p", str(build),
                                  "--runtime-id", inst.runtime_name], timeout=120)
    if rc != 0:
        rec["status"] = "failed"
        rec["error"] = (err or "runtime create failed").strip()[:400]
        return rec

    interval = inst.interval_seconds or 300
    deadline = time.time() + interval + VERIFY_BUFFER
    rec["status"] = verify(inst, deadline, log)
    return rec


def main(argv):
    ap = argparse.ArgumentParser(description="Deploy a strategy package (create wallets → deploy → verify).")
    ap.add_argument("package", help="Strategy id (e.g. spider, as discover emits) or package dir (strategies/spider).")
    ap.add_argument("--budget", type=float, default=None, help="Total USDC split across the new wallets by funding_share (required for a real run).")
    ap.add_argument("--decision-model", default=None, help="Bare model name (only if a runtime has a decision_mode: llm action).")
    ap.add_argument("--dry-run", action="store_true", help="Validate + plan only; no wallet creation, no create.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])

    msgs = []
    log = (lambda m: msgs.append(m)) if a.json else (lambda m: print(m))

    try:
        pkg = _pkg.load(a.package)
    except _pkg.BadPackage as e:
        raise SystemExit(f"error: {e}")
    errs = _pkg.validate(pkg)
    if errs:
        print(f"✗ {pkg.id}: invalid package", file=sys.stderr)
        for e in errs:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    if pkg.any_needs_model and not a.decision_model and not a.dry_run:
        raise SystemExit("error: this strategy has a decision_mode: llm action — pass --decision-model <bare-model>")
    model_env = pkg.model_env

    if a.budget is None and not a.dry_run:
        raise SystemExit("error: --budget <total> is required (deploy always creates new wallets)")

    # Pre-check: deploy always creates fresh wallets, so refuse if any leg is already live —
    # never create+fund a wallet only to collide on a fixed runtime id. (Skipped in dry-run.)
    if not a.dry_run:
        live = [i.runtime_name for i in pkg.instances if _cli.find_runtime(i.runtime_name)]
        if live:
            raise SystemExit(f"error: {pkg.id} already deployed ({', '.join(live)}) — run close.py first "
                             f"(redeploy = close then deploy)")

    mcp = None
    results = []
    for inst in pkg.instances:
        try:
            if a.dry_run:
                wallet = f"<NEW ${inst.wallet_env} via strategy_create_custom_strategy(${wallet_amount(a.budget, inst.funding_share):g})>"
            else:
                mcp = mcp or MCPClient()
                wallet, _sid = create_wallet(mcp, pkg, inst, wallet_amount(a.budget, inst.funding_share), log)
            results.append(deploy_instance(pkg, inst, wallet, model_env, a.decision_model,
                                           a.dry_run, log))
        except (MCPError, RuntimeError, TimeoutError, _pkg.BadPackage) as e:
            results.append({"instance": inst.name, "runtime_id": inst.runtime_name, "status": "failed",
                            "error": str(e)})

    statuses = {r["status"] for r in results}
    overall = ("planned" if a.dry_run else
               "failed" if "failed" in statuses else
               "live" if statuses <= {"live"} else "registered")
    report = {"strategy": pkg.id, "version": pkg.version, "status": overall,
              "attribution": {"skillName": pkg.id, "skillVersion": pkg.version},
              "instances": results}

    if a.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{pkg.id} v{pkg.version}: {overall}")
        for r in results:
            extra = f"  ({r['error']})" if r.get("error") else ""
            print(f"  - {r['instance']}: {r['status']}  wallet={r.get('wallet','?')}{extra}")
        if overall in ("registered", "live") and not a.dry_run:
            print("\nNote: `registered` ≠ ticking. Confirm with: openclaw senpi status -r <runtime_id> --json")
    sys.exit(2 if overall == "failed" else 0)


if __name__ == "__main__":
    main(sys.argv)
