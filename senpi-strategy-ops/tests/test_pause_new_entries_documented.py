"""Pausing new entries on a live strategy has one documented method, and the skill routes to it.

The method is `decision_mode: none` on every `OPEN_POSITION` action: the action skips every signal, and the
position tracker plus the DSL exit keep managing the positions already open. Removing the tracker breaks
boot when DSL is on, so the procedure keeps it. `enabled: false` (not read), `slots: 0` (becomes 1) and
`max_entries_per_day: 0` (below the minimum of 1) pause nothing, so the procedure names them as wrong.
Text is matched with whitespace collapsed, so a rewrap cannot break a check."""
import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = OPS / "SKILL.md"
EDITING = OPS / "references" / "editing-a-live-strategy.md"


def _headings(text):
    """(offset, level, title) for each markdown heading outside code fences."""
    out, fenced, pos = [], False, 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                out.append((pos, len(m.group(1)), m.group(2).strip()))
        pos += len(line)
    return out


def _section(text, title_pattern):
    """The body of the first heading matching `title_pattern`, up to the next heading of the same or
    higher level. None when no heading matches."""
    hs = _headings(text)
    for i, (pos, level, title) in enumerate(hs):
        if re.search(title_pattern, title, re.I):
            end = next((p for p, lv, _ in hs[i + 1:] if lv <= level), len(text))
            return text[pos:end]
    return None


def _flat(text):
    return " ".join(text.split())


class PauseNewEntriesProcedure(unittest.TestCase):
    def setUp(self):
        section = _section(EDITING.read_text(), r"pause new entries")
        self.assertIsNotNone(section, f"{EDITING.name} has no 'Pause new entries' section")
        self.text = _flat(section)

    def test_it_names_the_method(self):
        self.assertIn("`decision_mode: none`", self.text)
        self.assertIn("OPEN_POSITION", self.text)

    def test_it_keeps_what_dsl_needs(self):
        for needle in ("`POSITION_TRACKER`", "`position_tracker`", "`exit`", "DSL"):
            self.assertIn(needle, self.text, needle)
        self.assertRegex(self.text, r"(?i)\bkeep\b")

    def test_it_names_the_methods_that_pause_nothing(self):
        for needle in ("`enabled: false`", "`slots: 0`", "`max_entries_per_day: 0`"):
            self.assertIn(needle, self.text, needle)

    def test_it_says_how_to_resume(self):
        self.assertRegex(self.text, r"(?i)resume[^.]*decision_mode[^.]*(`rule`|`llm`)")


class SkillRoutesAPauseToTheProcedure(unittest.TestCase):
    def test_the_edit_section_routes_pause_requests(self):
        section = _section(SKILL.read_text(), r"Applying an edit")
        self.assertIsNotNone(section)
        paras = [_flat(p) for p in re.split(r"\n\s*\n", section)]
        hits = [p for p in paras
                if re.search(r"(?i)\bpause\b", p) and re.search(r"(?i)stop new trades", p)
                and "references/editing-a-live-strategy.md" in p and "`decision_mode: none`" in p]
        self.assertTrue(hits, "SKILL.md 'Applying an edit' has no paragraph that routes "
                              "'pause' / 'stop new trades' to the procedure with `decision_mode: none`")


if __name__ == "__main__":
    unittest.main()
