#!/usr/bin/env python3
"""Offline tests for the deploy package model — accept-flat, prescriptive validation, and the
`ensure_pkg` local-authoritative fix. No network, no wallets. Motivated by the 3-model deploy
bake-off (Samurai/Qwen, Gemini, GLM), where every model tripped on the same author↔ops gap.

    python3 -m pytest senpi-strategy-ops/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
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


# ─────────────────────────── template-only mode (temporary gate) ───────────────────────────
# While the from-scratch builder is being hardened, deploy.py refuses to FUND a package whose id
# isn't a catalog template. These lock in the four behaviours that keep it safe + reversible:
# blocks scratch, allows templates, honours the override, and never touches non-`create` commands.
from types import SimpleNamespace  # noqa: E402


def test_template_only_blocks_non_catalog_create(monkeypatch):
    """A from-scratch id (not in the catalog) is refused at `create` — before any wallet is funded —
    with the warm template pointer, not a stack trace."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", True)
    monkeypatch.setattr(deploy, "_catalog_template_ids", lambda: {"raven", "wolf"})
    monkeypatch.delenv("SENPI_ALLOW_CUSTOM_DEPLOY", raising=False)
    with pytest.raises(SystemExit) as ei:
        deploy._template_only_guard(SimpleNamespace(id="my-scratch-idea"), "create")
    msg = str(ei.value)
    assert "my-scratch-idea" in msg and "senpi-strategy-discover" in msg


def test_template_only_allows_catalog_template(monkeypatch):
    """A real catalog template deploys unimpeded (and a re-deploy of an already-fetched template is
    NOT falsely blocked — membership is by id, not by whether it's cached on disk)."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", True)
    monkeypatch.setattr(deploy, "_catalog_template_ids", lambda: {"raven", "wolf"})
    monkeypatch.delenv("SENPI_ALLOW_CUSTOM_DEPLOY", raising=False)
    deploy._template_only_guard(SimpleNamespace(id="raven"), "create")  # no raise


def test_template_only_override_env_lets_internal_deploys_through(monkeypatch):
    """SENPI_ALLOW_CUSTOM_DEPLOY=1 is the internal escape hatch — fleet/CI can still deploy a new,
    not-yet-catalogued strategy from scratch."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", True)
    monkeypatch.setattr(deploy, "_catalog_template_ids", lambda: {"raven"})
    monkeypatch.setenv("SENPI_ALLOW_CUSTOM_DEPLOY", "1")
    deploy._template_only_guard(SimpleNamespace(id="brand-new-fleet-agent"), "create")  # no raise


def test_template_only_ignores_non_create_commands(monkeypatch):
    """validate/status/runtime/verify are never gated — a user can still VALIDATE a scratch package
    (and get feedback); only funding is blocked."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", True)
    monkeypatch.setattr(deploy, "_catalog_template_ids", lambda: {"raven"})
    monkeypatch.delenv("SENPI_ALLOW_CUSTOM_DEPLOY", raising=False)
    for cmd in ("validate", "status", "runtime", "verify"):
        deploy._template_only_guard(SimpleNamespace(id="my-scratch-idea"), cmd)  # no raise


def test_template_only_fails_open_when_catalog_unreadable(monkeypatch):
    """If the catalog can't be fetched (None), the guard does NOT block — funding needs the network
    anyway, so failing open here can't let an offline scratch deploy complete."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", True)
    monkeypatch.setattr(deploy, "_catalog_template_ids", lambda: None)
    monkeypatch.delenv("SENPI_ALLOW_CUSTOM_DEPLOY", raising=False)
    deploy._template_only_guard(SimpleNamespace(id="my-scratch-idea"), "create")  # no raise


def test_template_only_mode_off_is_a_clean_noop(monkeypatch):
    """Flipping TEMPLATE_ONLY_MODE = False fully restores custom deploys — the one-line switch back."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "TEMPLATE_ONLY_MODE", False)
    monkeypatch.delenv("SENPI_ALLOW_CUSTOM_DEPLOY", raising=False)
    deploy._template_only_guard(SimpleNamespace(id="my-scratch-idea"), "create")  # no raise


def test_catalog_ids_parses_skills_and_caches(monkeypatch):
    """_catalog_template_ids extracts skills[].id from the fetched catalog.json and caches per run
    (one network hit); an HTTP error or exception fails open to None (→ guard won't block)."""
    deploy = _import_deploy()
    monkeypatch.setattr(deploy, "_CATALOG_IDS", None)
    body = json.dumps({"skills": [{"id": "raven"}, {"id": "wolf"}, {"no_id": 1}]}).encode()
    calls = {"n": 0}

    def _fake_get(host, path, accept, timeout):
        calls["n"] += 1
        assert "catalog.json" in path
        return 200, body
    monkeypatch.setattr(deploy._fetch, "_get", _fake_get)
    assert deploy._catalog_template_ids() == {"raven", "wolf"}
    assert deploy._catalog_template_ids() == {"raven", "wolf"}  # cached
    assert calls["n"] == 1

    monkeypatch.setattr(deploy, "_CATALOG_IDS", None)
    monkeypatch.setattr(deploy._fetch, "_get", lambda *a, **k: (404, b""))
    assert deploy._catalog_template_ids() is None  # HTTP error → fail open


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
