"""An edit to a running strategy is not live until it is applied.

`openclaw senpi update` without `--apply` only plans, and only the FIRST line of its output says so
(`Dry run for <runtime_id> — nothing has been applied.`), so output cut down by `tail`/`head` reads like a
change that went through. The skill therefore says: never trim `openclaw senpi` output, an edit is live only
once `--apply` exits 0 and the running strategy shows it, and until then the user hears "saved, not applied".
The reference names the two reads that show it. Needles are matched with whitespace collapsed, so a rewrap
cannot break them, and every one is absent from the pre-change text, so each fails if its rule is removed."""

import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
SKILL = OPS / "SKILL.md"
EDITING = OPS / "references" / "editing-a-live-strategy.md"


def _flat(path):
    return " ".join(path.read_text().split())


class AnEditIsLiveOnlyAfterItIsApplied(unittest.TestCase):
    def test_the_rule_is_resident_in_the_skill(self):
        text = _flat(SKILL)
        for needle in (
            "**Saved is not applied.**",
            "`update` without `--apply` only plans — its first line reads "
            "`Dry run for <runtime_id> — nothing has been applied.`",
            "never pipe `openclaw senpi` output through `tail`/`head`",
            "An edit is live only once `--apply` exits `0` and the running strategy shows the change",
            "until then tell the user it is **saved, not applied** — never \"done\" or \"live\"",
            "**Re-running `create` (or `senpi deploy`) will NOT apply it**",
        ):
            self.assertIn(needle, text, needle)

    def test_the_reference_names_the_reads_that_show_it(self):
        text = _flat(EDITING)
        for needle in (
            "# 4. Confirm the running strategy has it — both read-only.",
            "openclaw senpi events -r <runtime_id> --name runtime.updated",
            "a recipe edit that landed now plans \"The recipe is unchanged.\"",
            "**An edit is live only after step 3 succeeds and step 4 shows it.**",
            "Until then tell the user the edit is **saved, not applied** — never \"done\" or \"live\"",
            "`Dry run for <runtime_id> — nothing has been applied.` Nothing at the end repeats it, "
            "so never pipe `openclaw senpi` output through `tail` or `head`",
            "its first line is `Updated <runtime_id>.`, and no `WARNING` says the runtime's external "
            "scanners are NOT running",
            "so it keeps the old code until step 3 restarts the scanners",
            "for a code-only edit, the `runtime.updated` entry is the proof",
            "A `PASS` from step 1 is not step 4.",
        ):
            self.assertIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
