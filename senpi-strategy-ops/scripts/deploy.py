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
  python3 deploy.py verify   <id>                                  # READ-ONLY: is it live? (deploys nothing)
  python3 deploy.py status   [<id>]                                # the last deploy job's status

TWO subcommands move money — `create` and `runtime` — and they drive the SAME idempotent verb:
re-running either resumes (the verb reconciles against the backend + the runtime registry and adopts
whatever exists), so they are one path under two names, not two steps. **`verify` is not one of
them.** It is the read-only check its name has always promised: a composite of `strategy_list` +
`openclaw senpi runtime list` + `openclaw senpi status --json` (plus a verbatim relay of the last
deploy job's `[W_*]` warns) that only QUOTES what it read. It starts no job, funds nothing, installs
nothing — so an agent following an old transcript ("just re-check it: deploy.py verify spider")
against a package whose funded wallet was deliberately left runtime-less cannot start it trading.
When something IS missing, verify NAMES the resume (`create`/`runtime`) and says what it will do; it
never runs it, and it never emits a teardown command.

Exit codes for `validate`/`create`/`runtime`/`status` mirror the verb's own (D-12): 0 live · 2
refused · 3 failed · 4 installed-unobserved · 5 interrupted · 6 pending (a wallet still funding, or
the job still running) · 1 internal/transport error. **2 is any gate saying no with nothing created
past it** — the verb's refusals, and this wrapper's own structural preflight (`validate`, and the
same pass the action subcommands run before the verb is started); both are deterministic and refuse
identically on a re-run.

**`verify` runs no job, so it returns none of that range beyond 0/3/1**: `0` verified (every instance
has a live wallet AND a registered runtime AND its health read healthy) · `3` NOT verified (something
is missing or unhealthy — each instance says what, quoted, with the one non-destructive next step for
that actual state) · `1` COULD NOT CHECK (a read it needs failed). 1 is fail-closed and load-bearing:
an unreadable surface must never render as verified, and must never steer at the money path over a
package that may be perfectly live.

1 is also the fallback for a status this wrapper does not recognise, AND for a start we could not
follow (spawn failure, start timeout, or a 0-exit start that printed no deployId — in those last two
the job may well be running: read `openclaw senpi deploy status`; a runtime plugin that PREDATES the
verb is the opposite case — the CLI never parsed the command, so nothing was dispatched and the
message says to update the plugin instead), AND for a `status <id>` whose id is not the recorded
job's package, a `status` given `--ref` (it fetches nothing), or a `status` whose read produced NO
SNAPSHOT at all (the verb's own `[NOT_FOUND]`, or a failed read — relayed in the verb's words, never
restated here as an absence). In all of those the question could not be answered, no deploy outcome
is being reported, and re-running refuses identically. A `status` that DOES get a snapshot always
answers with that job's own D-12 code, however the `deploy status` call exited: its exit code is the
job's verdict, not a health signal about the call. There is no `cancel`: undeploying a strategy is
closing it (`close.py`), and a wedged job frees its own slot at the deploy deadline.

There is NO local deploy-state file any more. The backend strategies and the runtime registry ARE
the record — the sidecar `.deploy-state.json` was the source of the whole `E_STATE_*` lost-state
class, and it is gone. Package resolution (path or bare catalog id, with the remote fetch) and the
structural preflight stay here; the live-universe gate and everything after them live in the
runtime (`[E_UNIVERSE_NOT_LIVE]`, pre-money, relayed verbatim like every other refusal). `validate`
still REPORTS the universe locally, from the same predicates the runtime ports — a report beside
the verdict, never the invariant.
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
from mcp_client import MCPClient  # noqa: E402

START_TIMEOUT = 60      # the verb detaches in ~1s; this is only the spawn budget
STATUS_TIMEOUT = 30
# The JOB's wallet-ACTIVE budget, forwarded verbatim to the verb — so this default MIRRORS the verb's
# own (`--max-wait <seconds>`, default 150). It is not just a wait: the verb sizes its wall-clock
# watchdog from it (`(maxWait + tickWait) * instances + slack`), so raising it here would silently
# multiply how long a wedged deploy holds the single-flight slot on a multi-instance package.
DEFAULT_MAX_WAIT = 150
# How long THIS script polls the detached job for a terminal state when the caller says nothing — a
# different clock from the forwarded one, and a BOUNDED one. An agent's tool harness kills an exec at
# ~180s: a longer foreground poll (this was 600) loses the report AND the exit code while the detached
# job runs on, which is the whole failure the detached design exists to avoid. Lapsing is not a
# failure — a job still running is the pending path (exit 6, the snapshot printed, `deploy status` to
# watch it), which is why a budget that RETURNS beats one that waits. An EXPLICIT --max-wait replaces
# it in BOTH directions: a larger one so the wrapper never gives up on a job it just told the verb to
# wait longer for, a smaller one because a caller asking for a fast return must actually get one.
POLL_BUDGET = 150
POLL_EVERY = 5

# D-12: the verb's exit-code contract, mirrored here so `deploy.py` and `openclaw senpi deploy
# status` answer identically. 1 is reserved for internal/transport errors and is ALSO the
# unknown-status fallback, so a status this wrapper has never heard of can never read as success.
# Anything richer than the overall status: read the report (`--json`).
EXIT_CODES = {
    "live": 0,
    "refused": 2,
    "failed": 3,
    "installed-unobserved": 4,
    "interrupted": 5,
    # A wallet still funding when the poll budget ran out — resumable, not an error.
    "pending": 6,
}
EXIT_INTERNAL = 1
EXIT_PENDING = 6


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
    first; `validate` additionally reports the live universe beside this pass — see
    `universe_report` — which needs the network and so is not part of "every error deploy.py
    can see" locally.)"""
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


def universe_report(pkg):
    """The live-universe check as a REPORT for `validate` — never a gate.

    The invariant itself is the verb's: `openclaw senpi deploy` refuses a dead hardcoded name
    pre-money (`[E_UNIVERSE_NOT_LIVE]`) and fails closed when the instrument list is unreadable.
    This runs the SAME `validate_universe` predicates the runtime ports, so the two cannot disagree
    on one live list, and it exists only so the taught step-0 preflight can report a dead universe
    before a whole deploy round-trip. Returns `(errors, note)`: an unreachable list is a LOUD note,
    never a silent pass and never a blocked deploy.

    The two failure modes are kept apart on purpose. Reading the live list is a NETWORK/token
    problem; reading the package's own YAML is a problem in a file the author owns, and reporting
    the second as the first points them at the MCP server for a broken indent. (Declared files are
    already covered by `full_validate`, so a scan failure here means a stray YAML in the package
    dir — worth saying out loud, not worth failing an otherwise valid package over.)"""
    try:
        import validate_universe as _vu
        live = _vu.live_instruments()
    except Exception as e:  # noqa: BLE001 — loud note, never a silent pass
        return [], (f"live-universe check could not run here: the live instrument list could not be "
                    f"read ({e}) — nothing about the universe is verified; `senpi deploy` still "
                    f"enforces it before money moves")
    try:
        unknown = _vu.unknown_tickers(_vu.package_tickers(str(pkg.dir)), live)
    except Exception as e:  # noqa: BLE001 — a package problem, said as one
        # The scan globs the package dir itself, and a PyYAML error raised from a STRING carries no
        # path — so the note promises only what it can produce: the directory it scanned, plus
        # whatever the error itself says. Never "the file it names".
        return [], (f"could not scan this package for hardcoded instruments ({e}) — nothing about "
                    f"the universe is verified here; the failure came from parsing a YAML file "
                    f"under {pkg.dir} (the error above may not name which one — check the "
                    f"runtime*.yaml files there), then re-run validate. `senpi deploy` enforces "
                    f"the universe before money moves either way")
    if unknown:
        return ([f"hardcodes instrument(s) not live on Hyperliquid: {', '.join(unknown)} "
                 f"(the deploy verb will refuse this pre-money: [E_UNIVERSE_NOT_LIVE])"], None)
    return [], None


# ---------- the verb ----------

def budget_arg(v):
    """A dollar amount as the verb's `--budget` flag accepts it: bare digits, no `$`, no comma
    grouping (the flag parses a float, so grouping would fail)."""
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def _relay(rc, out, err):
    """Print a failed CLI call's own words, unedited, and hand back the code to exit with. The verb's
    refusals already carry their code, their facts and their next step — anything added here would be a
    second, drifting producer, so the verb's OWN exit code (2 refused, 3 failed, …) passes through.

    A non-positive rc is `run_cli`'s own signal — a spawn failure or the START_TIMEOUT — i.e. we never
    heard the verb answer. That is transport breakage: **1**, per this script's header contract. It must
    never read as `2`/refused, which tells the agent a gate said no and nothing was created, while a
    dispatched job may be funding a wallet right now."""
    text = _cli.error_tail(err, out) or "openclaw senpi deploy failed (no error output)"
    print(text, file=sys.stderr)
    return rc if rc and rc > 0 else EXIT_INTERNAL


# A runtime plugin that PREDATES the `senpi deploy` verb (a wedged self-update — a real fleet class)
# answers the start call with its CLI parser's unknown-command error, not the verb's. That is neither
# transport breakage nor a refusal: the command never reached a gateway, so nothing was dispatched,
# and the "read `openclaw senpi deploy status`" next step names a verb this box does not have either.
_UNKNOWN_CMD = ("unknown command", "unknown argument", "unrecognized command", "unrecognised command")
# A bracketed [CODE] means the VERB is speaking (a refusal, or an error it raised), whatever words
# follow — so a refusal that happens to contain "unknown argument" is never read as a missing verb.
_REFUSAL_CODE = re.compile(r"\[[A-Z][A-Z_]{2,}\]")


def _predates_the_verb(text):
    """True when a failed start reads as "this CLI has no `deploy` command", not as a verb answer."""
    s = str(text or "")
    if _REFUSAL_CODE.search(s):
        return False
    low = s.lower()
    return "deploy" in low and any(m in low for m in _UNKNOWN_CMD)


def start_deploy(pkg, a, log):
    """Start the detached job. Returns the deployId, or exits with the verb's own refusal text."""
    args = ["openclaw", "senpi", "deploy", "-p", str(pkg.dir), "--json"]
    if a.budget is not None:
        args += ["--budget", budget_arg(a.budget)]
    if getattr(a, "decision_model", None):
        args += ["--decision-model", a.decision_model]
    if getattr(a, "tick_wait", None) is not None:
        args += ["--tick-wait", str(a.tick_wait)]
    # `--max-wait` is the JOB's wallet-ACTIVE budget, not just this script's poll budget. Forward it:
    # a wrapper that only polled longer would let a caller who raised it believe they gave the funding
    # step more room while the job still ran on the verb's default.
    if getattr(a, "max_wait", None) is not None:
        args += ["--max-wait", str(a.max_wait)]
    if getattr(a, "dry_run", False):
        print("planned: " + " ".join(args))
        raise SystemExit(0)
    log("  starting the deploy job…")
    rc, out, err = _cli.run_cli(args, timeout=START_TIMEOUT)
    if rc != 0:
        if _predates_the_verb(_cli.error_tail(err, out)):
            # Exit 1 (the question could not be answered) — never the parser's own code, which may be
            # 2 and would read as "a gate refused the deploy, nothing created past it". Nothing was
            # created, but nothing was gated either: the box cannot run this command at all.
            print(_cli.error_tail(err, out), file=sys.stderr)
            print("This box's runtime plugin has no `senpi deploy` verb — it predates it (usually a "
                  "wedged self-update).\n"
                  "  NOTHING WAS DISPATCHED: no job started, no wallet created, no funds moved, and "
                  "`openclaw senpi deploy status` has nothing to report here — there is nothing to "
                  "read and nothing to clean up.\n"
                  "  Update the runtime plugin, then re-run this exact command:\n"
                  "    openclaw plugins install @senpi-ai/runtime\n"
                  "  Agent boxes also self-update on their own schedule, so retrying in a few minutes "
                  "is the other way out.", file=sys.stderr)
            raise SystemExit(EXIT_INTERNAL)
        code = _relay(rc, out, err)
        # A START_TIMEOUT is the no-deployId case wearing a different hat: the gateway was reached and
        # may have dispatched the job, we just stopped waiting for the answer. Say so HERE — at the
        # moment of failure, in the stderr an agent actually reads. (A spawn failure is different:
        # `openclaw` never ran, so nothing was dispatched and there is nothing to go read.)
        if rc == -1 and not str(err or "").startswith(_cli.SPAWN_FAILED_PREFIX):
            print("  That is a timeout waiting for the START call, not a verdict: the gateway may have "
                  "taken the deploy, so a job MAY BE RUNNING (a wallet may already be funded).\n"
                  "  Read what actually happened, and report THAT:  openclaw senpi deploy status",
                  file=sys.stderr)
        raise SystemExit(code)
    started = _cli._extract_json(out) or {}
    deploy_id = started.get("deployId")
    if not deploy_id:
        # The verb exited 0 — it ACCEPTED the deploy — but printed no deployId, so this script cannot
        # follow the job. The job is very likely running (reconcile → preflight → create+fund may be in
        # flight this second). Say that, and never call it a refusal.
        tail = _cli.error_tail(err, out)
        if tail:
            print(tail, file=sys.stderr)
        print("openclaw senpi deploy exited 0 but printed no deployId, so this script cannot follow the "
              "job.\n"
              "  The outcome is UNKNOWN, and no gate said no: the deploy MAY BE RUNNING right now — "
              "reconcile → preflight → create+fund can be in flight, and a wallet may already be "
              "funded.\n"
              "  Read what actually happened, and report THAT:  openclaw senpi deploy status",
              file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL)
    return deploy_id


def read_status(deploy_id):
    """`deploy status --json` → `(snapshot | None, the call's own words when there is no snapshot)`.

    **`deploy status` exits with the JOB's D-12 code** — 2 refused, 3 failed, 5 interrupted, 6 still
    running — and sets it BEFORE printing the snapshot, on the `--json` path too. Its exit code is a
    verdict about the deploy, not a health signal about the call, so treating a non-zero rc as
    unreadable discarded every non-live snapshot: a refused deploy then polled as unreadable for the
    whole budget and reported a transport error, and `status` claimed no job existed while one was
    running. The snapshot on stdout IS the answer, whatever the code beside it.

    Unreadable is only "no snapshot came back": a spawn failure, the STATUS_TIMEOUT, or the verb's
    own `ok:false` error (`[NOT_FOUND]` when no deploy has ever run on this agent). Those hand back
    the verb's text so a caller can relay it instead of composing an absence it never read."""
    args = ["openclaw", "senpi", "deploy", "status"]
    if deploy_id:
        args.append(deploy_id)
    args.append("--json")
    _rc, out, err = _cli.run_cli(args, timeout=STATUS_TIMEOUT)
    snap = _cli._extract_json(out)
    if isinstance(snap, dict):
        return snap, None
    return None, _cli.error_tail(err, out)


def status_snapshot(deploy_id):
    """The job snapshot as JSON, or None when the call produced none (see `read_status`)."""
    return read_status(deploy_id)[0]


def check_status_package(arg, snap):
    """Hold a `status <id>` to the snapshot it is about to print, or refuse.

    There is ONE deploy-job record per agent and it is not package-addressed: `status_snapshot(None)`
    answers with the LAST job whatever package it ran. Printing polar's report and polar's exit code
    under a `status spider` prompt invites an agent to bind the wrong package's verdict, so a named
    package that the job does not match is a refusal, not a decoration. A snapshot that names no
    package at all can't be confirmed either way — say so rather than let the id ride along silently."""
    if not arg:
        return
    want = Path(arg).name
    if not want:
        # `.`, `/`, `./` — a path that names no package id. There is nothing to hold the job to, so
        # refusing here would build a refusal out of an empty string ("is not 'spider'" with no
        # spider). Say the argument asserted nothing and print the job.
        print(f"note: {arg!r} names no package id, so this job was not checked against it. Read the "
              f"package off the report below.", file=sys.stderr)
        return
    got = ((snap or {}).get("meta") or {}).get("packageId")
    if not got:
        print(f"note: this deploy job does not name its package, so it could NOT be confirmed as "
              f"{want!r}. Read the package off the report below before binding this verdict to {want}.",
              file=sys.stderr)
        return
    if str(got) != want:
        print(f"error: the last deploy job on this agent is {str(got)!r}, not {want!r} — refusing to "
              f"print it under a {want!r} prompt.\n"
              f"  There is one deploy-job record per agent; `status` is not package-addressed.\n"
              f"  NO DEPLOY STATE IS REPORTED HERE — this says nothing about {want}'s deploy, and "
              f"nothing about {got}'s beyond its name. Re-running this exact command will refuse "
              f"again (exit 1 here means the question could not be answered, not a transport blip).\n"
              f"  What {want} is doing right now:  python3 "
              f"{Path(__file__).with_name('status.py').name} {want}\n"
              f"  The {got} job's report:          python3 {Path(__file__).name} status {got}",
              file=sys.stderr)
        raise SystemExit(EXIT_INTERNAL)


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


def exit_code_for(snap):
    """The D-12 code for a snapshot. Mirrors the verb's own `exitCodeForDeploy`, exactly."""
    state = (snap or {}).get("state") or {}
    # A snapshot with no state is a broken gateway contract, not a running job.
    if not state.get("status"):
        return EXIT_INTERNAL
    # `interrupted` is a terminal STATE, not an overall: a gateway restart killed the job. Nothing
    # is running, so it must never read as pending.
    if state.get("status") == "interrupted":
        return EXIT_CODES["interrupted"]
    if state.get("status") != "done":
        return EXIT_PENDING
    return EXIT_CODES.get(state.get("overall"), EXIT_INTERNAL)


def run_deploy(pkg, a, log):
    """Start → poll → relay. Exit code: the D-12 map (see EXIT_CODES)."""
    deploy_id = start_deploy(pkg, a, log)
    poll_budget = getattr(a, "poll_budget", None)
    if poll_budget is None:
        poll_budget = POLL_BUDGET
    snap = wait_for_terminal(deploy_id, poll_budget, log)
    if snap is None:
        print(f"Could not read the deploy job's status. Check it directly: "
              f"openclaw senpi deploy status {deploy_id}", file=sys.stderr)
        return EXIT_INTERNAL
    print_status(deploy_id, a.json, snap)
    state = snap.get("state") or {}
    if state.get("status") == "running":
        # In `--json` mode stdout is the machine surface — the snapshot document and nothing else.
        # This trailer used to land on the same stdout right after it, so `json.loads` broke on
        # exactly the pending outcome an agent must parse to decide whether to keep watching. The
        # steer is not dropped, it moves to stderr (where the human still reads it).
        print(f"\nStill running after {poll_budget}s — the job continues in the background. "
              f"Watch it: openclaw senpi deploy status {deploy_id}",
              file=sys.stderr if a.json else sys.stdout)
    return exit_code_for(snap)


# ---------- verify: a READ-ONLY composite (it never starts the verb) ----------

# `verify`'s own exit codes. It runs no job, so it returns none of the job's D-12 range beyond these:
#   0 verified · 3 not verified · 1 could not check (a read failed — no verdict is rendered).
VERIFY_OK, VERIFY_NOT, VERIFY_UNREADABLE = 0, 3, EXIT_INTERNAL

_WARN_CODE = re.compile(r"\[W_[A-Z][A-Z_]*\]")


class ReadFailed(Exception):
    """A surface `verify` needs came back unusable. Fail CLOSED: no verdict is rendered at all."""


def _sanitize_strategy_name(raw, fallback):
    """The backend strategyName sanitizer: whitespace → '-', keep only [A-Za-z0-9_-], trim
    leading/trailing -/_, cap at 40 chars; an empty result falls back to the (truncated) package id."""
    s = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"\s+", "-", str(raw).strip())).strip("-_")[:40]
    return s or str(fallback)[:40]


def _wallet_name(pkg, inst):
    """The strategyName the deploy verb gives this instance's wallet: `<id>-<instance>` for a
    multi-instance package, else the bare `<id>` — sanitized the way the backend sanitizes it.
    `verify` re-derives the SAME name to match a live strategy back to its instance, so the check and
    the deploy cannot disagree about which wallet belongs to which sleeve."""
    multi = len(pkg.instances) > 1
    raw = f"{pkg.id}-{inst.name}" if (multi and inst.name) else str(pkg.id)
    return _sanitize_strategy_name(raw, pkg.id)


def verify_reads(pkg):
    """The three read-only surfaces `verify` composes, or `ReadFailed` naming the one that failed.

    Every one of them is a READ: MCP `strategy_list` (what the backend says is live), `openclaw senpi
    runtime list` (what is registered on this box — the authoritative inventory; `status --json` is
    transiently empty and is NOT one), and `openclaw senpi status --json` (the runtime's OWN health
    verdict per runtime, the reliable liveness source). Nothing here creates, funds, installs or
    starts anything."""
    try:
        res = MCPClient().mcp_call("strategy_list", timeout=STATUS_TIMEOUT, status=_cli.LIVE_STATUSES)
    except Exception as e:  # noqa: BLE001 — any transport/tool failure is "we could not read it"
        raise ReadFailed(f"MCP `strategy_list` could not be read ({e})")
    strategies = [s for s in _cli.find_list(res, "strategies") if _cli.strategy_open(s)]
    # `or_none` is the whole point: [] means "no runtimes", None means "the inventory is unreadable",
    # and a check that reads the second as the first reports every strategy on the box as runtime-less.
    runtimes = _cli.list_runtimes_or_none()
    if runtimes is None:
        raise ReadFailed("`openclaw senpi runtime list` could not be read — the runtime inventory is "
                         "not visible from here")
    args = ["openclaw", "senpi", "status", "--json"]
    health = _cli.cli_json(args, STATUS_TIMEOUT)
    if health is None:
        raise ReadFailed("`openclaw senpi status --json` could not be read — no runtime health verdict "
                         "is available")
    entries = _cli.find_list(health, "statuses")
    if not entries and any(_cli.runtime_running(r) for r in runtimes):
        # The gateway transiently answers with an empty `statuses[]` even while runtimes are running
        # (why `runtime list` is the inventory and this call is only health). One retry, as
        # `runtime_health_map` does — otherwise a flaky read turns every instance into could-not-check.
        retry = _cli.cli_json(args, STATUS_TIMEOUT)
        entries = _cli.find_list(retry, "statuses") if retry is not None else entries
    return strategies, runtimes, {_cli.runtime_name(e): e for e in entries}


def _raw_health(entry):
    """The health string the runtime itself published, for QUOTING. `health_verdict` classifies it;
    this is what gets printed, so the report never invents a word the runtime did not say."""
    h = _cli._deep_first(entry, ["overallHealth", "health", "overall", "status"])
    return None if h is None else str(h)


def verify_instance(pkg, inst, strategies, runtimes, hmap):
    """One instance's row: what exists, quoted — plus the ONE non-destructive next step for what does
    not. Never a close/teardown command, and never a step that is wrong for the state actually read."""
    want = _wallet_name(pkg, inst)
    rt_name = getattr(inst, "runtime_name", None) or f"{pkg.id}-{inst.name}"
    row = {"instance": inst.name, "strategy_name": want, "runtime": None, "wallet": None,
           "status": None, "funded": None, "health": None, "ok": False, "issue": None,
           "next": None, "unreadable": None}
    # The one next step for every state this check cannot resolve on its own: read-only, and never a
    # close — a check that emits a teardown command is a check that can lose someone's money.
    triage = (f"python3 {Path(__file__).with_name('status.py').name} {pkg.id}   # read-only: map each "
              f"wallet to its runtime/strategy")

    pkg_live = [s for s in strategies if _cli.strategy_skill(s) == pkg.id]
    cands = [s for s in pkg_live if _cli.strategy_name(s) == want]
    if not cands and len(pkg.instances) == 1:
        cands = list(pkg_live)          # single instance: the package's lone live strategy is it
    if not cands:
        if pkg_live:
            # Wallets for this package exist but none carries this instance's name (a create-time
            # name-rejection fallback, or a renamed wallet). NOT "nothing exists": steering at
            # `create --budget` here funds a SECOND wallet beside a possibly live, funded one.
            row["issue"] = (f"{len(pkg_live)} live {pkg.id} wallet(s) exist but none is named {want!r} "
                            f"— this instance could not be matched to one")
            row["next"] = triage
        else:
            row["issue"] = f"no live strategy named {want!r} on the backend — nothing is funded here"
            row["next"] = (f"python3 {Path(__file__).name} create {pkg.id} --budget <usd>   # RUNS THE "
                           f"DEPLOY VERB: creates and funds a wallet, installs the runtime and starts "
                           f"trading")
        return row
    wallets = {str(_cli.strategy_wallet(s)).lower() for s in cands if _cli.strategy_wallet(s)}
    if len(cands) > 1 or len(wallets) > 1:
        row["issue"] = (f"{len(cands)} live strategies match instance {inst.name!r} — which one is this "
                        f"instance cannot be told from here")
        row["next"] = triage
        return row

    strat = cands[0]
    wallet = _cli.strategy_wallet(strat)
    row.update({"wallet": str(wallet) if wallet else None, "status": _cli.strategy_status(strat),
                "funded": _cli.strategy_funded(strat)})
    if not wallet:
        row["issue"] = ("the live strategy record carries no readable wallet address, so its runtime "
                        "could not be matched")
        row["next"] = triage
        return row

    rt = next((r for r in runtimes if _cli.wallet_match(_cli.runtime_wallet(r), wallet)), None)
    if rt is None:
        named = next((r for r in runtimes if _cli.runtime_name(r) == rt_name), None)
        if named:
            row["issue"] = (f"runtime {rt_name!r} is registered but bound to wallet "
                            f"{str(_cli.runtime_wallet(named) or '?')} — not this strategy's "
                            f"{str(wallet)[:10]}…")
            row["next"] = triage
            return row
        row["issue"] = (f"the wallet {str(wallet)[:10]}… is live and funded ({row['funded']}) but NO "
                        f"runtime is registered for it — funded and not trading")
        row["next"] = (f"python3 {Path(__file__).name} runtime {pkg.id}   # RUNS THE DEPLOY VERB: it "
                       f"installs the runtime and STARTS TRADING this funded wallet")
        return row

    row["runtime"] = _cli.runtime_name(rt)
    listed = str(_cli.dig(rt, "status", "state") or "?")
    if not _cli.runtime_running(rt) or _cli.runtime_no_entry_scanners(rt):
        row["issue"] = f"runtime {row['runtime']!r} is registered but `runtime list` reads {listed!r}"
        row["next"] = triage
        return row
    entry = hmap.get(row["runtime"])
    if entry is None:
        row["unreadable"] = (f"`openclaw senpi status --json` reported no health for running runtime "
                             f"{row['runtime']!r}")
        return row
    row["health"] = _raw_health(entry)
    if row["health"] is None:
        row["unreadable"] = (f"`openclaw senpi status --json` carries no health field for runtime "
                             f"{row['runtime']!r}")
        return row
    if _cli.health_verdict(entry) != "healthy":
        # Quoted, never re-derived: degraded / unhealthy / the runtime's fail-closed `unknown` (no tick
        # has proven the scanner yet) are all "not confirmed live", and each is triage, not a redeploy.
        row["issue"] = (f"runtime {row['runtime']!r} is running but its health reads "
                        f"{row['health']!r} (`openclaw senpi status --json`)")
        row["next"] = triage
        return row
    row["ok"] = True
    return row


def _warn_lines(obj):
    """Every `[W_*]` line the deploy snapshot carries, verbatim, in the order found. Walks the whole
    document so a warn added to a new field still reaches the reader — this relays, it never composes."""
    found, seen = [], set()
    stack = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str) and _WARN_CODE.search(cur) and cur not in seen:
            seen.add(cur)
            found.append(cur)
    return found


def deploy_warns(pkg_id):
    """Warn lines from the agent's LAST deploy job, iff that job ran THIS package.

    No snapshot is not a failure — a package deployed before the verb legitimately has none, so a
    missing job is skipped silently. There is one job record per agent and it is not
    package-addressed, so another package's warns are skipped too: relaying polar's shortfall under a
    `verify spider` prompt is an invitation to bind the wrong package's numbers."""
    snap, _tail = read_status(None)
    if not isinstance(snap, dict):
        return []
    if str(((snap.get("meta") or {}).get("packageId") or "")) != str(pkg_id):
        return []
    return _warn_lines(snap)


def cmd_verify(pkg, a):
    """Is `<id>` live? A READ-ONLY composite — it starts no deploy, moves no money, and installs
    nothing. Exit: 0 verified · 3 not verified · 1 could not check."""
    if not pkg.instances:
        return _verify_unreadable(pkg, [f"{pkg.id} declares no instances, so there is nothing to "
                                        f"check against"], a.json)
    try:
        strategies, runtimes, hmap = verify_reads(pkg)
    except ReadFailed as e:
        return _verify_unreadable(pkg, [str(e)], a.json)
    rows = [verify_instance(pkg, inst, strategies, runtimes, hmap) for inst in pkg.instances]
    unreadable = [r["unreadable"] for r in rows if r["unreadable"]]
    if unreadable:
        return _verify_unreadable(pkg, unreadable, a.json)
    warns = deploy_warns(pkg.id)
    verdict = "verified" if (rows and all(r["ok"] for r in rows)) else "not-verified"
    if a.json:
        print(json.dumps({"verdict": verdict, "id": pkg.id, "instances": rows,
                          "warnings": warns, "unreadable": []}, indent=2))
        return VERIFY_OK if verdict == "verified" else VERIFY_NOT
    if verdict == "verified":
        print(f"✓ {pkg.id}: VERIFIED — {len(rows)} instance(s) live and healthy. Read-only check: "
              f"nothing was deployed, funded or installed.")
        for r in rows:
            print(f"    - {r['instance']}: {r['runtime']} {r['health']}  {str(r['wallet'])[:10]}…  "
                  f"funded {r['funded']}  [{r['status']}]")
    else:
        bad = [r for r in rows if not r["ok"]]
        print(f"✗ {pkg.id}: NOT VERIFIED — {len(bad)} of {len(rows)} instance(s) are not confirmed "
              f"live. This check read only; nothing was changed.", file=sys.stderr)
        for r in rows:
            if r["ok"]:
                print(f"    - {r['instance']}: OK — {r['runtime']} {r['health']}, funded {r['funded']}",
                      file=sys.stderr)
                continue
            print(f"    - {r['instance']}: {r['issue']}\n        Next: {r['next']}", file=sys.stderr)
    for w in warns:
        # The last deploy job's own words about THIS package — relayed, never restated.
        print(f"warn (from the last deploy job): {w}", file=sys.stderr)
    return VERIFY_OK if verdict == "verified" else VERIFY_NOT


def _verify_unreadable(pkg, reasons, as_json):
    """A read failed, so there IS no verdict. Say only that — a "couldn't check" that renders as
    verified is a lie, and one that renders as not-verified steers at the money path over a package
    that may be perfectly live."""
    if as_json:
        print(json.dumps({"verdict": "could-not-check", "id": pkg.id, "instances": [],
                          "warnings": [], "unreadable": reasons}, indent=2))
        return VERIFY_UNREADABLE
    print(f"? {pkg.id}: COULD NOT CHECK — a read this check needs failed, so NOTHING about {pkg.id} "
          f"is verified here (this says nothing about whether it is live):", file=sys.stderr)
    for r in reasons:
        print(f"    - {r}", file=sys.stderr)
    print(f"  Nothing was changed. Retry, or triage read-only: python3 "
          f"{Path(__file__).with_name('status.py').name} {pkg.id}", file=sys.stderr)
    return VERIFY_UNREADABLE


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
    pc.add_argument("--max-wait", type=int, default=None,
                    help=f"Seconds the JOB waits for wallets to reach ACTIVE, forwarded to the "
                         f"verb (default {DEFAULT_MAX_WAIT} — the verb's own). An explicit value "
                         f"is also how long this script polls.")
    pc.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pc.add_argument("--tick-wait", type=int, default=None,
                    help="Seconds the job waits to observe one verified scanner tick (0 skips).")
    pc.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("runtime", help="Resume/complete the same deploy (installs the runtime(s)).")
    common(pr)
    pr.add_argument("--budget", type=float, default=None, help="Only needed if a wallet still has to be created.")
    pr.add_argument("--max-wait", type=int, default=None,
                    help=f"Seconds the JOB waits for wallets to reach ACTIVE, forwarded to the "
                         f"verb (default {DEFAULT_MAX_WAIT} — the verb's own). An explicit value "
                         f"is also how long this script polls.")
    pr.add_argument("--decision-model", default=None, help="Bare model name (only for a decision_mode: llm action).")
    pr.add_argument("--tick-wait", type=int, default=None,
                    help="Seconds the job waits to observe one verified scanner tick (0 skips).")
    pr.add_argument("--dry-run", action="store_true")

    # `verify` is the READ-ONLY check. It takes NONE of the deploy flags — a check that accepts
    # `--budget` is a check that can fund a wallet, which is exactly the trap this command is not.
    # `--max-wait`/`--tick-wait`/`--dry-run` are meaningless with no job to run or plan.
    pv = sub.add_parser("verify",
                        help="READ-ONLY check: is <id> live? (strategy + runtime + health; deploys nothing)")
    common(pv)

    # `status` reports the agent's LAST deploy job — one record, not package-addressed — so it needs
    # no package and never resolves (or fetches) one. An id may still be given: it is checked against
    # the job, so a mismatch refuses instead of printing another package's verdict under it.
    ps = sub.add_parser("status", help="Show the last deploy job for this agent.")
    ps.add_argument("package", nargs="?", default=None,
                    help="Optional: the package you expect this job to be. A mismatch is refused.")
    ps.add_argument("--json", action="store_true")
    # Accepted but never advertised: `status` fetches nothing, so --ref selects nothing. Taking it here
    # buys the refusal below — argparse's own "unrecognized arguments" exits 2, which this wrapper's map
    # reads as "the deploy was refused, nothing created".
    ps.add_argument("--ref", default=None, help=argparse.SUPPRESS)

    pval = sub.add_parser("validate",
                          help="Preflight: is the package deploy-ready? (structural + render — no side effects)")
    common(pval)

    a = ap.parse_args(argv[1:])
    log = (lambda m: None) if a.json else (lambda m: print(m))

    # `status` reports the agent's last deploy JOB, which has nothing to do with the package on
    # disk — so it must not resolve (and possibly remote-fetch) a package just to print a snapshot.
    if a.cmd == "status":
        if a.ref:
            print(f"error: `status` reads this agent's deploy-job record — it resolves and fetches no "
                  f"package, so --ref selects nothing. Nothing was read; re-run without it:\n"
                  f"  python3 {Path(__file__).name} status [<id>]", file=sys.stderr)
            sys.exit(EXIT_INTERNAL)
        snap, tail = read_status(None)
        if snap is None:
            # No snapshot came back at all. That is the verb's `[NOT_FOUND]` (no deploy has ever run
            # here — and that refusal carries its own start command) or a status read that failed;
            # this wrapper cannot tell them apart, so it relays the verb's words rather than
            # asserting an absence. It used to compose "No deploy job recorded … Start one: create
            # <id> --budget <usd>" for every non-live job, steering at a FUNDED deploy while a job
            # was running or had just been refused.
            print(tail or "openclaw senpi deploy status returned no snapshot and no error text.",
                  file=sys.stderr)
            print("  No deploy state was read here, so nothing about any deploy is reported. Read it "
                  "directly:  openclaw senpi deploy status", file=sys.stderr)
            sys.exit(EXIT_INTERNAL)
        check_status_package(a.package, snap)
        print_status(None, a.json, snap)
        sys.exit(exit_code_for(snap))

    pkg = ensure_pkg(a.package, a.ref, log)

    # `verify` reads the package only for its instance list — it deploys nothing, so the pre-deploy
    # structural gate (whose whole job is to refuse BEFORE money moves) has nothing to protect here,
    # and refusing a read-only check over a render error would just hide the live state being asked about.
    if a.cmd == "verify":
        sys.exit(cmd_verify(pkg, a))

    # `validate` is the standalone, side-effect-free preflight; the action subcommands run the SAME
    # full check before the verb touches money.
    gate = full_validate(pkg)
    if a.cmd == "validate":
        # `validate` also REPORTS the live universe (the verb enforces it; this saves a deploy
        # round-trip). A dead name is an error like any other; an unreadable instrument list is a
        # note that never changes the exit code — it is not this command's invariant to hold.
        u_errors, u_note = universe_report(pkg)
        errors = gate + u_errors
        if a.json:
            print(json.dumps({"status": "valid" if not errors else "invalid", "id": pkg.id,
                              "errors": errors, **({"note": u_note} if u_note else {})}))
        else:
            # The note rides EVERY verdict, not just the clean one: it says a check did not run,
            # which is as true of an otherwise-invalid package as of a green one.
            if u_note:
                print(f"note: {u_note}", file=sys.stderr)
            if errors:
                print(f"✗ {pkg.id}: {len(errors)} issue(s) to fix before deploy:", file=sys.stderr)
                for e in errors:
                    print(f"    - {e}", file=sys.stderr)
            else:
                print(f"✓ {pkg.id}: deploy-ready ({len(pkg.instances)} instance(s))")
        sys.exit(EXIT_CODES["refused"] if errors else 0)
    if gate:
        # A gate said no and nothing was created past it — D-12's 2, the same code the verb's own
        # refusals carry. It exited 1, which is this wrapper's "the question could not be answered"
        # class: an agent reading that is taught the job may well be running (go read `deploy
        # status`) and that a retry is worth a try. Neither is true of a deterministic package error.
        print(f"✗ {pkg.id}: {len(gate)} issue(s) to fix before deploy — nothing was started:",
              file=sys.stderr)
        for e in gate:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["refused"])

    # An EXPLICIT --max-wait is the caller's answer to BOTH clocks: it is forwarded to the verb and
    # it is how long this script polls — a shorter one has to return sooner, a larger one is honoured
    # even past the ~180s tool timeout, because the caller asked for it by name. Unset: the verb's own
    # default is forwarded, and polling keeps its own budget, sized to return inside that timeout.
    a.poll_budget = POLL_BUDGET if a.max_wait is None else a.max_wait
    if a.max_wait is None:
        a.max_wait = DEFAULT_MAX_WAIT

    sys.exit(run_deploy(pkg, a, log))


if __name__ == "__main__":
    main(sys.argv)
