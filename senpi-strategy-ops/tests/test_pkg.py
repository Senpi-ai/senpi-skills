#!/usr/bin/env python3
"""Offline tests for the deploy package model — accept-flat, prescriptive validation, and the
`ensure_pkg` local-authoritative fix. No network, no wallets. Motivated by the 3-model deploy
bake-off (Samurai/Qwen, Gemini, GLM), where every model tripped on the same author↔ops gap.

    python3 -m pytest senpi-strategy-ops/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _pkg  # noqa: E402

RUNTIME_TMPL = """\
name: {name}
group: {group}
strategy:
  wallet: "${{{wallet_env}}}"
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
{sds}
"""

_SDS = "    signal_data_schema:\n      score:\n        type: float\n"


def _runtime(name, group, wallet_env, with_sds=True):
    return RUNTIME_TMPL.format(name=name, group=group, wallet_env=wallet_env,
                               sds=(_SDS if with_sds else ""))


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def make_flat(tmp_path, pkg_id="tech-breakout", name=None, group=None,
              wallet_env="TECHBREAKOUT_WALLET", with_sds=True, version="1.0.0"):
    """A FLAT package — the layout agents naturally scaffold: strategy.yaml (NO instances) + a root
    runtime.yaml + scanners/. `name`/`group` default to the CORRECT values; pass wrong ones to test
    the prescriptive validators."""
    d = tmp_path / pkg_id
    strat = f"id: {pkg_id}\nversion: \"{version}\"\n" if version else f"id: {pkg_id}\n"
    _write(d, "strategy.yaml", strat)
    _write(d, "runtime.yaml", _runtime(name or f"{pkg_id}-main", group or pkg_id, wallet_env, with_sds))
    _write(d, "scanners/scan.py", "def scan(inputs, ctx):\n    return []\n")
    return d


def make_nested(tmp_path, pkg_id="lion", wallet_env="LION_WALLET"):
    """A NESTED multi-field package with an explicit single instance under main/ — the canonical form
    the deployer has always accepted. Guards that accept-flat is purely ADDITIVE."""
    d = tmp_path / pkg_id
    _write(d, "strategy.yaml",
           f"id: {pkg_id}\nversion: \"1.0.0\"\n"
           f"instances:\n  - name: main\n    runtime: main/runtime.yaml\n"
           f"    wallet_env: {wallet_env}\n    funding_share: 1.0\n")
    _write(d, "main/runtime.yaml", _runtime(f"{pkg_id}-main", pkg_id, wallet_env))
    _write(d, "main/scanners/scan.py", "def scan(inputs, ctx):\n    return []\n")
    return d


# ───────────────────────────────── accept flat ─────────────────────────────────

def test_flat_package_loads_with_synthesized_main_instance(tmp_path):
    """A flat package (no `instances`) loads — the deployer synthesizes the canonical single `main`
    instance. This is the #1 error all three bake-off models hit ('instances must be non-empty')."""
    pkg = _pkg.load(str(make_flat(tmp_path)))
    assert len(pkg.instances) == 1
    inst = pkg.instances[0]
    assert inst.name == "main"
    assert inst.runtime_rel == "runtime.yaml"          # points at the ROOT runtime, no main/ nesting
    assert inst.funding_share == 1.0


def test_flat_wallet_env_detected_from_runtime(tmp_path):
    """The synthesized instance binds `wallet_env` to the `${...}` the flat runtime already uses — so
    render substitutes correctly with zero agent effort."""
    pkg = _pkg.load(str(make_flat(tmp_path, wallet_env="VECTOR_WALLET")))
    assert pkg.instances[0].wallet_env == "VECTOR_WALLET"


def test_flat_package_validates_clean(tmp_path):
    """A flat package with the correct name/group/binding is DEPLOY-READY as authored — validate
    returns no errors (the deployer meets the agent where it builds)."""
    pkg = _pkg.load(str(make_flat(tmp_path)))
    assert _pkg.validate(pkg) == []


def test_flat_missing_instances_and_no_runtime_raises(tmp_path):
    """No instances AND no root runtime.yaml is genuinely unusable — still a BadPackage (with the
    updated message pointing at either fix)."""
    d = tmp_path / "empty"
    _write(d, "strategy.yaml", "id: empty\nversion: \"1.0.0\"\n")
    with pytest.raises(_pkg.BadPackage) as ei:
        _pkg.load(str(d))
    assert "instances" in str(ei.value)


def test_nested_package_unchanged(tmp_path):
    """Accept-flat is ADDITIVE: an explicit nested single-instance package still loads with its
    declared instance and validates clean (Lion/Cougar multi-instance path is untouched)."""
    pkg = _pkg.load(str(make_nested(tmp_path)))
    assert len(pkg.instances) == 1 and pkg.instances[0].runtime_rel == "main/runtime.yaml"
    assert _pkg.validate(pkg) == []


# ─────────────────────────── prescriptive validation ───────────────────────────

def test_validate_prescriptive_name(tmp_path):
    """The recurring bake-off tripwire: a flat runtime named `<id>` instead of `<id>-main`. The error
    now PRESCRIBES the exact value to set, not just 'X != Y'."""
    pkg = _pkg.load(str(make_flat(tmp_path, pkg_id="vector", name="vector")))
    errs = _pkg.validate(pkg)
    assert any("set runtime `name: vector-main`" in e for e in errs)


def test_validate_requires_signal_data_schema(tmp_path):
    """external_scanner with no signal_data_schema → a clear, pre-funding error naming the map and its
    placement (Gemma nested it inside `inputs`; Gemini omitted it)."""
    pkg = _pkg.load(str(make_flat(tmp_path, with_sds=False)))
    errs = _pkg.validate(pkg)
    assert any("signal_data_schema" in e and "sibling of `inputs`" in e for e in errs)


def test_validate_flags_missing_version(tmp_path):
    """Sanity: an existing invariant still fires (version required)."""
    pkg = _pkg.load(str(make_flat(tmp_path, version=None)))
    assert any("version" in e for e in _pkg.validate(pkg))


# ─────────────────────────── ensure_pkg / full_validate (deploy.py) ───────────────────────────

def _import_deploy():
    try:
        import deploy  # noqa
        return deploy
    except Exception as e:  # noqa — mcp_client deps may be absent in a bare test env
        pytest.skip(f"deploy.py not importable in this env ({e})")


def test_ensure_pkg_local_invalid_never_fetches_remote(tmp_path, monkeypatch):
    """The GLM footgun: an invalid LOCAL package must surface its real error, NOT silently fall back to
    a (stale) remote fetch. ensure_pkg must not call the remote fetcher when the path is on disk."""
    deploy = _import_deploy()
    called = {"fetch": False}
    monkeypatch.setattr(deploy._fetch, "fetch_package",
                        lambda *a, **k: called.__setitem__("fetch", True))
    d = tmp_path / "broken"
    _write(d, "strategy.yaml", "id: broken\nversion: \"1.0.0\"\n")   # on disk, but invalid (no runtime)
    with pytest.raises(_pkg.BadPackage):
        deploy.ensure_pkg(str(d), None, lambda m: None)
    assert called["fetch"] is False                                  # never reached the remote


def test_full_validate_catches_unresolved_placeholder(tmp_path):
    """The render dry-run in full_validate surfaces an unresolved `${...}` (a runtime that references
    an env the manifest doesn't bind) BEFORE any wallet is funded."""
    deploy = _import_deploy()
    d = make_flat(tmp_path, pkg_id="leaky", wallet_env="LEAKY_WALLET")
    # inject a second placeholder (valid top-level key) the render step can't resolve
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text() + "note: \"${UNBOUND_THING}\"\n")
    pkg = _pkg.load(str(d))
    errs = deploy.full_validate(pkg)
    assert any("UNBOUND_THING" in e for e in errs)


# ─────────────────────── marginPct fraction-vs-percent guard ───────────────────────
# marginPct is a PERCENT in (0,100]; the v2 FRACTION form (0.10 meant 10) sizes 100× too small and
# every order is rejected below the ~$10 min notional. The scaffold doc used to teach the fraction —
# these lock the deploy-time backstop that refuses it, and that legit percents never false-positive.

def test_margin_offenders_flags_fraction_passes_percent():
    off = _pkg.margin_fraction_offenders
    assert off({"scanners": [{"inputs": {"marginPct": 0.10}}]}) == [("scanners[0].inputs.marginPct", 0.10)]
    assert off({"strategy": {"margin_pct": 0.2}}) == [("strategy.margin_pct", 0.2)]
    assert off({"inputs": {"marginPctBase": 0.15, "marginPctCap": 25}}) == [("inputs.marginPctBase", 0.15)]
    assert off({"inputs": {"marginPct": 1}}) == [("inputs.marginPct", 1)]        # 1 == the (0,1] boundary
    # legit percents and non-margin keys never flag
    assert off({"strategy": {"margin_pct": 20}, "inputs": {"marginPctBase": 18, "marginPctCap": 25}}) == []
    assert off({"inputs": {"minScore": 0.5, "leverage": 0.5, "volFloorPctOfMedian": 0.2}}) == []


def test_validate_refuses_fraction_marginpct(tmp_path):
    """A scanner-inputs marginPct: 0.1 (the doc-copy slip) is refused pre-funding, prescribing `Set 10`."""
    d = make_flat(tmp_path, pkg_id="fracbug")
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text().replace("inputs: {}", "inputs:\n      marginPct: 0.1"))
    errs = _pkg.validate(_pkg.load(str(d)))
    assert any("looks like a FRACTION" in e and "Set 10" in e for e in errs)


def test_validate_accepts_percent_marginpct(tmp_path):
    """A percent marginPct (10) validates clean — the guard has no false positive on the correct form."""
    d = make_flat(tmp_path, pkg_id="okmargin")
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text().replace("inputs: {}", "inputs:\n      marginPct: 10"))
    assert _pkg.validate(_pkg.load(str(d))) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
