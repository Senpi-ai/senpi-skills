#!/usr/bin/env python3
"""Deploy a strategy PACKAGE — a thin wrapper over the runtime's `openclaw senpi deploy` verb.

The runtime owns the whole go-live path now: funds preflight → wallet create+fund (with
skillName/skillVersion attribution) → runtime install → one observed scanner tick. It runs the
job DETACHED, so this script starts it and then polls `openclaw senpi deploy status` until the
job is terminal, relaying the verb's report VERBATIM. Nothing here re-derives a number, a
lifecycle claim, or a refusal string — the `[E_*]` codes pass straight through.

  python3 deploy.py validate <id>                                  # preflight (no side effects)
  python3 deploy.py create   <id> --budget <usd> [--max-wait S]    # run the deploy (create+fund+install+tick)
  python3 deploy.py runtime  <id> [--decision-model M]             # resume/complete the same deploy
  python3 deploy.py verify   <id> [--max-wait S]                   # re-run: reconciles, then observes a tick
  python3 deploy.py status   <id>                                  # the last deploy job's status

The three action subcommands all drive the SAME idempotent verb; re-running any of them resumes
(the verb reconciles against the backend + the runtime registry and adopts whatever exists). They
are kept as distinct verbs so existing docs, habits and transcripts stay valid.

There is NO local deploy-state file any more. The backend strategies and the runtime registry ARE
the record — the sidecar `.deploy-state.json` was the source of the whole `E_STATE_*` lost-state
class, and it is gone. Package resolution (path or bare catalog id, with the remote fetch) and the
structural/universe preflight stay here; everything after them lives in the runtime.
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

START_TIMEOUT = 60      # the verb detaches in ~1s; this is only the spawn budget
STATUS_TIMEOUT = 30
DEFAULT_MAX_WAIT = 600  # how long THIS script waits for the detached job to reach a terminal state
POLL_EVERY = 5
TERMINAL_BAD = ("refused", "failed")


# ---------- package resolution (unchanged — discovery stays in skills) ----------

def ensure_pkg(arg, ref, log):
    # A package that EXISTS on disk is authoritative — load it and let any BadPackage surface as the
    # real, fixable error. NEVER fall through to a (possibly stale) remote fetch just because a local
    # package is invalid: that silently deploys the wrong version and discards the author's local fixes.
    # Only a bare id that isn't a local directory triggers the catalog fetch from remote.
    if (_pkg.resolve_pkg_dir(arg) / "strategy.yaml").is_file():
        return _pkg.load(arg)
    sid = Path(arg).name
    # Fetch to the DURABLE root (absolute, CWD-independent), never a CWD-relative path: a relative
    # dest resolved inside a managed skill dir gets wiped on the next SKILL.md version bump.
    dest_root = _pkg.strategies_root()
    # A dest dir carrying deploy state but no loadable strategy.yaml is a partially-wiped DEPLOYED
    # package — fetching would graft pristine catalog files onto a live deploy's remains. Refuse; this
    # needs eyes, not a fetch. (This script no longer WRITES `.deploy-state.json`, but boxes deployed
    # before the verb still carry one, and `_pkg.resolve_pkg_dir` still reads it as the tie-break.)
    if (dest_root / sid / ".deploy-state.json").is_file():
        raise SystemExit(
            f"error: {dest_root / sid} carries deploy state (.deploy-state.json) but no loadable "
            f"strategy.yaml — refusing to fetch the catalog copy over a deployed package's remains.\n"
            f"  Inspect the directory and restore its files (or move the state aside) first.")
    log(f"package {sid!r} not on disk — fetching from remote into {dest_root}…")
    try:
        _fetch.fetch_package(sid, dest_root, ref=ref)
        return _pkg.load(dest_root / sid)
    except (_fetch.FetchError, _pkg.BadPackage) as e:
        raise SystemExit(
            f"error: {e}\n"
            f"  {arg!r} is not a package on disk (tried {arg!r}, {dest_root / sid}, and "
            f"'strategies/{sid}' relative to the current directory) and could not be fetched as a "
            f"catalog id.\n"
            f"  Deploying a locally-authored package? Pass its DIRECTORY path instead of a bare id, "
            f"e.g.: deploy.py validate /data/workspace/strategies/{sid}")


def full_validate(pkg):
    """Every error deploy.py can see, in ONE pass, with NO side effects: structural (`_pkg.validate`)
    plus a render dry-run per instance (unresolved `${...}`, a `decision_mode: llm` with no model). Lets
    `validate` and the pre-deploy gate report everything BEFORE a wallet is funded. (Runtime-engine
    schema errors still surface from the verb's install step, but everything modellable here is caught
    first.)"""
    errs = list(_pkg.validate(pkg))
    for inst in pkg.instances:
        if inst.runtime_doc is None:
            continue  # already reported by _pkg.validate
        try:
            inst.render("0x0000000000000000000000000000000000000000",
                        model_env=pkg.model_env, model="validation-model")
        except _pkg.BadPackage as e:
            errs.append(str(e))
    return errs


def universe_preflight(pkg, log):
    """Refuse to deploy a package whose hardcoded tickers aren't live HL instruments (a dead name
    silently no-trades; the xyz:NASDAQ incident). Stays on this side because it is a PACKAGE check,
    not a deploy step. Best-effort: if the live instrument list is unreachable we proceed."""
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
    except Exception as e:  # noqa: BLE001 — enrichment, never a gate on its own
        log(f"  (universe preflight skipped: {e})")


# ---------- the verb ----------

def budget_arg(v):
    """A dollar amount as the verb's `--budget` flag accepts it: bare digits, no `$`, no comma
    grouping (the flag parses a float, so grouping would fail)."""
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def _relay(rc, out, err):
    """Print a failed CLI call's own words, unedited. The verb's refusals already carry their code,
    their facts and their next step — anything added here would be a second, drifting producer."""
    text = _cli.error_tail(err, out) or "openclaw senpi deploy failed (no error output)"
    print(text, file=sys.stderr)
    return rc if rc and rc > 0 else 2


def start_deploy(pkg, a, log):
    """Start the detached job. Returns the deployId, or exits with the verb's own refusal text."""
    args = ["openclaw", "senpi", "deploy", "-p", str(pkg.dir), "--json"]
    if a.budget is not None:
        args += ["--budget", budget_arg(a.budget)]
    if getattr(a, "decision_model", None):
        args += ["--decision-model", a.decision_model]
    if getattr(a, "tick_wait", None) is not None:
        args += ["--tick-wait", str(a.tick_wait)]
    if getattr(a, "dry_run", False):
        print("planned: " + " ".join(args))
        raise SystemExit(0)
    log("  starting the deploy job…")
    rc, out, err = _cli.run_cli(args, timeout=START_TIMEOUT)
    if rc != 0:
        raise SystemExit(_relay(rc, out, err))
    started = _cli._extract_json(out) or {}
    deploy_id = started.get("deployId")
    if not deploy_id:
        raise SystemExit(_relay(2, out, err))
    return deploy_id


def status_snapshot(deploy_id):
    """The job snapshot as JSON, or None if the status call did not return one this pass."""
    args = ["openclaw", "senpi", "deploy", "status"]
    if deploy_id:
        args.append(deploy_id)
    args.append("--json")
    rc, out, _err = _cli.run_cli(args, timeout=STATUS_TIMEOUT)
    if rc != 0:
        return None
    snap = _cli._extract_json(out)
    return snap if isinstance(snap, dict) else None


def print_status(deploy_id, as_json, snap):
    """Relay the verb's own rendering — the human table comes from `deploy status`, unedited."""
    if as_json:
        print(json.dumps(snap, indent=2))
        return
    args = ["openclaw", "senpi", "deploy", "status"]
    if deploy_id:
        args.append(deploy_id)
    rc, out, err = _cli.run_cli(args, timeout=STATUS_TIMEOUT)
    print(out if out.strip() else _cli.error_tail(err, out))
    return rc


def wait_for_terminal(deploy_id, max_wait, log):
    """Poll until the job leaves `running`, or the wait budget runs out. Returns the last snapshot."""
    deadline = time.time() + max_wait
    snap = None
    while True:
        fresh = status_snapshot(deploy_id)
        if fresh:
            snap = fresh
            state = (snap.get("state") or {}).get("status")
            if state != "running":
                return snap
            log(f"  {(snap.get('state') or {}).get('phase', '…')}…")
        if time.time() >= deadline:
            return snap
        time.sleep(POLL_EVERY)


def run_deploy(pkg, a, log):
    """Start → poll → relay. Exit code: 2 when the verb's report is refused/failed, else 0."""
    deploy_id = start_deploy(pkg, a, log)
    snap = wait_for_terminal(deploy_id, a.max_wait, log)
    if snap is None:
        print(f"Could not read the deploy job's status. Check it directly: "
              f"openclaw senpi deploy status {deploy_id}", file=sys.stderr)
        return 2
    print_status(deploy_id, a.json, snap)
    state = snap.get("state") or {}
    if state.get("status") == "running":
        print(f"\nStill running after {a.max_wait}s — the job continues in the background. "
              f"Watch it: openclaw senpi deploy status {deploy_id}")
        return 0
    return 2 if state.get("overall") in TERMINAL_BAD else 0


# ---------- cli ----------

def main(argv):
    ap = argparse.ArgumentParser(
        description="Deploy a strategy package via the runtime's `openclaw senpi deploy` verb.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("package", help="Strategy id (e.g. spider) or package dir (strategies/spider).")
        p.add_argument("--ref", default=None, help="Branch/ref to fetch the package from if not on disk.")
        p.add_argument("--json", action="store_true")

    pc = sub.add_parser("create", help="Run the deploy: create + fund the wallet(s), install, observe a tick.")
    common(pc)
    pc.add_argument("--budget", type=float, default=None, help="Total USDC split across wallets by funding_share.")
    pc.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="How long to wait for the job (s).")
    pc.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pc.add_argument("--tick-wait", type=int, default=None,
                    help="Seconds the job waits to observe one verified scanner tick (0 skips).")
    pc.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("runtime", help="Resume/complete the same deploy (installs the runtime(s)).")
    common(pr)
    pr.add_argument("--budget", type=float, default=None, help="Only needed if a wallet still has to be created.")
    pr.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="How long to wait for the job (s).")
    pr.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pr.add_argument("--tick-wait", type=int, default=None,
                    help="Seconds the job waits to observe one verified scanner tick (0 skips).")
    pr.add_argument("--dry-run", action="store_true")

    pv = sub.add_parser("verify", help="Re-run the deploy: reconciles what exists, then observes a scanner tick.")
    common(pv)
    pv.add_argument("--budget", type=float, default=None, help="Only needed if a wallet still has to be created.")
    pv.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="How long to wait for the job (s).")
    pv.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pv.add_argument("--tick-wait", type=int, default=None,
                    help="Seconds the job waits to observe one verified scanner tick (0 skips).")
    pv.add_argument("--dry-run", action="store_true")

    ps = sub.add_parser("status", help="Show the last deploy job for this agent.")
    common(ps)

    pval = sub.add_parser("validate",
                          help="Preflight: is the package deploy-ready? (structural + render — no side effects)")
    common(pval)

    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    # `status` reports the agent's last deploy JOB, which has nothing to do with the package on
    # disk — so it must not resolve (and possibly remote-fetch) a package just to print a snapshot.
    if a.cmd == "status":
        snap = status_snapshot(None)
        if snap is None:
            print("No deploy job recorded on this agent. Start one:\n"
                  f"  python3 {Path(__file__).name} create <id> --budget <usd>", file=sys.stderr)
            sys.exit(2)
        print_status(None, a.json, snap)
        sys.exit(2 if (snap.get("state") or {}).get("overall") in TERMINAL_BAD else 0)

    pkg = ensure_pkg(a.package, a.ref, log)

    # `validate` is the standalone, side-effect-free preflight; the action subcommands run the SAME
    # full check before the verb touches money.
    gate = full_validate(pkg)
    if a.cmd == "validate":
        if a.json:
            print(json.dumps({"status": "valid" if not gate else "invalid", "id": pkg.id, "errors": gate}))
        elif gate:
            print(f"✗ {pkg.id}: {len(gate)} issue(s) to fix before deploy:", file=sys.stderr)
            for e in gate:
                print(f"    - {e}", file=sys.stderr)
        else:
            print(f"✓ {pkg.id}: deploy-ready ({len(pkg.instances)} instance(s))")
        sys.exit(2 if gate else 0)
    if gate:
        print(f"✗ {pkg.id}: {len(gate)} issue(s) to fix before deploy:", file=sys.stderr)
        for e in gate:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    universe_preflight(pkg, log)
    sys.exit(run_deploy(pkg, a, log))


if __name__ == "__main__":
    main(sys.argv)
