#!/usr/bin/env python3
"""author_build.py — the OpenClaw -> Claude Code handoff, driven end to end by a fake `claude`.

The fake speaks the same stream-json the real CLI does (a `result` event carrying
`structured_output`), so these tests cover the wrapper's state machine, the proof check that
decides `done`, the nudge loop, and the guard hook — everything except the model.

    python3 -m pytest senpi-strategy-author/tests/test_author_build.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
GUARD = HERE.parent / "engine" / "hooks" / "guard.py"
sys.path.insert(0, str(SCRIPTS))

import author_build as ab  # noqa: E402

FAKE_CLAUDE = textwrap.dedent(r'''
    #!/usr/bin/env python3
    # Fake `claude -p`: behaviour is scripted per call by FAKE_SCRIPT (a JSON list, one entry per call).
    import json, os, sys
    sys.path.insert(0, os.environ["AB_SCRIPTS"])
    import author_build as ab
    argv = sys.argv[1:]
    counter = os.path.join(os.environ["SENPI_AUTHOR_JOB_DIR"], "fake-calls")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    with open(os.path.join(os.environ["SENPI_AUTHOR_JOB_DIR"], f"fake-argv-{n}.json"), "w") as f:
        json.dump({"argv": argv, "env": {k: os.environ.get(k) for k in (
            "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")}}, f)
    step = json.loads(os.environ["FAKE_SCRIPT"])[n]
    sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else argv[argv.index("--resume") + 1]
    pkg = os.getcwd()
    if step.get("write_package"):
        os.makedirs(os.path.join(pkg, "scanners"), exist_ok=True)
        open(os.path.join(pkg, "strategy.yaml"), "w").write("schema_version: 1\nid: demo\nversion: '1.0.0'\n")
        open(os.path.join(pkg, "runtime.yaml"), "w").write("name: demo-main\ngroup: demo\n")
        open(os.path.join(pkg, "scanners", "scan.py"), "w").write("def scan(inputs, ctx):\n    return []\n")
    if step.get("write_proof"):
        fp, files = ab.fingerprint(__import__("pathlib").Path(pkg) / "runtime.yaml", ["./scanners"])
        json.dump({"record_version": 1, "fingerprint": fp, "files": files, "recipe": "runtime.yaml",
                   "scanner_roots": ["./scanners"], "runtime_version": "9.9.9", "verdict": "PASS",
                   "observations": []}, open(os.path.join(pkg, ".senpi-proof.json"), "w"))
    print(json.dumps({"type": "system", "subtype": "init", "session_id": sid}))
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": os.path.join(pkg, "scanners/scan.py")}}]}}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": sid,
                      "total_cost_usd": 0.5, "num_turns": 3, "structured_output": step["out"]}))
''').lstrip()

SPEC = {
    "id": "demo", "name": "Demo", "thesis": "test", "user_confirmed": True,
    "decisions": {d: "x" for d in ab.DECISIONS},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("SENPI_AUTHOR_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AB_SCRIPTS", str(SCRIPTS))
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "strategies"))
    monkeypatch.setenv("SENPI_AUTHOR_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("SENPI_RUNTIME_DIR", str(tmp_path / "no-runtime"))  # version check off
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "no-state"))
    monkeypatch.setenv("AI_API_KEY", "Bearer sk-box-key")
    monkeypatch.setenv("SENPI_BASE_URL", "https://models.senpi.ai/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak-to-child")
    for var in ("SENPI_AUTHOR_ANTHROPIC_API_KEY", "SENPI_AUTHOR_ANTHROPIC_BASE_URL", "SENPI_AUTHOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC))
    return tmp_path, spec


def script(monkeypatch, *steps):
    monkeypatch.setenv("FAKE_SCRIPT", json.dumps(list(steps)))


def only_job(tmp):
    jobs = list((tmp / "jobs").iterdir())
    assert len(jobs) == 1
    return ab.Job(jobs[0].name)


# ---------------------------------------------------------------- spec gate

@pytest.mark.parametrize("patch,needle", [
    ({"user_confirmed": False}, "user_confirmed"),
    ({"id": "Bad_ID"}, "slug"),
    ({"decisions": {"universe": "x"}}, "decisions missing"),
    ({"name": ""}, "`name`"),
])
def test_start_refuses_unconfirmed_or_malformed_spec(env, capsys, patch, needle):
    tmp, spec = env
    spec.write_text(json.dumps({**SPEC, **patch}))
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["refused"]
    assert needle in capsys.readouterr().err
    assert not (tmp / "jobs").exists() or not any((tmp / "jobs").iterdir())


def test_start_refuses_to_overwrite_an_existing_package(env, capsys):
    tmp, spec = env
    (tmp / "strategies" / "demo").mkdir(parents=True)
    (tmp / "strategies" / "demo" / "strategy.yaml").write_text("id: demo\n")
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["refused"]
    assert "--edit" in capsys.readouterr().err


# ---------------------------------------------------------------- the round trip

def test_needs_input_then_answer_then_done(env, monkeypatch, capsys):
    tmp, spec = env
    script(monkeypatch,
           {"out": {"status": "needs_input", "summary": "one gap", "question": "Volume or OI?",
                    "options": ["volume", "open interest"]}},
           {"write_package": True, "write_proof": True,
            "out": {"status": "done", "summary": "built", "validate": {"verdict": "PASS",
                    "stage_lines": ["✓ static", "✓ import", "✓ live"]}}})

    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["needs_input"]
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("AUTHOR_BUILD ") and len(line) <= 400
    note = json.loads(line.split(" ", 1)[1])
    assert note["state"] == "needs_input" and note["question"] == "Volume or OI?"

    job = only_job(tmp)
    first = json.loads((job.dir / "fake-argv-0.json").read_text())
    sid = first["argv"][first["argv"].index("--session-id") + 1]
    assert job.read()["session_id"] == sid

    assert ab.main(["answer", "--job", job.id, "--text", "volume"]) == ab.EXIT["done"]
    second = json.loads((job.dir / "fake-argv-1.json").read_text())
    assert second["argv"][second["argv"].index("--resume") + 1] == sid       # same session, resumed
    assert "volume" in second["argv"][second["argv"].index("-p") + 1]
    res = job.result()
    assert res["status"] == "done" and res["proof"]["ok"] is True
    assert res["cost_usd"] == 1.0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1].split(" ", 1)[1])["proof"] is True


def test_done_without_proof_is_sent_back_then_accepted(env, monkeypatch):
    tmp, spec = env
    script(monkeypatch,
           {"write_package": True, "out": {"status": "done", "summary": "trust me"}},
           {"write_proof": True, "out": {"status": "done", "summary": "now proven"}})
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["done"]
    job = only_job(tmp)
    nudge = json.loads((job.dir / "fake-argv-1.json").read_text())["argv"]
    assert "--resume" in nudge and "no_proof" in nudge[nudge.index("-p") + 1]
    assert job.result()["summary"] == "now proven"


def test_done_that_never_proves_ends_failed(env, monkeypatch):
    tmp, spec = env
    liar = {"write_package": True, "out": {"status": "done", "summary": "trust me"}}
    script(monkeypatch, liar, liar, liar)
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["failed"]
    job = only_job(tmp)
    assert job.read()["state"] == "failed"
    assert "proof is not valid" in job.result()["blocking_finding"]
    assert int((job.dir / "fake-calls").read_text()) == 1 + ab.MAX_NUDGES


def test_edit_after_proof_is_caught(env, monkeypatch):
    tmp, spec = env
    script(monkeypatch, {"write_package": True, "write_proof": True, "out": {"status": "done", "summary": "ok"}})
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["done"]
    pkg = tmp / "strategies" / "demo"
    (pkg / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    return [1]\n")
    rep = ab.verify_proof(pkg)
    assert rep["ok"] is False and rep["reason"] == "content_changed"
    assert rep["instances"][0]["changed_files"] == ["scanners/scan.py"]


def test_child_gets_the_box_model_gateway_not_the_global_key(env, monkeypatch):
    tmp, spec = env
    script(monkeypatch, {"out": {"status": "failed", "summary": "x", "blocking_finding": "y"}})
    ab.main(["start", "--spec", str(spec)])
    job = only_job(tmp)
    call = json.loads((job.dir / "fake-argv-0.json").read_text())
    assert call["env"]["ANTHROPIC_BASE_URL"] == "https://models.senpi.ai"
    assert call["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-box-key"
    assert call["env"]["ANTHROPIC_API_KEY"] is None
    assert call["env"]["ANTHROPIC_MODEL"] == ab.DEFAULT_MODEL
    argv = call["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in argv and argv[argv.index("--setting-sources") + 1] == ""
    settings = json.loads((job.dir / "settings.json").read_text())
    assert "mcp__senpi__strategy_create_custom_strategy" in settings["permissions"]["deny"]
    assert "SENPI_AUTH_TOKEN" not in (job.dir / "mcp.json").read_text().replace("${SENPI_AUTH_TOKEN}", "")
    rules = (job.dir / "build-rules.md").read_text()
    assert "{{" not in rules and str(ab.SKILL_DIR) in rules


def test_harness_failure_is_error_not_failed(env, monkeypatch):
    tmp, spec = env
    monkeypatch.setenv("SENPI_AUTHOR_CLAUDE_BIN", str(tmp / "does-not-exist"))
    assert ab.main(["start", "--spec", str(spec)]) == ab.EXIT["error"]
    assert "not installed" in only_job(tmp).read()["message"]


def test_edit_job_stages_a_copy_and_promote_swaps_it_in(env, monkeypatch):
    tmp, spec = env
    live = tmp / "strategies" / "demo"
    (live / "scanners").mkdir(parents=True)
    (live / "strategy.yaml").write_text("schema_version: 1\nid: demo\nversion: '1.0.0'\n")
    (live / "runtime.yaml").write_text("name: demo-main\ngroup: demo\n")
    (live / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    return []\n")
    (live / ".deploy-state.json").write_text("{}")
    script(monkeypatch, {"write_package": True, "write_proof": True, "out": {"status": "done", "summary": "edited"}})
    assert ab.main(["start", "--spec", str(spec), "--edit", str(live)]) == ab.EXIT["done"]
    job = only_job(tmp)
    staged = Path(job.read()["package_dir"])
    assert staged != live and job.dir in staged.parents
    assert not (live / ".senpi-proof.json").exists()                 # original untouched until promote
    assert ab.main(["promote", "--job", job.id]) == 0
    assert ab.verify_proof(live)["ok"] is True
    assert (live / ".deploy-state.json").exists()
    assert list(tmp.glob("strategies/demo.bak-*"))


def test_status_and_wait_are_read_only_views(env, monkeypatch, capsys):
    tmp, spec = env
    script(monkeypatch, {"out": {"status": "needs_input", "summary": "s", "question": "q?"}})
    ab.main(["start", "--spec", str(spec)])
    job = only_job(tmp)
    capsys.readouterr()
    assert ab.main(["status", "--job", job.id]) == ab.EXIT["needs_input"]
    view = json.loads(capsys.readouterr().out)
    assert view["result"]["question"] == "q?"
    assert ab.main(["wait", "--job", job.id, "--timeout", "1"]) == ab.EXIT["needs_input"]
    assert int((job.dir / "fake-calls").read_text()) == 1                 # neither ran a turn


def test_a_dead_runner_reads_as_error(env):
    tmp, _ = env
    job = ab.Job("demo-x")
    job.dir.mkdir(parents=True)
    job.write(job="demo-x", state="running", pid=999999)
    assert ab._effective(job.read())["state"] == "error"


# ---------------------------------------------------------------- the guard hook

def guard(tmp, tool, tool_input):
    pkg, jobd = tmp / "pkg", tmp / "job"
    pkg.mkdir(exist_ok=True); jobd.mkdir(exist_ok=True)
    env = dict(os.environ, SENPI_AUTHOR_PKG_DIR=str(pkg), SENPI_AUTHOR_JOB_DIR=str(jobd))
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": str(pkg)}), capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


@pytest.mark.parametrize("tool,inp", [
    ("mcp__senpi__market_list_instruments", {}),
    ("mcp__senpi__discovery_get_top_traders", {}),
    ("mcp__senpi__strategy_get_clearinghouse_state", {}),
    ("Write", {"file_path": "scanners/scan.py"}),
    ("Edit", {"file_path": "{pkg}/runtime.yaml"}),
    ("Bash", {"command": "python3 -m unittest discover tests"}),
    ("Bash", {"command": "openclaw senpi validate {pkg}"}),
    ("Bash", {"command": "python3 /skills/senpi-strategy-ops/scripts/deploy.py validate {pkg}"}),
    ("Bash", {"command": "python3 x.py > {pkg}/out.txt 2>/dev/null"}),
    ("Bash", {"command": "rm -rf scanners/__pycache__"}),
    ("Bash", {"command": "python3 /data/.openclaw/skills/senpi-strategy-author/scripts/validate_strategy.py {pkg}"}),
    ("Bash", {"command": "python3 /data/.openclaw/skills/senpi-strategy-author/scripts/author_build.py check {pkg}"}),
    ("Bash", {"command": "cd {pkg} && /usr/local/bin/openclaw senpi validate ."}),
    # Seen live: a heredoc writing strategy.yaml names the env var the manifest binds.
    ("Bash", {"command": "cat > strategy.yaml <<'EOF'\ndefaults: { auth_token_env: SENPI_AUTH_TOKEN }\nEOF"}),
    ("Bash", {"command": "cat > scanners/scan.py <<'EOF'\n# never curl here; kill_switch below\nEOF\npython3 -m unittest"}),
])
def test_guard_allows_what_authoring_needs(tmp_path, tool, inp):
    inp = {k: v.replace("{pkg}", str(tmp_path / "pkg")) for k, v in inp.items()}
    rc, err = guard(tmp_path, tool, inp)
    assert rc == 0, err


@pytest.mark.parametrize("tool,inp,needle", [
    ("mcp__senpi__create_position", {}, "not a read-only"),
    ("mcp__senpi__strategy_create_custom_strategy", {}, "not a read-only"),
    ("mcp__senpi__some_new_tool", {}, "not a read-only"),
    ("mcp__datadog__search_logs", {}, "not available"),
    ("Write", {"file_path": "/data/workspace/strategies/other/scan.py"}, "outside the package"),
    ("Write", {"file_path": "../other/scan.py"}, "outside the package"),
    ("Bash", {"command": "python3 /s/senpi-strategy-ops/scripts/deploy.py create demo --budget 50"}, "senpi-strategy-ops"),
    ("Bash", {"command": "python3 /s/senpi-strategy-ops/scripts/close.py demo"}, "closing"),
    ("Bash", {"command": "openclaw senpi deploy -p ."}, "only `openclaw senpi validate`"),
    ("Bash", {"command": "openclaw cron add"}, "only `openclaw senpi validate`"),
    ("Bash", {"command": "cd /x && /usr/local/bin/openclaw senpi update . --apply"}, "only `openclaw senpi validate`"),
    ("Bash", {"command": "curl https://mcp.prod.senpi.ai/mcp"}, "network"),
    ("Bash", {"command": "pip install requests"}, "installing"),
    ("Bash", {"command": "printenv"}, "environment"),
    ("Bash", {"command": "echo $SENPI_AUTH_TOKEN"}, "credential"),
    ("Bash", {"command": "echo ${ANTHROPIC_AUTH_TOKEN}"}, "credential"),
    ("Bash", {"command": "python3 -c 'import os;print(os.environ[\"AI_API_KEY\"])'"}, "credential"),
    ("Bash", {"command": "env | grep KEY"}, "environment"),
    ("Bash", {"command": "python3 - <<'EOF'\nc.call('create_position', {})\nEOF"}, "money-moving"),
    ("Bash", {"command": "bash <<EOF\nopenclaw senpi deploy -p .\nEOF"}, "only `openclaw senpi validate`"),
    ("Bash", {"command": "cat /data/.openclaw/config/senpi.token"}, "credential"),
    ("Bash", {"command": "python3 -c 'c.call(\"strategy_close\")'"}, "money-moving"),
    ("Bash", {"command": "rm -rf /data/workspace/strategies"}, "outside the package"),
    ("Bash", {"command": "echo x > /data/workspace/AGENTS.md"}, "outside the package"),
    ("Bash", {"command": "cp scan.py /data/.openclaw/skills/x/"}, "outside the package"),
])
def test_guard_blocks_what_authoring_never_needs(tmp_path, tool, inp, needle):
    rc, err = guard(tmp_path, tool, inp)
    assert rc == 2 and needle in err, err


# ---------------------------------------------------------------- parity with the runtime

def test_fingerprint_matches_the_runtime_byte_for_byte(tmp_path):
    """Hash a package with the runtime's own fingerprintPackage and with ours. Needs a built runtime."""
    rt = Path(os.environ.get("SENPI_RUNTIME_DIR_FOR_TESTS") or
              HERE.parents[2] / "senpi-trading-runtime")
    mod = rt / "dist" / "validate" / "proof.js"
    if not mod.is_file() or not shutil.which("node"):
        pytest.skip("runtime dist not available")
    pkg = tmp_path / "p"
    (pkg / "scanners" / "sub").mkdir(parents=True)
    (pkg / "scanners" / "__pycache__").mkdir()
    (pkg / "runtime.yaml").write_text("name: p-main\n")
    (pkg / "scanners" / "scan.py").write_text("def scan(i, c):\n    return []  # é ✓\n")
    (pkg / "scanners" / "sub" / "b.py").write_text("x = 1\n")
    (pkg / "scanners" / "A.py").write_text("y = 2\n")
    (pkg / "scanners" / "__pycache__" / "scan.cpython-312.pyc").write_bytes(b"\0")
    (pkg / "scanners" / "x.pyc").write_bytes(b"\0")
    js = (f"import {{ fingerprintPackage }} from {json.dumps(str(mod))};"
          f"console.log(JSON.stringify(fingerprintPackage({json.dumps(str(pkg / 'runtime.yaml'))}, ['./scanners'])));")
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True)
    theirs = json.loads(out.stdout)
    ours_fp, ours_files = ab.fingerprint(pkg / "runtime.yaml", ["./scanners"])
    assert ours_files == theirs["files"]
    assert ours_fp == theirs["fingerprint"]
