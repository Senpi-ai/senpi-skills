#!/usr/bin/env python3
"""Build a confirmed strategy spec into a validated package with Claude Code, on this host.

The OpenClaw agent owns the conversation: the template offer, the 7-decision interview and the
spec replay the user says "yes" to. This script is the handoff after that yes. It runs a headless
Claude Code session (`claude -p`) that writes the package, lints it and runs the
`openclaw senpi validate` gate until it passes. It never deploys: going live stays with
senpi-strategy-ops.

One exec = one turn. Each turn ends in exactly one state, printed as the LAST stdout line:

    AUTHOR_BUILD {"job": ..., "state": "done" | "needs_input" | "failed" | "error", ...}

OpenClaw's exec backgrounds any command after at most 120s, and senpi-agent runs with heartbeats
off, so the exit notice cannot be relied on to wake the agent. The skill therefore runs
`start --detach` (returns the job id at once) and loops `wait` (returns within ~100s: the end state,
or `running` plus recent steps to narrate). The line still stays under 400 chars, OpenClaw's
exit-notify tail, for hosts where that notice does arrive.

  start   --spec <spec.json> [--edit <pkg-dir>] [--detach]  first turn of a new job
  answer  --job <id> (--text <s> | --file <path>) [--detach] resume a job that needs input
  status  --job <id> [--full]                                read-only; progress + result
  wait    --job <id> [--timeout <s>]                          block until the turn ends (read-only)
  check   <pkg-dir>                                           lints + the gate (Claude Code runs this)
  verify-proof <pkg-dir>                                      is .senpi-proof.json valid for these bytes?
  promote --job <id>                                          edit jobs: copy the staged package over
                                                              the original (the user's yes first)
  list                                                        recent jobs
  doctor                                                      can this host run the engine? (exit 0 = yes)

Exit codes (start / answer / wait): 0 done · 10 needs_input · 1 failed · 2 refused (bad
invocation, nothing ran) · 3 error (the harness broke, not the strategy) · 4 still running.

The wrapper, not the model, decides `done`: a turn Claude Code reports as done is accepted only if
the package's `.senpi-proof.json` matches its current bytes. Otherwise the session is resumed with
the reason, at most MAX_NUDGES times, then the job is marked failed.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import yaml  # prefer PyYAML when present
except ImportError:  # agent hosts may lack PyYAML AND pip — same vendored fallback validate_strategy.py uses
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _yaml as yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
SKILLS_ROOT = SKILL_DIR.parent
ENGINE_DIR = SKILL_DIR / "engine"
OPS_SCRIPTS = SKILLS_ROOT / "senpi-strategy-ops" / "scripts"
RUNTIME_SKILL = SKILLS_ROOT / "senpi-trading-runtime"

EXIT = {"done": 0, "needs_input": 10, "failed": 1, "refused": 2, "error": 3, "running": 4}
TERMINAL = {"done", "failed", "error"}
MAX_NUDGES = 2
NOTIFY_LINE_MAX = 390  # OpenClaw's exit-notify tail is 400 chars
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
DECISIONS = ("universe", "data", "edge", "shape", "cardinality", "memory", "exit_risk")

# Proof fingerprinting — must match runtime src/validate/proof.ts byte for byte.
PROOF_FILE = ".senpi-proof.json"
_PROOF_EXCLUDED = {PROOF_FILE, ".DS_Store"}
_PROOF_EXCLUDED_DIRS = {"__pycache__", ".git"}
_PROOF_EXCLUDED_SUFFIXES = (".pyc",)


# ---------------------------------------------------------------- paths & env

def workspace_dir():
    return Path(os.environ.get("OPENCLAW_WORKSPACE_DIR") or "/data/workspace")


def strategies_dir():
    # Same resolution as senpi-strategy-ops/scripts/_pkg.py — the durable root, never a skill dir.
    return Path(os.environ.get("SENPI_STRATEGIES_DIR") or (workspace_dir() / "strategies"))


def jobs_dir():
    return Path(os.environ.get("SENPI_AUTHOR_JOBS_DIR") or (workspace_dir() / ".author-jobs"))


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------- job state

class Job:
    def __init__(self, job_id):
        self.id = job_id
        self.dir = jobs_dir() / job_id

    @property
    def status_path(self):
        return self.dir / "status.json"

    @property
    def result_path(self):
        return self.dir / "result.json"

    @property
    def events_path(self):
        return self.dir / "events.jsonl"

    def exists(self):
        return self.status_path.is_file()

    def read(self):
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}

    def write(self, **fields):
        st = self.read()
        st.update(fields, updated_at=now_iso())
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2) + "\n")
        tmp.replace(self.status_path)
        return st

    def result(self):
        try:
            return json.loads(self.result_path.read_text())
        except (OSError, ValueError):
            return None

    def write_result(self, result):
        tmp = self.result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=2) + "\n")
        tmp.replace(self.result_path)


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def notify_line(job, st, result=None):
    """The one line OpenClaw reads back. Compact by construction; the full answer is `status`."""
    out = {"job": job.id, "state": st.get("state"), "id": st.get("strategy_id")}
    r = result or {}
    if st.get("state") == "needs_input":
        out["question"] = (r.get("question") or "")[:200]
    elif st.get("state") == "done":
        out["verdict"] = (r.get("validate") or {}).get("verdict")
        out["proof"] = (r.get("proof") or {}).get("ok")
    elif st.get("message"):
        out["why"] = str(st["message"])[:160]
    out["next"] = f"author_build.py status --job {job.id}"
    line = "AUTHOR_BUILD " + json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    while len(line) > NOTIFY_LINE_MAX and out.get("question"):
        out["question"] = out["question"][:-20] + "…"
        line = "AUTHOR_BUILD " + json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    return line


# ---------------------------------------------------------------- proof verification

def _walk(root, package_root, into):
    try:
        names = sorted(os.listdir(root))  # JS sorts by UTF-16 unit; identical for BMP names
    except OSError:
        return
    for name in names:
        if name in _PROOF_EXCLUDED or name.endswith(_PROOF_EXCLUDED_SUFFIXES):
            continue
        full = root / name
        if full.is_dir():
            if name not in _PROOF_EXCLUDED_DIRS:
                _walk(full, package_root, into)
            continue
        try:
            into[os.path.relpath(full, package_root)] = hashlib.sha256(full.read_bytes()).hexdigest()
        except OSError:
            pass  # absent from the map → reads as changed, never as proven


def fingerprint(recipe_path, scanner_roots):
    package_root = recipe_path.parent
    files = {}
    try:
        files[os.path.relpath(recipe_path, package_root)] = hashlib.sha256(recipe_path.read_bytes()).hexdigest()
    except OSError:
        pass
    for root in scanner_roots:
        _walk((package_root / root).resolve(), package_root, files)
    ordered = sorted(files.items())
    # JSON.stringify of [[k, v], ...]: no spaces, non-ASCII left as-is.
    fp = hashlib.sha256(json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return fp, dict(ordered)


def installed_runtime_version():
    """The runtime build on this box, so a proof recorded under another build is caught here."""
    state = Path(os.environ.get("OPENCLAW_STATE_DIR") or "/data/.openclaw")
    candidates = []
    if os.environ.get("SENPI_RUNTIME_DIR"):
        candidates.append(Path(os.environ["SENPI_RUNTIME_DIR"]) / "package.json")
    for base in (state, Path("/data/.openclaw")):
        candidates += [base / "npm/node_modules/@senpi-ai/runtime/package.json",
                       base / "npm/node_modules/@senpi/runtime/package.json",
                       base / "extensions/runtime/package.json"]
    for p in candidates:
        try:
            return json.loads(p.read_text()).get("version")
        except (OSError, ValueError):
            continue
    return None


def recipe_dirs(pkg_dir):
    """The dirs that each hold one runtime.yaml: the root (flat) or one per declared instance."""
    try:
        manifest = yaml.safe_load((pkg_dir / "strategy.yaml").read_text()) or {}
    except (OSError, Exception):
        manifest = {}
    instances = manifest.get("instances") if isinstance(manifest, dict) else None
    if instances:
        return [(i.get("name") or "?", (pkg_dir / i.get("runtime", "")).parent) for i in instances
                if isinstance(i, dict)]
    return [("main", pkg_dir)]


def verify_proof(pkg_dir):
    """{ok, reason, instances[]} — pure observation, like runtime verifyProof."""
    pkg_dir = Path(pkg_dir).resolve()
    rt_version = installed_runtime_version()
    report = {"ok": True, "reason": None, "runtime_version": rt_version, "instances": []}
    for name, d in recipe_dirs(pkg_dir):
        entry = {"instance": name, "dir": str(d)}
        try:
            rec = json.loads((d / PROOF_FILE).read_text())
        except (OSError, ValueError):
            entry.update(ok=False, reason="no_proof")
        else:
            fp, files = fingerprint(d / rec.get("recipe", "runtime.yaml"), rec.get("scanner_roots") or [])
            if rec.get("verdict") != "PASS":
                entry.update(ok=False, reason="not_pass")
            elif fp != rec.get("fingerprint"):
                recorded = rec.get("files") or {}
                changed = sorted({f for f, h in recorded.items() if files.get(f) != h} |
                                 {f for f in files if f not in recorded})
                entry.update(ok=False, reason="content_changed", changed_files=changed)
            elif rt_version and rec.get("runtime_version") != rt_version:
                entry.update(ok=False, reason="runtime_version_changed",
                             proved_under=rec.get("runtime_version"))
            else:
                entry.update(ok=True, reason=None, proved_under=rec.get("runtime_version"),
                             observations=rec.get("observations"))
        report["instances"].append(entry)
        if not entry["ok"] and report["ok"]:
            report.update(ok=False, reason=entry["reason"])
    return report


# ---------------------------------------------------------------- check (lint + gate)

def validate_cmd():
    return shlex.split(os.environ.get("SENPI_VALIDATE_CMD") or "openclaw senpi validate")


def _run_step(label, argv, cwd=None):
    print(f"\n=== {label}: {' '.join(shlex.quote(a) for a in argv)}", flush=True)
    try:
        rc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL).returncode
    except FileNotFoundError as e:
        print(f"✗ could not run {argv[0]}: {e}", flush=True)
        rc = 127
    print(f"=== {label}: exit {rc}", flush=True)
    return rc


def cmd_check(a):
    pkg = Path(a.package).resolve()
    if not (pkg / "strategy.yaml").is_file():
        print(f"✗ {pkg} has no strategy.yaml — pass the package root.", file=sys.stderr)
        return EXIT["refused"]
    py = sys.executable or "python3"
    results = {
        "lint": _run_step("lint (validate_strategy.py)", [py, str(SCRIPTS_DIR / "validate_strategy.py"), str(pkg)]),
        "universe": _run_step("universe (validate_universe.py)", [py, str(OPS_SCRIPTS / "validate_universe.py"), str(pkg)]),
        "deploy_contract": _run_step("deploy contract (deploy.py validate)", [py, str(OPS_SCRIPTS / "deploy.py"), "validate", str(pkg)]),
    }
    gate = []
    for name, d in recipe_dirs(pkg):
        gate.append(_run_step(f"GATE ({name})", validate_cmd() + [str(d)]))
    worst_gate = 1 if 1 in gate else (max(gate) if gate else 1)
    print("\n=== summary: " + ", ".join(f"{k}={v}" for k, v in results.items()) +
          f", gate={gate} (0 PASS · 2 UNPROVEN · 1 FAIL)", flush=True)
    if worst_gate != 0:
        return worst_gate
    return 0 if all(v == 0 for v in results.values()) else 5


def cmd_verify_proof(a):
    rep = verify_proof(a.package)
    print(json.dumps(rep, indent=2))
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------- the Claude Code turn

READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
BUILTIN_TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
MUTATING_SENPI_TOOLS = (  # runtime src/scanners/external/scaffold/senpi_mcp.py MUTATION_TOOLS
    "create_position", "close_position", "edit_position", "cancel_order", "send_usdc",
    "transfer_spot_to_perps", "strategy_create", "strategy_create_custom_strategy", "strategy_close",
    "strategy_close_positions", "strategy_update", "strategy_pause", "strategy_top_up",
    "strategy_withdraw_funds", "strategy_bridge_funds_from_hyperliquid_to_evm", "ratchet_stop_add",
    "ratchet_stop_edit", "ratchet_stop_delete", "user_claim_referral_rewards",
)


def claude_bin():
    return os.environ.get("SENPI_AUTHOR_CLAUDE_BIN") or shutil.which("claude") or "claude"


def write_session_files(job, pkg_dir):
    """Per-job settings + MCP config. Hook commands carry absolute paths, so no CWD assumption."""
    py = shlex.quote(sys.executable or "python3")
    hooks = ENGINE_DIR / "hooks"
    settings = {
        "permissions": {
            "allow": ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "StructuredOutput", "mcp__senpi"],
            "deny": [f"mcp__senpi__{t}" for t in MUTATING_SENPI_TOOLS] + ["WebFetch", "WebSearch"],
        },
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit|mcp__.*",
                "hooks": [{"type": "command", "command": f"{py} {shlex.quote(str(hooks / 'guard.py'))}", "timeout": 20}],
            }],
            "PostToolUse": [{
                "matcher": "Write|Edit|MultiEdit",
                "hooks": [{"type": "command", "command": f"{py} {shlex.quote(str(hooks / 'lint_on_edit.py'))}", "timeout": 60}],
            }],
        },
    }
    (job.dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    # ${VAR} is expanded by Claude Code at load time, so the token never lands on disk here.
    mcp = {"mcpServers": {"senpi": {
        "type": "http",
        "url": "${SENPI_MCP_URL}",
        "headers": {"Authorization": "Bearer ${SENPI_AUTH_TOKEN}"},
    }}}
    (job.dir / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n")
    rules = (ENGINE_DIR / "build-rules.md").read_text()
    for key, val in {"SKILL_DIR": SKILL_DIR, "SKILLS_ROOT": SKILLS_ROOT, "STRATEGIES_DIR": strategies_dir()}.items():
        rules = rules.replace("{{" + key + "}}", str(val))
    (job.dir / "build-rules.md").write_text(rules)
    (job.dir / "scratch").mkdir(exist_ok=True)


def build_claude_argv(job, st, prompt, first):
    argv = [
        claude_bin(), "-p", prompt,
        "--session-id" if first else "--resume", st["session_id"],
        "--output-format", "stream-json", "--verbose",
        "--json-schema", (ENGINE_DIR / "result.schema.json").read_text(),
        "--settings", str(job.dir / "settings.json"),
        "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", str(job.dir / "mcp.json"),
        "--tools", BUILTIN_TOOLS,
        "--permission-mode", "dontAsk",
        "--append-system-prompt-file", str(job.dir / "build-rules.md"),
        "--add-dir", str(SKILLS_ROOT), str(strategies_dir()),
        "--max-budget-usd", str(env_float("SENPI_AUTHOR_MAX_BUDGET_USD", 10.0)),
    ]
    argv += ["--model", model_route()[2]]
    return argv


DEFAULT_MODEL = "samurai-pro"  # senpi-agent's SENPI_PRIMARY_MODEL; an alias on models.senpi.ai


def model_route():
    """(base_url, token, model) for the claude child.

    Default: the box's own model gateway — the same AI_API_KEY + SENPI_BASE_URL (models.senpi.ai)
    the OpenClaw agent already uses. LiteLLM serves the Anthropic Messages API at /v1/messages, so
    the base is SENPI_BASE_URL minus its OpenAI-style `/v1`. SENPI_AUTHOR_ANTHROPIC_* override it,
    e.g. to point at Anthropic directly. ANTHROPIC_API_KEY is deliberately NOT set box-wide: the
    runtime's llm decision_mode switches provider when it sees that variable.
    """
    base = os.environ.get("SENPI_AUTHOR_ANTHROPIC_BASE_URL")
    if not base:
        base = re.sub(r"/v1/?$", "", (os.environ.get("SENPI_BASE_URL") or "https://models.senpi.ai/v1").strip())
    # Same precedence as senpi-agent's resolveEffectiveApiKey: AI_API_KEY, then SENPI_API_KEY.
    token = (os.environ.get("SENPI_AUTHOR_ANTHROPIC_API_KEY") or os.environ.get("AI_API_KEY")
             or os.environ.get("SENPI_API_KEY") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    # Same model the OpenClaw agent runs on unless overridden: SENPI_MODEL, then samurai-pro.
    model = os.environ.get("SENPI_AUTHOR_MODEL") or os.environ.get("SENPI_MODEL") or DEFAULT_MODEL
    return base, token, model


def claude_env(job, st):
    env = dict(os.environ)
    base, token, model = model_route()
    env.pop("ANTHROPIC_API_KEY", None)  # one credential, sent as a Bearer token
    env["ANTHROPIC_BASE_URL"] = base
    if token:
        env["ANTHROPIC_AUTH_TOKEN"] = token
    # Every model Claude Code might pick on its own (background, subagent) resolves to the alias the
    # proxy actually serves — an unknown model name is a failed request, not a fallback.
    for var in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        env[var] = model
    env.setdefault("SENPI_MCP_URL", "https://mcp.prod.senpi.ai/mcp")
    env.update({
        "SENPI_AUTHOR_JOB_DIR": str(job.dir),
        "SENPI_AUTHOR_PKG_DIR": st["package_dir"],
        "SENPI_AUTHOR_SKILL_DIR": str(SKILL_DIR),
        "SENPI_AUTHOR_BUILD": str(Path(__file__).resolve()),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    })
    return env


def run_claude(job, st, prompt, first):
    """One `claude -p` invocation. Returns the final `result` event (or None) and a failure note."""
    argv = build_claude_argv(job, st, prompt, first)
    timeout = env_int("SENPI_AUTHOR_TURN_TIMEOUT_S", 1500)  # under OpenClaw exec's 1800s default
    with open(job.events_path, "a") as out, open(job.dir / "claude.stderr.log", "a") as err:
        out.write(json.dumps({"type": "wrapper", "event": "turn_start", "at": now_iso(), "first": first}) + "\n")
        out.flush()
        try:
            proc = subprocess.Popen(argv, cwd=st["package_dir"], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, env=claude_env(job, st), start_new_session=True)
        except FileNotFoundError:
            return None, f"Claude Code is not installed on this host ({argv[0]} not found)"
        job.write(claude_pid=proc.pid)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
            return None, f"turn exceeded {timeout}s and was stopped"
    final = None
    for ev in read_events(job, since_last_turn_start=True):
        if ev.get("type") == "result":
            final = ev
    if final is None:
        tail = (job.dir / "claude.stderr.log").read_text()[-300:].strip()
        return None, f"claude exited {proc.returncode} without a result" + (f": {tail}" if tail else "")
    return final, None


def read_events(job, since_last_turn_start=False):
    try:
        lines = job.events_path.read_text().splitlines()
    except OSError:
        return []
    evs = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if since_last_turn_start and ev.get("type") == "wrapper" and ev.get("event") == "turn_start":
            evs = []
            continue
        evs.append(ev)
    return evs


def progress(job, n=8):
    """Recent tool activity, for a user-facing 'what is it doing' line."""
    steps = []
    for ev in read_events(job, since_last_turn_start=True):
        if ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") != "tool_use" or c.get("name") == "StructuredOutput":
                continue
            inp = c.get("input") or {}
            what = inp.get("file_path") or inp.get("command") or inp.get("pattern") or ""
            if c.get("name") in ("Write", "Edit", "Read") and what:
                what = Path(what).name
            steps.append(f"{c.get('name')}: {str(what)[:90]}")
    return steps[-n:]


def run_turn(job, prompt, first):
    st = job.write(state="running", pid=os.getpid(), turn=job.read().get("turn", 0) + 1, message=None)
    nudges = 0
    cost = float(st.get("cost_usd") or 0)
    while True:
        final, failure = run_claude(job, st, prompt, first)
        first = False
        if failure:
            st = job.write(state="error", message=failure, pid=None, claude_pid=None)
            return st, None
        cost += float(final.get("total_cost_usd") or 0)
        out = final.get("structured_output")
        if final.get("is_error") or not isinstance(out, dict):
            st = job.write(state="error", pid=None, claude_pid=None, cost_usd=round(cost, 4),
                           message=f"no structured result ({final.get('subtype')}): {str(final.get('result'))[:200]}")
            return st, None
        status = out.get("status")
        if status == "done":
            proof = verify_proof(st["package_dir"])
            if not proof["ok"] and nudges < MAX_NUDGES:
                nudges += 1
                prompt = ("The wrapper re-checked .senpi-proof.json and it does NOT prove this package: "
                          f"{json.dumps(proof)[:1200]}\n"
                          f"Run `python3 {Path(__file__).resolve()} check {st['package_dir']}` until the gate "
                          "passes, then return done. If it cannot pass, return failed with the blocking finding.")
                continue
            out["proof"] = proof
            if not proof["ok"]:
                status = "failed"
                out.setdefault("blocking_finding", f"reported done, but the proof is not valid: {proof['reason']}")
        elif status not in ("needs_input", "failed"):
            status = "failed"
            out.setdefault("blocking_finding", f"unknown status {out.get('status')!r}")
        result = dict(out, status=status, job=job.id, session_id=st["session_id"],
                      package_dir=st["package_dir"], strategy_id=st.get("strategy_id"),
                      mode=st.get("mode"), cost_usd=round(cost, 4), num_turns=final.get("num_turns"),
                      permission_denials=final.get("permission_denials") or [], at=now_iso())
        if st.get("mode") == "edit":
            result["staged_dir"] = st["package_dir"]
            result["original_dir"] = st.get("original_dir")
        job.write_result(result)
        st = job.write(state=status, pid=None, claude_pid=None, cost_usd=round(cost, 4),
                       message=out.get("blocking_finding") if status == "failed" else None)
        return st, result


# ---------------------------------------------------------------- spec & prompts

def load_spec(path):
    try:
        spec = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return None, f"cannot read spec {path}: {e}"
    if not isinstance(spec, dict):
        return None, "spec must be a JSON object"
    problems = []
    if not ID_RE.match(str(spec.get("id", ""))):
        problems.append("`id` must be a lowercase slug (a-z, 0-9, '-'), 2-40 chars, starting with a letter")
    for k in ("name", "thesis"):
        if not str(spec.get(k, "")).strip():
            problems.append(f"`{k}` is required")
    dec = spec.get("decisions")
    if not isinstance(dec, dict):
        problems.append("`decisions` must be an object with the 7 decisions")
    else:
        missing = [d for d in DECISIONS if not str(dec.get(d, "")).strip()]
        if missing:
            problems.append("decisions missing: " + ", ".join(missing))
    if spec.get("user_confirmed") is not True:
        problems.append("`user_confirmed` must be true — replay the full spec to the user and get an explicit yes first")
    return (spec, None) if not problems else (None, "; ".join(problems))


def first_prompt(spec, st):
    mode = st.get("mode")
    lines = [
        f"JOB {st['job']} — {'EDIT an existing package' if mode == 'edit' else 'BUILD a new package'}.",
        f"Package directory (your working directory; the ONLY place you may write): {st['package_dir']}",
        f"Strategy id: {spec['id']}   (runtime.yaml name: {spec['id']}-main, group: {spec['id']})",
        f"Check command (lint + gate, run it after every change): python3 {Path(__file__).resolve()} check {st['package_dir']}",
        "",
        "The user CONFIRMED this spec in chat. Build exactly this; do not re-ask anything it answers:",
        "```json", json.dumps(spec, indent=2), "```",
    ]
    if mode == "edit":
        lines += ["", f"This is a staged copy of {st.get('original_dir')}. Change only what the spec asks."]
    return "\n".join(lines)


# ---------------------------------------------------------------- commands

def _emit(job, st, result=None):
    print(notify_line(job, st, result), flush=True)
    return EXIT.get(st.get("state"), EXIT["error"])


def _detach(job, argv_tail):
    log = open(job.dir / "runner.log", "a")
    proc = subprocess.Popen([sys.executable or "python3", str(Path(__file__).resolve())] + argv_tail,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    st = job.write(state="running", pid=proc.pid)
    print(notify_line(job, st), flush=True)
    return EXIT["running"]


def cmd_start(a):
    spec, err = load_spec(a.spec)
    if err:
        print(f"✗ refused: {err}", file=sys.stderr)
        return EXIT["refused"]
    job = Job(f"{spec['id']}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}")
    job.dir.mkdir(parents=True, exist_ok=True)
    (job.dir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    if a.edit:
        original = Path(a.edit).resolve()
        if not (original / "strategy.yaml").is_file():
            print(f"✗ refused: {original} is not a package (no strategy.yaml)", file=sys.stderr)
            shutil.rmtree(job.dir, ignore_errors=True)
            return EXIT["refused"]
        pkg = job.dir / "work" / original.name
        shutil.copytree(original, pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".deploy-state.json"))
        mode, original_dir = "edit", str(original)
    else:
        pkg = strategies_dir() / spec["id"]
        if pkg.exists() and any(pkg.iterdir()):
            print(f"✗ refused: {pkg} already exists. Pick another id, or pass --edit {pkg} to change it.",
                  file=sys.stderr)
            shutil.rmtree(job.dir, ignore_errors=True)
            return EXIT["refused"]
        pkg.mkdir(parents=True, exist_ok=True)
        mode, original_dir = "new", None
    st = job.write(job=job.id, state="queued", mode=mode, strategy_id=spec["id"], package_dir=str(pkg),
                   original_dir=original_dir, session_id=str(uuid.uuid4()), turn=0, cost_usd=0,
                   created_at=now_iso())
    write_session_files(job, pkg)
    (job.dir / "prompt-1.md").write_text(first_prompt(spec, st))
    if a.detach:
        return _detach(job, ["_run", "--job", job.id, "--first"])
    return _emit(job, *run_turn(job, (job.dir / "prompt-1.md").read_text(), first=True))


def _load_job(job_id):
    job = Job(job_id)
    if not job.exists():
        print(f"✗ no job {job_id!r} under {jobs_dir()}", file=sys.stderr)
        return None
    return job


def cmd_answer(a):
    job = _load_job(a.job)
    if job is None:
        return EXIT["refused"]
    st = job.read()
    if st.get("state") == "running" and pid_alive(st.get("pid")):
        print("✗ refused: this job is still running a turn — `wait` for it first.", file=sys.stderr)
        return EXIT["refused"]
    if st.get("state") != "needs_input":
        print(f"✗ refused: job is {st.get('state')!r}, not waiting for input.", file=sys.stderr)
        return EXIT["refused"]
    text = Path(a.file).read_text() if a.file else a.text
    if not (text or "").strip():
        print("✗ refused: empty answer", file=sys.stderr)
        return EXIT["refused"]
    turn = int(st.get("turn", 1)) + 1
    prompt = f"The user answered your question:\n\n{text.strip()}\n\nContinue the build."
    (job.dir / f"prompt-{turn}.md").write_text(prompt)
    if a.detach:
        return _detach(job, ["_run", "--job", job.id, "--prompt-file", str(job.dir / f"prompt-{turn}.md")])
    return _emit(job, *run_turn(job, prompt, first=False))


def cmd_run(a):  # internal: the detached body of start/answer
    job = _load_job(a.job)
    if job is None:
        return EXIT["refused"]
    prompt = Path(a.prompt_file).read_text() if a.prompt_file else (job.dir / "prompt-1.md").read_text()
    return _emit(job, *run_turn(job, prompt, first=a.first))


def _effective(st):
    if st.get("state") == "running" and not pid_alive(st.get("pid")):
        return dict(st, state="error", message="the build process died mid-turn (host restart?) — re-run `answer` or start again")
    return st


def cmd_status(a):
    job = _load_job(a.job)
    if job is None:
        return EXIT["refused"]
    st = _effective(job.read())
    view = {k: st.get(k) for k in ("job", "state", "mode", "strategy_id", "package_dir", "original_dir",
                                    "turn", "cost_usd", "message", "updated_at")}
    if st.get("state") == "running":
        view["recent_steps"] = progress(job)
    res = job.result()
    if res and st.get("state") != "running":
        view["result"] = res if a.full else {k: res.get(k) for k in (
            "status", "summary", "question", "options", "why", "validate", "warnings",
            "exit_preview", "blocking_finding", "staged_dir", "proof")}
        if not a.full and isinstance(view["result"].get("proof"), dict):
            view["result"]["proof"] = {k: view["result"]["proof"].get(k) for k in ("ok", "reason", "runtime_version")}
    print(json.dumps(view, indent=2, ensure_ascii=False))
    return EXIT.get(st.get("state"), EXIT["error"]) if st.get("state") != "queued" else EXIT["running"]


def cmd_wait(a):
    job = _load_job(a.job)
    if job is None:
        return EXIT["refused"]
    deadline = time.time() + a.timeout
    while True:
        st = _effective(job.read())
        if st.get("state") not in ("running", "queued"):
            return _emit(job, st, job.result())
        if time.time() >= deadline:
            # Still building: say what it is doing, so the agent can narrate between waits.
            for step in progress(job, n=5):
                print(f"  · {step}", flush=True)
            return _emit(job, st)
        time.sleep(3)


def cmd_promote(a):
    job = _load_job(a.job)
    if job is None:
        return EXIT["refused"]
    st, res = job.read(), job.result() or {}
    if st.get("mode") != "edit":
        print("✗ refused: only edit jobs are promoted (a new build is already in place).", file=sys.stderr)
        return EXIT["refused"]
    if st.get("state") != "done":
        print(f"✗ refused: job is {st.get('state')!r}; only a done job is promoted.", file=sys.stderr)
        return EXIT["refused"]
    staged, original = Path(st["package_dir"]), Path(st["original_dir"])
    proof = verify_proof(staged)
    if not proof["ok"]:
        print(f"✗ refused: the staged package is not proven ({proof['reason']}). Nothing was changed.",
              file=sys.stderr)
        return EXIT["refused"]
    backup = original.with_name(f"{original.name}.bak-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d%H%M%S')}")
    shutil.copytree(original, backup)
    for child in original.iterdir():
        if child.name == ".deploy-state.json":
            continue  # the deploy record belongs to the live package, not the edit
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(staged, original, dirs_exist_ok=True)
    after = verify_proof(original)
    job.write(promoted_at=now_iso(), promoted_backup=str(backup))
    print(json.dumps({"promoted": str(original), "backup": str(backup), "proof_ok": after["ok"]}))
    return 0 if after["ok"] else EXIT["error"]


def cmd_doctor(a):
    """Can this host run the engine? Exit 0 = hand off; anything else = build inline as before."""
    base, token, model = model_route()
    checks = {
        "engine_enabled": os.environ.get("SENPI_AUTHOR_ENGINE", "claude-code") != "inline",
        "claude_cli": bool(shutil.which(claude_bin()) or Path(claude_bin()).is_file()),
        "model_credential": bool(token),
        "senpi_auth_token": bool(os.environ.get("SENPI_AUTH_TOKEN")),
        "validate_cmd": bool(shutil.which(validate_cmd()[0])),
        "strategies_dir_writable": os.access(strategies_dir() if strategies_dir().exists() else strategies_dir().parent, os.W_OK),
    }
    ok = all(checks.values())
    print(json.dumps({"ready": ok, "checks": checks, "model": model, "model_base_url": base,
                      "strategies_dir": str(strategies_dir()), "jobs_dir": str(jobs_dir())}, indent=2))
    me = Path(__file__).resolve()
    if ok:
        # Said here, in the tool output, because an agent follows what a command tells it next more
        # reliably than a section of SKILL.md — observed: an agent ran doctor, got ready, and then
        # wrote the package itself.
        print(f"\nENGINE READY. Do NOT write strategy.yaml / runtime.yaml / scanners yourself.\n"
              f"After the user's yes to the replayed spec:\n"
              f"  1. write the spec JSON to {workspace_dir() / '.author-specs'}/<id>.json (user_confirmed: true)\n"
              f"  2. python3 {me} start --detach --spec {workspace_dir() / '.author-specs'}/<id>.json\n"
              f"  3. python3 {me} wait --job <job>   (repeat while state is running; narrate the steps)\n"
              f"  4. python3 {me} status --job <job> -> relay done / ask needs_input / report failed")
    else:
        print("\nENGINE NOT READY — build inline (SKILL.md stages 2-9).")
    return 0 if ok else 1


def cmd_list(a):
    root = jobs_dir()
    rows = []
    for d in sorted(root.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:a.limit]:
        st = _effective(json.loads(d.read_text()))
        rows.append({k: st.get(k) for k in ("job", "state", "strategy_id", "mode", "updated_at")})
    print(json.dumps(rows, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="author_build.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--spec", required=True); s.add_argument("--edit")
    s.add_argument("--detach", action="store_true"); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("answer"); s.add_argument("--job", required=True)
    g = s.add_mutually_exclusive_group(required=True); g.add_argument("--text"); g.add_argument("--file")
    s.add_argument("--detach", action="store_true"); s.set_defaults(fn=cmd_answer)
    s = sub.add_parser("_run"); s.add_argument("--job", required=True); s.add_argument("--prompt-file")
    s.add_argument("--first", action="store_true"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("status"); s.add_argument("--job", required=True); s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_status)
    # 100s default: OpenClaw's exec backgrounds anything past 120s (yieldMs is clamped there).
    s = sub.add_parser("wait"); s.add_argument("--job", required=True); s.add_argument("--timeout", type=int, default=100)
    s.set_defaults(fn=cmd_wait)
    s = sub.add_parser("check"); s.add_argument("package"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("verify-proof"); s.add_argument("package"); s.set_defaults(fn=cmd_verify_proof)
    s = sub.add_parser("promote"); s.add_argument("--job", required=True); s.set_defaults(fn=cmd_promote)
    s = sub.add_parser("list"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
