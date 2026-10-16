"""A behavioural change that does not bump the version never reaches a live box.

`deploy.py` refreshes a package on disk only when the fetched version differs from the local one,
and on a match it DELETES the copy it just fetched:

    if loadable and new_v is not None and new_v == old_v:
        log(f"{sid!r} already current at {new_v} — not refreshing")
        shutil.rmtree(fresh.parent, ignore_errors=True)

So a fix shipped without a `strategy.yaml` version bump is invisible to every box already holding
that version. It fetches the fixed copy, compares the version, throws the fix away, and reports
"already current". Nothing fails. Nothing warns. The strategy keeps trading the old behaviour.

**This is how penguin's 3% pre-move gate failed to exist in production for four days.** #825 added
the gate to penguin's runtime.yaml, scan.py and scoring.py on 2026-10-02 and left the version at
1.1.0. Jason's agent had refetched penguin 1.1.0 at 00:07:41 UTC that day; the gate merged at
21:33:12 UTC. Every `validate` after that logged `'penguin' already current at 1.1.0 — not
refreshing`, and on 2026-10-06 a fresh $3,820 deploy still ran the ungated scanner. The gate was
in the template the whole time. An audit then found **62 packages** in the same state, including:

  * `5ed84ae1` 2026-10-01 — "the fraction guard is strictly below 1 — 1% no longer sizes at 100%",
    across 35 `scan.py` files. A SIZING bug, unpropagated.
  * `99760337` 2026-09-28 — "never upper-case the asset symbol that reaches an order".
  * #822's five raised score floors, #821's price ladder, #825's pre-move gate.

The naive rule — "any edit under a package requires a version bump" — is unusable here: most
commits in this tree are comments and prose, and a guard that fires on those gets bumped past
without being read. So `behaviour_hash.py` hashes what the runtime actually READS: YAML through
`yaml.safe_load`, Python through its AST with docstrings stripped. Comments, formatting and prose
are invisible to it; a changed threshold, gate or expression is not.

Two halves, and both are needed:
  * THIS TEST asserts the committed lock matches the working tree — so a behavioural change with a
    stale lock fails CI.
  * `behaviour_hash.py --write` REFUSES to record a changed hash under an unchanged version. That
    is what stops "just regenerate the lock" from laundering the bug past this test.

Run: python3 -m pytest strategies/tests/test_version_tracks_behaviour.py -q
Regenerate after an intentional change: python3 senpi-trading-runtime/scripts/behaviour_hash.py --write
"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-trading-runtime", "scripts"))

import behaviour_hash as BH  # noqa: E402

LOCK_PATH = os.path.join(ROOT, "strategies", "version_lock.json")
with open(LOCK_PATH, encoding="utf-8") as _fh:
    LOCK = (json.load(_fh) or {}).get("packages") or {}

COMPUTED = BH.all_packages()

_REGEN = "python3 senpi-trading-runtime/scripts/behaviour_hash.py --write"


def _bump_cap(text):
    """Return `text` with maxPreMovePct raised by 6, read from the file rather than hardcoded.

    These mutation helpers used to hardcode the cap's then-current value. When the default moved on
    2026-10-07 the replace became a silent no-op and both tests failed claiming the hash was inert —
    the hash was fine, the test had rotted. Same shape as the hardcoded version string that broke
    the laundering test when #828 bumped penguin. Read the value, change it arithmetically.
    """
    m = re.search(r"^(\s*maxPreMovePct:\s*)([0-9.]+)", text, re.M)
    assert m, "no maxPreMovePct to mutate — the fixture package changed shape"
    return text[:m.start()] + f"{m.group(1)}{float(m.group(2)) + 6}" + text[m.end():]


def test_the_lock_covers_every_package():
    missing = sorted(set(COMPUTED) - set(LOCK))
    extra = sorted(set(LOCK) - set(COMPUTED))
    assert not missing, (
        f"packages absent from the lock: {missing}. A package nobody tracks can change behaviour "
        f"silently and never propagate — run `{_REGEN}`.")
    assert not extra, f"lock names packages that no longer exist: {extra} — run `{_REGEN}`."


@pytest.mark.parametrize("pkg", sorted(COMPUTED))
def test_behaviour_matches_the_lock(pkg):
    """The whole point. A changed hash under an unchanged version is the silent-propagation bug."""
    want, got = LOCK.get(pkg) or {}, COMPUTED[pkg]
    if want.get("behaviour") == got["behaviour"] and want.get("version") == got["version"]:
        return
    if want.get("behaviour") != got["behaviour"] and want.get("version") == got["version"]:
        pytest.fail(
            f"{pkg}: BEHAVIOUR CHANGED, VERSION DID NOT (still {got['version']}).\n"
            f"  This change will not reach any box that already holds {got['version']}: deploy.py\n"
            f"  refreshes on version inequality only, and on a match it deletes the copy it just\n"
            f"  fetched. The box will keep running the old behaviour and report \"already current\".\n"
            f"  Bump `version:` in strategies/{pkg}/strategy.yaml, then run `{_REGEN}`.\n"
            f"  (If the edit really was comments or prose only, the hash would not have moved —\n"
            f"  it ignores comments, formatting and docstrings. A moved hash means behaviour.)")
    pytest.fail(
        f"{pkg}: the lock is out of date (lock {want.get('version')}/{want.get('behaviour')}, "
        f"tree {got['version']}/{got['behaviour']}). Run `{_REGEN}` in the same PR as the change, "
        f"so the behaviour change is visible in the diff rather than inferred from it.")


def test_the_hash_ignores_comments_and_prose():
    """If it did not, this guard would fire on most commits in this tree and get routed around.

    Both halves matter: a comment edit must NOT move the hash, and a value edit MUST. Asserting
    only the first would pass for a hash that is always constant.
    """
    import tempfile
    import shutil
    src = os.path.join(ROOT, "strategies", "penguin")
    tmp = tempfile.mkdtemp(prefix="bhtest-")
    try:
        dst = os.path.join(tmp, "penguin")
        shutil.copytree(src, dst)
        _, base, _ = BH.package_hash(dst)

        rt = os.path.join(dst, "main", "runtime.yaml")
        with open(rt, encoding="utf-8") as fh:
            text = fh.read()

        # 1. a comment-only edit must not move it
        with open(rt, "w", encoding="utf-8") as fh:
            fh.write("# an added comment line, nothing else\n" + text)
        _, after_comment, _ = BH.package_hash(dst)
        assert after_comment == base, (
            "a comment-only edit moved the behaviour hash — this guard would then fire on prose "
            "commits, and a guard that cries wolf gets bumped past unread")

        # 2. a `#` comment block in a scanner must not move it either
        sc = os.path.join(dst, "main", "scanners", "scoring.py")
        with open(sc, encoding="utf-8") as fh:
            py = fh.read()
        with open(sc, "w", encoding="utf-8") as fh:
            fh.write("# a new comment block\n#   second line\n\n" + py)
        _, after_pycomment, _ = BH.package_hash(dst)
        assert after_pycomment == base, "a comment block in a scanner moved the behaviour hash"
        with open(sc, "w", encoding="utf-8") as fh:
            fh.write(py)

        # 3. a VALUE edit must move it — otherwise the hash is inert and proves nothing
        with open(rt, "w", encoding="utf-8") as fh:
            fh.write(_bump_cap(text))
        _, after_value, _ = BH.package_hash(dst)
        assert after_value != base, (
            "raising maxPreMovePct did NOT move the behaviour hash. The hash is not "
            "reading the config, so this entire guard is inert.")

        # 4. and so must a code edit
        with open(rt, "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(sc, "w", encoding="utf-8") as fh:
            fh.write(py.replace("floor=True", "floor=False", 1))
        _, after_code, _ = BH.package_hash(dst)
        assert after_code != base, "a changed default in scoring.py did not move the hash"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_hash_is_portable_across_python_versions():
    """The lock is generated on whatever interpreter the author has and verified on CI's 3.11.

    The first implementation used `ast.dump`, whose output is version-dependent — the lock was
    generated on 3.14 and CI mismatched **every** package, which would have made this guard either
    permanently red or permanently ignored. `tokenize` has the same defect across the 3.11/3.12
    boundary, where f-strings became FSTRING_START/MIDDLE/END.

    So this pins the implementation choice, not just its output: the canonicaliser must stay
    textual. A future "cleanup" that reaches for a parser reintroduces a bug that only shows up in
    CI, on a different machine, as 121 simultaneous failures.
    """
    # Check what the module IMPORTS, via its own parse tree. A text search would match the
    # docstring that explains why these are banned — the prohibition matching itself.
    import ast as _ast
    path = os.path.join(ROOT, "senpi-trading-runtime", "scripts", "behaviour_hash.py")
    with open(path, encoding="utf-8") as fh:
        tree = _ast.parse(fh.read())
    imported = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = imported & {"ast", "tokenize", "dis", "marshal", "py_compile", "symtable"}
    assert not banned, (
        f"behaviour_hash.py imports {sorted(banned)}. Parser, token and bytecode output are NOT "
        f"stable across Python versions, so a lock written on one interpreter would mismatch on "
        f"another — which is how this guard first shipped red on all 121 packages. Keep the "
        f"canonical form textual.")

    # f-strings are the concrete 3.12 tripwire: canonicalising them must not depend on the lexer.
    sample = 'x = 1\nlabel = f"{x:>{3}} and {x!r}"\n# a comment\n\n'
    once = BH._canon_py(sample)
    assert once == BH._canon_py(sample), "the canonicaliser is not deterministic"
    assert b"a comment" not in once, "comment lines are not being stripped"
    assert b'f"{x:>{3}} and {x!r}"' in once, (
        "a nested-format f-string did not survive canonicalisation verbatim — the canonical form "
        "is interpreting the source rather than normalising its text")


def test_write_refuses_to_launder_a_change_past_the_guard():
    """The test above can be satisfied by regenerating the lock. That must not be enough.

    Without this, the workflow "change behaviour, run --write, commit" produces a green CI and a
    change that still never propagates. So `--write` itself refuses when a hash moved under an
    unchanged version, and that refusal is what makes the bump mandatory rather than advisory.
    """
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="bhwrite-")
    try:
        # A repo-shaped copy with one package, so --write operates on a tree we can mutate.
        fake = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(fake, "strategies"))
        shutil.copytree(os.path.join(ROOT, "strategies", "penguin"),
                        os.path.join(fake, "strategies", "penguin"))
        os.makedirs(os.path.join(fake, "senpi-trading-runtime", "scripts"))
        shutil.copy(os.path.join(ROOT, "senpi-trading-runtime", "scripts", "behaviour_hash.py"),
                    os.path.join(fake, "senpi-trading-runtime", "scripts", "behaviour_hash.py"))
        script = os.path.join(fake, "senpi-trading-runtime", "scripts", "behaviour_hash.py")

        def run(*args):
            return subprocess.run([sys.executable, script, *args],
                                  capture_output=True, text=True, cwd=fake)

        assert run("--write").returncode == 0, "the initial write should succeed"

        # change behaviour, leave the version alone
        rt = os.path.join(fake, "strategies", "penguin", "main", "runtime.yaml")
        with open(rt, encoding="utf-8") as fh:
            text = fh.read()
        with open(rt, "w", encoding="utf-8") as fh:
            fh.write(_bump_cap(text))

        r = run("--write")
        assert r.returncode != 0, (
            "--write accepted a behaviour change with no version bump, so the lock can be "
            "regenerated to hide exactly the bug this guards")
        assert "without a version bump" in (r.stderr + r.stdout), \
            f"the refusal does not explain itself: {r.stderr or r.stdout}"
        assert "penguin" in (r.stderr + r.stdout), "the refusal does not name the package"

        # bump the version and it goes through. Read the version rather than hardcoding it: the
        # first cut of this test replaced a literal "1.1.0", #828 moved penguin to 1.2.0, the
        # replacement silently became a no-op, and the test failed claiming a bumped version was
        # refused — when nothing had actually been bumped.
        import re
        sy = os.path.join(fake, "strategies", "penguin", "strategy.yaml")
        with open(sy, encoding="utf-8") as fh:
            man = fh.read()
        m = re.search(r'^version:\s*"?(\d+)\.(\d+)\.(\d+)"?', man, re.M)
        assert m, "penguin's manifest has no parseable version line"
        bumped = f'version: "{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"'
        with open(sy, "w", encoding="utf-8") as fh:
            fh.write(man[:m.start()] + bumped + man[m.end():])
        r2 = run("--write")
        assert r2.returncode == 0, f"a bumped version should write cleanly: {r2.stderr or r2.stdout}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_deploy_py_still_refreshes_on_version_inequality_only():
    """This guard exists BECAUSE of that line. If deploy.py starts comparing content instead, this
    whole file becomes unnecessary — so fail loudly rather than keep enforcing a rule nobody needs.

    deploy.py is Sarvesh's tooling and not ours to change; this only watches it.
    """
    with open(os.path.join(ROOT, "senpi-strategy-ops", "scripts", "deploy.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert "already current at" in src, (
        "deploy.py no longer logs 'already current at' — its refresh logic changed. Re-read it: if "
        "it now compares CONTENT rather than the version, this guard and the lock can be deleted.")
    assert "new_v == old_v" in src, (
        "deploy.py's version-equality refresh check is gone. If it now refreshes on content, drop "
        "this guard; if it refreshes some other way, re-derive what the lock has to protect.")
