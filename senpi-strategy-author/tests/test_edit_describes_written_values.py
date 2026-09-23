"""Two rules from one morning's users — the strategy did what the file said, not what we said.

* A user was told a tightened config "would only open 4 to 6 trades"; the file the agent had just
  written set `max_entries_per_day: 12`. The runtime opened 8 and was inside its limit. The user
  reasonably concluded the strategy was broken. Nothing was, except the description. That cap also
  resumed at the UTC rollover and fired 8 entries in ten minutes — the skill called it a "pace
  limit", which is the wording that made a legitimate burst read as a malfunction.
* A second strategy ran under 14 configs in 39 hours and was then reported as producing no signals.
  It had never run long enough to judge. The user asked for each of those edits by name, so the
  rule warns before the next one rather than withholding it — the decision stays theirs.
"""
import pathlib

_SKILL = pathlib.Path(__file__).resolve().parents[1] / "SKILL.md"
_TEXT = _SKILL.read_text(encoding="utf-8")


def test_max_entries_is_described_as_a_ceiling_not_a_pace():
    """A daily count, not a rate: the whole allowance can fire at once after 00:00 UTC."""
    assert "**daily ceiling, not a pace**" in _TEXT
    assert "bypass_max_entries_per_day_on_profit" in _TEXT, (
        "two catalog templates ship the profit bypass, where the cap does not bind at all on a "
        "green day — 'ceiling' is wrong there unless the exception is named")
    assert "pace limit" not in _TEXT, (
        "max_entries_per_day is still called a 'pace limit'; it paces nothing, and that wording is "
        "what makes a burst of entries at the UTC rollover read as a malfunction")


def test_the_edit_rules_sit_in_the_editing_section_and_warn_rather_than_withhold():
    """A rule about editing that lives elsewhere is one the agent reads before it matters."""
    parts = _TEXT.split("## Editing an existing strategy", 1)
    assert len(parts) == 2, "the 'Editing an existing strategy' section moved or was renamed"
    section = parts[1]
    for needle in ("**Describe the edit in the values you actually wrote:**",
                   "never a number you expect it to produce",
                   "**Count your own edits:**",
                   "has not run long enough",
                   "whether to keep tuning is theirs to decide"):
        assert needle in section, needle
    # The user asked for each config by name. Warn before the next edit; never decline it.
    assert "say that and stop" not in _TEXT, (
        "the retuning rule must warn before applying the next edit, not refuse an instruction the "
        "user gave explicitly — their strategy, their call")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
