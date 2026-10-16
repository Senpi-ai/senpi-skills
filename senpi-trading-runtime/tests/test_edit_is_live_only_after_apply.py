"""An edit to a running strategy is live only after `openclaw senpi update --apply`, and the scan contract
says what a tick keeps.

The runtime scaffold imports `scan.py` once when the scanner process starts, so an edit on disk does not
reach a running scanner until an apply restarts it; `senpi validate` runs the copy on disk in a fresh
process and never inspects the running one; a plan's only "nothing has been applied" line is its first.
`ctx.state` records are persisted as JSON with every key, and a tick is discarded whole — state and
signals — on an exception, a timeout, a non-list return, a value `json.dumps` rejects, or `[]` after a
failed `ctx.senpi_mcp` call. Needles are matched with whitespace collapsed, so a rewrap cannot break them."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "SKILL.md")
SCAN_CONTRACT = os.path.join(HERE, "..", "references", "scan-contract.md")


def _flat(path):
    return " ".join(open(path, encoding="utf-8").read().split())


def test_the_skill_says_saved_is_not_applied():
    text = _flat(SKILL)
    for needle in (
        "**Saved is not applied.** Without `--apply`, `update` changes nothing, and only the first line of "
        "its output says so — `Dry run for <runtime_id> — nothing has been applied.` — so never pipe "
        "`openclaw senpi` output through `tail` or `head`.",
        "a running strategy keeps its old code until `--apply` restarts its scanners",
        "`senpi validate` runs the copy on disk, never the running one",
        "re-running `deploy` on a strategy that is already running applies nothing",
        "The edit is live once the apply exits `0` with `Updated <runtime_id>.` as its first line and no "
        "`WARNING` that its external scanners are NOT running, and the running strategy shows it",
        "openclaw senpi events -r <runtime_id> --name runtime.updated",
        "a recipe edit that landed plans \"The recipe is unchanged.\"",
        "Until both reads show it, tell the user the edit is **saved, not applied**.",
    ):
        assert needle in text, needle


def test_the_scan_contract_says_what_a_tick_keeps_and_when_an_edit_runs():
    text = _flat(SCAN_CONTRACT)
    for needle in (
        "### What a tick keeps, and when an edit reaches a running scanner",
        "**Every key of every record you `append` is saved** to the state file as JSON — nothing is dropped "
        "or trimmed",
        "caps how many records are kept, not how large each one is",
        "an `int` key returns as a string and a tuple as a list",
        "**These discard the whole tick — state not advanced, no signals delivered:**",
        "a record holds anything `json.dumps` rejects",
        "`scan()` returns `[]` after any `ctx.senpi_mcp` call in that tick failed, even one your code caught",
        "**`scan.py` is imported once, when the scanner process starts**",
        "A running scanner keeps the code it started with",
        "a crash or a gateway restart also triggers, unannounced",
        "Re-running a deploy on a strategy that is already running applies nothing",
        "**`openclaw senpi validate` never inspects the running scanner.**",
        "a `PASS` proves the edit runs, not that it is live",
    ):
        assert needle in text, needle
