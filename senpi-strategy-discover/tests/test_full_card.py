"""`--card` gives the agent each pick's own words on how it sells, sizes and re-buys.

The shortlist cuts `tagline` / `belief_plain` / `thesis` at 120 characters to fit the result budget.
126 of the 145 cards describe their exit, and on all but 10 of them it sits past the cut (keyword count,
16 Nov 2026). In a blind
test the agent then described exits from the entry rule ("exits when the trend breaks" for Mustang and
Yak, whose cards say a trailing stop decides every sale) and sized Kudu as budget ÷ 11 when its card
gives each name 4% of the account as margin. These tests keep the uncut text one offline call away, inside
the result budget, and keep the shortlist saying so.

    python3 -m pytest senpi-strategy-discover/tests/test_full_card.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

CATALOG = os.path.join(HERE, "..", "catalog.json")
RECORDS = {r["id"]: r for r in json.load(open(CATALOG))["skills"]}
TEXT = ("tagline", "belief_plain", "thesis")


def _run(argv, capsys):
    rc = discover.main(["--catalog", CATALOG] + argv)
    return rc, json.loads(capsys.readouterr().out)


def test_card_returns_the_uncut_text_in_the_order_asked(monkeypatch, capsys):
    def no_network():
        raise AssertionError("--card must stay offline")
    monkeypatch.setattr(discover, "_get_client", no_network)
    rc, out = _run(["--card", "kudu, YAK,mustang,not-a-template", "--budget", "500"], capsys)
    assert rc == 0
    assert [c["id"] for c in out["cards"]] == ["kudu", "yak", "mustang"]
    assert out["unknown"] == ["not-a-template"]
    for card in out["cards"]:
        for field in TEXT:
            assert card[field] == RECORDS[card["id"]][field], f"{card['id']}.{field} was cut"
        assert "market_facts" not in card
        assert card["caveats"] is not None and card["version"] == RECORDS[card["id"]]["version"]
        assert card["tags"], "an in-budget call keeps everything"
    assert "deferred" not in out and "note" not in out


def test_four_long_cards_stay_in_budget_with_every_shown_card_whole(capsys):
    """Review of #986: `--card puffin,ibis-concentrated,pelican,penguin` printed 16.4k, over OpenClaw's
    16,000 exec cap, so the JSON was cut mid-card and the last card's sell / size / re-buy text was lost.
    Over the budget the tags go, then whole cards are deferred to a second call — text is never cut."""
    for asked in (["puffin", "ibis-concentrated", "pelican", "penguin"],
                  ["ibis", "ibis-concentrated", "puffin", "pelican"]):
        rc = discover.main(["--catalog", CATALOG, "--card", ",".join(asked)])
        raw = capsys.readouterr().out.strip()
        out = json.loads(raw)
        assert rc == 0
        assert len(raw) <= discover.OUTPUT_BUDGET < 16_000, f"{asked}: {len(raw)} chars"
        shown = [c["id"] for c in out["cards"]]
        assert shown + out.get("deferred", []) == asked, "every id is shown or deferred, in order"
        assert out["deferred"] and ",".join(out["deferred"]) in out["note"], out.get("note")
        for card in out["cards"]:
            for field in TEXT:
                assert card[field] == RECORDS[card["id"]][field], f"{card['id']}.{field} was cut"
        rc = discover.main(["--catalog", CATALOG, "--card", ",".join(out["deferred"])])
        rest = json.loads(capsys.readouterr().out)
        assert [c["id"] for c in rest["cards"]] == out["deferred"] and "deferred" not in rest


def test_the_shortlist_says_its_text_is_cut_and_how_to_get_the_rest(monkeypatch, capsys):
    def offline():
        raise RuntimeError("offline test")
    monkeypatch.setattr(discover, "_get_client", offline)   # user context degrades to a warning
    rc, out = _run(["--no-market", "--theme", "quality megacap big-tech basket long-term"], capsys)
    assert rc == 0
    cut = [c for c in out["candidates"] for f in TEXT if str(c.get(f) or "").endswith("…")]
    assert cut, "expected the shortlist to cut some card text"
    assert out["meta"]["full_card"] == discover.FULL_CARD_NOTE
    assert "--card" in discover.FULL_CARD_NOTE


def test_no_note_when_nothing_was_cut():
    short = {"id": "x", "tagline": "short", "belief_plain": "short", "thesis": "short", "tags": []}
    res = discover.limit_result({"candidates": [dict(short)], "meta": {}}, 8)
    assert "full_card" not in res["meta"]
    long_ = dict(short, tagline="y" * (discover.TEXT_FIELD_LIMIT + 1))
    res = discover.limit_result({"candidates": [long_], "meta": {}}, 8)
    assert res["meta"]["full_card"] == discover.FULL_CARD_NOTE
