# Handoff: the `deploy.py upgrade` verb, removed at the D1 deploy-verb convergence

**Status:** removed from `senpi-strategy-ops/scripts/deploy.py` when `origin/main` was merged into
`feat/deploy-verb-convergence`. **Nothing about it was rejected on its merits.** It was merged,
released and running on `main`; it is gone here only because the code it stood on no longer exists.

**Audience:** whoever re-adds it after D1 closes — most likely its original author. Everything
needed to bring it back is in this file, so re-adding it is mechanical rather than archaeology.

**Provenance:** PR #528 (`strategy-upgrade-verb`), plus its review follow-ups `ea78628e`,
`90842e88`, `a476ab50`, `eb641e1e`. Last living form: `origin/main` at `6c7fee4f`,
`senpi-strategy-ops/scripts/deploy.py`.

---

## 1. Why it could not come across with the merge

`deploy.py` on this branch is a **thin wrapper over the runtime's `openclaw senpi deploy` verb**
(commit `2df07f50`, "deploy.py: thin wrapper over senpi deploy (state file removed)"). The runtime
now owns the whole go-live path: reconcile → funds preflight → wallet create+fund → install → one
observed scanner tick.

`cmd_upgrade` called, directly, six things that the rewrite deleted or changed:

| It called | Fate on this branch |
|---|---|
| `load_state` / `save_state` / `inst_state` and the `.deploy-state.json` sidecar | **Deleted.** The sidecar was the source of the whole `E_STATE_*` lost-state class. The backend strategies and the runtime registry are the record now. |
| `cmd_create` (PHASE B `pending`/`creating` branch) | **Deleted** — replaced by `run_deploy`, which starts the detached verb and relays its report. |
| `cmd_runtime` (PHASE B `active` branch) | **Deleted** — same. `create` and `runtime` are now two names for one idempotent verb. |
| `cmd_verify(pkg, a, log)` (PHASE B `registered` branch) | **Signature and semantics changed** to `cmd_verify(pkg, a)`. `verify` is now READ-ONLY: it starts no job, funds nothing, and never resumes a deploy. PHASE B used it as a resume step. |
| `_arm_wallet`, `_scope_pkg`, `_scope_flag` | Removed with it — no consumer left. Source preserved in §3. |
| `_emit`, `_exit_code` | Removed with it. The wrapper uses the verb's D-12 exit-code map instead. |

## 2. The blocker you will hit first — read this before writing any code

**`openclaw senpi deploy` has no `--instance` flag.** Its full option list
(`senpi-trading-runtime/src/cli/senpi-commands.ts`, the `deploy` command) is:

```
-p, --path <dir>   --budget <usd>   --decision-model <m>   --tick-wait <s>   --max-wait <s>   --json
```

`upgrade` is defined as a **per-arm** operation — "acts on ONE arm at a time … Each arm is closed
and redeployed on its own fresh wallet; siblings keep running." There is no way to express "deploy
only the `preipo` sleeve" to the verb. A port that ignores this would redeploy **every** sleeve of
a multi-arm package on a fresh wallet, which is exactly the un-consented flatten the consent gate
exists to prevent.

**So the port is blocked on a runtime feature: per-instance scoping in the deploy verb.** That is a
`senpi-trading-runtime` change, not a skills change, and it should land first.

Once it exists, the shape of the port is small, because the runtime verb is idempotent and
reconciles against the backend:

* **PHASE A survives nearly as-is.** Resolve the arm's wallet, refuse on anything but a clean
  resolve or a verified-absent `none`, gate the flatten on `--yes`, close via `close.close_one`.
  Its only state dependency is the `_upgrade` phase block, and that can be re-derived from the
  backend on each call: if the arm still has an open strategy, it is still closing; if it has none,
  it is ready to redeploy.
* **PHASE B collapses to one call** — `run_deploy(pkg, a, log)` with the arm scoping forwarded —
  replacing the three-step `pending`→create / `active`→runtime / `registered`→verify state machine
  and its "registered but not live → delete runtime, reset to active" recovery. The verb's own
  reconcile does that job.
* `_exit_code`'s `closing`/`closed` → **3** rule is still worth keeping and is independent of all
  of the above: `closed` means the old arm is gone, funds are back in main, and nothing is deployed
  yet. A `$?`/`&&` caller must not read that as "done".

## 3. The removed source, verbatim

Taken from `origin/main:senpi-strategy-ops/scripts/deploy.py` — helpers first
(`_scope_pkg` / `_scope_flag` / `_arm_wallet`, lines 443-493), then `_emit` / `cmd_upgrade` /
`_exit_code` (lines 1065-1227).

```python
def _scope_pkg(pkg, instance_name):
    """Narrow a multi-instance package to ONE instance for a single-arm op (redeploy/upgrade one sleeve,
    leaving siblings running). Mutates pkg.instances in place — each command runs in a fresh process — so
    every per-instance loop, plus the create live-guard, acts on this arm only; the siblings' deploy-state
    entries are left untouched. Raises on an unknown instance name."""
    names = [i.name for i in pkg.instances]
    if instance_name not in names:
        raise SystemExit(f"error: no instance {instance_name!r} in {pkg.id} (have: {', '.join(names)})")
    kept = [i for i in pkg.instances if i.name == instance_name]
    # Fund THIS arm with the full --budget: its fractional share of the whole package is irrelevant when
    # it's the only arm being (re)deployed, and keeping it would scale the budget down (a 0.10-share sleeve
    # funded at 10% of what the user asked). Treat the scoped arm as the whole.
    kept[0].funding_share = 1.0
    # Preserve the TRUE arity before narrowing — `_wallet_name` derives `<id>-<arm>` vs bare `<id>` from
    # it, and reading the post-narrow `len(pkg.instances)` (== 1) would collapse a multi-arm arm's wallet
    # name to the bare `<id>`, breaking every by-name lookup (upgrade's fallback, scoped create's naming).
    pkg.full_instance_count = len(names)
    pkg.instances = kept


def _scope_flag(a):
    """` --instance <arm>` when the current op is scoped, else "". Threaded into every resume hint so an
    agent following one mid-single-arm-op re-runs SCOPED — an unscoped `create` on a multi-arm package
    refuses on live siblings and can close a runtime-less sibling WITHOUT consent."""
    return f" --instance {a.instance}" if getattr(a, "instance", None) else ""


def _arm_wallet(pkg, inst, mcp):
    """This arm's ``(wallet, kind)``. ``kind`` is None on a clean resolve; otherwise a REFUSAL kind the
    caller must NOT treat as "safe to fund fresh":
      None         — resolved: ``wallet`` is the arm's address (its live runtime, or the unique ACTIVE
                     strategy carrying the name ``create`` gave it).
      "none"       — verified ABSENT: the read succeeded and no ACTIVE <id> wallet matches → fund fresh is safe.
      "unreadable" — the ``strategy_list`` read FAILED → we don't know; refuse (a money path can't fund blind).
      "unnamed"/"ambiguous" — a wallet exists but no UNIQUE name match (a name-rejection fallback, or a prior
                     double-fund left two) → one may be a funded LIVE arm; refuse, never fund next to it.
    Prefers the live runtime; falls back to the arm's stable strategyName via ``_recover_wallet`` (shared
    tri-state, so this and ``create``'s guard resolve identically and can't drift). The fail-CLOSED read +
    tri-state is what stops an unreadable/ambiguous backend from disarming BOTH the consent gate and the
    double-fund guard at once."""
    rt = _cli.find_runtime(inst.runtime_name)
    wallet = _cli.runtime_wallet(rt) if rt else None
    if wallet:
        return wallet, None
    active = _cli.strategies_for_or_none(mcp, skill_name=pkg.id, statuses=["ACTIVE"])
    if active is None:
        return None, "unreadable"
    w, kind, _why = _recover_wallet(pkg, inst, active)
    return w, kind



def _emit(a, log, out):
    """Emit an upgrade PHASE-A verdict. These are hand-built dicts, not `report()` rows, so they need the
    same --json handling report() gives: under --json `log` is a no-op, so without this the consent text —
    the one thing an agent must relay to the user before a flatten — would reach an empty stdout."""
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        log("\n  " + out["note"])
    return out


def cmd_upgrade(pkg, a, log):
    """Resumable single-arm UPGRADE — apply an edited scan.py / scoring.py / runtime.yaml to a LIVE
    strategy by closing it and redeploying on a FRESH wallet, one step per call (the way `create` resumes).
    The supported way to re-score / re-scan / re-tune a deployed strategy until in-place scanner reload
    lands, at which point the close/redeploy guts swap for it. Two invariants:
      • routes through the tested `close → create → runtime → verify` path, so the runtime yaml is rendered
        INSIDE the instance dir (`./scanners` resolves) on a FRESH wallet — never a hand-rendered root yaml
        or a raw `strategy_create_custom_strategy` (the naked-wallet / "NO ENTRY SCANNERS" traps);
      • consent-gated — closing a live arm market-exits its positions, so it refuses without `--yes`.
    Per arm (`--instance <arm>`), siblings untouched. State: the deploy-state `_upgrade` block, phase
    `closing` (async flatten in flight) → `redeploy`."""
    # `upgrade` acts on ONE arm. main() already narrowed pkg to a single arm when --instance was given;
    # a still-multi-instance pkg here means the caller didn't name which sleeve to upgrade.
    if len(pkg.instances) != 1:
        names = ", ".join(i.name for i in pkg.instances if i.name)
        raise SystemExit(
            f"error: `upgrade` acts on ONE arm at a time — pass --instance <arm> (have: {names}). "
            f"Each arm is closed and redeployed on its own fresh wallet; siblings keep running.")
    if a.budget is None and not a.dry_run:
        raise SystemExit("error: --budget <usd> is required for `upgrade` — it funds the FRESH wallet the "
                         "arm is redeployed onto (the old wallet is retired on close).")
    inst = pkg.instances[0]
    mcp = MCPClient()
    st = load_state(pkg)
    up = st.get("_upgrade") or {}
    rerun = (f"python3 {Path(__file__).name} upgrade {pkg.id} --instance {inst.name}"
             + (f" --budget {budget_arg(a.budget)}" if a.budget is not None else ""))

    # ---------- PHASE A: close the currently-live arm (once), consent-gated ----------
    # Skipped on --dry-run: the preview must be side-effect + network free, so it routes straight to the
    # create dry-run without probing the backend for a live arm.
    if not a.dry_run and up.get("phase") != "redeploy":
        blocked = lambda why: _emit(a, log, {  # noqa: E731 — one refusal shape, reused
            "strategy": pkg.id, "instance": inst.name, "status": "blocked",
            "note": f"[E_STATE_AMBIGUOUS] {why} Refusing so upgrade can't skip consent or fund a second "
                    f"wallet. Triage read-only first, then resolve WITH THE USER:\n"
                    f"      python3 {Path(__file__).with_name('status.py').name} {pkg.id}"})

        if up.get("phase") == "closing":
            # A close was triggered on a prior call. strategy_close is async — poll the strategyIds we
            # CLOSED, directly. (Name-matching here would false-report `closed`: `close_one` deletes the
            # runtime, and a CLOSING strategy has already left ACTIVE, so the name fallback returns nothing
            # while the flatten is still in flight.) Read fail-CLOSED: on a failed read, keep waiting.
            ids = set(up.get("closing_ids") or [])
            rows = _cli.strategies_for_or_none(mcp, skill_name=pkg.id)
            if rows is None:
                return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                      "note": f"couldn't read strategy_list — re-run `{rerun}` to keep polling the close."})
            if [s for s in rows if _cli.strategy_open(s) and _cli.strategy_id_of(s) in ids]:
                return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                      "note": f"old {inst.runtime_name} still flattening — re-run `{rerun}` to continue."})
            up["phase"] = "redeploy"; up.pop("closing_ids", None); st["_upgrade"] = up; save_state(pkg, st)
            return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closed",
                                  "note": f"old arm closed, funds returning to main. Re-run `{rerun}` to redeploy on "
                                          f"a FRESH wallet. (If create reports `underfunded`, the funds are still "
                                          f"returning — wait a moment and re-run.)"})

        # START. Resolve the arm's wallet via the shared tri-state resolver, and REFUSE on anything but a
        # clean resolve or a verified-absent `none` — an unreadable/ambiguous backend must not disarm the
        # consent gate + double-fund guard (fund a fresh wallet next to an unread live one).
        arm_wallet, arm_kind = _arm_wallet(pkg, inst, mcp)
        if arm_kind in ("unreadable", "unnamed", "ambiguous"):
            return blocked(f"can't safely resolve `{inst.runtime_name}`'s wallet ({arm_kind}).")

        open_mine = []
        if arm_wallet:
            rows = _cli.strategies_for_or_none(mcp, wallet=arm_wallet)  # fail-CLOSED — never fund on a blind read
            if rows is None:
                return blocked(f"couldn't read strategy_list to confirm `{inst.runtime_name}`'s open book.")
            open_mine = [s for s in rows if _cli.strategy_open(s)]

        if open_mine:
            # The arm is LIVE. Closing it MARKET-EXITS its positions — never do that silently.
            if not a.yes:
                return _emit(a, log, {
                    "strategy": pkg.id, "instance": inst.name, "status": "needs-consent", "wallet": arm_wallet,
                    "note": (f"UPGRADE will CLOSE the live `{inst.runtime_name}` on wallet {arm_wallet}: it "
                             f"market-exits any open position (often NONE if the strategy isn't entering — that's "
                             f"the usual re-tune case), returns funds to your main wallet, and redeploys the edited "
                             f"arm on a FRESH wallet. The old wallet is retired, and a custom ratchet/stop ladder on "
                             f"the old positions does NOT carry over — re-apply it after if wanted. Confirm with the "
                             f"user, then re-run with --yes:\n      {rerun} --yes")})
            # consent given → close THIS arm via the tested close primitive, remembering its strategyIds so
            # the closing-wait can poll them directly, then hand off to redeploy on a fresh wallet.
            import close as _close  # noqa: E402 — sibling module, lazy import
            runtimes = _cli.list_runtimes()
            recs = [_close.close_one(pkg.id, s, runtimes, False, log) for s in open_mine]
            bad = [r for r in recs if r.get("status") == "failed"]
            if bad:
                # A failed close (runtime still listed after two delete attempts → it may re-enter
                # positions) must SURFACE, not be swallowed. State stays put, so the next run re-attempts;
                # advancing to `closing` would poll a strategy nothing is closing, forever.
                return _emit(a, log, {
                    "strategy": pkg.id, "instance": inst.name, "status": "failed",
                    "note": "close FAILED, nothing redeployed: "
                            + "; ".join(str(r.get("error") or "?") for r in bad)
                            + f"\n      Resolve it, then re-run `{rerun} --yes`."})
            st["instances"][inst.name] = {"status": "pending"}  # forget the old id → create makes a FRESH wallet
            st["_upgrade"] = {"phase": "closing",
                              "closing_ids": [_cli.strategy_id_of(s) for s in open_mine if _cli.strategy_id_of(s)]}
            save_state(pkg, st)
            return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "closing",
                                  "note": (f"Closing `{inst.runtime_name}` (flatten positions + return funds; "
                                           f"async). Re-run `{rerun}` to redeploy once it's closed.")})

        # Nothing live to close, reached two ways: arm_kind == "none" (verified absent — genuinely not
        # deployed), OR a wallet resolved but its strategy is already closed so `open_mine` is empty (a
        # runtime-less trap `create`'s own live-guard then backstops). Either way → straight to redeploy.
        up["phase"] = "redeploy"; st["_upgrade"] = up
        save_state(pkg, st)

    # ---------- PHASE B: redeploy the arm on a fresh wallet — one resumable step per call ----------
    s = inst_state(st, inst.name)
    status = s.get("status")
    if a.dry_run or not s.get("strategyId") or status in (None, "pending", "creating"):
        return cmd_create(pkg, a, log)
    if status == "active":
        return cmd_runtime(pkg, a, log)
    # registered → a fast single check (max_wait=0); verify deletes state on `live`, clearing _upgrade too.
    av = argparse.Namespace(**{**vars(a), "max_wait": 0})
    out = cmd_verify(pkg, av, log)
    if out.get("status") != "live" and st.get("_upgrade"):
        # Registered but NOT live mid-upgrade (e.g. the edited scanner is broken — exactly when the user
        # re-edits and re-runs). Verify-only would loop forever and the re-edit would never re-render
        # (cmd_runtime idempotent-skips the same wallet). Drop the runtime + reset to `active` so the next
        # run re-registers the CURRENT on-disk edit instead of re-judging the stale deployment.
        _cli.run_cli(["openclaw", "senpi", "runtime", "delete", inst.runtime_name], timeout=60)
        s["status"] = "active"
        save_state(pkg, st)
        return _emit(a, log, {"strategy": pkg.id, "instance": inst.name, "status": "not-live",
                              "note": f"redeploy registered but not live yet (scanner unconfirmed). Re-run "
                                      f"`{rerun}` — it re-registers the current edit (fix the scanner on disk "
                                      f"first if it's broken)."})
    return out


# ---------- cli ----------

def _exit_code(status):
    """Process exit code for a command result. **0** = done / informational; **2** = refused or failed
    (action required — `failed`/`underfunded`/`not-live`/`needs-consent`/`blocked`); **3** = RESUMABLE,
    re-run (in-flight). `closing`/`closed` exit 3 — NOT 0 — so a `$?`/`&&` caller can't misread upgrade's
    most dangerous in-flight state (`closed`: the old arm is gone, funds are back in main, and NOTHING is
    deployed yet) as "done" and stop, stranding the user's capital. These two statuses are emitted only by
    `upgrade`; the standalone deploy steps keep their existing exit-0 done-for-this-step semantics."""
    if status in ("failed", "underfunded", "not-live", "needs-consent", "blocked"):
        return 2
    if status in ("closing", "closed"):
        return 3
    return 0


```

And its subparser, from `main(argv)`:

```python

    pu = sub.add_parser("upgrade",
                        help="Apply an edited scan.py/scoring.py/runtime.yaml to a LIVE strategy: close the "
                             "arm + redeploy on a FRESH wallet (resumable; consent-gated). Per arm.")
    common(pu); _inst(pu)
    pu.add_argument("--budget", type=float, default=None,
                    help="USDC to fund the fresh wallet the arm is redeployed onto (required).")
    pu.add_argument("--yes", action="store_true",
                    help="Consent to FLATTEN: closing the live arm market-exits its open positions. Required "
                         "to proceed while the arm holds a book.")
    pu.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pu.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT, help="Poll budget for the create step (s).")
    pu.add_argument("--dry-run", action="store_true", help="Show the plan (routes to the create dry-run) with no side effects.")
```

## 4. Every doc site that referenced it

All removed in the same merge. Line numbers are `origin/main` at `6c7fee4f`.

| File | Lines | What was there |
|---|---|---|
| `senpi-strategy-ops/SKILL.md` | 15, 21, 50 | Frontmatter description + the command table row |
| `senpi-strategy-ops/SKILL.md` | 134, 194 | The two "to apply an edit, use `deploy.py upgrade`" steers away from `close`/`create` |
| `senpi-strategy-ops/SKILL.md` | 300, 305, 315-357 | The whole **Upgrade / redeploy a deployed strategy** section: the pre-flight checklist, the resumable step loop, the `NEVER during an upgrade` list, and the note that it drives `close → create → runtime → verify` under the hood |
| `senpi-strategy-ops/references/lifecycle.md` | 92 | The lifecycle-table row |
| `senpi-strategy-author/SKILL.md` | 361-362 | The author→ops routing for an edit to an already-live strategy |

Two other occurrences of the word were **kept**, because they are ordinary English and not the verb:
`senpi-strategy-ops/SKILL.md:258` and `references/lifecycle.md:124`, both "…to **upgrade**
process-level 'running' to the runtime's own health verdict".

When the verb comes back, the author→ops routing in `senpi-strategy-author/SKILL.md` is the one
most easily forgotten and the most costly to omit: without it, an agent editing a live strategy
falls through to the fresh-deploy loop.

## 5. Its test coverage, also removed

`senpi-strategy-ops/tests/test_deploy_gates.py` was deleted in this merge (its deploy gates moved
into the runtime verb). Four of its classes were `upgrade`'s and are worth restoring with it —
recover them from `origin/main:senpi-strategy-ops/tests/test_deploy_gates.py`:

| Class | Lines | Covers |
|---|---|---|
| `ScopePkgSingleArm` | 619-648 | `--instance` narrowing; a scoped arm funds FULLY rather than being scaled by its `funding_share` |
| `UpgradeGates` | 650-674 | The two pre-MCP refusals: multi-arm with no `--instance`, and a missing `--budget` |
| `UpgradeDispatch` | 676-723 | PHASE B routing — one tested step per call |
| `UpgradeArity` | 725-744 | A scoped multi-arm keeps its full `<id>-<arm>` wallet name; a true single-arm stays bare |
| `UpgradeConsentPhase` | 746-889 | PHASE A: the consent gate, the fail-CLOSED backend reads, the ambiguous/unreadable refusals, the closing-wait polling ids directly, and a failed close surfacing instead of advancing |

`UpgradeDispatch` and `UpgradeArity` will need the most rework, since PHASE B collapses to a single
`run_deploy` call. `UpgradeConsentPhase` should survive nearly intact — it is PHASE A, which is the
part that does not depend on the deleted state machine.

## 6. One thing to fix while you are in here

Independent of `upgrade`, and **already filed as its own runtime task** — recorded here only so it
is not lost twice:

`origin/main`'s `deploy.py:347` `is_name_rejection` routes **SERR083** to the unnamed-create retry,
with a reproduced incident behind it: a strategy stuck in `PENDING_FUNDING` keeps holding its
`strategyName`, so every create reusing that name 500s until the record expires ~15 minutes later,
and the error's own advice ("Retry with the same payload") can never clear it. Dropping the name
clears it instantly.

The runtime's classifier does not cover it:

```ts
// senpi-trading-runtime/src/deploy/orchestrator.ts
const NAME_REJECTION_RE = /SERR05[568]|name/i;
```

`SERR083`'s message — "Custom strategy creation failed unexpectedly. Retry with the same payload."
— matches neither the code alternation nor the literal word "name". The money path lives in the
runtime now, so the fix belongs there; a guard re-added on the skills side would be advisory over
an engine that still gets it wrong.
