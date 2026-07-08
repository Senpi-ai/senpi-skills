#!/usr/bin/env python3
"""Deploy a strategy PACKAGE in three short, resumable steps (so no single call blocks past the
tool/session timeout). The SCRIPT does the work deterministically — the agent just runs the steps
in order and re-runs a step until it reports done.

  python3 deploy.py create  <id> --budget <usd> [--max-wait S] [--dry-run] [--json]
  python3 deploy.py runtime  <id> [--decision-model M] [--dry-run] [--json]
  python3 deploy.py verify   <id> [--max-wait S] [--json]
  python3 deploy.py status   <id> [--json]

Step 1 `create`  — creating wallets & funding them: per instance strategy_create_custom_strategy (records
                   strategyId IMMEDIATELY), then poll strategy_list until ACTIVE, BOUNDED by --max-wait.
                   Not all ACTIVE yet → exits `creating`; re-run `create` to RESUME (never re-creates).
                   Refuses if it finds skillName==<id> strategies not in the state file (close first).
Step 2 `runtime` — setting up the autonomous trading strategy: render each runtime.yaml with its wallet →
                   openclaw senpi runtime create. Fast, self-healing. AFTER THIS, DEPLOY IS DONE — the
                   strategy trades autonomously (scans on its own interval). Do NOT wait for the first tick.
`verify`         — OPTIONAL, only if asked "is it scanning yet?": one non-blocking check that a scan fired.

State lives in <pkg>/.deploy-state.json: per instance {strategyId, wallet, status}. Every sub-action
is persisted, so a kill mid-step just means re-run that step. The package is fetched from the remote
on first use if it isn't on disk.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli  # noqa: E402
import _fetch  # noqa: E402
import _pkg  # noqa: E402
import _smoke  # noqa: E402
from mcp_client import MCPClient, MCPError  # noqa: E402

SUBMIT_TIMEOUT = 60     # HTTP timeout for the async create submit
POLL_HTTP_TIMEOUT = 15  # HTTP timeout for fast read polls
DEFAULT_MAX_WAIT = 150  # per-call poll budget (s) — stays under the ~180s tool timeout
VERIFY_BUFFER = 120
POLL_EVERY = 10
FEE_BUFFER = 1.5        # USDC reserved per wallet for the creation fee (observed ~$1)
MIN_WALLET = 100.0      # platform minimum per strategy wallet
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


def delete_state(pkg):
    """Remove the ephemeral deploy state — called once a deploy is fully live, or on close."""
    p = _state_path(pkg)
    try:
        p.unlink()
    except OSError:
        pass


def inst_state(st, name):
    return st["instances"].setdefault(name, {"status": "pending"})


def available_usd(mcp):
    """Free USDC the create call funds from (Hyperliquid perps). None if unreadable → caller falls
    back to the requested budget."""
    try:
        res = mcp.mcp_call("account_get_portfolio", timeout=POLL_HTTP_TIMEOUT)
    except MCPError:
        return None
    port = _cli.dig(_cli.dig(res, "data", default={}) or {}, "portfolio", default={}) or {}
    avail = _cli.dig(port, "total_in_hyperliquid", "total_withdrawable")
    return float(avail) if isinstance(avail, (int, float)) else None


def plan_funding(need, budget, available):
    """Per-instance initialBudget for the instances still needing a wallet. Splits the requested
    budget by funding_share, but caps the TOTAL at the live available balance minus a per-wallet fee
    buffer (so sequential funding + creation fees can't leave a later instance $1 short). Floors at MIN_WALLET."""
    raw = {i.name: max(MIN_WALLET, round((budget or 0) * (i.funding_share or (1.0 / len(need))), 2)) for i in need}
    total = sum(raw.values())
    if available is not None:
        cap = max(0.0, available - FEE_BUFFER * len(need))
        if total > cap and total > 0:
            scale = cap / total
            raw = {n: max(MIN_WALLET, round(a * scale, 2)) for n, a in raw.items()}
    return raw


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
    if a.dry_run:
        for inst in pkg.instances:
            s = inst_state(st, inst.name)
            s.setdefault("status", "pending")
            intended = max(MIN_WALLET, round((a.budget or 0) * (inst.funding_share or 1.0), 2))
            s["plan"] = f"strategy_create_custom_strategy(~${intended:g} capped to live balance, skillName={pkg.id}, skillVersion={pkg.version})"
        return report(pkg, st, "planned", as_json=a.json)
    if a.budget is None:
        raise SystemExit("error: --budget <total> is required for `create`")

    mcp = MCPClient()

    # Universe preflight — refuse to fund a package whose hardcoded tickers aren't live HL
    # instruments (a dead name silently no-trades; the xyz:NASDAQ incident). Best-effort: if the
    # live list itself is unreachable we proceed (create would fail loudly on MCP anyway).
    try:
        import validate_universe as _vu
        unknown = _vu.unknown_tickers(_vu.package_tickers(str(pkg.dir)), _vu.live_instruments())
        if unknown:
            raise SystemExit(
                f"error: {pkg.id} hardcodes instrument(s) not live on Hyperliquid: {', '.join(unknown)}\n"
                f"Fix the package universe first (senpi-strategy-author edit path); details:\n"
                f"  python3 {Path(__file__).with_name('validate_universe.py')} {pkg.dir}")
    except SystemExit:
        raise
    except Exception as e:  # noqa
        log(f"  (universe preflight skipped: {e})")

    # Smoke gate — run scan() ONCE before funding any wallet. A package strategy exists to run a
    # PRODUCING scanner; funding one whose scan() throws / returns a non-list / emits a shape that fails
    # signal_data_schema just parks capital in a strategy that can't trade (the divergence-play incident).
    # Blocks ONLY on an unambiguous defect (threw / bad-return / bad-shape); an empty result (a
    # legitimately quiet strategy) or a harness hiccup (setup-error/timeout) just warns and proceeds.
    if not a.no_smoke:
        for inst in pkg.instances:
            es = inst.external_scanner
            if not es:
                continue  # a pure position_tracker/built-in package has nothing to smoke here
            smp = (inst.runtime_doc or {}).get("strategy", {}).get("margin_pct") if inst.runtime_doc else None
            v = _smoke.smoke(str(inst.runtime_path), es, wallet=_smoke.ZERO_WALLET, strategy_margin_pct=smp)
            for w in v.get("sizing_warnings", []):  # loud, but not a hard block — sizing is a judgment call
                log(f"  [{inst.name}] ⚠ SIZING: {w}")
            if v["status"] in _smoke.BLOCK:
                msg = (f"error: {pkg.id} [{inst.name}] failed the pre-fund scan() smoke test "
                       f"({v['status']}): {v['detail']}")
                if v["violations"]:
                    msg += "\n  violations:\n    - " + "\n    - ".join(v["violations"][:6])
                if v["traceback"]:
                    msg += "\n  traceback (tail):\n" + "\n".join(v["traceback"].strip().splitlines()[-6:])
                msg += ("\n  Fix scan()/scoring.py/runtime.yaml in source, then re-run create "
                        "(or --no-smoke if you're certain it's a false positive). No wallet was funded.")
                raise SystemExit(msg)
            if v["status"] in _smoke.WARN:
                log(f"  [{inst.name}] smoke: {v['status']} — {v['detail']}")
            else:
                log(f"  [{inst.name}] smoke: scan() emitted {v['n_signals']} valid signal(s) ✓")

    # Reconcile recorded strategies against the backend — drop any that aren't ACTIVE so we never
    # reuse a CLOSED wallet or get stuck on a FAILED one. Self-heals stale state; no manual editing.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        sid = s.get("strategyId")
        if not sid:
            continue
        m = _cli.strategies_for(mcp, strategy_id=sid, timeout=POLL_HTTP_TIMEOUT)
        status = str(_cli.strategy_status(m[0]) if m else "").upper()
        if status != "ACTIVE":
            log(f"  [{inst.name}] recorded strategy {sid[:8]} is {status or 'gone'} — discarding, will recreate")
            st["instances"][inst.name] = {"status": "pending"}
    save_state(pkg, st)

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

    # size the to-create instances against the LIVE available balance (minus a per-wallet fee buffer),
    # so sequential funding + creation fees can't leave a later instance short.
    amounts = plan_funding(need, a.budget, available_usd(mcp)) if need else {}

    # create any instance that has no strategyId yet — record the id IMMEDIATELY (before polling),
    # so an interrupted run resumes instead of re-creating.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        if s.get("strategyId"):
            continue
        amt = amounts.get(inst.name, max(MIN_WALLET, round((a.budget or 0) * (inst.funding_share or 1.0), 2)))
        # Name each wallet for its role in the strategy (matches the pkg-instance runtime naming),
        # so wallets are legible in the app / balances / notifications instead of a bare 0x address.
        # e.g. a WhaleHunter deploy → "whalehunter-long", "whalehunter-short". Sanitized to the
        # strategyName rules: 3-40 chars, no whitespace (-> '-'), [A-Za-z0-9_-] only.
        multi = len(pkg.instances) > 1
        sname = f"{pkg.id}-{inst.name}" if (multi and inst.name) else str(pkg.id)
        sname = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"\s+", "-", sname.strip())).strip("-_")[:40] or str(pkg.id)[:40]
        log(f"  [{inst.name}] creating wallet {sname!r} (initialBudget=${amt:g})…")

        def _create(name=None):
            kw = dict(initialBudget=amt, positions=[], skillName=pkg.id, skillVersion=pkg.version)
            if name:
                kw["strategyName"] = name
            return mcp.mcp_call("strategy_create_custom_strategy", timeout=SUBMIT_TIMEOUT, **kw)

        try:
            res = _create(sname)
        except MCPError as e:
            # Naming is best-effort — a name conflict/format rejection must never block the deploy.
            if any(c in str(e) for c in ("SERR055", "SERR056", "SERR058")) or "name" in str(e).lower():
                log(f"  [{inst.name}] name {sname!r} rejected ({e}); creating without a custom name")
                try:
                    res = _create()
                except MCPError as e2:
                    s["status"] = "pending"
                    s["error"] = f"create submit: {e2}"
                    save_state(pkg, st)
                    return report(pkg, st, "failed", as_json=a.json)
            else:
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
    if not_ready and not a.dry_run:
        raise SystemExit(f"error: wallets not ready for {', '.join(not_ready)} — run `deploy.py create {pkg.id}` first")
    if pkg.any_needs_model and not a.decision_model and not a.dry_run:
        raise SystemExit("error: a runtime has a decision_mode: llm action — pass --decision-model <bare-model>")

    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        wallet = s.get("wallet") or "0x<wallet-from-create>"  # placeholder only reachable in --dry-run
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
        # Reconcile an existing runtime of this id: same+correct wallet → already deployed (skip);
        # stale (wallet differs, or its wallet is CLOSED — e.g. orphaned by a prior close) → delete it
        # and recreate. Self-heals the "already exists" / "wallet CLOSED" collisions.
        existing = _cli.find_runtime(inst.runtime_name)
        if existing:
            if _cli.wallet_match(_cli.runtime_wallet(existing), wallet):
                s.update(status="registered", error=None)
                save_state(pkg, st)
                continue
            log(f"  [{inst.name}] stale runtime {inst.runtime_name!r} (wallet mismatch) — deleting")
            _cli.run_cli(["openclaw", "senpi", "runtime", "delete", inst.runtime_name], timeout=60)
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
    note = ("Some instances failed to register — see errors above." if failed else
            "Done — " + pkg.id + " is deployed and trading autonomously (it scans on its own cadence and "
            "opens positions when its signals fire). No need to wait for the first tick. "
            "Optional: `deploy.py verify " + pkg.id + "` only if you want to confirm a scan has fired.")
    return report(pkg, st, overall, note=note, as_json=a.json)


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


def _ticked(sc):
    """Has this scanner actually RUN (an invocation) — independent of whether it EMITTED a signal?
    `runCount` counts EMITS, so a healthy but selective scanner that correctly stayed quiet reads
    `runCount: 0` forever; keying liveness off it falsely reports such a scanner as "never ticked".
    Liveness is `alive` / `lastRun*` / invocation counts; `runCount`/`signals` > 0 only CONFIRM."""
    if not isinstance(sc, dict):
        return False
    if _cli.dig(sc, "alive"):
        return True
    if _cli.dig(sc, "lastRunFinishedAt", "lastRunStartedAt", "lastRunAt"):
        return True
    for k in ("runs", "ticks", "runCount", "signals"):
        v = _cli.dig(sc, k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return True
    return False


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
        sname = inst.external_scanner.get("name")
        # Prefer the purpose-built per-scanner health RPC (separates runs from signals, exposes alive);
        # fall back to the full state RPC. Liveness via _ticked (NOT runCount — see its docstring).
        health = _cli.cli_json(["openclaw", "senpi", "scanner", "-r", inst.runtime_name, "--json"], POLL_HTTP_TIMEOUT)
        sc = _deep_find_scanner(health, sname) if health else None
        if sc is None:
            state = _cli.cli_json(["openclaw", "senpi", "state", "-r", inst.runtime_name, "--json"], POLL_HTTP_TIMEOUT)
            sc = _deep_find_scanner(state, sname) if state else None
        if _ticked(sc):
            s["status"] = "live"
            save_state(pkg, st)
        else:
            pending.append((inst.name, f"awaiting first tick (~{inst.interval_seconds or '?'}s cadence)"))
    return pending


def cmd_verify(pkg, a, log):
    # Single fast check by default — the first scan() tick is gated by the scanner's interval_seconds
    # (e.g. spider swing = 300s), so blocking here would just burn the tool budget. Report liveness or
    # the expected cadence and let the agent re-run when it's worth re-checking. `--max-wait S` opts
    # into a bounded poll for fast instances.
    st = load_state(pkg)
    deadline = time.time() + a.max_wait
    while True:
        pending = _check_ticks(pkg, st)
        if not pending:
            out = report(pkg, st, "live", as_json=a.json)
            delete_state(pkg)  # deploy complete → state is ephemeral; next deploy starts clean
            return out
        if time.time() >= deadline:
            note = "Not ticking yet — each instance's first scan() fires on its interval_seconds:\n  " + \
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
    pc.add_argument("--no-smoke", action="store_true",
                    help="Skip the pre-fund scan() smoke test (escape hatch for a known false positive).")

    pr = sub.add_parser("runtime", help="Step 2: render + create the runtime(s) on the ready wallet(s).")
    common(pr)
    pr.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pr.add_argument("--dry-run", action="store_true")

    pv = sub.add_parser("verify", help="Step 3: confirm each scanner is ticking (fast single check; re-run as needed).")
    common(pv)
    pv.add_argument("--max-wait", type=int, default=0,
                    help="Default 0 = one fast check (first tick is gated by interval_seconds; re-run later). "
                         ">0 keeps polling up to S seconds (useful for fast instances).")

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
