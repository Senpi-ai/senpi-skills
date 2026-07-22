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
    # A package that EXISTS on disk is authoritative — load it and let any BadPackage surface as the
    # real, fixable error. NEVER fall through to a (possibly stale) remote fetch just because a local
    # package is invalid: that silently deploys the wrong version and discards the author's local fixes.
    # Only a bare id that isn't a local directory triggers the catalog fetch from remote.
    if (_pkg.resolve_pkg_dir(arg) / "strategy.yaml").is_file():
        return _pkg.load(arg)
    sid = Path(arg).name
    log(f"package {sid!r} not on disk — fetching from remote…")
    try:
        _fetch.fetch_package(sid, "strategies", ref=ref)
        return _pkg.load(sid)
    except (_fetch.FetchError, _pkg.BadPackage) as e:
        raise SystemExit(
            f"error: {e}\n"
            f"  {arg!r} is not a package on disk (tried {arg!r} and 'strategies/{arg}' relative to the "
            f"current directory) and could not be fetched as a catalog id.\n"
            f"  Deploying a locally-authored package? Pass its DIRECTORY path instead of a bare id, "
            f"e.g.: deploy.py validate /data/workspace/strategies/{sid}")


def full_validate(pkg):
    """Every error deploy.py can see, in ONE pass, with NO side effects: structural (`_pkg.validate`)
    plus a render dry-run per instance (unresolved `${...}`, a `decision_mode: llm` with no model). Lets
    `validate` and the `create` preflight report everything BEFORE a wallet is funded. (Runtime-engine
    schema errors still surface at `runtime`, but everything modellable here is caught first.)"""
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


def _safe_unlink(p):
    """Delete a file, ignoring if it's already gone."""
    try:
        Path(p).unlink()
    except OSError:
        pass


def delete_state(pkg):
    """Remove the ephemeral deploy state — called once a deploy is fully live, or on close. Also sweeps
    any rendered `<inst>.deploy.runtime.yaml` build artifacts: they carry a baked-in wallet, and a stale
    one left on disk is exactly what a lost-state manual redeploy wrongly picks up (the reuse trap)."""
    _safe_unlink(_state_path(pkg))
    for inst in pkg.instances:
        if inst.runtime_path:
            _safe_unlink(inst.runtime_path.with_name(f"{inst.name}.deploy.runtime.yaml"))


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
    """Per-instance initialBudget for the instances still needing a wallet, split by funding_share and
    floored at MIN_WALLET. Returns (amounts, shortfall).

    The requested budget is a HARD TARGET, not a suggestion: if the live balance can't cover it (minus a
    per-wallet fee buffer) we return a `shortfall` dict and the caller HALTS — we never silently fund
    LESS than asked. The old behaviour scaled every wallet down to fit `available`, which quietly turned
    a "$1,000 / $2,000" request into two $100 floor wallets; that silent under-funding is the bug this
    removes. (`available` unreadable → shortfall stays None → proceed; create would fail loudly anyway.)"""
    raw = {i.name: max(MIN_WALLET, round((budget or 0) * (i.funding_share or (1.0 / len(need))), 2)) for i in need}
    total = round(sum(raw.values()), 2)
    shortfall = None
    if available is not None:
        usable = max(0.0, round(available - FEE_BUFFER * len(need), 2))
        if total > usable:
            shortfall = {"requested": total, "available": round(float(available), 2),
                         "usable": usable, "short_by": round(total - usable, 2), "wallets": len(need)}
    return raw, shortfall


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
                  + (f"  requested=${r['requested']:g}" if r.get("requested") else "")
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

    # NEVER reuse an existing strategy's wallet. Re-using a funded, runtime-less wallet is the trap an
    # agent keeps falling into (creates <id>, never deploys the runtime, keeps landing back on the same
    # dead wallet); creating a second alongside it double-funds. So every `create` deploys on a FRESH
    # wallet, resolving any existing OPEN <id> strategy first:
    #   • RUNNING runtime → a live, working deploy: REFUSE to silently flatten it; redeploy is explicit
    #     (close.py first). Protects a real book from an accidental `create`.
    #   • no running runtime → the funded-but-stuck trap: CLOSE it (recovers its funds), then this deploy
    #     creates a fresh wallet. strategy_close is async, so hand off and re-run `create` once it's closed.
    runtimes = _cli.list_runtimes()
    existing_open = [s for s in _cli.strategies_for(mcp, skill_name=pkg.id) if _cli.strategy_open(s)]

    def _has_running_runtime(s):
        rt = _cli.find_runtime_by_wallet(_cli.strategy_wallet(s))
        return bool(rt) and _cli.runtime_running(rt)

    live = [s for s in existing_open if _has_running_runtime(s)]
    if live:
        raise SystemExit(
            f"error: {pkg.id} is already deployed AND running ({len(live)} live wallet(s)) — `create` will not "
            f"silently close a live strategy. To redeploy on a fresh wallet, close it first:\n"
            f"  python3 {Path(__file__).with_name('close.py')} {pkg.id}\n"
            f"Or just re-check it:  python3 {Path(__file__).name} verify {pkg.id}")

    if existing_open:  # open but NOT running → the runtime-less trap: close (recover funds) → fresh wallet
        import close as _close  # noqa: E402 — sibling module, lazy import
        for s in existing_open:
            _close.close_one(pkg.id, s, runtimes, False, log)
        for inst in pkg.instances:  # forget the old ids so the re-run makes NEW wallets, never resumes them
            prev = inst_state(st, inst.name)
            st["instances"][inst.name] = ({"status": "pending", "requested": prev["requested"]}
                                          if prev.get("requested") else {"status": "pending"})
        save_state(pkg, st)
        return report(pkg, st, "closing-existing", note=(
            f"Found {len(existing_open)} existing {pkg.id} strateg(y/ies) with NO running runtime — closing "
            f"them (recovering funds) so this deploys on a FRESH wallet, never reusing the runtime-less one. "
            f"strategy_close is async; re-run `python3 {Path(__file__).name} create {pkg.id} --budget "
            f"{a.budget:g}` once they're CLOSED and the funds are back."), as_json=a.json)

    need = [i for i in pkg.instances if not inst_state(st, i.name).get("strategyId")]

    # Size the to-create instances against the LIVE available balance. The requested --budget is a HARD
    # TARGET: if the balance can't cover it, HALT with the shortfall (fund more / lower the ask) rather
    # than silently funding the $100 floor. Nothing is created on this path.
    amounts, shortfall = plan_funding(need, a.budget, available_usd(mcp)) if need else ({}, None)
    if shortfall:
        return report(pkg, st, "underfunded", note=(
            f"Requested ${shortfall['requested']:g} across {shortfall['wallets']} wallet(s) "
            f"(min ${MIN_WALLET:g}/wallet), but only ${shortfall['available']:g} is accessible "
            f"(${shortfall['usable']:g} after fees) — short by ${shortfall['short_by']:g}. "
            f"NOT funding; no wallet was created. Add USDC (or free some from another strategy), or "
            f"confirm a lower amount with the user and re-run `create` with --budget ≤ ${shortfall['usable']:g}."),
            as_json=a.json)

    # create any instance that has no strategyId yet — record the id IMMEDIATELY (before polling),
    # so an interrupted run resumes instead of re-creating.
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        if s.get("strategyId"):
            continue
        amt = amounts.get(inst.name, max(MIN_WALLET, round((a.budget or 0) * (inst.funding_share or 1.0), 2)))
        s["requested"] = amt  # remember what the user asked to fund → reconciled against actual at verify
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

def _recover_wallet(pkg, inst, active):
    """Re-resolve one instance's FRESH wallet from the live ACTIVE <id> strategies when the deploy
    state was lost (the sub-agent died before persisting it). Returns (wallet, error).

    NEVER guesses: if the backend is ambiguous (0, or >1 candidate wallets for the instance) it
    returns an error so cmd_runtime refuses rather than binding a runtime to the wrong/old wallet —
    the exact reuse trap (agent hand-registers onto a stale wallet from a leftover rendered yaml)."""
    if len(pkg.instances) > 1:  # multi-instance: match by the name create assigned each wallet
        cands = [s for s in active if _cli.strategy_name(s) == f"{pkg.id}-{inst.name}"]
    else:  # single-instance: the lone ACTIVE <id> strategy is this instance
        cands = list(active)
    wallets = {str(_cli.strategy_wallet(s)).lower() for s in cands if _cli.strategy_wallet(s)}
    if cands and len(wallets) == 1:
        return _cli.strategy_wallet(cands[0]), None
    if not cands:
        return None, f"no ACTIVE {pkg.id} wallet on the backend for instance {inst.name!r}"
    return None, f"{len(cands)} ACTIVE {pkg.id} wallets match instance {inst.name!r} — ambiguous, won't guess"


def cmd_runtime(pkg, a, log):
    st = load_state(pkg)

    # Self-heal a lost/partial deploy state: if `create` succeeded but its state file was lost (the
    # sub-agent died before persisting), re-resolve each instance's FRESH wallet from the live ACTIVE
    # <id> strategies instead of dead-ending. Otherwise the agent improvises a manual `runtime update`
    # onto an OLD wallet baked into a leftover rendered yaml / a pre-existing same-name runtime — the
    # reuse trap. We never guess: an ambiguous backend refuses with a redeploy-fresh instruction.
    missing = [i for i in pkg.instances if not inst_state(st, i.name).get("wallet")]
    if missing and not a.dry_run:
        active = _cli.strategies_for(MCPClient(), skill_name=pkg.id, statuses=["ACTIVE"])
        recovered, unresolved = [], []
        for inst in missing:
            w, err = _recover_wallet(pkg, inst, active)
            if w:
                inst_state(st, inst.name).update(wallet=w, status="active")
                recovered.append(inst.name)
            else:
                unresolved.append((inst.name, err))
        if recovered:
            save_state(pkg, st)
            log(f"  deploy-state was lost — recovered fresh wallet(s) from the backend for: {', '.join(recovered)}")
        if unresolved:
            lines = "\n".join(f"    - {n}: {why}" for n, why in unresolved)
            raise SystemExit(
                f"error: wallet(s) not ready and not safely recoverable:\n{lines}\n"
                f"Do NOT hand-register a runtime on an old wallet. Redeploy on a fresh wallet:\n"
                f"  python3 {Path(__file__).with_name('close.py')} {pkg.id}   # if a stale {pkg.id} wallet is lingering\n"
                f"  python3 {Path(__file__).name} create {pkg.id} --budget <usd>")
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
        # Reconcile an existing runtime of this id. The runtime is ALWAYS (re)built from scratch on the
        # freshly-resolved wallet — we never reuse an old wallet a same-name runtime is still bound to:
        #   same + correct wallet → already deployed on this wallet (idempotent skip);
        #   wallet differs / unreadable / its wallet is CLOSED (orphaned by a prior close) → DELETE the
        #   old runtime and recreate on the fresh wallet (never `runtime update` it in place).
        existing = _cli.find_runtime(inst.runtime_name)
        if existing:
            if _cli.wallet_match(_cli.runtime_wallet(existing), wallet):
                s.update(status="registered", error=None)
                save_state(pkg, st)
                _safe_unlink(build)  # runtime owns its config now — drop the rendered yaml so no stale wallet lingers
                continue
            log(f"  [{inst.name}] existing runtime {inst.runtime_name!r} is on a different/old wallet "
                f"— deleting and recreating on the fresh wallet (never reusing the old one)")
            _cli.run_cli(["openclaw", "senpi", "runtime", "delete", inst.runtime_name], timeout=60)
        build.write_text(text)
        log(f"  [{inst.name}] runtime create…")
        rc, _o, err = _cli.run_cli(["openclaw", "senpi", "runtime", "create", "-p", str(build),
                                    "--runtime-id", inst.runtime_name], timeout=120)
        if rc != 0:
            s.update(error=(err or "runtime create failed").strip()[:300])
            save_state(pkg, st)
            continue  # keep the rendered yaml on failure for debugging
        s.update(status="registered", error=None)
        save_state(pkg, st)
        _safe_unlink(build)  # registered — the runtime holds its own config; remove the rendered wallet-bearing yaml

    if a.dry_run:
        return report(pkg, st, "planned", as_json=a.json)
    failed = [i.name for i in pkg.instances if inst_state(st, i.name).get("error")]
    overall = "failed" if failed else "registered"
    note = ("Some instances failed to register — see errors above." if failed else
            "Registered — now confirm it's actually live: run `deploy.py verify " + pkg.id + "`. "
            "That gate checks every instance is runtime-running + scanner-active + DSL-wired + funded; "
            "the strategy is NOT live until it passes. (verify does not wait for the first scan tick.)")
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


def _scanner_verdict(inst, state, status):
    """(status, detail) for the instance's external_scanner — judged from what the RUNTIME reports,
    and NEVER failing closed on a read it couldn't get. Called ONLY after the caller has confirmed the
    runtime is RUNNING (via `runtime list`), so the reliable backbone is already established here.
      ticked     — runCount>0 / heartbeat (lastAliveAt) / runtime-reported healthy — actually scanning
      scheduled  — registered + enabled + healthy, no tick yet (first tick fires on interval_seconds)
      supervised — reads unavailable this pass, but the runtime is running and supervising the declared
                   scanner ⇒ live-but-unmeasured (the flaky status/state reads are enrichment, not a gate)
      broken     — POSITIVE evidence of breakage: disabled / erroring / runtime-reported unhealthy

    `ticked`/`scheduled`/`supervised` all count as LIVE. Two enrichment sources, keyed by the stable
    scanner name (== the runtime's `scannerId`):
      • `senpi state`  — the rich per-scanner row (runCount, lastAliveAt, lastError, enabled, health).
        Best when available, but `getSystemState` THROWS for minutes after a fresh start, so `state`
        is often None here (see `_cli.runtime_state`).
      • `senpi status` — the runtime's own per-scanner health verdict. Lighter, and it keeps
        answering while `state` is still throwing — so it's the fallback that keeps a live-but-not-
        -yet-introspectable scanner from being branded dead.

    IMPORTANT — external scanners: runCount/lastRun stay 0/null until the runtime hears the first
    POST, and a healthy scanner that finds no setup still ticks (barren heartbeat). So absence of
    runs is NEVER breakage on its own; `health`/`lastAliveAt` and the `status` verdict carry the
    truth. (Live-confirmed: a runtime whose `state` threw for ~9 min while both scanners logged and
    `status` said '2/2 enabled and healthy' — the old code called that 'scanner not mounted'.)"""
    name = inst.external_scanner.get("name")
    sc = _deep_find_scanner(state, name) if state else None
    if sc:
        if _cli.dig(sc, "enabled", default=True) is False:
            return "broken", "scanner disabled"
        err = _cli.dig(sc, "lastError")
        cec = _cli.dig(sc, "consecutiveErrorCount", default=0) or 0
        if err or (isinstance(cec, (int, float)) and cec >= 1):
            return "broken", f"scanner erroring: {str(err)[:120] if err else f'{int(cec)} consecutive errors'}"
        if str(_cli.dig(sc, "health") or "").lower() == "unhealthy":
            return "broken", "scanner reported unhealthy by the runtime"
        runs = _cli.dig(sc, "runCount", "ticks", "runs", default=0) or 0
        if isinstance(runs, (int, float)) and runs > 0:
            return "ticked", f"{int(runs)} scan(s)"
        if _cli.dig(sc, "lastAliveAt"):
            return "ticked", "external scanner heartbeat live"
        return "scheduled", f"awaiting first tick (~{inst.interval_seconds or '?'}s cadence)"
    # `state` unreadable → trust the runtime's own scanner-health from `senpi status`
    sh, _list_seen = _cli.scanner_health_in_status(status, name)
    if sh == "unhealthy":
        return "broken", "scanner reported unhealthy by the runtime (per status)"
    if sh in ("healthy", "degraded"):
        return "ticked", f"healthy per runtime status ({sh})"
    # Neither `state` nor `status` was readable this pass — but we only reach here AFTER the caller
    # confirmed the runtime is RUNNING via `runtime list` (the authoritative inventory; `status`/`state`
    # JSON are flaky-empty for a minute+ after start — seen live: verify got nothing while a manual
    # `status -r`/`state -r` seconds apart returned healthy). A running runtime SPAWNS + SUPERVISES this
    # external scanner (restarting it on crash), and the scanner is declared in the deployed runtime.yaml
    # — so running runtime + declared scanner ⇒ it's being driven. Report LIVE-but-unmeasured, never
    # `broken`. A genuinely broken scanner still trips the `broken` branches above whenever a read lands.
    return "supervised", "runtime running + scanner supervised (live health read unavailable this pass)"


def _dsl_verdict(inst, status_json):
    """(status, detail) for DSL protection. The STATIC check — the deployed runtime.yaml has an
    `exit.dsl_preset` — is load-bearing (it closes the funded-but-no-DSL hole); the runtime monitor's
    `enabled` flag (from `senpi status`) confirms it wired. If status is unreadable we trust the static
    config — never fail the gate on an unreadable status alone."""
    if not inst.has_dsl:
        return "config-missing", "runtime.yaml has no exit.dsl_preset — positions would run naked"
    dsl = _cli._deep_first(status_json, ["dsl"]) if status_json else None
    if isinstance(dsl, dict) and _cli.dig(dsl, "enabled") is False:
        return "monitor-down", "DSL configured but its monitor is disabled in the runtime"
    return "wired", "exit.dsl_preset present; DSL monitor active"


def _budget_verdict(s, funded_by_id):
    """(status, detail) comparing the wallet's ACTUAL funded USDC to what was requested. Best-effort: if
    we can't read the funded amount we don't block (the create-time shortfall halt is the primary guard;
    this reconciliation also catches a backend partial-fund)."""
    req = s.get("requested")
    funded = funded_by_id.get(s.get("strategyId"))
    if not req or funded is None:
        return "ok", (f"${funded:g}" if isinstance(funded, (int, float)) else "")
    if funded < req * 0.9:
        return "underfunded", f"funded ${funded:g} of requested ${req:g}"
    return "ok", f"${funded:g} (asked ${req:g})"


def _check_live(pkg, st, mcp):
    """One pass over every instance → the composite live verdict: runtime running AND scanner active AND
    DSL wired AND budget funded. Returns a list of per-instance rows."""
    # one strategy_list read → actual funded amount per strategyId (best-effort budget reconciliation)
    funded_by_id = {}
    try:
        for m in _cli.strategies_for(mcp, skill_name=pkg.id, timeout=POLL_HTTP_TIMEOUT):
            fid = _cli.strategy_id_of(m)
            fv = _cli.dig(_cli.strategy_obj(m), "totalFunded", "netFunded", "initialBudget")
            if fid and isinstance(fv, (int, float)):
                funded_by_id[fid] = float(fv)
    except Exception:  # noqa: BLE001 — a read hiccup must not fail the gate; budget stays best-effort
        pass
    health = _cli.runtime_health_map()  # one status --json for all runtimes' DSL/health lines
    rows = []
    for inst in pkg.instances:
        s = inst_state(st, inst.name)
        # `runtime_health_map` (getHealthStatus) lists ONLY running runtimes, so a hit already proves
        # 'running' — skip the extra `runtime list` call (its default 60s timeout is verify's worst
        # tail-latency) in the common path. Only when the map is flaky-empty for this runtime do we
        # fall back to the authoritative text list to tell 'not running' from 'status hiccup'.
        status = health.get(inst.runtime_name)
        if status:
            running = True
        else:
            rt = _cli.find_runtime(inst.runtime_name)
            running = bool(rt) and _cli.runtime_running(rt)
            if running:
                status = _cli.runtime_status(inst.runtime_name, POLL_HTTP_TIMEOUT)
        if not running:
            rows.append({"instance": inst.name, "live": False, "scanner": "no-runtime",
                         "dsl": "-", "budget": "-", "reason": "runtime not running"})
            continue
        state = _cli.runtime_state(inst.runtime_name, POLL_HTTP_TIMEOUT)
        sc_st, sc_d = _scanner_verdict(inst, state, status)
        dsl_st, dsl_d = _dsl_verdict(inst, status)
        bud_st, bud_d = _budget_verdict(s, funded_by_id)
        sc_live = sc_st in ("ticked", "scheduled", "supervised")
        live = sc_live and dsl_st == "wired" and bud_st == "ok"
        s["status"] = "live" if live else s.get("status", "registered")
        save_state(pkg, st)
        reason = "; ".join(d for ok, d in
                           ((sc_live, sc_d), (dsl_st == "wired", dsl_d),
                            (bud_st == "ok", bud_d)) if not ok and d)
        rows.append({"instance": inst.name, "live": live, "scanner": sc_st, "dsl": dsl_st,
                     "budget": bud_st, "reason": reason})
    return rows


def cmd_verify(pkg, a, log):
    # THE liveness gate: a strategy is `live` only when EVERY instance has a running runtime + an active
    # scanner (ticked / scheduled / supervised) + a wired DSL + a funded budget. The reliable backbone is
    # `runtime list` (running) + the deployed runtime.yaml (scanner + DSL preset) + MCP budget — none of
    # which depend on the flaky `status`/`state` JSON; those only DOWNGRADE a scanner to `broken` on
    # positive evidence. It does NOT wait for a scan tick (a scheduled/supervised scanner is already
    # live); --max-wait only re-checks a runtime that hasn't finished registering yet.
    mcp = MCPClient()
    st = load_state(pkg)
    deadline = time.time() + a.max_wait
    while True:
        rows = _check_live(pkg, st, mcp)
        live = bool(rows) and all(r["live"] for r in rows)
        if live or time.time() >= deadline:
            status = "live" if live else "not-live"
            out = {"strategy": pkg.id, "version": pkg.version, "status": status, "instances": rows}
            if a.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"\n{pkg.id} v{pkg.version}: {status}")
                for r in rows:
                    print(f"  - {r['instance']}: scanner={r['scanner']}, dsl={r['dsl']}, "
                          f"budget={r['budget']}" + (f"  → {r['reason']}" if r["reason"] else ""))
                if not live:
                    print(f"\nNOT live — fix the flagged component(s) and re-run `deploy.py verify {pkg.id}`. "
                          "A strategy is live only when every instance is runtime-running + scanner-active "
                          "+ DSL-wired + funded.")
            if live:
                delete_state(pkg)  # deploy complete → state is ephemeral; next deploy starts clean
            return out
        log("  re-checking liveness…")
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
                         ">0 keeps polling up to S seconds (useful for fast instances).")

    ps = sub.add_parser("status", help="Show the deploy state.")
    common(ps)

    pval = sub.add_parser("validate",
                          help="Preflight: is the package deploy-ready? (structural + render — no side effects)")
    common(pval)

    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    pkg = ensure_pkg(a.package, a.ref, log)

    # `validate` is the standalone, side-effect-free preflight; `create` runs the SAME full check
    # before funding any wallet; runtime/verify/status keep the structural gate.
    gate = full_validate(pkg) if a.cmd in ("validate", "create") else _pkg.validate(pkg)
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

    if a.cmd == "create":
        out = cmd_create(pkg, a, log)
    elif a.cmd == "runtime":
        out = cmd_runtime(pkg, a, log)
    elif a.cmd == "verify":
        out = cmd_verify(pkg, a, log)
    else:  # status
        out = report(pkg, load_state(pkg), "status", as_json=a.json)

    sys.exit(2 if out.get("status") in ("failed", "underfunded", "not-live") else 0)


if __name__ == "__main__":
    main(sys.argv)
