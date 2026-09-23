"""Three rules from one morning's users — the strategy did what the file said, not what we said.

* A user was told a tightened config "would only open 4 to 6 trades"; the file the agent had just
  written set `max_entries_per_day: 12`. The runtime opened 8 and was within its limit. The user
  reasonably concluded the strategy was broken. Nothing was broken except the description.
* That same cap resumed at the UTC rollover and fired 8 entries in ten minutes, 12 inside two hours.
  The skill called `max_entries_per_day` a "pace limit", which is what set the expectation.
* A second user's strategy ran under 14 distinct configs in 39 hours - seven runtime restarts in one
  three-hour stretch - and then complained it produced no signals. It never ran long enough to judge.
"""
import pathlib
import re

_SKILL = pathlib.Path(__file__).resolve().parents[1] / "SKILL.md"
_TEXT = _SKILL.read_text(encoding="utf-8")


def _needles(*needles):
    for needle in needles:
        assert needle in _TEXT, needle


def test_max_entries_is_described_as_a_ceiling_not_a_pace():
    """It is a daily count, not a rate — the whole allowance can fire at once after 00:00 UTC."""
    _needles("**daily ceiling, not a pace**")
    assert "pace limit" not in _TEXT, (
        "max_entries_per_day is still called a 'pace limit'; it paces nothing, and that wording is "
        "what makes a burst of entries at the UTC rollover read as a malfunction.")


def test_an_edit_is_described_in_the_values_actually_written():
    _needles("**Describe the edit in the values you actually wrote:**",
             "never a number you merely expect it to produce")


def test_repeated_retuning_is_named_as_the_problem():
    _needles("**Count your own edits, too:**",
             "has never run long enough to judge")


def test_the_rules_sit_in_the_editing_section():
    """A rule about editing that lives somewhere else is a rule the agent reads before it matters."""
    editing = _TEXT.split("## Editing an existing strategy", 1)
    assert len(editing) == 2, "the 'Editing an existing strategy' section moved or was renamed"
    assert "**Describe the edit in the values you actually wrote:**" in editing[1]
    assert "**Count your own edits, too:**" in editing[1]


def test_version_bumped_and_readme_agrees():
    m = re.search(r'^  version: "(\d+\.\d+\.\d+)"', _TEXT, re.M)
    assert m, "frontmatter version not found"
    readme = (_SKILL.parents[1] / "README.md").read_text(encoding="utf-8")
    row = re.search(r"\| \[`senpi-strategy-author`\]\([^)]+\) \| (\d+\.\d+\.\d+) \|", readme)
    assert row and row.group(1) == m.group(1), (
        f"README says {row and row.group(1)}; SKILL.md says {m.group(1)}")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
