#!/usr/bin/env python3
"""A template deploys under the user's name: `_fork.fork` makes `<owner>-<template>` with the identity
rewritten and everything else untouched; `deploy.py create <template>` without an owner is refused."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
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
    env = dict(os.environ, SENPI_STRATEGIES_DIR=str(root))
    return subprocess.run([sys.executable, DEPLOY] + args, env=env, capture_output=True, text=True, timeout=120)


def test_cli_fork_verb_and_the_bare_template_refusal(tmp_path):
    _template(tmp_path)
    out = _run(["fork", "phalanx", "--owner", "Ignas", "--json"], tmp_path)
    assert out.returncode == 0, out.stderr
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["id"] == "ignas-phalanx" and doc["display"] == "Ignas's Phalanx" and doc["errors"] == [] and doc["dir"] == str(tmp_path / "ignas-phalanx")
    assert (tmp_path / "ignas-phalanx" / "strategy.yaml").is_file()
    # a bare template id through create with no owner: refused (2), nothing planned
    out = _run(["create", "phalanx", "--budget", "100", "--dry-run"], tmp_path)
    assert out.returncode == 2 and "deploys under the user's name" in out.stderr and "--owner" in out.stderr and "planned:" not in out.stdout
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
