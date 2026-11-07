"""Every skill that tells a simulation's wallet from a live one holds the same `PAPER_ADDRESS_RE`.

Skills install standalone, so each keeps its own copy. When hypaper's address format changes, every
copy changes on the same day; this test fails while any copy is left behind."""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPIES = ("senpi-strategy-ops/scripts/_cli.py", "senpi-improve-trades/scripts/review.py",
          "quant-desk/scripts/hl_api.py")
LINE = re.compile(r'^PAPER_ADDRESS_RE = re\.compile\((r".*")\)$', re.M)


def _pattern(rel):
    with open(os.path.join(REPO, rel)) as fh:
        found = LINE.findall(fh.read())
    assert len(found) == 1, (rel, found)
    return found[0]


def test_every_copy_is_the_same_regex():
    patterns = {rel: _pattern(rel) for rel in COPIES}
    assert len(set(patterns.values())) == 1, patterns


def test_the_regex_tells_a_simulation_wallet_from_a_live_one():
    rx = re.compile(eval(_pattern(COPIES[0])))  # noqa: S307 — a raw-string literal read from our own file
    assert rx.match("0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5")
    assert not rx.match("0x" + "ab" * 20)
