"""The deploy preflight refuses runtime.yaml keys that look like a way to stop new entries but stop nothing.

The runtime ignores `enabled` on an action (the action schema has no such key and passes unknown keys
through), never reads `strategy.enabled`, and turns `strategy.slots: 0` into 1. Each of these deploys clean
and keeps trading. `_pkg.validate` is the structural pass that `deploy.py full_validate` runs before both
`create` and `update`, so the refusal there guards both. Every error names `decision_mode: none`, the key
that does stop new entries (watch-only). Fixtures are copies of real packages under `strategies/`, changed one line at a
time, so the new check is the only variable."""
import re
import shutil
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent
REPO = OPS.parent
STRATEGIES = REPO / "strategies"
sys.path.insert(0, str(OPS / "scripts"))

import _pkg  # noqa: E402
import deploy  # noqa: E402

WATCH_ONLY = "decision_mode: none"


def _copy(tmp_path, sid):
    dest = tmp_path / sid
    shutil.copytree(STRATEGIES / sid, dest)
    return dest


def _edit(path, pattern, repl):
    text = path.read_text()
    new, n = re.subn(pattern, repl, text, flags=re.M)
    assert n == 1, f"fixture edit {pattern!r} matched {n} times in {path}"
    path.write_text(new)


def _add_action_key(rt, action, line):
    _edit(rt, rf"^(  - name: {action}\b[^\n]*\n)", rf"\g<1>    {line}\n")


def _penguin(tmp_path):
    d = _copy(tmp_path, "penguin")
    return d, d / "main" / "runtime.yaml"


def _errs(pkg_dir):
    return _pkg.validate(_pkg.load(str(pkg_dir)))


# ─────────────────────────── refused ───────────────────────────

@pytest.mark.parametrize("value", ["false", "null"])
def test_enabled_on_an_action_is_refused_whatever_its_value(tmp_path, value):
    d, rt = _penguin(tmp_path)
    _add_action_key(rt, "penguin_main_entry", f"enabled: {value}")
    errs = _errs(d)
    assert len(errs) == 1, errs
    assert "penguin_main_entry" in errs[0] and "enabled" in errs[0] and WATCH_ONLY in errs[0], errs


def test_strategy_enabled_false_is_refused(tmp_path):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^  enabled: true\b", "  enabled: false")
    errs = _errs(d)
    assert len(errs) == 1, errs
    assert "strategy.enabled" in errs[0] and WATCH_ONLY in errs[0], errs


@pytest.mark.parametrize("value", ["0", "-1"])
def test_slots_zero_or_below_is_refused(tmp_path, value):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^  slots: 1\b", f"  slots: {value}")
    errs = _errs(d)
    assert len(errs) == 1, errs
    assert "slots" in errs[0] and WATCH_ONLY in errs[0], errs


def test_the_deploy_gate_for_create_and_update_reports_it(tmp_path):
    d, rt = _penguin(tmp_path)
    _add_action_key(rt, "penguin_main_entry", "enabled: false")
    errs = deploy.full_validate(_pkg.load(str(d)))
    assert any("penguin_main_entry" in e and WATCH_ONLY in e for e in errs), errs


# ─────────────────────────── accepted (negative controls) ───────────────────────────

def test_penguin_in_watch_only_passes(tmp_path):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^(  - name: penguin_main_entry\n    action_type: OPEN_POSITION\n    decision_mode:) rule\b",
          r"\g<1> none")
    assert _errs(d) == []
    assert deploy.full_validate(_pkg.load(str(d))) == []


def test_no_catalog_package_trips_the_check():
    flagged = {}
    for man in sorted(STRATEGIES.glob("*/strategy.yaml")):
        try:
            pkg = _pkg.load(str(man.parent))
        except _pkg.BadPackage:
            continue
        hits = [e for e in _pkg.validate(pkg) if WATCH_ONLY in e]
        if hits:
            flagged[man.parent.name] = hits
    assert not flagged, flagged


def test_author_and_ops_word_the_refusal_the_same(tmp_path):
    """author-green == deploy-green: both validators hold the rule with the same message."""
    sys.path.insert(0, str(REPO / "senpi-strategy-author" / "scripts"))
    import validate_strategy as vs
    d, rt = _penguin(tmp_path)
    _add_action_key(rt, "penguin_main_entry", "enabled: false")
    _edit(rt, r"^  enabled: true\b", "  enabled: false")
    _edit(rt, r"^  slots: 1\b", "  slots: 0")
    ops = sorted(e for e in _errs(d) if WATCH_ONLY in e)
    author = sorted(e for e in vs.validate(d) if WATCH_ONLY in e)
    assert len(ops) == 3 and ops == author, (ops, author)
    watch_only, prt = _penguin(tmp_path / "watch_only")
    _edit(prt, r"^(  - name: penguin_main_entry\n    action_type: OPEN_POSITION\n    decision_mode:) rule\b",
          r"\g<1> none")
    assert vs.validate(watch_only) == []
    negative = {"strategy": {"slots": -1}}
    assert vs.inert_entry_stop_offenders(negative) == _pkg.inert_entry_stop_offenders(negative) != []
