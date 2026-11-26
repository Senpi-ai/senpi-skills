"""A template with phase1 trailing off says, next to that switch, that max_loss_pct is still the stop.

Every catalog template ships `phase1.enabled: false` with `max_loss_pct` nested under it. Agents
reading a forked runtime.yaml took the nesting to mean the floor is off too, and told users about
to go live that the strategy had no hard stop, offering to "fix" it by re-enabling phase 1. The
runtime starts such a position in phase 2 with the max-loss floor as its exchange stop-loss from
entry, so the note sits directly above `enabled: false`, where that reading happens.

Run: python3 -m pytest strategies/tests/test_phase1_off_names_the_hard_stop.py -q
"""
import glob
import os
import re

import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOTE = (
    "# HARD STOP: max_loss_pct is an exchange stop-loss at -max_loss_pct ROE from entry; "
    "enabled: false turns off the trailing only, never the stop."
)


def _phase1_off_templates():
    for path in sorted(glob.glob(os.path.join(_ROOT, "*", "*", "runtime.yaml"))):
        with open(path) as f:
            text = f.read()
        preset = ((yaml.safe_load(text) or {}).get("exit") or {}).get("dsl_preset") or {}
        if (preset.get("phase1") or {}).get("enabled") is False:
            yield path, text


def _line_above_enabled(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*phase1:\s*$", line):
            for j in range(i + 1, len(lines)):
                if re.match(r"^\s*enabled:\s*false\b", lines[j]):
                    return lines[j - 1].strip()
    return None


def test_there_are_phase1_off_templates():
    assert sum(1 for _ in _phase1_off_templates()) > 100


def test_every_phase1_off_template_names_the_hard_stop():
    missing = [
        os.path.relpath(path, _ROOT)
        for path, text in _phase1_off_templates()
        if _line_above_enabled(text) != NOTE
    ]
    assert not missing, f"{len(missing)} templates lack the hard-stop note above phase1.enabled: false: {missing[:10]}"
