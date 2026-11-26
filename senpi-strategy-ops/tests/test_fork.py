#!/usr/bin/env python3
"""A template deploys under the user's name: `_fork.fork` makes `<owner>-<template>` with the identity
rewritten and everything else untouched; `deploy.py create <template>` without an owner is refused."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import _fork  # noqa: E402
import _pkg  # noqa: E402

DEPLOY = os.path.join(HERE, "..", "scripts", "deploy.py")

MANIFEST = """\
# Phalanx — a comment that must survive the fork
schema_version: 1

id: phalanx
version: "1.1.0"

catalog:
  name: "Phalanx — Proven-Cohort Rotation"
  emoji: "🛡️"
  tier: advanced

instances:
  - name: main
    runtime: main/runtime.yaml         # → runtime name phalanx-main, group phalanx
    wallet_env: PHALANX_WALLET
    funding_share: 1.0
"""

RUNTIME = """\
# ── PHALANX · single instance ─────────────────────────────────────────────────
name: phalanx-main
group: phalanx
version: 3.0.0
description: >
  PHALANX — proven-cohort rotation. Reads the proven cohort and tracks where
  their headcount is shifting each tick.

strategy:
  wallet: "${PHALANX_WALLET}"
  slots: 3
  margin_pct: 15
  default_leverage: 3

exit:
  dsl_preset: let_winners_run

scanners:
  - type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300
    inputs:
      tiltThreshold: 65
      marginPctBase: 15
    signal_data_schema:
      score:
        type: float
"""


def _template(root, manifest=MANIFEST, runtime=RUNTIME):
    d = root / "phalanx"
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(manifest)
    (d / "main" / "runtime.yaml").write_text(runtime)
    (d / "main" / "scanners" / "scan.py").write_text("print('hi')\n")
    (d / ".deploy-state.json").write_text("{}")          # a legacy marker that must NOT be copied
    (d / "main" / ".senpi-proof.json").write_text("{}")  # the template's proof is not the fork's
    return d


def test_names_follow_the_decided_shape():
    pkg = _pkg.load(_template(Path(pytest.ensuretemp("t1") if hasattr(pytest, "ensuretemp") else __import__("tempfile").mkdtemp())))
    assert _fork.names_for(pkg, owner="Ignas") == ("ignas-phalanx", "Ignas's Phalanx")
    assert _fork.names_for(pkg, owner="Purple Frog_99") == ("purple-frog-99-phalanx", "Purple Frog_99's Phalanx")
    assert _fork.names_for(pkg, name="Shield Wall") == ("shield-wall", "Shield Wall")
    assert _fork.names_for(pkg, owner="ignas", name="Shield Wall")[0] == "shield-wall"   # their words win
    with pytest.raises(_fork.ForkError):
        _fork.names_for(pkg)
    with pytest.raises(_fork.ForkError):
        _fork.names_for(pkg, owner="!!!")
    with pytest.raises(_fork.ForkError):
        _fork.names_for(pkg, name="ab")
    for uid in ("M123456", "m123456", " M42 ", "M123456-7a611", "m123456-7A611"):   # a user ID is never a name,
        for flag in ("owner", "name"):                                           # whichever flag carries it
            with pytest.raises(_fork.ForkError, match="Senpi ID, not a name"):
                _fork.names_for(pkg, **{flag: uid})
    assert _fork.names_for(pkg, name="Mars Base")[0] == "mars-base"        # an M-word is still a name


def _stub(pkg_id, *instances):
    from types import SimpleNamespace
    return SimpleNamespace(id=pkg_id, instances=[SimpleNamespace(name=n) for n in instances or ("main",)],
                           catalog={"name": pkg_id.title()})


SIMULATION_BASE_MAX = 29   # runtime src/deploy/package.ts: "<id>-<instance>" + "-simulation" fits the 40 cap


def _fits_simulation(fid, pkg):
    return all(len(f"{fid}-{i.name}") <= SIMULATION_BASE_MAX for i in pkg.instances)


def test_a_long_username_still_makes_a_simulation_sized_id():
    hawk = _stub("hawk")
    fid, display = _fork.names_for(hawk, owner="SparkTurquoiseIceCream")
    assert _fits_simulation(fid, hawk), fid
    assert fid.startswith("sparkturquoise") and fid.endswith("-hawk") and display == "SparkTurquoiseIceCream's Hawk"
    assert _fork.names_for(hawk, owner="SparkTurquoiseIceCream")[0] == fid                       # stable


def test_a_long_template_fits_and_stays_one_id_per_strategy():
    ids = set()
    for tpl in ("penguins-duo-chase-150bp", "penguins-duo-chase-200bp", "penguins-duo-chase-300bp"):
        pkg = _stub(tpl)
        for owner in ("kinsyu", "SparkTurquoiseIceCream"):
            fid = _fork.names_for(pkg, owner=owner)[0]
            assert _fits_simulation(fid, pkg) and len(fid) >= _fork.MIN_ID, fid
            ids.add(fid)
    assert len(ids) == 6
    athena = _stub("athena-concentrated", "phalanx", "aegis")
    assert _fits_simulation(_fork.names_for(athena, owner="SparkTurquoiseIceCream")[0], athena)
    long_words = _fork.names_for(hawk := _stub("hawk"), name="My very own long strategy name of words")[0]
    assert _fits_simulation(long_words, hawk) and long_words.startswith("my-very-own")


def test_ids_that_already_fit_are_unchanged():
    assert _fork.names_for(_stub("hawk"), owner="Ignas")[0] == "ignas-hawk"
    assert _fork.names_for(_stub("phalanx"), owner="Purple Frog_99")[0] == "purple-frog-99-phalanx"
    assert _fork.names_for(_stub("hawk"), owner="TwentyCharUsername1")[0] == "twentycharusername1-hawk"
    assert _fork.names_for(_stub("hawk"), name="Shield Wall")[0] == "shield-wall"


def test_fork_rewrites_identity_and_nothing_else(tmp_path):
    tpl = _template(tmp_path)
    pkg = _pkg.load(tpl)
    dest, info = _fork.fork(pkg, tmp_path, owner="Ignas")
    assert dest == tmp_path / "ignas-phalanx" and info["id"] == "ignas-phalanx" and info["display"] == "Ignas's Phalanx"
    assert info["runtimes"] == ["ignas-phalanx-main"] and info["forked_from"] == {"id": "phalanx", "version": "1.1.0"} and not info["reused"]
    man = (dest / "strategy.yaml").read_text()
    assert man.startswith("# Phalanx — a comment that must survive the fork\n")     # textual edit, comments kept
    assert "\nid: ignas-phalanx\n" in man and '  name: "Ignas\'s Phalanx"' in man and "  emoji: \"🛡️\"" in man
    assert 'forked_from:\n  id: phalanx\n  version: "1.1.0"' in man
    rt = (dest / "main" / "runtime.yaml").read_text()
    assert rt.startswith("# ── PHALANX") and "\nname: ignas-phalanx-main\n" in rt and "\ngroup: ignas-phalanx\n" in rt
    assert "description: >\n  Ignas's Phalanx — forked from Phalanx 1.1.0.\n  PHALANX — proven-cohort rotation." in rt
    assert "tiltThreshold: 65" in rt and '"${PHALANX_WALLET}"' in rt          # levers and the wallet binding untouched
    assert not (dest / ".deploy-state.json").exists() and not (dest / "main" / ".senpi-proof.json").exists()
    forked = _pkg.load(dest)
    assert forked.id == "ignas-phalanx" and _pkg.validate(forked) == [] and forked.catalog["name"] == "Ignas's Phalanx"
    # idempotent: the same fork is reused, never overwritten
    again, info2 = _fork.fork(pkg, tmp_path, owner="Ignas")
    assert again == dest and info2["reused"] and info2["display"] == "Ignas's Phalanx"
    # a fork of a fork is refused; another occupant of the name is refused
    with pytest.raises(_fork.ForkError):
        _fork.fork(forked, tmp_path, owner="Jason")
    (tmp_path / "shield-wall").mkdir()
    (tmp_path / "shield-wall" / "strategy.yaml").write_text("id: shield-wall\nversion: '1'\ninstances: [{name: main, runtime: r.yaml}]\n")
    with pytest.raises(_fork.ForkError):
        _fork.fork(pkg, tmp_path, name="Shield Wall")


def test_a_named_fork_of_a_fork_copies_its_edits_as_an_independent_variation(tmp_path):
    parent, _ = _fork.fork(_pkg.load(_template(tmp_path)), tmp_path, owner="Alice")
    rt = parent / "main" / "runtime.yaml"
    rt.write_text(rt.read_text().replace("tiltThreshold: 65", "tiltThreshold: 72"))   # the parent's own edit
    (parent / ".deploy-state.json").write_text("{}")                                    # the parent's deploy state
    dest, info = _fork.fork(_pkg.load(parent), tmp_path, name="Penguin Wide Stop")
    assert dest == tmp_path / "penguin-wide-stop" and info["runtimes"] == ["penguin-wide-stop-main"] and not info["reused"]
    assert info["forked_from"] == {"id": "phalanx", "version": "1.1.0"}               # lineage stays the template's
    man = (dest / "strategy.yaml").read_text()
    assert "\nid: penguin-wide-stop\n" in man and '  name: "Penguin Wide Stop"' in man
    assert man.count("forked_from:") == 1 and 'forked_from:\n  id: phalanx\n  version: "1.1.0"' in man
    text = (dest / "main" / "runtime.yaml").read_text()
    assert "\nname: penguin-wide-stop-main\n" in text and "\ngroup: penguin-wide-stop\n" in text
    assert "description: >\n  Penguin Wide Stop — copied from Alice's Phalanx.\n" in text
    assert "tiltThreshold: 72" in text                                                 # the parent's edit came along
    assert not (dest / ".deploy-state.json").exists()
    assert _pkg.validate(_pkg.load(dest)) == []
    assert "tiltThreshold: 72" in rt.read_text() and "\nname: alice-phalanx-main\n" in rt.read_text()   # parent untouched
    with pytest.raises(_fork.ForkError, match="exists"):                                # never overwrites a copy
        _fork.fork(_pkg.load(parent), tmp_path, name="Penguin Wide Stop")
    with pytest.raises(_fork.ForkError, match="--name"):                                # an owner alone is not a variation name
        _fork.fork(_pkg.load(parent), tmp_path, owner="Jason")


def test_cli_fork_of_a_fork_with_a_name(tmp_path):
    _fork.fork(_pkg.load(_template(tmp_path)), tmp_path, owner="Alice")
    out = _run(["fork", str(tmp_path / "alice-phalanx"), "--name", "Penguin Wide Stop", "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["id"] == "penguin-wide-stop" and doc["errors"] == [] and doc["dir"] == str(tmp_path / "penguin-wide-stop")


def test_fork_handles_a_quoted_description_and_a_flat_package(tmp_path):
    rt = RUNTIME.replace('description: >\n  PHALANX — proven-cohort rotation. Reads the proven cohort and tracks where\n  their headcount is shifting each tick.\n',
                         'description: "PHALANX — proven-cohort rotation."\n')
    man = MANIFEST.replace("instances:\n  - name: main\n    runtime: main/runtime.yaml         # → runtime name phalanx-main, group phalanx\n    wallet_env: PHALANX_WALLET\n    funding_share: 1.0\n", "")
    d = tmp_path / "phalanx"
    (d / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(man)
    (d / "runtime.yaml").write_text(rt)
    (d / "scanners" / "scan.py").write_text("print('hi')\n")
    pkg = _pkg.load(d)
    assert len(pkg.instances) == 1 and pkg.instances[0].runtime_rel == "runtime.yaml"
    dest, info = _fork.fork(pkg, tmp_path, name="Shield Wall")
    text = (dest / "runtime.yaml").read_text()
    assert "description: >\n  Shield Wall — forked from Phalanx 1.1.0.\n  PHALANX — proven-cohort rotation.\n" in text
    assert "\nname: shield-wall-main\n" in text and "\ngroup: shield-wall\n" in text
    assert _pkg.validate(_pkg.load(dest)) == [] and info["display"] == "Shield Wall"


def _run(args, root):
    env = {k: v for k, v in os.environ.items() if k != "SENPI_AUTH_TOKEN"}   # offline: no username to read
    env["SENPI_STRATEGIES_DIR"] = str(root)
    return subprocess.run([sys.executable, DEPLOY] + args, env=env, capture_output=True, text=True, timeout=120)


def test_cli_fork_verb_and_the_bare_template_refusal(tmp_path):
    _template(tmp_path)
    out = _run(["fork", "phalanx", "--owner", "Ignas", "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["id"] == "ignas-phalanx" and doc["display"] == "Ignas's Phalanx" and doc["errors"] == [] and doc["dir"] == str(tmp_path / "ignas-phalanx")
    assert (tmp_path / "ignas-phalanx" / "strategy.yaml").is_file()
    # a bare template id through create when no username can be read: refused (2), the agent asks for a name
    out = _run(["create", "phalanx", "--budget", "100", "--dry-run"], tmp_path)
    assert out.returncode == 2 and "deploys under the user's name" in out.stderr and "could not be read" in out.stderr
    assert "--name" in out.stderr and "planned:" not in out.stdout
    # a user ID passed as the owner is refused, not turned into `m123456-phalanx`
    out = _run(["fork", "phalanx", "--owner", "M123456"], tmp_path)
    assert out.returncode == 2 and "Senpi ID, not a name" in out.stderr and not (tmp_path / "m123456-phalanx").exists()
    # the path the refusal above points at: an ID put in --name is refused too
    out = _run(["create", "phalanx", "--budget", "100", "--dry-run", "--name", "M123456"], tmp_path)
    assert out.returncode == 2 and "Senpi ID, not a name" in out.stderr and not (tmp_path / "m123456").exists()
    # with an owner the plan is for the fork
    out = _run(["create", "phalanx", "--budget", "100", "--dry-run", "--owner", "Ignas"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert "planned:" in out.stdout and str(tmp_path / "ignas-phalanx") in out.stdout and "ignas-phalanx-main" not in out.stderr
    # an explicit directory (an author's own package) is never second-guessed
    out = _run(["create", str(tmp_path / "phalanx"), "--budget", "100", "--dry-run"], tmp_path)
    assert out.returncode == 0 and str(tmp_path / "phalanx") in out.stdout
    # the fork itself deploys by directory with no owner needed
    out = _run(["create", str(tmp_path / "ignas-phalanx"), "--budget", "100", "--dry-run"], tmp_path)
    assert out.returncode == 0 and str(tmp_path / "ignas-phalanx") in out.stdout


def _deploy():
    try:
        import deploy  # noqa
        return deploy
    except Exception as e:  # noqa — mcp_client deps may be absent in a bare test env
        pytest.skip(f"deploy.py not importable in this env ({e})")


class _FakeMCP:
    token = "t"

    def __init__(self, doc):
        self.doc = doc

    def mcp_call(self, tool, **_):
        assert tool == "user_get_me"
        return self.doc


def test_the_username_comes_from_user_get_me_and_never_falls_back_to_the_id(monkeypatch):
    deploy = _deploy()
    me = {"success": True, "data": {"user": {"id": "M123456", "userName": "PurpleFrog"}}}
    monkeypatch.setattr(deploy, "MCPClient", lambda: _FakeMCP(me))
    assert deploy._username() == "PurpleFrog"
    me["data"]["user"]["userName"] = None
    assert deploy._username() is None                 # no username: the agent asks, never "M123456"
    tokenless = _FakeMCP(me)
    tokenless.token = ""
    monkeypatch.setattr(deploy, "MCPClient", lambda: tokenless)
    assert deploy._username() is None


def test_create_of_a_bare_template_forks_under_the_username(tmp_path, monkeypatch):
    deploy = _deploy()
    _template(tmp_path)
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path))
    monkeypatch.setattr(deploy, "_username", lambda: "PurpleFrog")
    a = argparse.Namespace(cmd="create", package="phalanx", owner=None, name=None, dry_run=True)
    forked = deploy._fork_for_deploy(_pkg.load(tmp_path / "phalanx"), a, log=lambda m: None)
    assert forked.id == "purplefrog-phalanx" and forked.catalog["name"] == "PurpleFrog's Phalanx"


# ---------- forked_from.parent: the copy step records which node the copy counts under ----------

SIM = "simulation-3f2a6b1c-9d4e-4a5b-8c7d-0e1f2a3b4c5d"
LIVE = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

FORK_MANIFEST = """\
# Alice's copy — a comment that must survive the next copy
schema_version: 1

id: alice-phalanx
version: "1.1.0"

catalog:
  name: "Alice's Phalanx"
  emoji: "🛡️"
  tier: advanced

forked_from:
@BLOCK@
instances:
  - name: main
    runtime: main/runtime.yaml
    wallet_env: PHALANX_WALLET
    funding_share: 1.0
"""


def _parent_of(dest):
    """`forked_from.parent` of the copy, read by PyYAML (through `_pkg.load`) and by the vendored parser."""
    import _yaml
    text = (dest / "strategy.yaml").read_text()
    via_pkg = (_pkg.load(dest).manifest.get("forked_from") or {}).get("parent")
    via_vendored = (_yaml.safe_load(text).get("forked_from") or {}).get("parent")
    assert via_pkg == via_vendored, (via_pkg, via_vendored)
    assert isinstance(via_pkg, str), via_pkg
    return via_pkg


def _without_parent(text):
    """The manifest with its one `parent:` line taken out: everything else must be the source's bytes."""
    lines = text.split("\n")
    at = [i for i, l in enumerate(lines) if re.match(r"^[ \t]+parent:", l)]
    assert len(at) == 1, f"expected exactly one parent line, found {len(at)}:\n{text}"
    return "\n".join(lines[:at[0]] + lines[at[0] + 1:])


def _catalog_copy_expected(fid, display):
    """The manifest of a catalog copy, without its parent line."""
    return (MANIFEST.replace("\nid: phalanx\n", f"\nid: {fid}\n")
            .replace('  name: "Phalanx — Proven-Cohort Rotation"', f'  name: "{display}"')
            .replace('version: "1.1.0"\n', 'version: "1.1.0"\nforked_from:\n  id: phalanx\n  version: "1.1.0"\n', 1))


def _fork_source(root, block):
    """A user's fork on disk (manifest id `alice-phalanx`, template `phalanx`) whose forked_from block is `block`."""
    d = _template(root / "src", manifest=FORK_MANIFEST.replace("@BLOCK@\n", block))
    rt = d / "main" / "runtime.yaml"
    rt.write_text(rt.read_text().replace("name: phalanx-main", "name: alice-phalanx-main")
                  .replace("group: phalanx", "group: alice-phalanx"))
    return d


def _copied_expected(src_text, fid, display):
    return (src_text.replace("\nid: alice-phalanx\n", f"\nid: {fid}\n")
            .replace('  name: "Alice\'s Phalanx"', f'  name: "{display}"'))


def _snapshot(d):
    return {p.relative_to(d): p.read_bytes() for p in sorted(Path(d).rglob("*")) if p.is_file()}


def test_a_catalog_copy_records_its_template_as_the_parent(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    dest, _ = _fork.fork(pkg, tmp_path, name="Shield Wall")
    assert _parent_of(dest) == "template:phalanx"
    assert _without_parent((dest / "strategy.yaml").read_text()) == _catalog_copy_expected("shield-wall", "Shield Wall")


def test_a_copy_of_a_fork_keeps_the_parent_it_carries(tmp_path):
    block = f'  id: phalanx\n  parent: {SIM}\n  # the template it came from\n  version: "1.1.0"\n\n'
    src = _fork_source(tmp_path, block)
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop")
    assert _parent_of(dest) == SIM
    expected = _copied_expected((src / "strategy.yaml").read_text(), "wide-stop", "Wide Stop")
    assert (dest / "strategy.yaml").read_text() == expected                 # the block is copied as it was
    assert _pkg.validate(_pkg.load(dest)) == []


def test_a_copy_of_a_fork_without_a_parent_records_the_forks_template(tmp_path):
    block = '  id: phalanx\n  # the template it came from\n  version: "1.1.0"\n\n'
    src = _fork_source(tmp_path, block)
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop")
    assert _parent_of(dest) == "template:phalanx"                           # forked_from.id, not the source's own id
    expected = _copied_expected((src / "strategy.yaml").read_text(), "wide-stop", "Wide Stop")
    assert _without_parent((dest / "strategy.yaml").read_text()) == expected
    assert _pkg.validate(_pkg.load(dest)) == []


def test_fork_of_wins_over_a_parent_already_set(tmp_path):
    block = f'  id: phalanx\n  parent: {SIM}\n  # the template it came from\n  version: "1.1.0"\n\n'
    src = _fork_source(tmp_path, block)
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop", fork_of=LIVE)
    assert _parent_of(dest) == LIVE
    expected = _without_parent(_copied_expected((src / "strategy.yaml").read_text(), "wide-stop", "Wide Stop"))
    assert _without_parent((dest / "strategy.yaml").read_text()) == expected


def test_a_one_line_forked_from_is_rewritten_as_a_block_with_the_parent(tmp_path):
    flow = 'forked_from: {id: phalanx, version: "1.1.0"}  # lineage\n'
    src = _template(tmp_path / "src", manifest=FORK_MANIFEST.replace("forked_from:\n@BLOCK@\n", flow))
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop")
    assert _parent_of(dest) == "template:phalanx"
    assert _pkg.load(dest).manifest["forked_from"] == {"id": "phalanx", "version": "1.1.0", "parent": "template:phalanx"}
    assert _pkg.validate(_pkg.load(dest)) == []


def test_fork_of_replaces_a_parent_written_over_several_lines(tmp_path):
    block = '  id: phalanx\n  parent: >-\n    template:phalanx\n\n  version: "1.1.0"\n\n'
    src = _fork_source(tmp_path, block)
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop", fork_of=SIM)
    assert _parent_of(dest) == SIM
    assert _pkg.load(dest).manifest["forked_from"]["version"] == "1.1.0"


@pytest.mark.parametrize("kw", [dict(owner="Ignas"), dict(owner="Ignas", name="   ")])
def test_fork_of_without_a_name_is_refused(tmp_path, kw):
    pkg = _pkg.load(_template(tmp_path))
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(_fork.ForkError, match="--name"):
        _fork.fork(pkg, tmp_path, fork_of=SIM, **kw)
    assert sorted(p.name for p in tmp_path.iterdir()) == before             # nothing written


def test_an_authored_copy_records_custom_as_the_parent(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    dest, _ = _fork.fork(pkg, tmp_path, name="Shield Wall", authored=True)
    assert _parent_of(dest) == "custom:phalanx"
    assert _without_parent((dest / "strategy.yaml").read_text()) == _catalog_copy_expected("shield-wall", "Shield Wall")
    dest, _ = _fork.fork(pkg, tmp_path, name="Wide Stop", authored=True, fork_of=SIM)   # --fork-of still wins
    assert _parent_of(dest) == SIM


def test_cli_an_explicit_directory_is_authored_and_a_bare_id_is_a_template(tmp_path):
    _template(tmp_path)
    out = _run(["fork", str(tmp_path / "phalanx"), "--name", "Shield Wall", "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert _parent_of(tmp_path / "shield-wall") == "custom:phalanx"
    out = _run(["create", str(tmp_path / "phalanx"), "--budget", "100", "--dry-run", "--name", "Wide Stop"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert _parent_of(tmp_path / "wide-stop") == "custom:phalanx"
    out = _run(["fork", "phalanx", "--name", "Tight Stop", "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    assert _parent_of(tmp_path / "tight-stop") == "template:phalanx"


def test_fork_of_is_written_as_given_and_a_blank_one_is_refused(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    odd = 'a "b": #c \\ d'
    dest, _ = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=odd)
    assert _parent_of(dest) == odd
    with pytest.raises(_fork.ForkError, match="--fork-of"):
        _fork.fork(pkg, tmp_path, name="Wide Stop", fork_of="  ")
    assert not (tmp_path / "wide-stop").exists()


def test_the_reuse_path_writes_nothing(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    # a fork with no parent line: reuse without --fork-of adds none
    unparented, _ = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=SIM)
    man = unparented / "strategy.yaml"
    man.write_text(_without_parent(man.read_text()))
    before = _snapshot(unparented)
    again, info = _fork.fork(pkg, tmp_path, name="Shield Wall")
    assert again == unparented and info["reused"] and _snapshot(unparented) == before
    # a fork that carries a parent keeps it
    carried, _ = _fork.fork(pkg, tmp_path, owner="Ignas")
    before = _snapshot(carried)
    again, info = _fork.fork(pkg, tmp_path, owner="Ignas")
    assert again == carried and info["reused"] and _snapshot(carried) == before
    assert _parent_of(carried) == "template:phalanx"


def test_fork_of_reuses_a_copy_only_when_it_has_that_parent(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    first, _ = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=SIM)
    before = _snapshot(first)
    again, info = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=SIM)      # the same command again resumes
    assert again == first and info["reused"] and _snapshot(first) == before
    with pytest.raises(_fork.ForkError, match="--name"):                          # another parent is refused
        _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=LIVE)
    assert _snapshot(first) == before and _parent_of(first) == SIM
    unparented, _ = _fork.fork(pkg, tmp_path, name="Wide Stop")
    (unparented / "strategy.yaml").write_text(_without_parent((unparented / "strategy.yaml").read_text()))   # no parent
    before = _snapshot(unparented)
    with pytest.raises(_fork.ForkError, match="--name"):
        _fork.fork(pkg, tmp_path, name="Wide Stop", fork_of=LIVE)
    assert _snapshot(unparented) == before


@pytest.mark.parametrize("ff", ['forked_from:\n  id:\n  version: "1.1.0"\n\n', "forked_from: {}\n"])
def test_a_copy_of_a_fork_with_no_template_id_and_no_parent_has_no_parent(tmp_path, ff):
    src = _template(tmp_path / "src", manifest=FORK_MANIFEST.replace("forked_from:\n@BLOCK@\n", ff))
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Wide Stop")
    assert (_pkg.load(dest).manifest["forked_from"] or {}).get("parent") is None
    assert "parent:" not in (dest / "strategy.yaml").read_text()
    dest, _ = _fork.fork(_pkg.load(src), tmp_path, name="Tight Stop", fork_of=SIM)   # --fork-of still names one
    assert _parent_of(dest) == SIM


def _runtime_name(d):
    return _pkg.load(d).instances[0].runtime_doc.get("name")


def test_a_second_run_into_the_same_name_never_sees_a_half_made_copy(tmp_path, monkeypatch):
    pkg = _pkg.load(_template(tmp_path))
    real, other = _fork._prefix_description, []

    def racing(text, prefix):                       # the manifest is written, the runtimes are not yet
        if not other:
            other.append(None)
            d, info = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=SIM)
            other[0] = (d, info["reused"], _runtime_name(d))
        return real(text, prefix)

    monkeypatch.setattr(_fork, "_prefix_description", racing)
    dest, info = _fork.fork(pkg, tmp_path, name="Shield Wall", fork_of=SIM)
    assert other[0] == (dest, False, "shield-wall-main")   # the other run made its own whole copy
    assert info["reused"] and _runtime_name(dest) == "shield-wall-main" and _parent_of(dest) == SIM
    assert _pkg.validate(_pkg.load(dest)) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["phalanx", "shield-wall"]   # no staging left


def test_a_copy_killed_half_way_leaves_nothing_under_its_name(tmp_path):
    _template(tmp_path)
    code = ("import os, signal, sys; sys.path.insert(0, sys.argv[1]); import _fork, _pkg\n"
            "_fork._prefix_description = lambda t, p: os.kill(os.getpid(), signal.SIGKILL)\n"
            "_fork.fork(_pkg.load(sys.argv[2] + '/phalanx'), sys.argv[2], name='Shield Wall', fork_of='" + SIM + "')\n")
    out = subprocess.run([sys.executable, "-c", code, os.path.join(HERE, "..", "scripts"), str(tmp_path)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == -9, (out.returncode, out.stderr)
    assert not (tmp_path / "shield-wall").exists()
    dest, info = _fork.fork(_pkg.load(tmp_path / "phalanx"), tmp_path, name="Shield Wall", fork_of=SIM)
    assert not info["reused"] and _runtime_name(dest) == "shield-wall-main"


def test_staging_directories_in_the_source_are_not_copied(tmp_path):
    src = _template(tmp_path)
    dest, _ = _fork.fork(_pkg.load(src), src, name="Shield Wall")          # the strategies root is the package dir
    assert sorted(p.name for p in dest.iterdir()) == ["main", "strategy.yaml"]


def test_a_copy_that_fails_while_copying_leaves_nothing_under_its_name(tmp_path):
    src = _template(tmp_path)
    (src / "main" / "scanners" / "gone.py").symlink_to(tmp_path / "missing.py")
    with pytest.raises(Exception):
        _fork.fork(_pkg.load(src), tmp_path, name="Shield Wall")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["phalanx"]


def test_cli_fork_of_on_the_fork_verb_and_on_create_with_a_name(tmp_path):
    _template(tmp_path)
    out = _run(["fork", "phalanx", "--name", "Shield Wall", "--fork-of", SIM, "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["errors"] == [] and _parent_of(tmp_path / "shield-wall") == SIM
    out = _run(["create", "phalanx", "--budget", "100", "--dry-run", "--name", "Wide Stop", "--fork-of", LIVE], tmp_path)
    assert out.returncode == 0, out.stderr
    assert "planned:" in out.stdout and _parent_of(tmp_path / "wide-stop") == LIVE


# `create <dir>` with no name deploys the directory as it is, so only the CLI check refuses its --fork-of
@pytest.mark.parametrize("verb", [["fork", "phalanx"], ["create", "@DIR@", "--budget", "100", "--dry-run"]])
@pytest.mark.parametrize("flags", [[], ["--owner", "Ignas", "--name", "  "]])
def test_cli_fork_of_without_a_name_is_refused(tmp_path, verb, flags):
    _template(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    out = _run([str(tmp_path / "phalanx") if v == "@DIR@" else v for v in verb] + flags + ["--fork-of", SIM], tmp_path)
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert "unrecognized arguments" not in out.stderr and "--name" in out.stderr, out.stderr
    assert "planned:" not in out.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == before             # no copy made


LONG_OWNER = "JollyMercuryBeetroot1"          # slugs to 20 characters: `<owner>-phalanx-main` is too long for a simulation
LEGACY_ID = "jollymercurybeetroot-phalanx"    # the id a fork got before ids were cut to fit a simulation


def _fork_at(root, fid, template="phalanx"):
    """A fork of `template` under `root/fid`, as an older ops version made it."""
    src = root / "_made"
    src.mkdir(exist_ok=True)
    man = MANIFEST.replace("id: phalanx", f"id: {template}")
    made, _ = _fork.fork(_pkg.load(_template(src, manifest=man)), src, owner="Ignas")
    dest = root / fid
    made.rename(dest)
    for f in (dest / "strategy.yaml", dest / "main" / "runtime.yaml"):
        f.write_text(f.read_text().replace(f"ignas-{template}", fid))
    (dest / ".deploy-state.json").write_text('{"live": true}')
    return dest


def test_a_fork_made_under_the_old_uncut_id_is_the_one_a_live_deploy_reuses(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    assert _fork.names_for(pkg, owner=LONG_OWNER)[0] != LEGACY_ID      # the cut id differs from the old one
    legacy = _fork_at(tmp_path, LEGACY_ID)
    dest, info = _fork.fork(pkg, tmp_path, owner=LONG_OWNER)
    assert dest == legacy and info["reused"] and info["id"] == LEGACY_ID
    assert info["runtimes"] == [f"{LEGACY_ID}-main"] and info["dir"] == str(legacy)
    assert (legacy / ".deploy-state.json").read_text() == '{"live": true}'            # untouched
    made = sorted(p.name for p in tmp_path.iterdir() if (p / "strategy.yaml").is_file())
    assert made == sorted(["phalanx", LEGACY_ID]), made                                      # no second fork beside it


def test_a_simulation_never_reuses_an_old_id_too_long_to_run_as_one(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    _fork_at(tmp_path, LEGACY_ID)
    dest, info = _fork.fork(pkg, tmp_path, owner=LONG_OWNER, simulation=True)
    assert info["id"] == _fork.names_for(pkg, owner=LONG_OWNER)[0] and not info["reused"]
    assert _fits_simulation(info["id"], pkg) and dest.name == info["id"]


def test_an_old_id_held_by_a_fork_of_another_template_is_not_reused(tmp_path):
    pkg = _pkg.load(_template(tmp_path))
    _fork_at(tmp_path, LEGACY_ID, template="hawk")
    dest, info = _fork.fork(pkg, tmp_path, owner=LONG_OWNER)
    assert info["id"] == _fork.names_for(pkg, owner=LONG_OWNER)[0] and not info["reused"]


def test_create_of_a_template_deploys_the_old_live_fork_not_a_second_one(tmp_path, monkeypatch):
    deploy = _deploy()
    _template(tmp_path)
    legacy = _fork_at(tmp_path, LEGACY_ID)
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path))
    monkeypatch.setattr(deploy, "_username", lambda: LONG_OWNER)
    a = argparse.Namespace(cmd="create", package="phalanx", owner=None, name=None, dry_run=True, simulation=False)
    forked = deploy._fork_for_deploy(_pkg.load(tmp_path / "phalanx"), a, log=lambda m: None)
    assert forked.id == LEGACY_ID and Path(forked.dir) == legacy
