"""The durable-root rule named only a skill directory as the hazard, which reads as "anywhere else is
fine" and leaves `/tmp` — where an agent naturally puts a `git clone` to compare templates — looking
safe. It is not: scanners are read from the deploy directory, so a package deployed from `/tmp` trades
until the next restart and then loses every scanner while its wallet stays funded and ACTIVE."""

import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


class DurableRootNamesEphemeralPaths(unittest.TestCase):
    def test_the_rule_names_tmp(self):
        text = " ".join(SKILL.read_text().split())
        start = text.find("the package belongs in the **durable strategies root**")
        assert start >= 0, "SKILL.md lost its durable strategies root rule"
        rule = text[start:text.find("Mechanics + state machine:", start)]
        assert "/tmp" in rule, f"the rule is back to naming only a skill dir: {rule}"


if __name__ == "__main__":
    unittest.main()
