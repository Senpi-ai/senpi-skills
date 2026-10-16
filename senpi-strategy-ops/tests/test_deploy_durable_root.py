#!/usr/bin/env python3
"""A package outside the durable strategies root is refused by every command that installs or
re-points a runtime: the runtime keeps reading its scanners from that directory, and a box restart
wipes everything outside the volume (e.g. a package deployed from a /tmp clone)."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
import types
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_fork import _deploy, _run, _template  # noqa: E402


def test_create_and_runtime_refuse_a_package_outside_the_root_and_take_one_inside(tmp_path):
    root, elsewhere = tmp_path / "root", tmp_path / "tmp-clone"
    root.mkdir()
    outside = _template(elsewhere)
    for cmd in ("create", "runtime"):
        out = _run([cmd, str(outside), "--budget", "100", "--dry-run"], root)
        assert out.returncode == 2, out.stderr
        assert "outside the durable strategies root" in out.stderr and f"cp -r {outside.resolve()}" in out.stderr
        assert "planned:" not in out.stdout
    inside = _template(root)
    out = _run(["create", str(inside), "--budget", "100", "--dry-run"], root)
    assert out.returncode == 0 and "planned:" in out.stdout, out.stderr


def test_update_refuses_before_the_verb_is_called(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = _template(tmp_path / "tmp-clone")
    out = _run(["update", str(outside), "--id", "phalanx-main", "--apply"], root)
    assert out.returncode == 2 and "outside the durable strategies root" in out.stderr, out.stderr


def test_a_host_with_no_durable_root_holds_nothing(monkeypatch):
    deploy = _deploy()
    monkeypatch.setattr(deploy._pkg, "strategies_root", lambda: Path("strategies"))
    assert deploy.outside_durable_root(types.SimpleNamespace(id="x", dir="/tmp/x"), "create") is None
