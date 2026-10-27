"""The author validator refuses runtime.yaml keys that look like a pause but pause nothing.

The runtime ignores `enabled` on an action, never reads `strategy.enabled`, and turns `strategy.slots: 0`
into 1, so each of these validates clean today and keeps trading. strategy-ops `_pkg.validate` holds the
same rule, so a package this validator passes is not refused at deploy. Every error names
`decision_mode: none`, the key that does pause entries. Fixtures are copies of real packages under
`strategies/`, changed one line at a time.

    python3 -m pytest senpi-strategy-author/tests/
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

AUTHOR = Path(__file__).resolve().parent.parent
STRATEGIES = AUTHOR.parent / "strategies"
sys.path.insert(0, str(AUTHOR / "scripts"))

import validate_strategy as vs  # noqa: E402

PAUSE = "decision_mode: none"


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


# ─────────────────────────── refused ───────────────────────────

@pytest.mark.parametrize("value", ["true", "false", "null", '"false"'])
def test_enabled_on_an_action_is_refused_whatever_its_value(tmp_path, value):
    d, rt = _penguin(tmp_path)
    _add_action_key(rt, "penguin_main_entry", f"enabled: {value}")
    errs = vs.validate(d)
    assert len(errs) == 1, errs
    assert "penguin_main_entry" in errs[0] and "enabled" in errs[0] and PAUSE in errs[0], errs


def test_enabled_on_a_non_entry_action_is_refused(tmp_path):
    d, rt = _penguin(tmp_path)
    _add_action_key(rt, "position_tracker_action", "enabled: false")
    errs = vs.validate(d)
    assert len(errs) == 1, errs
    assert "position_tracker_action" in errs[0] and PAUSE in errs[0], errs


def test_enabled_on_an_action_in_a_later_instance_is_refused(tmp_path):
    d = _copy(tmp_path, "athena")
    assert vs.validate(d) == []
    _add_action_key(d / "aegis" / "runtime.yaml", "aegis_main_entry", "enabled: false")
    errs = vs.validate(d)
    assert len(errs) == 1, errs
    assert "aegis" in errs[0] and "aegis_main_entry" in errs[0] and PAUSE in errs[0], errs


def test_strategy_enabled_false_is_refused(tmp_path):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^  enabled: true\b", "  enabled: false")
    errs = vs.validate(d)
    assert len(errs) == 1, errs
    assert "strategy.enabled" in errs[0] and PAUSE in errs[0], errs


@pytest.mark.parametrize("value", ["0", "-1"])
def test_slots_zero_or_below_is_refused(tmp_path, value):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^  slots: 1\b", f"  slots: {value}")
    errs = vs.validate(d)
    assert len(errs) == 1, errs
    assert "slots" in errs[0] and PAUSE in errs[0], errs


# ─────────────────────────── accepted (negative controls) ───────────────────────────

def test_the_real_penguin_package_passes(tmp_path):
    d, _ = _penguin(tmp_path)
    assert vs.validate(d) == []


def test_penguin_with_entries_paused_passes(tmp_path):
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^(  - name: penguin_main_entry\n    action_type: OPEN_POSITION\n    decision_mode:) rule\b",
          r"\g<1> none")
    assert vs.validate(d) == []


def test_missing_slots_passes(tmp_path):
    """No catalog package omits `slots`, but the runtime defaults it to 1: an omission is not a pause."""
    d, rt = _penguin(tmp_path)
    _edit(rt, r"^  slots: 1\b[^\n]*\n", "")
    assert vs.validate(d) == []


def test_no_catalog_package_trips_the_check():
    flagged = {}
    for man in sorted(STRATEGIES.glob("*/strategy.yaml")):
        hits = [e for e in vs.validate(man.parent) if PAUSE in e]
        if hits:
            flagged[man.parent.name] = hits
    assert not flagged, flagged
