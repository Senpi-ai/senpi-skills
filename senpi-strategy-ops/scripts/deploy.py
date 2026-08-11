#!/usr/bin/env python3
"""Deploy a strategy PACKAGE — a thin wrapper over the runtime's `openclaw senpi deploy` verb.

The runtime owns the whole go-live path now: funds preflight → wallet create+fund (with
skillName/skillVersion attribution) → runtime install → one observed scanner tick. It runs the
job DETACHED, so this script starts it and then polls `openclaw senpi deploy status` until the
job is terminal, relaying the verb's report VERBATIM. Nothing here re-derives a number, a
lifecycle claim, or a refusal string — the `[E_*]` codes pass straight through.

ONE refusal is repaired instead of relayed, and only one: a deploy refused because the package's
proof was recorded under a different runtime BUILD (`STALE_PROOF_REASON`). The runtime self-updates
in place, so every update invalidates every proof on the box at once, and relaying that would make
our own release cadence a fleet-wide deploy outage. The repair is `senpi validate` + ONE re-run —
see `stale_proof_dirs`, which keys on the gate's structured evidence, never on its wording.

  python3 deploy.py validate <id>                                  # preflight (no side effects)
  python3 deploy.py create   <id> --budget <usd> [--max-wait S]    # run the deploy (create+fund+install+tick)
  python3 deploy.py runtime  <id> [--decision-model M]             # resume/complete the same deploy
  python3 deploy.py verify   <id>                                  # READ-ONLY: is it live? (deploys/fetches nothing)
  python3 deploy.py status   [<id>]                                # the last deploy job's status

TWO subcommands move money — `create` and `runtime` — and they drive the SAME idempotent verb:
re-running either resumes (the verb reconciles against the backend + the runtime registry and adopts
whatever exists), so they are one path under two names, not two steps. **`verify` is not one of
them.** It is the read-only check its name has always promised: a composite of `strategy_list` +
`openclaw senpi runtime list` + `openclaw senpi status --json` (plus a verbatim relay of the last
deploy job's `[W_*]` warns) that only QUOTES what it read. It starts no job, funds nothing, installs
nothing and FETCHES nothing (it resolves the package on disk only — downloading and writing one is a
side effect too) — so an agent following an old transcript ("just re-check it: deploy.py verify
spider") against a package whose funded wallet was deliberately left runtime-less cannot start it
trading. When something IS missing, verify NAMES the resume (`create`/`runtime`) and says what it will
do; it never runs it, and it never emits a teardown command. The step it names is chosen against the
state it actually READ: only an `ACTIVE` wallet is resumable, a transitional or teardown status
(`PAUSED`/`CLOSING_POSITIONS`, or a deploy still in flight) gets read-only triage, and a deploy job
already RUNNING for this package sends the reader to that job's report instead of a second dispatch.

Exit codes for `validate`/`create`/`runtime`/`status` mirror the verb's own (D-12): 0 live · 2
refused · 3 failed · 4 installed-unobserved · 5 interrupted · 6 pending (a wallet still funding, or
the job still running) · 1 internal/transport error. **2 is any gate saying no with nothing created
past it** — the verb's refusals, and this wrapper's own structural preflight (`validate`, and the
same pass the action subcommands run before the verb is started); both are deterministic and refuse
identically on a re-run.

**`verify` runs no job, so it returns none of that range beyond 0/3/1**: `0` verified (every instance
has an ACTIVE wallet AND a registered runtime AND its health read healthy) · `3` NOT verified (something
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
class, and it is gone. Package resolution (path or bare catalog id, with the remote fetch — on the
money path only; `verify` resolves locally) and the structural preflight stay here; the live-universe
gate and everything after them live in the
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


def local_pkg(arg):
    """`verify`'s package resolution: LOCAL ONLY — returns the package, or None if it isn't on disk.

    `ensure_pkg` falls through to a remote fetch that DOWNLOADS and WRITES a full package under the
    durable strategies root. That is a network+disk side effect from a command whose whole promise is
    "read-only — nothing was changed", and on a bare catalog id it is the common case, not an edge.
    A check reads what is here; fetching is the money path's job."""
    return _pkg.load(arg) if (_pkg.resolve_pkg_dir(arg) / "strategy.yaml").is_file() else None


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
# answers with its CLI PARSER's error, not the verb's. That is neither transport breakage nor a
# refusal: the command line never reached a gateway, so nothing was dispatched, no job of the verb's
# can exist, and the "read `openclaw senpi deploy status`" next step names a verb this box does not
# have either.
#
# WHAT SUCH A BOX ACTUALLY PRINTS — reproduced against Commander 14.0.3 (the runtime's own) with a
# program shaped like `registerSenpiCli` (senpi-trading-runtime `src/cli/senpi-commands.ts`);
# `CommanderRejectionShapes` in tests/test_deploy_wrapper.py regenerates these and holds this list
# to them:
#
#   `senpi` WITH its root `.action()` — the `--cheatsheet` handler, every runtime since 2026-03-27:
#     senpi deploy status --json  ->  error: unknown option '--json'
#     senpi deploy status         ->  error: too many arguments for 'senpi'. Expected 0 arguments but got 2.
#     senpi deploy -p <dir> …     ->  error: unknown option '-p'
#   `senpi` with no root action — runtimes older than that:
#     all three                   ->  error: unknown command 'deploy'
#
# The root action is why: Commander dispatches `deploy status` INTO it instead of raising
# unknownCommand, so neither "unknown command" nor even the word "deploy" appears on any box in the
# fleet. Matching those (what this did) detected only pre-2026-03-27 runtimes — nothing that exists.
# So the match is on the PARSE-ERROR grammar, anchored to the line Commander writes, and it never
# scans the payload for "deploy": the answer text legitimately contains that word in cases that are
# not this one.
_CLI_PARSE_ERROR = re.compile(r"^\s*error: (?:unknown command|unknown option|too many arguments)\b",
                              re.M)
# The same three shapes WITH the token they name — Commander quotes exactly one right after the
# phrase (`unknown option '--json'`, `unknown command 'deploy'`, `too many arguments for 'senpi'`).
# The token is the only thing that says WHOSE parser spoke; see `_cli_rejected_the_command`.
# Quoting style is Commander ≥ v7's. Commander ≤ v6 wrote a mixed pair (``unknown option `--json'``)
# which this pattern does not match, so such a box would fall to could-not-check rather than
# no-job — the safe direction, and unreachable anyway: openclaw runs Commander 14.
_CLI_PARSE_ERROR_TOKEN = re.compile(
    r"^\s*error: (?:unknown command|unknown option|too many arguments for)\s+'([^']*)'", re.M)
# A bracketed [CODE] means the VERB is speaking (a refusal, or an error it raised), whatever words
# follow — so a refusal that happens to quote a parser error is never read as a missing verb.
_REFUSAL_CODE = re.compile(r"\[[A-Z][A-Z_]{2,}\]")


def _cli_rejected_the_command(text, ours=None):
    """True when a CLI PARSER answered instead of the verb — i.e. the command line never ran.

    Usually a plugin that predates the `senpi deploy` verb; the same shape covers a plugin that
    predates one of its FLAGS, and the answer to both is the same update.

    `ours` — the argv this script actually ran — makes it answer "whose parser?", which the grammar
    alone cannot. The verb runs every gateway round-trip as an INNER `openclaw gateway call …
    --params …` with **stderr inherited** (runtime `src/cli/senpi-commands.ts`, `callGatewayJson`:
    `stdio: ["inherit", "pipe", "inherit"]`), so an openclaw that rejects a flag the PLUGIN passes
    — plugin-newer-than-openclaw, a live configuration: the image pins a prebuilt OpenClaw tree
    while the runtime self-updates from npm — writes `error: unknown option '--params'` onto exactly
    the same stderr, in exactly the same grammar. Read as ours, that says "this box has no deploy
    verb" about a box that HAS it and may be running a job right now. Passing `ours` requires every
    token the error quotes to be one WE sent; `--params` never is.

    Callers that only need "was anything dispatched?" leave `ours` unset: nothing ran either way."""
    s = str(text or "")
    if _REFUSAL_CODE.search(s):
        return False
    if ours is not None:
        quoted = _CLI_PARSE_ERROR_TOKEN.findall(s)
        # No tokenisable parse error, or one naming a token we never sent: not our parser's answer.
        # `all` over an empty list would say True, which is the fail-open direction.
        return bool(quoted) and all(tok in ours for tok in quoted)
    return bool(_CLI_PARSE_ERROR.search(s))


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
        tail = _cli.error_tail(err, out)
        if _cli_rejected_the_command(tail):
            # No `ours=` here, deliberately: on the START path the question is only "was anything
            # dispatched?", and a parse error answers NO whichever parser raised it — the outer
            # `senpi deploy` or the inner `gateway call` it shells out to. (The job READ has to tell
            # them apart, because its answer decides whether a money steer is printed; see
            # `_no_deploy_job_answer`.)
            # Exit 1 (the question could not be answered) — never the parser's own code, which may be
            # 2 and would read as "a gate refused the deploy, nothing created past it". Nothing was
            # created, but nothing was gated either: the box cannot run this command at all.
            print(tail, file=sys.stderr)
            # WHICH side is stale changes the cure, and the same discriminator the job read uses
            # answers it here: text naming the `gateway call` round-trip can only come from a verb
            # that RAN, so the plugin has the verb and it is this box's openclaw that is behind.
            # Prescribing a plugin install there is worse than useless — the plugin is already the
            # newer side, and installing a newer one widens the gap it is being blamed for.
            if _GATEWAY_ROUND_TRIP.search(tail):
                cause = ("The verb IS present on this box: it ran, and shelled out to `openclaw "
                         "gateway call`, and THAT call was rejected. The runtime plugin and this "
                         "box's openclaw binary disagree about the gateway-call arguments — the "
                         "plugin is newer than the openclaw it is running on.")
                cure = ("  Updating the plugin will NOT fix this — it is already the newer side. "
                        "What is behind is the openclaw binary, which ships in the agent image, so "
                        "no plugin install moves it.\n"
                        "  Report the skew with the rejected argument named above and this "
                        "(read-only):\n"
                        "    openclaw --version\n"
                        "  Re-running this exact command reproduces it identically until the box's "
                        "openclaw is updated.")
            else:
                cause = ("Its runtime plugin has no `senpi deploy` verb (or not this flag of it): "
                         "it predates the verb, usually a wedged self-update.")
                cure = ("  Update the runtime plugin, then re-run this exact command:\n"
                        "    openclaw plugins install @senpi-ai/runtime\n"
                        "  Agent boxes also self-update on their own schedule, so retrying in a few "
                        "minutes is the other way out.")
            print(f"This box's CLI rejected the command above before anything was dispatched. "
                  f"{cause}\n"
                  f"  NOTHING WAS DISPATCHED: no job started, no wallet created, no funds moved, and "
                  f"`openclaw senpi deploy status` has nothing to report here — there is nothing to "
                  f"read and nothing to clean up.\n"
                  f"{cure}", file=sys.stderr)
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


def _status_argv(deploy_id=None):
    """The exact `deploy status` command this script runs. ONE producer, because a classifier that
    has to answer "was that OUR parser?" compares the error's quoted token against these tokens —
    an argv built twice is a classifier keyed to a command nobody sent."""
    args = ["openclaw", "senpi", "deploy", "status"]
    if deploy_id:
        args.append(deploy_id)
    args.append("--json")
    return args


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
    the verb's text so a caller can relay it instead of composing an absence it never read.

    The call's own words come back even when stdout DID parse: the two streams answer different
    questions, and a JSON object on stdout is not proof that stderr said nothing (one stray JSON log
    line is enough to make `_extract_json` produce a dict while the verb's real `[NOT_FOUND]` — or a
    parser's rejection — sits on stderr). Callers that must classify the answer, not just print it,
    need both; a caller that only prints uses the text when there is no snapshot, as before."""
    _rc, out, err = _cli.run_cli(_status_argv(deploy_id), timeout=STATUS_TIMEOUT)
    snap = _cli._extract_json(out)
    if isinstance(snap, dict):
        # stderr ONLY here: stdout is the payload, and feeding it back as "the call's words" would
        # hand a classifier the document it already rejected.
        return snap, _cli.error_tail(err)
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


# ---------- the one repair: a proof the runtime moved out from under ----------

# The single proof state this wrapper repairs instead of relaying. `openclaw senpi deploy` refuses,
# pre-money, any package whose `.senpi-proof.json` was recorded under a different runtime build —
# and the fleet updates the runtime IN PLACE, hourly, which invalidates every proof on the box at
# once. Relayed, that turns our own release cadence into a deploy outage for every agent whose
# package was proven before the update.
#
# The other two reasons are deliberately NOT repaired. `no_proof` and `content_changed` both mean a
# human or an agent changed something (or never proved it at all), and re-proving on their behalf
# would record a PASS over an edit nobody has looked at — which is the exact fund-what-nobody-
# watched-run failure the gate exists to prevent.
STALE_PROOF_REASON = "runtime_version_changed"

# How long ONE `openclaw senpi validate` gets. The runtime's own default per-scanner tick budget is
# 120s (`DEFAULT_TICK_BUDGET_SECONDS`, src/validate/run.ts) and validate spawns Python and imports
# the scanner around it, so this is that plus headroom. A validate that outruns it comes back rc=-1
# and is reported as a FAILED re-validation — never as a repair, because no proof was written.
VALIDATE_TIMEOUT = 180


def _refused_steps(snap):
    """Every `refused` step row in a terminal report — the rows a gate wrote."""
    for inst in ((snap or {}).get("report") or {}).get("instances") or []:
        for outcome in ((inst or {}).get("steps") or {}).values():
            if isinstance(outcome, dict) and outcome.get("status") == "refused":
                yield outcome


def _instance_dirs(pkg):
    """Every distinct directory holding an instance's runtime.yaml, package root as the floor.

    The verb's own fallback, mirrored: when `verifyProof` reports no instance identity,
    `failingTarget` (runtime `src/deploy/proof-gate.ts`) targets every instance dir — which on a
    flat package IS the package root."""
    dirs = []
    for inst in getattr(pkg, "instances", None) or ():
        path = getattr(inst, "runtime_path", None)
        d = str(Path(path).parent) if path else None
        if d and d not in dirs:
            dirs.append(d)
    return dirs or [str(pkg.dir)]


def stale_proof_dirs(snap, pkg):
    """The directories to re-prove when this deploy was refused ONLY because its proof predates the
    running runtime — otherwise `[]`.

    Read off the gate's STRUCTURED evidence (`evidence.reason`), never off its prose. The three
    proof states are one refusal family whose wording the runtime owns and rewords, and two of them
    must reach a human; a wrapper keyed on words repairs the wrong one the day the sentence changes.
    `reason` is the field that distinguishes them — the bracketed code does too, but it is derived
    from the same verdict and only the reason is documented as the discriminator.

    Fail-closed in every direction: a non-refused job, a snapshot with no report (a runtime older
    than the gate), a refusal with no evidence, an unrecognised reason, ANY other refused step
    riding along, or an `instance_dir` that is not a plain relative subpath — all of them return
    `[]`, and the verb's refusal is relayed exactly as it was written."""
    state = (snap or {}).get("state") or {}
    if state.get("status") != "done" or state.get("overall") != "refused":
        return []
    refusals = list(_refused_steps(snap))
    if not refusals:
        return []
    # EVERY refused row must be this one reason. A stale proof beside another gate's refusal is not
    # ours to repair: re-running would only be refused again, by the gate we did not answer.
    if any(((r.get("evidence") or {}).get("reason")) != STALE_PROOF_REASON for r in refusals):
        return []
    dirs = []
    for r in refusals:
        rel = (r.get("evidence") or {}).get("instance_dir")
        if not rel or not isinstance(rel, str):
            continue
        # `instance_dir` is documented as relative to the package root (`relative(pkg.dir,
        # inst.runtimeYamlDir)`). Anything else — absolute, or climbing out with `..` — is a shape
        # this wrapper does not recognise, and it would point a live validation at a directory
        # outside the package. Relay instead; never act on a path we cannot place.
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            return []
        d = str(Path(pkg.dir) / p)
        if d not in dirs:
            dirs.append(d)
    return dirs or _instance_dirs(pkg)


def revalidate(dirs, log):
    """Re-record each directory's proof. Returns `(ok, the failing call's own words)`.

    `openclaw senpi validate <dir>` is the ONLY command that writes a `.senpi-proof.json` — a full,
    unscoped run at live depth that PASSES — which is why the python validator is not an option
    here. Its exit codes are validate's own: `0` PASS · `1` FAIL · `2` UNPROVEN · `3` invocation
    error. Only `0` records a proof, so only `0` is a repair; everything else hands back validate's
    text for the caller to surface unedited."""
    for d in dirs:
        log(f"  re-proving {d} (openclaw senpi validate)…")
        rc, out, err = _cli.run_cli(["openclaw", "senpi", "validate", str(d)], timeout=VALIDATE_TIMEOUT)
        if rc != 0:
            return False, (_cli.error_tail(err, out, limit=2000)
                           or f"openclaw senpi validate {d} exited {rc} and printed nothing.")
    return True, ""


def _poll_deploy(pkg, a, log):
    """One start → poll pass: `(deployId, snapshot | None, the poll budget it used)`."""
    deploy_id = start_deploy(pkg, a, log)
    poll_budget = getattr(a, "poll_budget", None)
    if poll_budget is None:
        poll_budget = POLL_BUDGET
    return deploy_id, wait_for_terminal(deploy_id, poll_budget, log), poll_budget


def _unreadable_job(deploy_id):
    print(f"Could not read the deploy job's status. Check it directly: "
          f"openclaw senpi deploy status {deploy_id}", file=sys.stderr)
    return EXIT_INTERNAL


def run_deploy(pkg, a, log):
    """Start → poll → relay. Exit code: the D-12 map (see EXIT_CODES).

    ONE repair rides this path and only one: a deploy refused because the package's proof was
    recorded under a different runtime build is re-proven and re-run ONCE (`STALE_PROOF_REASON`).
    Everything else — every other gate, and the other two proof states — is relayed verbatim, and a
    re-validation that does not PASS carries validate's own findings out without re-running the
    deploy."""
    deploy_id, snap, poll_budget = _poll_deploy(pkg, a, log)
    if snap is None:
        return _unreadable_job(deploy_id)
    dirs = stale_proof_dirs(snap, pkg)
    if dirs:
        # stderr, always: in `--json` mode stdout is the snapshot document and nothing else.
        print(f"note: the deploy was REFUSED before anything was created — the proof for "
              f"{', '.join(dirs)} was recorded under a different runtime build than the one running "
              f"here (the runtime updates itself in place, so this happens to a package nobody "
              f"touched). Re-proving it now with `openclaw senpi validate`; if that PASSES the "
              f"deploy is re-run ONCE, and if it does not, its findings are printed here and the "
              f"deploy is not re-run. Nothing was created, funded or installed by the refused run.",
              file=sys.stderr)
        ok, text = revalidate(dirs, log)
        if ok:
            deploy_id, fresh, poll_budget = _poll_deploy(pkg, a, log)
            if fresh is None:
                return _unreadable_job(deploy_id)
            snap = fresh
        else:
            print(text, file=sys.stderr)
            print(f"`openclaw senpi validate` did not PASS, so no proof was recorded and the deploy "
                  f"was NOT re-run — its refusal is reported below, unchanged. Nothing was created, "
                  f"funded or installed by either call. Fix what validate reported above, then "
                  f"re-run this command.", file=sys.stderr)
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

# Flags `verify` carried while it was a wrapper around the deploy verb. They are ACCEPTED and
# IGNORED, never rejected: argparse answers an unknown flag with exit **2**, and 2 is D-12's
# "refused — a gate said no, nothing was created, retrying refuses identically". An agent replaying
# a pre-3.1.0 transcript (`deploy.py verify spider --max-wait 300`) would read that as "the deploy
# was refused" about a package that may be perfectly live, and would never re-run without the stale
# flag — the exact stale-transcript path the read-only rewrite exists to protect. So the check runs,
# unchanged, and stderr says which flag was dropped and where the current contract is written down.
# Only these five: a typo (`--bugdet`) still errors, because it is not the command its author meant.
OBSOLETE_VERIFY_FLAGS = {
    "--budget": "verify is the READ-ONLY check — it funds no wallet, so there is nothing to size",
    "--max-wait": "verify starts no job, so there is nothing to wait for",
    "--tick-wait": "verify observes no tick — it reads the health the runtime already published",
    "--decision-model": "verify installs nothing, so no action's model is chosen here",
    "--dry-run": "verify has no plan mode — being read-only, the check itself IS the whole effect",
}


def _obsolete_dest(flag):
    """A dest of its own per obsolete flag, so nothing downstream can read one as a live option —
    `--dry-run` in particular must not reach the `--dry-run --json` refusal, which is the money
    path's (there is no JSON rendering of a plan; a read-only verdict renders fine)."""
    return "_obsolete_" + flag.lstrip("-").replace("-", "_")


def warn_obsolete_verify_flags(a):
    """Name every obsolete flag this invocation carried, and where the current contract lives.

    stderr only, and before the verdict: stdout stays exactly one document (`--json` callers parse
    it), and the verdict below is byte-for-byte the one the flagless command renders."""
    used = [(flag, getattr(a, _obsolete_dest(flag), None)) for flag in OBSOLETE_VERIFY_FLAGS]
    used = [(flag, val) for flag, val in used if val not in (None, False)]
    if not used:
        return
    skill = Path(__file__).resolve().parents[1]
    n = len(used)
    print(f"warning: `verify` IGNORED {n} flag{'' if n == 1 else 's'} it no longer has — "
          f"{'it' if n == 1 else 'they'} changed NOTHING about the check below, and nothing was "
          f"deployed, funded, installed or planned:", file=sys.stderr)
    for flag, val in used:
        shown = flag if val is True else f"{flag} {val}"
        print(f"    - {shown} — obsolete: {OBSOLETE_VERIFY_FLAGS[flag]}.", file=sys.stderr)
    print(f"  The verdict below is exactly the one this command renders without "
          f"{'it' if n == 1 else 'them'}. If you are replaying an older transcript, that transcript "
          f"is out of date — the CURRENT contract is:\n"
          f"    python3 {Path(__file__).name} verify --help   # verify's flags and exit codes "
          f"(0 verified / 3 not verified / 1 could not check)\n"
          f"    {skill / 'SKILL.md'}   # what verify checks and how to read its verdict\n"
          f"    {skill / 'references' / 'lifecycle.md'}   # the full deploy.py reference\n"
          f"  (These flags live on `create`/`runtime` — the MONEY path, which this check is not. "
          f"What to do next is the verdict below, not these flags.)", file=sys.stderr)


_WARN_CODE = re.compile(r"\[W_[A-Z][A-Z_]*\]")

# The verb's own "no deploy has ever run on this agent" refusal, matched on the taxonomy code (not on
# prose) and ANCHORED to the start of a line — that is where the code actually sits, because the CLI
# prints the refusal's own message and every refusal message OPENS with its code (runtime
# `src/deploy/register.ts`: "[NOT_FOUND] No deploy has been started on this agent. …"). Unanchored,
# any line that merely QUOTES a backend `[NOT_FOUND]` — a gateway wrapper, an interleaved log line —
# read as "no job ever ran" while a job could be funding a wallet. `error_tail` merges both streams,
# so that text is not this script's to trust beyond its shape.
_NO_DEPLOY_JOB = re.compile(r"^\s*\[NOT_FOUND\]", re.M)

# Evidence that the VERB ran and reached its gateway round-trip — which a box without the verb never
# does. Both phrasings come from the runtime's `reportUnreadableGateway` (`src/cli/senpi-commands.ts`),
# which only fires after the verb's action is executing: "the `gateway call` subprocess failed …
# exit 1 (transport/internal)" and "the gateway answered but its response could not be read — exit 1
# (transport/internal)". Where this appears, the answer is transport breakage, never a missing verb —
# whatever parse error rode along with it from the inner call's inherited stderr.
_GATEWAY_ROUND_TRIP = re.compile(r"`?gateway call`?|\(transport/internal\)", re.I)


def _no_deploy_job_answer(text):
    """True when the CALL'S OWN WORDS positively answer "no deploy job exists here".

    Two answers say it, and nothing else does. The verb's own `[NOT_FOUND]` refusal — no deploy has
    ever been started on this agent. And a CLI that REJECTED the command line (`_cli_rejected_the
    _command`): it never reached a gateway, so it is not running a verb job either. Precisely: the
    CLI PROCESS is not running one. The long-lived GATEWAY owns the job, and the two can disagree —
    if the whole plugin were missing from the CLI (`error: unknown command 'senpi'`, a token that IS
    ours) while the gateway still held it loaded with a job in flight, this would answer no-job
    wrongly. It cannot arrive here: `verify_reads` runs `openclaw senpi runtime list` and `senpi
    status --json` FIRST and a CLI with no `senpi` command fails both the same way, so verify has
    already returned could-not-check on the runtime-inventory read. That containment lives in the
    composite's read order, not in this function — anything that reorders those reads, or calls this
    without them, has to bring its own answer for the whole-plugin case. The narrower shapes this
    function is really for (verb absent, flag unknown) leave the rest of the CLI working, so they
    reach here with the inventory reads already answered. That second answer is fleet reality, not
    theory — skills update hourly from `main` while the runtime self-updates
    from npm on its own cadence, so during a rollout window a box legitimately has new skills and a
    pre-verb runtime. Reading it as unreadable made `verify` print COULD NOT CHECK (1) over a
    perfectly live strategy on every run, with a "re-reading is the answer" steer that can never
    come true there.

    Only ever given text the CLI produced — never a string this script composed about the answer:
    a synthesised description is not evidence, and scanning one for these shapes is how a detector
    starts matching its own prose.

    Everything else (a spawn failure, the STATUS_TIMEOUT, any other error) is a read that did not
    happen and stays could-not-check.

    Two guards keep the parse-error leg on the safe side of the money question, because here —
    unlike the start path, where nothing was dispatched whoever's parser spoke — a wrong "no job"
    lets the row name `create --budget` while a job may be funding a wallet:

      * the tokens must be OURS (`_status_argv`), so the verb's inner `gateway call` being rejected
        by an older openclaw is not read as our verb missing, and
      * an answer that mentions the `gateway call` round-trip at all is transport breakage by
        definition: the verb RAN, far enough to try the gateway. A box that has no verb never gets
        there.

    LATENT, and unreachable only by luck: a parse error naming a token of OURS cannot distinguish
    "no verb" from "verb present, that token unknown". It is safe today because `deploy status` and
    its `--json` shipped as one unit, so no released verb rejects either. The day ANY token is added
    to this read, a slightly-older verb answers with it — ours by token, no gateway text — and a
    RUNNING job classifies as no-job. A flag is only the obvious half: an added POSITIONAL produces
    `error: too many arguments for 'status'`, and `'status'` is ours too. Adding anything to this
    read means bringing a different discriminator with it (probe the verb, not the token)."""
    s = str(text or "")
    if _NO_DEPLOY_JOB.search(s):
        return True
    if _GATEWAY_ROUND_TRIP.search(s):
        return False
    return _cli_rejected_the_command(s, ours=_status_argv())


def _job_snapshot(snap):
    """The deploy-job snapshot, or None when what came back is not one.

    Every snapshot the verb emits carries a `state.status` of `running`/`done`/`interrupted` — the
    field checked here (source of truth: the runtime's `DeploySnapshotLike` in
    `src/cli/deploy-render.ts`, where `meta` rides along and is read separately by the caller,
    which has its own answer for a snapshot that names no package).
    `_extract_json` recovers ANY JSON object it can find in stdout, so
    without this shape check an `{"ok": false, "error": {…}}` envelope — or one complete JSON log
    line in a timed-out call's partial stdout — was accepted as a snapshot, missed the packageId
    comparison and returned "no job running": fail-open on the single bit every money steer in this
    check hangs off."""
    if not isinstance(snap, dict):
        return None
    state = snap.get("state")
    if not isinstance(state, dict) or not state.get("status"):
        return None
    return snap


# The read-failure signal lives in `_cli`, beside the strict readers that raise it — verify is only
# its first consumer. Aliased here because every `except ReadFailed` in this file reads better bare.
ReadFailed = _cli.ReadFailed


def _sanitize_strategy_name(raw):
    """The deploy verb's `sanitizeStrategyName`, ported: trim, whitespace → '-', drop anything
    outside `[A-Za-z0-9_-]`, trim leading/trailing **'-' only**, cap at 40 chars.

    Source of truth: senpi-trading-runtime `src/deploy/package.ts`
    (`.trim().replace(/\\s+/g,"-").replace(/[^A-Za-z0-9_-]/g,"").replace(/^-+|-+$/g,"").slice(0,40)`).
    This used to `.strip("-_")`, trimming underscores the verb keeps — so for any id or instance name
    with an edge underscore, verify re-derived a DIFFERENT wallet name than the one the deploy
    created, and reported a live deploy as NOT VERIFIED. There is no fallback here either: the verb
    refuses at load unless `sanitizeStrategyName(id) == id`, so a name this returns empty is a name
    no deploy could have created."""
    s = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"\s+", "-", str(raw).strip()))
    return re.sub(r"^-+|-+$", "", s)[:40]


def _wallet_name(pkg, inst):
    """The strategyName the deploy verb gives this instance's wallet: `<id>-<instance>` for a
    multi-instance package, else the bare `<id>` — sanitized the way the verb sanitizes it.
    `verify` re-derives the SAME name to match a live strategy back to its instance, so the check and
    the deploy cannot disagree about which wallet belongs to which sleeve."""
    multi = len(pkg.instances) > 1
    raw = f"{pkg.id}-{inst.name}" if (multi and inst.name) else str(pkg.id)
    return _sanitize_strategy_name(raw)


def verify_reads(pkg):
    """The three read-only surfaces `verify` composes, or `ReadFailed` naming the one that failed.

    Every one of them is a READ: MCP `strategy_list` (what the backend says is live), `openclaw senpi
    runtime list` (what is registered on this box — the authoritative inventory; `status --json` is
    transiently empty and is NOT one), and `openclaw senpi status --json` (the runtime's OWN health
    verdict per runtime, the reliable liveness source). Nothing here creates, funds, installs or
    starts anything.

    All three are read STRICTLY, through the same `*_or_none` idiom: an unreadable surface answers
    None and is turned into a `ReadFailed` here, rather than degrading to "empty" — which would
    render as "nothing is deployed", the one answer a check must never invent."""
    try:
        mcp = MCPClient()
    except Exception as e:  # noqa: BLE001 — a client we cannot even build is an unreadable surface
        raise ReadFailed(f"MCP `strategy_list` could not be read ({e})")
    # None covers BOTH no-answer shapes — the call failed, or it answered with no recognisable
    # strategies list. The verdict is the same either way (render nothing), but the CAUSE is what an
    # operator acts on, so `why` carries it into the refusal instead of leaving them a bare "could
    # not read". A genuinely empty [] is an answer and falls through.
    why = []
    rows = _cli.list_strategies_or_none(mcp, timeout=STATUS_TIMEOUT, statuses=_cli.LIVE_STATUSES,
                                        why=why)
    if rows is None:
        raise ReadFailed(f"MCP `strategy_list` could not be read — the live-strategy inventory is "
                         f"not visible from here: {why[0] if why else 'no cause reported'}")
    strategies = [s for s in rows if _cli.strategy_open(s)]
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
    this is what gets printed, so the report never invents a word the runtime did not say.

    Only the HEALTH keys are read (`_cli.HEALTH_KEYS`). An entry whose only signal is a run state
    (`status: "running"`) has published no health, and quoting a run state under the word "health"
    is how an unproven runtime reads as a healthy one — that entry is unreadable health, and verify
    says so instead."""
    h = _cli._deep_first(entry, list(_cli.HEALTH_KEYS))
    return None if h is None else str(h)


def _funded_text(funded):
    """The funded figure for prose. `strategy_funded` is None when the backend record carried no
    amount that LANDED — say UNKNOWN and send the reader somewhere that can prove it, never a
    requested budget rendered as funded (see `_cli.strategy_funded`)."""
    return funded or "funded amount UNKNOWN — the strategy record carried no totalFunded/netFunded"


def verify_instance(pkg, inst, strategies, runtimes, hmap, job_running=False):
    """One instance's row: what exists, quoted — plus the ONE non-destructive next step for what does
    not. Never a close/teardown command, and never a step that is wrong for the state actually read."""
    want = _wallet_name(pkg, inst)
    rt_name = getattr(inst, "runtime_name", None) or f"{pkg.id}-{inst.name}"
    row = {"instance": inst.name, "strategy_name": want, "runtime": None, "wallet": None,
           "status": None, "funded": None, "health": None, "ok": False, "issue": None,
           "next": None, "unreadable": None, "collision": None}
    # The one next step for every state this check cannot resolve on its own: read-only, and never a
    # close — a check that emits a teardown command is a check that can lose someone's money.
    triage = (f"python3 {Path(__file__).with_name('status.py').name} {pkg.id}   # read-only: map each "
              f"wallet to its runtime/strategy")
    # A deploy job for THIS package running right now replaces every steer that would otherwise name
    # the resume: the resume IS that job, and a second dispatch races the one already funding a wallet.
    watch = "openclaw senpi deploy status   # read-only: the report of the job already running"

    # Match by the name the verb DERIVES, across every live strategy — then let attribution
    # DISAMBIGUATE between same-named candidates, never SHRINK the set to nothing.
    #
    # Both halves are regressions this has already had. Gating the match behind attribution dropped a
    # live funded wallet out of the check entirely and printed "nothing is funded here" over it —
    # `strategy_skill` falls back to tradingStrategyName, which on a multi-instance package can never
    # equal the package id ("spider-swing" != "spider") — a false quoted fact, steered at `create
    # --budget`. Matching on the name ALONE then adopted another package's wallet: single-instance
    # package `spider-swing` and `spider`'s `swing` sleeve derive the same name, so a never-deployed
    # sleeve rendered OK against someone else's wallet, runtime and health.
    #
    # So: a candidate is ruled out only by an attribution that was WRITTEN and names a DIFFERENT
    # package (`strategy_skill_declared`, no name fallback — silence is not a foreign owner).
    pkg_live = [s for s in strategies if _cli.strategy_skill(s) == pkg.id]
    named = [s for s in strategies if _cli.strategy_name_match(_cli.strategy_name(s), want)]
    is_foreign = [_cli.strategy_skill_declared(s) not in (None, pkg.id) for s in named]
    foreign = [s for s, bad in zip(named, is_foreign) if bad]
    cands = [s for s, bad in zip(named, is_foreign) if not bad]
    if not cands and len(pkg.instances) == 1:
        cands = list(pkg_live)          # single instance: the package's lone live strategy is it
    if not cands and foreign:
        # The name is TAKEN by another package's wallet, and nothing here belongs to this instance.
        # Not "nothing is funded" either: steering at `create --budget` would fund a second wallet
        # under a name already in use, on top of a wallet this package must not touch.
        # ONE producer for the stamps: the list, its rendering and every command built from it come
        # from here, so the row cannot name a stamp its command does not pass.
        owners = sorted({str(_cli.strategy_skill_declared(s)) for s in foreign})
        many = len(owners) > 1
        stamps = ", ".join(repr(o) for o in owners)
        status_py = Path(__file__).with_name('status.py').name
        row["collision"] = {"name": want, "attributed_to": owners}
        # QUOTE the stamp; do not assert who created the wallet. All this read is a `skillName`
        # field, and anything that calls the wallet-creation tool can write any value into it —
        # skill-script guards are advisory, so a raw MCP create, or this package under an older or
        # differently-cased id, produces exactly this row. "It was created by that package" is a
        # fact the check does not have, and stated as one over the user's OWN mis-stamped wallet it
        # is a false certainty attached to a do-not-deploy warning.
        #
        # The MATCH is what is asserted, not the record's name field: this row is reached because
        # `strategy_name` answered a name that MATCHES `want` (case-folded — the backend
        # case-normalizes what it stores), and that reader takes the first WRITTEN of
        # strategyName/tradingStrategyName/name the payload carries — so "the live strategy NAMED x"
        # would state which field was read, which it does not know.
        n = len(foreign)
        row["issue"] = (f"{'a live strategy matches' if n == 1 else f'{n} live strategies match'} the "
                        f"name this instance derives ({want!r}) but "
                        f"{'carries' if n == 1 else 'carry'} the skillName "
                        f"stamp{'s' if many else ''} {stamps}, not {pkg.id!r}. "
                        f"{'That stamp is' if not many else 'Those stamps are'} what the "
                        f"record{'' if n == 1 else 's'} say{'s' if n == 1 else ''}, not proof of who "
                        f"created {'it' if n == 1 else 'them'} (any caller of the wallet-creation "
                        f"tool can write any skillName), but {'it is not' if not many else 'none is'} "
                        f"this package's, so nothing here says this instance is deployed — and the "
                        f"name it would deploy under is taken")
        # The package-filtered triage would hide the very wallet in question: `status.py <id>` filters
        # on the same stamp, so a wallet stamped otherwise is not in that answer. Ask for it by the
        # stamp it carries — ONE COMMAND PER STAMP, because `status.py` takes a single id and a
        # joined list is a line that exits 2 on the read the reader was just told to make.
        reads = "\n        ".join(
            f"python3 {status_py} {o!r}   # read-only: what carries that stamp, and whether it is "
            f"yours" for o in owners)
        # One stamp: the route is a command. Several: a template naming the argument, never one
        # stamp picked arbitrarily and never a joined list — both would be commands that answer for
        # a wallet the reader did not ask about.
        verify_step = (f"python3 {Path(__file__).name} verify "
                       f"{'<that stamp>' if many else repr(owners[0])}")
        it, them, keeps = (("them", "they are", "the wallets go on running exactly as they are")
                           if many else
                           ("it", "it is", "the wallet goes on running exactly as it is"))
        row["next"] = (f"{reads}\n"
                       f"        python3 {status_py}   # read-only: every open strategy on this "
                       f"agent, whatever it is named\n"
                       f"        Do NOT deploy {pkg.id!r} until you know whose "
                       f"{'wallets those are' if many else 'wallet that is'} — it would fund a "
                       f"SECOND wallet under a name already in use.\n"
                       f"        If {'a stamp names' if many else f'{owners[0]!r} is'} a package you "
                       f"have, that package is where the wallet is checked and operated: "
                       f"{verify_step} (read-only).\n"
                       f"        If {them} your own under a stamp nothing owns, no tool on this path "
                       f"rewrites a strategy's skillName: {pkg.id!r} cannot adopt {it} under this "
                       f"name, and {keeps}.")
        return row
    if not cands:
        if pkg_live:
            # Wallets for this package exist but none carries this instance's name (a create-time
            # name-rejection fallback, or a renamed wallet). NOT "nothing exists": steering at
            # `create --budget` here funds a SECOND wallet beside a possibly live, funded one.
            row["issue"] = (f"{len(pkg_live)} live {pkg.id} wallet(s) exist but none is named {want!r} "
                            f"— this instance could not be matched to one")
            row["next"] = triage
        elif job_running:
            row["issue"] = (f"no live strategy named {want!r} on the backend YET — and a deploy job "
                            f"for {pkg.id} is RUNNING on this agent right now, so its wallet may be "
                            f"mid-creation")
            row["next"] = watch
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
    if not _cli.strategy_active(strat):
        # Open, but NOT trading — and not resumable from here. `PAUSED`/`CLOSING_POSITIONS` is a wallet
        # being wound down (close.py's doctrine path leaves exactly that window: positions closing,
        # runtime already gone) and the verb ADOPTS it as live, so a `runtime <id>` steer here
        # reinstalls a runtime on a strategy someone is tearing down. `CREATE_WALLET`/`FUND_WALLET`/
        # `INITIALIZE_POSITIONS` is a deploy still in flight. Neither is verified-live either: a
        # registered, healthy runtime on a closing wallet is not "live and healthy".
        row["issue"] = (f"the backend reads this strategy as {str(row['status'])!r}, not ACTIVE — it is "
                        f"not trading, so it cannot be confirmed live from here"
                        + (f" (a deploy job for {pkg.id} is RUNNING on this agent right now)"
                           if job_running else ""))
        row["next"] = watch if job_running else triage
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
        if job_running:
            row["issue"] = (f"the wallet {str(wallet)[:10]}… is live ({_funded_text(row['funded'])}) "
                            f"and no runtime is registered for it YET — a deploy job for {pkg.id} is "
                            f"RUNNING on this agent right now, which may be installing it")
            row["next"] = watch
            return row
        row["issue"] = (f"the wallet {str(wallet)[:10]}… is live ({_funded_text(row['funded'])}) but NO "
                        f"runtime is registered for it — funded and not trading")
        row["next"] = (f"python3 {Path(__file__).name} runtime {pkg.id}   # RUNS THE DEPLOY VERB: it "
                       f"installs the runtime and STARTS TRADING this funded wallet")
        if row["funded"] is None:
            # The amount at stake is the first thing a reader needs before running a money command.
            row["next"] += (f"\n        Read what is actually on that wallet FIRST: python3 "
                            f"{Path(__file__).with_name('status.py').name} {pkg.id}")
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
    verdict = _cli.health_verdict(entry)
    if row["health"] is None:
        if verdict in ("degraded", "unhealthy"):
            # No health field, but the entry says outright that it is broken. `health_verdict`
            # downgrades on positive broken evidence wherever it finds it (it only refuses to
            # PROMOTE off a run state), and declaring the row unreadable before it ran turned a
            # `{name, status: "failed"}` runtime into "COULD NOT CHECK — retry" instead of NOT
            # VERIFIED with the box's own word for it. The state is quoted as a run state, never
            # under the word "health".
            row["issue"] = (f"runtime {row['runtime']!r} published no health, and its run state reads "
                            f"{str(_cli.run_state(entry))!r} (`openclaw senpi status --json`) — that "
                            f"is broken evidence, not a missing read")
            row["next"] = triage
            return row
        # Nothing classifiable either way — including a bare `status: "running"`, which is a run
        # state and can never promote to healthy. Unreadable, not a verdict.
        row["unreadable"] = (f"`openclaw senpi status --json` carries no health field for runtime "
                             f"{row['runtime']!r}")
        return row
    if verdict != "healthy":
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


def deploy_job_facts(pkg_id):
    """`(warn lines, is a job for THIS package running right now)` from the agent's LAST deploy job,
    or `ReadFailed` when the job state could not be read at all.

    Three classifications, and the read is only ever one of them:

      * a **valid snapshot** (`_job_snapshot` — `state.status` present, the shape the verb emits),
      * a **positive no-job answer** (`_no_deploy_job_answer` — the verb's own `[NOT_FOUND]`, or a
        runtime with no `deploy` verb at all, which therefore has no verb job either),
      * anything else: the read did not happen. `ReadFailed` — never "no job".

    That last one is fail-closed on purpose. Every money steer this check emits is conditioned on the
    `running` bit, so a `deploy status` that simply did not answer must not let verify name
    `create`/`runtime` while a job may be funding the wallet at that moment. And the middle one is
    just as load-bearing in the other direction: mapping a no-verb box's unknown-command answer into
    could-not-check rendered a live, healthy package as uncheckable on every run.

    A package deployed before the verb legitimately has no job, so a no-job answer stays silent.
    There is one job record per agent and it is not package-addressed, so another package's job is
    skipped entirely: relaying polar's shortfall under a `verify spider` prompt is an invitation to
    bind the wrong package's numbers, and polar's running job says nothing about what spider's next
    step should be. A snapshot that names NO package cannot be sorted that way — so it is only safe
    while nothing is running; a RUNNING job whose package cannot be read leaves this check's whole
    question open, and says so."""
    raw, tail = read_status(None)
    snap = _job_snapshot(raw)
    if snap is None:
        # The CLI's own words decide, wherever they landed: `read_status` returns stderr even when
        # stdout parsed, because one stray JSON log line on stdout must not bury a `[NOT_FOUND]` (or
        # a parser rejection) on stderr — that buried it into a permanent could-not-check.
        if _no_deploy_job_answer(tail):
            return [], False
        # Only for the human/ReadFailed text — never fed back to the classifier above.
        why = tail or (f"the answer carried no deploy-job state: {json.dumps(raw)[:200]}"
                       if raw is not None else "")
        raise ReadFailed(f"`openclaw senpi deploy status` could not be read, so whether a deploy job "
                         f"is running right now is unknown — and every next step here depends on it: "
                         f"{why or 'no snapshot and no error text came back'}")
    running = ((snap.get("state") or {}).get("status") == "running")
    job_pkg = str(((snap.get("meta") or {}).get("packageId") or ""))
    if not job_pkg:
        if running:
            raise ReadFailed(f"a deploy job is RUNNING on this agent but its snapshot names no "
                             f"package, so whether it is {pkg_id}'s job cannot be told from here — "
                             f"and every next step here depends on that: openclaw senpi deploy status")
        return [], False
    if job_pkg != str(pkg_id):
        return [], False
    return _warn_lines(snap), running


def cmd_verify(pkg, a):
    """Is `<id>` live? A READ-ONLY composite — it starts no deploy, moves no money, and installs
    nothing. Exit: 0 verified · 3 not verified · 1 could not check."""
    if not pkg.instances:
        return _verify_unreadable(pkg.id, [f"{pkg.id} declares no instances, so there is nothing to "
                                           f"check against"], a.json)
    try:
        strategies, runtimes, hmap = verify_reads(pkg)
        # Read the job BEFORE the rows: whether a deploy is running right now decides which next step
        # each row is allowed to name — so an unreadable job read is could-not-check like any other.
        warns, job_running = deploy_job_facts(pkg.id)
    except ReadFailed as e:
        return _verify_unreadable(pkg.id, [str(e)], a.json)
    rows = [verify_instance(pkg, inst, strategies, runtimes, hmap, job_running)
            for inst in pkg.instances]
    unreadable = [r["unreadable"] for r in rows if r["unreadable"]]
    if unreadable:
        return _verify_unreadable(pkg.id, unreadable, a.json, job_running=job_running)
    verdict = "verified" if (rows and all(r["ok"] for r in rows)) else "not-verified"
    if a.json:
        # `next` is null here on purpose: a rendered verdict's next steps are per-instance (each
        # row's own `next`), so there is no package-level one to state. The key exists on all three
        # verdicts so a caller can read it unconditionally — see `_verify_unreadable`.
        print(json.dumps({"verdict": verdict, "id": pkg.id, "instances": rows,
                          "warnings": warns, "deploy_job_running": job_running,
                          "unreadable": [], "next": None}, indent=2))
        return VERIFY_OK if verdict == "verified" else VERIFY_NOT
    if verdict == "verified":
        print(f"✓ {pkg.id}: VERIFIED — {len(rows)} instance(s) live and healthy. Read-only check: "
              f"nothing was deployed, funded or installed.")
        for r in rows:
            print(f"    - {r['instance']}: {r['runtime']} {r['health']}  {str(r['wallet'])[:10]}…  "
                  f"funded {r['funded'] or 'UNKNOWN'}  [{r['status']}]")
    else:
        bad = [r for r in rows if not r["ok"]]
        print(f"✗ {pkg.id}: NOT VERIFIED — {len(bad)} of {len(rows)} instance(s) are not confirmed "
              f"live. This check read only; nothing was changed.", file=sys.stderr)
        for r in rows:
            if r["ok"]:
                print(f"    - {r['instance']}: OK — {r['runtime']} {r['health']}, funded "
                      f"{r['funded'] or 'UNKNOWN'}", file=sys.stderr)
                continue
            print(f"    - {r['instance']}: {r['issue']}\n        Next: {r['next']}", file=sys.stderr)
    if job_running:
        # A fact about the box, not a verdict: what this check read is a snapshot of a package that is
        # being changed as it reads.
        print(f"note: a deploy job for {pkg.id} is RUNNING on this agent right now, so what is above is "
              f"a mid-flight picture. Its own report: openclaw senpi deploy status", file=sys.stderr)
    for w in warns:
        # The last deploy job's own words about THIS package — relayed, never restated.
        print(f"warn (from the last deploy job): {w}", file=sys.stderr)
    return VERIFY_OK if verdict == "verified" else VERIFY_NOT


def _verify_unreadable(pkg_id, reasons, as_json, tail=None, job_running=None):
    """A read failed, so there IS no verdict. Say only that — a "couldn't check" that renders as
    verified is a lie, and one that renders as not-verified steers at the money path over a package
    that may be perfectly live. Takes the ID, not the package: the package itself is one of the
    things that may not have been readable, and `tail` is how that case names its own next step
    (retrying a package that is not on disk answers nothing).

    `job_running` keeps all three verdict shapes ONE schema: `None` when the job state was never
    read (most could-not-check paths fail before it), the read value when it was. A key that exists
    only on exit 0/3 makes a caller's `payload["deploy_job_running"]` a KeyError exactly on the
    answer that says "unknown".

    `tail` rides the JSON document as `next` for the same reason. The taught exit-1 doctrine is
    "re-read", and these two paths are the ONE could-not-check subclass where re-reading can never
    succeed — a package that does not parse, a package that is not here. Printed only on the human
    path, that steer was silently dropped for `--json` callers, who then loop re-running the check
    against a file that will never parse. It goes in the document rather than beside it: stdout
    carries the snapshot and nothing else (93ade5c7). ONE producer for that step, so the two callers
    cannot render different next steps for the same failed read."""
    step = tail or (f"  Nothing was changed. Retry, or triage read-only: python3 "
                    f"{Path(__file__).with_name('status.py').name} {pkg_id}")
    if as_json:
        print(json.dumps({"verdict": "could-not-check", "id": pkg_id, "instances": [],
                          "warnings": [], "deploy_job_running": job_running,
                          "unreadable": reasons, "next": step.strip()}, indent=2))
        return VERIFY_UNREADABLE
    print(f"? {pkg_id}: COULD NOT CHECK — a read this check needs failed, so NOTHING about {pkg_id} "
          f"is verified here (this says nothing about whether it is live):", file=sys.stderr)
    for r in reasons:
        print(f"    - {r}", file=sys.stderr)
    print(step, file=sys.stderr)
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

    # `verify` is the READ-ONLY check. NONE of the deploy flags does anything here — a check that
    # honours `--budget` is a check that can fund a wallet, which is exactly the trap this command is
    # not, and `--max-wait`/`--tick-wait`/`--dry-run` are meaningless with no job to run or plan. They
    # are still ACCEPTED, and ignored with a warning: rejecting them exits 2, which the taught D-12
    # map reads as "refused" (see OBSOLETE_VERIFY_FLAGS). Hidden from `--help` — the help text is the
    # CURRENT contract, and an accepted-but-dead flag advertised there would read as a live one.
    pv = sub.add_parser("verify",
                        help="READ-ONLY check: is <id> live? (strategy + runtime + health; deploys nothing)")
    common(pv)
    for flag in OBSOLETE_VERIFY_FLAGS:
        kw = ({"action": "store_true"} if flag == "--dry-run"
              # Untyped and unvalidated: the value is consumed only so it can never be read as the
              # package positional, and then dropped. `--budget nonsense` is ignored like any other.
              else {"default": None, "metavar": "IGNORED"})
        pv.add_argument(flag, dest=_obsolete_dest(flag), help=argparse.SUPPRESS, **kw)

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

    if getattr(a, "dry_run", False) and a.json:
        # `--dry-run` prints the PLANNED command as prose; there is no JSON rendering of a plan. The
        # two flags together used to print the prose line and no document at all, so a caller parsing
        # stdout got nothing — and the only fix that keeps the promise is refusing, not inventing a
        # shape. (A structured plan is the verb's `deploy --plan --json` work, not this wrapper's.)
        print(f"error: `--dry-run --json` is not a combination this command can answer — --dry-run "
              f"renders the planned `openclaw senpi deploy` line as PROSE, and there is no JSON "
              f"rendering of a plan. NOTHING WAS STARTED, planned or resolved here.\n"
              f"  The plan (read-only, nothing runs):  python3 {Path(__file__).name} {a.cmd} "
              f"{a.package} … --dry-run\n"
              f"  A JSON report needs a REAL run:      drop --dry-run — that RUNS THE DEPLOY VERB "
              f"(creates + funds wallets, installs, starts trading) and its report is what --json "
              f"renders.", file=sys.stderr)
        sys.exit(EXIT_INTERNAL)

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

    # `verify` reads the package only for its instance list — it deploys nothing, so the pre-deploy
    # structural gate (whose whole job is to refuse BEFORE money moves) has nothing to protect here,
    # and refusing a read-only check over a render error would just hide the live state being asked
    # about. It also resolves LOCALLY: `ensure_pkg` would fetch a bare catalog id from the remote and
    # WRITE it under the durable strategies root — a network+disk side effect from a command that
    # promises nothing was changed.
    if a.cmd == "verify":
        warn_obsolete_verify_flags(a)
        if a.ref:
            print(f"note: `verify` resolves the package on disk and fetches nothing, so --ref selected "
                  f"nothing.", file=sys.stderr)
        try:
            pkg = local_pkg(a.package)
        except _pkg.BadPackage as e:
            # The package is HERE but unmodellable (bad YAML, no id, no instances). That is an
            # unreadable surface like any other — and the one surface whose failure used to escape
            # as a raw traceback with an EMPTY stdout, so a `--json` caller parsed nothing at all.
            sid = Path(a.package).name
            sys.exit(_verify_unreadable(sid, [
                f"{_pkg.resolve_pkg_dir(a.package) / 'strategy.yaml'} could not be read as a "
                f"package, so this check has no instance list to check against: {e}"], a.json,
                tail=(f"  Nothing was read or changed, and this says nothing about whether {sid} is "
                      f"live. Retrying answers nothing — the file has to parse:\n"
                      f"    Fix the strategy.yaml named above, then re-run this check.\n"
                      f"    python3 {Path(__file__).with_name('status.py').name}   # read-only: "
                      f"everything actually live on this agent, whatever it is named")))
        if pkg is None:
            sid = Path(a.package).name
            status_py = Path(__file__).with_name('status.py').name
            sys.exit(_verify_unreadable(sid, [
                f"{a.package!r} is not on disk here (tried {a.package!r}, "
                f"{_pkg.strategies_root() / sid}, and 'strategies/{sid}' relative to the current "
                f"directory), so this check has no instance list to check against — and it does not "
                f"fetch: a read-only check must not download and write a package"], a.json,
                tail=(f"  Nothing was read, downloaded or changed. Retrying answers nothing — the "
                      f"package has to be here:\n"
                      f"    python3 {status_py}   # read-only: everything actually live on this "
                      f"agent, whatever it is named\n"
                      f"    python3 {Path(__file__).name} verify <dir>   # if you have the package "
                      f"locally, pass its DIRECTORY\n"
                      f"  (`create`/`runtime` DO fetch a bare catalog id — that is the money path, "
                      f"not this check.)")))
        sys.exit(cmd_verify(pkg, a))

    pkg = ensure_pkg(a.package, a.ref, log)

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
