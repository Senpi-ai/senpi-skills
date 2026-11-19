"""A plain `close.py --all` closes simulations as well as live strategies. "Return my funds to main"
used to route there, so a simulation the user kept running was closed for good, though a
simulation holds no funds to return. The user's words pick the world."""

import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = (OPS / "SKILL.md").read_text()
LIFECYCLE = (OPS / "references" / "lifecycle.md").read_text()


def _flat(text):
    return " ".join(text.split())


class CloseAllRoutesByTheUsersWords(unittest.TestCase):
    def test_description_sends_money_words_to_the_live_close(self):
        head = _flat(SKILL[: SKILL.find("\n---\n", 4)])
        self.assertIn('"return funds to main", "close all my strategies", "withdraw everything" '
                      '(→ close.py --all --live)', head)

    def test_description_sends_everything_words_to_the_plain_close_all(self):
        head = _flat(SKILL[: SKILL.find("\n---\n", 4)])
        self.assertIn('"close everything", "tear everything down", "strategies and simulations" '
                      '(→ close.py --all)', head)

    def test_the_body_says_when_to_ask(self):
        body = _flat(SKILL)
        self.assertIn("Money or live words", body)
        self.assertIn("`close.py --all --live`", body)
        self.assertIn("ask once", body)
        self.assertIn("close.py --all --dry-run", body)

    def test_the_not_closed_note_is_relayed_as_it_says(self):
        body = _flat(SKILL)
        self.assertIn("tell the user what it says about their simulations", body)
        self.assertNotIn("tell the user their simulations are still open and charged", body)

    def test_the_lifecycle_reference_no_longer_sends_return_funds_to_plain_all(self):
        self.assertNotIn('(for "close all strategies / return funds")', _flat(LIFECYCLE))
        self.assertIn('"return funds" is `--all --live`', _flat(LIFECYCLE))


if __name__ == "__main__":
    unittest.main()
