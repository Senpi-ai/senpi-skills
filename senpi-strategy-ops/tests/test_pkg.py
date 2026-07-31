#!/usr/bin/env python3
"""Offline tests for the deploy package model — accept-flat, prescriptive validation, and the
`ensure_pkg` local-authoritative fix. No network, no wallets. Motivated by the 3-model deploy
bake-off (Samurai/Qwen, Gemini, GLM), where every model tripped on the same author↔ops gap.

    python3 -m pytest senpi-strategy-ops/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from pathlib import Path

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


# ─────────────────────── durable strategies root (the 2026-07-30 wipe fix) ───────────────────────
# Remote fetches must land at an ABSOLUTE, CWD-independent root: a CWD-relative dest resolved inside
# a managed skill dir gets destroyed on the next SKILL.md version bump (the skills-manager
# swap-replace). These lock the env override, the resolution fallback, and the fetch destination.


def test_strategies_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    assert _pkg.strategies_root() == tmp_path / "durable"


def test_strategies_root_workspace_env_tier(monkeypatch, tmp_path):
    """Without SENPI_STRATEGIES_DIR, the agent workspace (OPENCLAW_WORKSPACE_DIR) is the root —
    the workspace is relocatable and /data/workspace must not be assumed."""
    monkeypatch.delenv("SENPI_STRATEGIES_DIR", raising=False)
    ws = tmp_path / "relocated-ws"
    ws.mkdir()
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(ws))
    assert _pkg.strategies_root() == ws / "strategies"


def test_strategies_root_workspace_env_honored_before_dir_exists(monkeypatch, tmp_path):
    """A SET OPENCLAW_WORKSPACE_DIR is honored even when the dir hasn't materialized yet (fresh
    volume, gateway not yet booted): the env var declares intent, and the fetch mkdir -p's on
    write. Gating this tier on is_dir() would silently fall through to CWD-relative — the exact
    incident behavior — on the boxes least likely to be watched."""
    monkeypatch.delenv("SENPI_STRATEGIES_DIR", raising=False)
    ws = tmp_path / "not-created-yet"
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(ws))
    assert _pkg.strategies_root() == ws / "strategies"


def test_strategies_root_relative_workspace_env_warns(monkeypatch, capsys):
    """A RELATIVE OPENCLAW_WORKSPACE_DIR is honored (platform sets it; overriding a set env would
    surprise) but warned about, exactly like a relative SENPI_STRATEGIES_DIR: a relative root is
    CWD-dependent, the wipe hole this module exists to close."""
    monkeypatch.delenv("SENPI_STRATEGIES_DIR", raising=False)
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", "relative-ws")
    assert _pkg.strategies_root() == Path("relative-ws") / "strategies"
    assert "RELATIVE" in capsys.readouterr().err


def test_strategies_root_cwd_fallback_warns(monkeypatch, tmp_path, capsys):
    """The last-resort CWD-relative fallback (dev host, no workspace) must be LOUD — it silently
    reintroduces the exact CWD-dependence the durable root exists to remove."""
    monkeypatch.delenv("SENPI_STRATEGIES_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
    if Path("/data/workspace").is_dir():
        pytest.skip("host has /data/workspace — fallback tier unreachable")
    assert _pkg.strategies_root() == Path("strategies")
    assert "may not survive skill updates" in capsys.readouterr().err


def test_resolve_pkg_dir_durable_root_from_any_cwd(monkeypatch, tmp_path):
    """A bare id resolves from the durable root when the CWD has no such package — so `deploy.py
    runtime <id>` finds a previously fetched package from ANY working directory."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    make_flat(tmp_path / "durable", pkg_id="spider")
    monkeypatch.chdir(tmp_path)  # CWD has no strategies/spider
    assert _pkg.resolve_pkg_dir("spider") == tmp_path / "durable" / "spider"


def test_resolve_pkg_dir_durable_wins_over_cwd(monkeypatch, tmp_path):
    """When BOTH exist, the durable copy wins: it holds the deploy state (.deploy-state.json), so a
    pristine repo checkout or stale skill-dir copy in the CWD must not shadow it — resolving the
    wrong copy can fund a wallet twice or register a runtime against a dead wallet."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    make_flat(tmp_path / "durable", pkg_id="spider")
    make_flat(tmp_path / "cwd" / "strategies", pkg_id="spider")
    monkeypatch.chdir(tmp_path / "cwd")
    assert _pkg.resolve_pkg_dir("spider") == tmp_path / "durable" / "spider"


def test_resolve_pkg_dir_path_form_finds_durable_package(monkeypatch, tmp_path):
    """The documented PATH form (strategies/<id>) must hit the durable copy too: the bare-id tiers
    key on Path(arg).name, exactly the id ensure_pkg would fetch. Building them as <tier>/<arg>
    doubled the prefix (<root>/strategies/<id>), so from a foreign CWD the deployed package looked
    missing and the fallback fetch OVERWROTE its tuned files in place — deploy state intact, files
    pristine — the worst variant of the catalog-defaults-onto-live-wallet failure."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    make_flat(tmp_path / "durable", pkg_id="spider")
    monkeypatch.chdir(tmp_path)  # no strategies/spider here — tier 1 misses
    assert _pkg.resolve_pkg_dir("strategies/spider") == tmp_path / "durable" / "spider"


def test_ensure_pkg_never_fetches_over_deploy_state(monkeypatch, tmp_path):
    """A dest dir carrying .deploy-state.json but no loadable strategy.yaml (partially wiped
    deployed package) must REFUSE the catalog fetch, not overwrite in place: the fetch would
    graft pristine files onto live deploy state."""
    deploy = _import_deploy()
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    broken = tmp_path / "durable" / "spider"
    broken.mkdir(parents=True)
    (broken / ".deploy-state.json").write_text("{}")
    called = []
    monkeypatch.setattr(deploy._fetch, "fetch_package",
                        lambda *a, **k: called.append(a))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        deploy.ensure_pkg("spider", None, lambda m: None)
    assert "deploy state" in str(e.value)
    assert not called  # the fetch must never have run


def test_resolve_pkg_dir_deploy_state_beats_pristine_durable(monkeypatch, tmp_path):
    """A legacy CWD-relative copy CARRYING deploy state must not be shadowed by a pristine durable
    copy (e.g. one a bare-id command fetched from the catalog): .deploy-state.json is what
    distinguishes 'the deployed package' from 'a checkout of the same id'. Without this, `runtime`
    self-heals the live wallet from the backend and renders the pristine copy's catalog-default
    runtime.yaml onto it — silently replacing the user's tuned parameters on live money."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    make_flat(tmp_path / "durable", pkg_id="spider")  # pristine fetch, no deploy state
    legacy = make_flat(tmp_path / "cwd" / "strategies", pkg_id="spider")
    (legacy / ".deploy-state.json").write_text("{}")
    monkeypatch.chdir(tmp_path / "cwd")
    assert _pkg.resolve_pkg_dir("spider") == Path("strategies") / "spider"


def test_resolve_pkg_dir_both_have_state_durable_wins_loudly(monkeypatch, tmp_path, capsys):
    """TWO copies with deploy state = two deploys of the same id — genuinely ambiguous. Durable wins
    (unchanged precedence), but LOUDLY: the backend reconcile owns wallet truth, the warning makes
    the ambiguity visible instead of silently picking."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    dur = make_flat(tmp_path / "durable", pkg_id="spider")
    (dur / ".deploy-state.json").write_text("{}")
    legacy = make_flat(tmp_path / "cwd" / "strategies", pkg_id="spider")
    (legacy / ".deploy-state.json").write_text("{}")
    monkeypatch.chdir(tmp_path / "cwd")
    assert _pkg.resolve_pkg_dir("spider") == tmp_path / "durable" / "spider"
    assert "deploy state" in capsys.readouterr().err


def test_resolve_pkg_dir_cwd_fallback_for_legacy_deploys(monkeypatch, tmp_path):
    """A pre-durable-root deploy that only exists CWD-relative is still found (legacy fallback)."""
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))  # exists, but empty
    (tmp_path / "durable").mkdir()
    make_flat(tmp_path / "cwd" / "strategies", pkg_id="spider")
    monkeypatch.chdir(tmp_path / "cwd")
    assert _pkg.resolve_pkg_dir("spider") == Path("strategies") / "spider"


def test_ensure_pkg_fetches_into_durable_root_regardless_of_cwd(monkeypatch, tmp_path):
    """The incident fix itself: a remote fetch writes to the durable root even when the CWD is a
    (managed, wipeable) skill dir — and the fetched package loads without CWD help."""
    deploy = _import_deploy()
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    fetched = {}

    def fake_fetch(sid, dest_root, ref=None, **kw):
        fetched["dest_root"] = dest_root
        make_flat(Path(dest_root), pkg_id=sid)

    monkeypatch.setattr(deploy._fetch, "fetch_package", fake_fetch)
    skill_dir = tmp_path / "skills" / "senpi-strategy-ops"
    skill_dir.mkdir(parents=True)
    monkeypatch.chdir(skill_dir)  # the CWD-lottery losing position
    pkg = deploy.ensure_pkg("tech-breakout", None, lambda m: None)
    assert fetched["dest_root"] == tmp_path / "durable"
    assert pkg.dir == (tmp_path / "durable" / "tech-breakout").resolve()
    assert not (skill_dir / "strategies").exists()  # nothing landed in the skill dir


def test_fetch_out_path_refuses_traversal(tmp_path):
    """Defense-in-depth: a remote tree entry with a `..` segment must never write outside the
    dest root. git won't emit `..` in tree paths, but the repo/ref are env-overridable."""
    import _fetch
    assert _fetch._out_path(tmp_path, "strategies/spider/runtime.yaml") \
        == tmp_path / "spider" / "runtime.yaml"
    with pytest.raises(_fetch.FetchError):
        _fetch._out_path(tmp_path, "strategies/../../evil.py")


def test_validate_universe_all_lists_durable_root(monkeypatch, tmp_path):
    """`validate_universe.py --all` must enumerate the durable root, not CWD-relative strategies/ —
    the same bug class as the fetch dest (a CWD glob inside a skill dir sees nothing / the wrong
    packages). On a dev host with no durable root, strategies_root() itself falls back to
    CWD-relative, so legacy behavior is preserved there."""
    import validate_universe
    monkeypatch.setenv("SENPI_STRATEGIES_DIR", str(tmp_path / "durable"))
    make_flat(tmp_path / "durable", pkg_id="spider")
    make_flat(tmp_path / "durable", pkg_id="tech-breakout")
    (tmp_path / "durable" / "not-a-pkg").mkdir()  # no strategy.yaml — must be skipped
    make_flat(tmp_path / "cwd" / "strategies", pkg_id="cwd-only")
    monkeypatch.chdir(tmp_path / "cwd")
    assert validate_universe.all_packages() == [
        str(tmp_path / "durable" / "spider"),
        str(tmp_path / "durable" / "tech-breakout"),
    ]


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
    """A scanner-inputs marginPct: 0.1 (the doc-copy slip) is refused pre-funding, prescribing `set 10`."""
    d = make_flat(tmp_path, pkg_id="fracbug")
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text().replace("inputs: {}", "inputs:\n      marginPct: 0.1"))
    errs = _pkg.validate(_pkg.load(str(d)))
    assert any("must be a PERCENT in (0,100]" in e and "set 10" in e for e in errs)


def test_validate_accepts_percent_marginpct(tmp_path):
    """A percent marginPct (10) validates clean — the guard has no false positive on the correct form."""
    d = make_flat(tmp_path, pkg_id="okmargin")
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text().replace("inputs: {}", "inputs:\n      marginPct: 10"))
    assert _pkg.validate(_pkg.load(str(d))) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
