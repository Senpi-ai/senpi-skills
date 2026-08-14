#!/usr/bin/env python3
"""The package id is the attribution stamp, so the author validator holds the same rule ops does.

`deploy` writes `strategy.yaml` `id` into the wallet's `skillName` VERBATIM while the backend stores
it case-normalized — a mixed-case id is stamped under one spelling and looked up under another, and
every consumer that finds a wallet by package id has to case-fold to see it. One canonical spelling
is what removes that. The rule lives here as well as in strategy-ops `_pkg.validate` so author-green
== deploy-green: a package this validator passes must not be refused at deploy.

    python3 -m pytest senpi-strategy-author/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_RUNTIME = """\
name: {sid}-main
group: {sid}
strategy:
  wallet: "${{{env}}}"
  slots: 1
  margin_pct: 20
exit:
  dsl_preset: let_winners_run
scanners:
  - type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 900
    inputs: {{}}
    signal_data_schema:
      score:
        type: float
"""


def _package(tmp_path, sid):
    """A minimal FLAT package the validator otherwise passes, so the id rule is the only variable."""
    d = tmp_path / sid
    (d / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(f"id: {sid}\nversion: \"1.0.0\"\n")
    env = "".join(c for c in sid.upper() if c.isalnum()) + "_WALLET"
    (d / "runtime.yaml").write_text(_RUNTIME.format(sid=sid, env=env))
    (d / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    return []\n")
    return d


def test_validate_refuses_a_mixed_case_package_id(tmp_path):
    errs = vs.validate(Path(_package(tmp_path, "Warpath")))
    assert any("must be lowercase" in e and "id: warpath" in e for e in errs)


def test_validate_accepts_the_lowercase_form(tmp_path):
    errs = vs.validate(Path(_package(tmp_path, "warpath")))
    assert not any("lowercase" in e for e in errs), errs
