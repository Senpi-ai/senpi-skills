#!/usr/bin/env python3
"""The address book: which wallets are the reader's, and how we know.

The desk speaks in the second person about a book it believes is the reader's. Getting that wrong is
not cosmetic — it hands a stranger's trading back to the reader as their own, with recommendations.
So the rules here are about what the desk is allowed to assume.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import addresses as ab  # noqa: E402

MINE = "0x" + "a" * 40
WHALE = "0x" + "b" * 40
SENPI = "0x" + "c" * 40


def test_the_book_holds_no_relationship_for_an_address_it_has_not_seen(tmp_path):
    """The BOOK's answer, which is not the desk's. `is_mine` is False here because nothing has been
    recorded — not because the address is judged to be someone else's. `resolve_whose` sits above
    this and defaults an unseen address to the reader's own book (see the test below); the two are
    different questions and this one must not be read as the product default."""
    book = ab.load(str(tmp_path))
    assert ab.relationship(book, WHALE) is None
    assert ab.is_mine(book, WHALE) is False


def test_verified_and_claimed_are_both_theirs_but_are_not_the_same_claim(tmp_path):
    book = ab.load(str(tmp_path))
    ab.mark_verified(book, [SENPI])
    ab.record(book, MINE, relationship=ab.CLAIMED)
    assert ab.is_mine(book, SENPI) and ab.is_mine(book, MINE)
    # the distinction survives, because only one of them is provable
    assert ab.relationship(book, SENPI) == ab.VERIFIED
    assert ab.relationship(book, MINE) == ab.CLAIMED


def test_a_relationship_never_downgrades(tmp_path):
    """Reading your own book in analyst mode must not make the desk forget it is yours — the next
    plain run would then speak to you about yourself in the third person."""
    book = ab.load(str(tmp_path))
    ab.record(book, MINE, relationship=ab.CLAIMED)
    ab.record(book, MINE, relationship=ab.ANALYZED)          # an --other run on their own wallet
    assert ab.relationship(book, MINE) == ab.CLAIMED
    ab.mark_verified(book, [MINE])
    ab.record(book, MINE, relationship=ab.ANALYZED)
    assert ab.relationship(book, MINE) == ab.VERIFIED


def test_addresses_are_case_insensitive_and_junk_is_refused(tmp_path):
    book = ab.load(str(tmp_path))
    ab.record(book, MINE.upper().replace("0X", "0x"), relationship=ab.CLAIMED)
    assert ab.is_mine(book, MINE)                            # keyed lowercase, matched either way
    before = json.dumps(book, sort_keys=True)
    for junk in ("", None, "0xnothex", "not-an-address", "0x123"):
        ab.record(book, junk, relationship=ab.CLAIMED)
    assert json.dumps(book, sort_keys=True) == before        # nothing junk ever enters the book


def test_the_not_indexed_set_is_the_list_behind_the_promise(tmp_path):
    """The desk tells a reader "I've flagged it to the team". That sentence is only honest if the
    address is actually recorded somewhere, and this is the somewhere."""
    book = ab.load(str(tmp_path))
    ab.record(book, MINE, relationship=ab.CLAIMED, indexed=False)
    ab.record(book, WHALE, relationship=ab.ANALYZED, indexed=True)
    ab.record(book, SENPI, relationship=ab.ANALYZED)          # indexed unknown — a quiet wallet
    assert ab.awaiting_index(book) == [MINE]


def test_the_book_survives_a_round_trip_and_a_corrupt_file(tmp_path):
    book = ab.load(str(tmp_path))
    ab.record(book, MINE, relationship=ab.CLAIMED, indexed=False, digest={"score": 62})
    ab.save(str(tmp_path), book)
    again = ab.load(str(tmp_path))
    assert ab.relationship(again, MINE) == ab.CLAIMED
    assert again["addresses"][MINE]["last_desk"] == {"score": 62}
    assert again["addresses"][MINE]["runs"] == 1
    # a corrupt book must not take the desk down with it
    (tmp_path / ab.BOOK).write_text("{ not json")
    assert ab.load(str(tmp_path))["addresses"] == {}


def test_runs_accumulate_so_a_rerun_can_say_what_changed(tmp_path):
    book = ab.load(str(tmp_path))
    ab.record(book, WHALE, relationship=ab.ANALYZED, digest={"score": 55})
    ab.record(book, WHALE, relationship=ab.ANALYZED, digest={"score": 61})
    e = ab.get(book, WHALE)
    assert e["runs"] == 2 and e["last_desk"]["score"] == 61 and e["first_seen"] <= e["last_run"]


def test_mine_and_analyzed_partition_the_book(tmp_path):
    book = ab.load(str(tmp_path))
    ab.mark_verified(book, [SENPI])
    ab.record(book, MINE, relationship=ab.CLAIMED)
    ab.record(book, WHALE, relationship=ab.ANALYZED)
    assert ab.mine(book) == sorted([SENPI, MINE])
    assert ab.analyzed(book) == [WHALE]


def test_an_unseen_address_is_the_readers_own_book(tmp_path):
    """The flagship path is a Hyperliquid trader pasting their own address, so that is the default —
    putting a question in front of it would sit on the one moment the product exists for."""
    sys.path.insert(0, str(HERE.parent / "scripts"))
    import desk
    book = ab.load(str(tmp_path))
    assert desk.resolve_whose(book, MINE) == "mine"            # unseen → theirs
    ab.mark_verified(book, [SENPI])
    assert desk.resolve_whose(book, SENPI) == "mine"           # senpi-issued → theirs
    ab.record(book, MINE, relationship=ab.CLAIMED)
    assert desk.resolve_whose(book, MINE) == "mine"            # claimed → theirs


def test_an_address_already_read_as_someone_elses_stays_someone_elses(tmp_path):
    """What the book adds is memory, not suspicion. They read a whale last week; a bare re-run must
    not start handing them the whale's leaks to fix."""
    sys.path.insert(0, str(HERE.parent / "scripts"))
    import desk
    book = ab.load(str(tmp_path))
    assert desk.resolve_whose(book, WHALE) == "mine"           # before we know anything
    ab.record(book, WHALE, relationship=ab.ANALYZED)
    assert desk.resolve_whose(book, WHALE) == "other"          # and after
    ab.record(book, WHALE, relationship=ab.CLAIMED)            # unless they claim it
    assert desk.resolve_whose(book, WHALE) == "mine"


def test_an_explicit_flag_still_wins_over_the_book(tmp_path):
    sys.path.insert(0, str(HERE.parent / "scripts"))
    import desk
    book = ab.load(str(tmp_path))
    ab.record(book, MINE, relationship=ab.CLAIMED)
    assert desk.resolve_whose(book, MINE, other=True) == "other"      # read my own book as an analyst
    assert desk.resolve_whose(book, WHALE, mine=True) == "mine"       # and the reverse
    assert desk.resolve_whose(book, WHALE, claim=True) == "mine"      # claiming reads it as theirs


# ── the coverage figure has to describe OUR index, not Hyperliquid's leaderboard ──
# Shipped in the 1.4.0 branch as 45,424 — within 2% of Hyperliquid's own leaderboard row count
# (~46,600) and a 73% overstatement of Senpi's index. Measured the same day: closed_positions
# 26,188 wallets, raw_fills 27,405, top_perps_traders_all_time 29,171, mdd 31,199. Nothing we hold
# is near 45k. The feature exists to tell a reader honestly that their wallet is NOT indexed, so an
# inflated denominator is the one error that defeats its whole purpose.

def _coverage():
    import json, pathlib
    return json.loads((pathlib.Path(__file__).resolve().parents[1]
                       / "references" / "coverage.json").read_text())


def test_the_indexed_count_is_our_index_not_the_leaderboard():
    n = _coverage()["indexed_wallets"]
    assert 10_000 < n < 40_000, (
        f"indexed_wallets={n:,} is outside the plausible range for Senpi's own index. "
        "Above ~40k means it was sourced from Hyperliquid's leaderboard row count (~46,600) "
        "rather than from our own tables.")


def test_the_skill_quotes_the_same_number_as_the_file():
    """A figure hardcoded in prose drifts away from the file it claims to read."""
    import pathlib, re
    skill = (pathlib.Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
    n = _coverage()["indexed_wallets"]
    assert f"**{n:,}** wallets so far" in skill, (
        f"SKILL.md must quote coverage.json's {n:,}; found "
        f"{re.findall(r'[*][*]([0-9,]+)[*][*] wallets so far', skill)}")


def test_the_stale_fallback_never_rounds_upward():
    """`as_of` goes stale and the skill falls back to a round number. Rounding UP would restate the
    original bug in a vaguer form, so the fallback must sit at or below the real figure."""
    import pathlib, re
    skill = (pathlib.Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
    m = re.search(r'say "over ([0-9,]+)" rather than', skill)
    assert m, "the stale fallback phrasing is gone — update this test with it"
    assert int(m.group(1).replace(",", "")) <= _coverage()["indexed_wallets"]
