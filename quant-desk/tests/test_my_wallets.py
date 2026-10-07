#!/usr/bin/env python3
"""Whose wallet it is (External Wallets R1, amendment A1): "my wallets" = the reader's SAVED wallets (the
ones they added in Your wallets — their claim, read from user_get_me) plus their Senpi strategy wallets. A typed "that one's mine" is this
run's voice only and is never saved. Unknown is never empty."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import addresses as ab  # noqa: E402
import desk  # noqa: E402

ACCESS = "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it."
CONN = "0x" + "d4" * 20
WHALE = "0x" + "b" * 40
STRAT = "0x" + "e5" * 20


class FakeMCP:
    def __init__(self, me=None, strategies=None, fail=()):
        self.me, self.strategies, self.fail = me, strategies, set(fail)

    def mcp_call(self, tool, timeout=12, **kw):
        if tool in self.fail:
            raise RuntimeError(f"{tool} HTTP 503")
        if tool == "user_get_me":
            return {"success": True, "data": self.me}
        if tool == "strategy_list":
            assert kw.get("status") == ["ACTIVE", "PAUSED", "CLOSED"]
            return {"success": True, "data": {"strategies": self.strategies or []}}
        raise AssertionError(tool)


def _me(status="ok", wallets=((CONN, "MetaMask"),)):
    user = {"wallets": [], "external_wallets_status": status}
    if status == "ok":
        user["external_wallets"] = [{"address": a.upper().replace("0X", "0x"), "label": l,
                                      "added_at": "2026-10-01T15:31:02.000Z", "access": ACCESS}
                                     for a, l in wallets]
    return {"user": user}


def test_my_wallets_lists_external_then_senpi():
    mw = desk.my_wallets(FakeMCP(_me(), [{"strategyWalletAddress": STRAT, "strategyName": "aegis",
                                          "status": "CLOSED"}]))
    assert mw["external_wallets_status"] == "ok"
    assert mw["external_wallets"] == [{"address": CONN, "label": "MetaMask",
                                        "added_at": "2026-10-01T15:31:02.000Z", "access": ACCESS}]
    assert mw["senpi_wallets_status"] == "ok"
    assert mw["senpi_wallets"] == [{"address": STRAT, "name": "aegis", "status": "CLOSED"}]


def test_an_older_mcp_is_unavailable_never_none():
    mw = desk.my_wallets(FakeMCP({"user": {"wallets": []}}, []))
    assert mw["external_wallets_status"] == "unavailable" and mw["external_wallets"] is None


def test_failed_reads_are_unavailable_each_on_its_own():
    mw = desk.my_wallets(FakeMCP(_me(), [], fail=("strategy_list",)))
    assert mw["external_wallets_status"] == "ok"
    assert mw["senpi_wallets_status"] == "unavailable" and mw["senpi_wallets"] is None
    mw = desk.my_wallets(FakeMCP(_me(), [], fail=("user_get_me",)))
    assert mw["external_wallets_status"] == "unavailable" and mw["senpi_wallets_status"] == "ok"


def test_a_failed_strategy_list_response_is_unavailable_not_an_empty_list():
    """The MCP returns errors as {success: false, ...} WITHOUT raising: that is not "no strategies"."""
    class ErrMCP(FakeMCP):
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "strategy_list":
                return {"success": False, "error": "UNAVAILABLE", "message": "upstream timeout"}
            return super().mcp_call(tool, timeout=timeout, **kw)
    mw = desk.my_wallets(ErrMCP(_me(), []))
    assert mw["senpi_wallets_status"] == "unavailable" and mw["senpi_wallets"] is None
    assert mw["external_wallets_status"] == "ok"

    class NoListMCP(FakeMCP):
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "strategy_list":
                return {"success": True, "data": {}}
            return super().mcp_call(tool, timeout=timeout, **kw)
    mw = desk.my_wallets(NoListMCP(_me(), []))
    assert mw["senpi_wallets_status"] == "unavailable" and mw["senpi_wallets"] is None


def test_no_token_means_both_unknown():
    mw = desk.my_wallets(None)
    assert mw["external_wallets"] is None and mw["senpi_wallets"] is None and "token" in mw["error"]


def test_a_saved_wallet_is_mine_even_if_once_read_as_a_strangers(tmp_path):
    book = ab.load(str(tmp_path))
    ab.record(book, CONN, relationship=ab.ANALYZED)
    assert desk.resolve_whose(book, CONN) == "other"
    assert desk.resolve_whose(book, CONN, saved=[CONN.upper().replace("0X", "0x")]) == "mine"
    assert desk.resolve_whose(book, CONN, other=True, saved=[CONN]) == "other"   # an explicit flag wins


def test_claim_is_voice_for_this_run_and_is_never_saved():
    src = (HERE.parent / "scripts" / "desk.py").read_text()
    assert "addr_book.CLAIMED if a.claim" not in src, "--claim still records ownership"
    assert 'rel = addr_book.ANALYZED if whose == "other" else None' in src


def test_an_own_voice_run_is_never_recorded_as_someone_elses(tmp_path, capsys):
    """`--mine` / `--claim` save nothing — and must not leave the address behind as `analyzed` either,
    or the next bare run in the same conversation speaks about the reader's own wallet in the third
    person."""
    fx = HERE / "fixtures" / "sample_trader.json"
    addr = json.loads(fx.read_text())["address"].lower()
    for flag in ("--mine", "--claim"):
        sd = tmp_path / flag.strip("-")
        assert desk.main([addr, flag, "--fixture", str(fx), "--dry", "--no-rank", "--no-cohort",
                          "--state-dir", str(sd), "--cache", ""]) == 0
        capsys.readouterr()
        book = ab.load(str(sd))
        assert ab.relationship(book, addr) is None, flag
        assert desk.resolve_whose(book, addr) == "mine", flag


def test_a_cached_run_is_revoiced_whenever_whose_differs(tmp_path, capsys):
    """Read as a stranger's (cached whose=other), then added to Your wallets: a bare re-run served from
    the cache must speak as "mine" — no flag is set, only the resolved voice differs from the cache's."""
    fx = HERE / "fixtures" / "sample_trader.json"
    rec = json.loads(fx.read_text())
    addr = rec["address"].lower()
    rec2 = dict(rec, user_get_me={"success": True, "data": _me(wallets=((addr, "MetaMask"),))})
    fx2 = tmp_path / "fx2.json"
    fx2.write_text(json.dumps(rec2))
    sd, cache = tmp_path / "sd", tmp_path / "cache"
    argv = [addr, "--fixture", str(fx2), "--no-rank", "--no-cohort", "--state-dir", str(sd),
            "--cache", str(cache), "--json"]
    assert desk.main(argv + ["--other"]) == 0
    assert json.loads(capsys.readouterr().out)["whose"] == "other"
    sec = desk.render.SECTIONS[0]
    assert desk.main(argv + ["--section", sec]) == 0      # served from the run cache, no flag set
    assert json.loads(capsys.readouterr().out)["whose"] == "mine"


def test_the_my_wallets_flag_prints_the_json(tmp_path, capsys):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"user_get_me": {"success": True, "data": _me()},
                              "strategy_list": {"success": True, "data": {"strategies": []}}}))
    assert desk.main(["--my-wallets", "--fixture", str(fx), "--state-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["external_wallets"][0]["access"] == ACCESS and out["senpi_wallets"] == []


def _skill():
    return " ".join((HERE.parent / "SKILL.md").read_text(encoding="utf-8").split())


def test_the_skill_resolves_mine_from_external_then_senpi():
    sk = _skill()
    for needle in ("desk.py --my-wallets", "saved wallets first", "it isn't saved",
                   "add it in Your wallets on senpi.ai (web)", ACCESS, "`external_wallets_status: \"unavailable\"`",
                   "I couldn't load your saved wallets", "never imply senpi checked who controls them"):
        assert needle in sk, needle


def test_the_description_owns_leaks_on_external_wallets():
    desc = _skill().split("license:", 1)[0]
    assert "SAVED wallet" in desc and "senpi-improve-trades" in desc
    for words in ('"my MetaMask"', '"my own Hyperliquid wallet"', '"my saved wallet"'):
        assert words in desc, words


def test_the_external_wallet_routing_is_an_aside_so_the_triggers_after_it_stay_the_desks():
    """The routing clause sits in parentheses: written as a dash clause ending in "senpi-improve-trades
    keeps the leaks of senpi strategies —", the trigger list after it read as improve-trades'."""
    desc = _skill().split("license:", 1)[0]
    aside = desc.split('"master my week" (on a SAVED wallet', 1)[1].split(")", 1)
    assert len(aside) == 2, "the saved-wallet routing is not a parenthetical after the trigger"
    assert aside[1].lstrip(", ").startswith('"analyze my wallet / my Hyperliquid address"')
    assert "senpi-improve-trades keeps the leaks of senpi strategies —" not in desc


def test_the_desk_and_improve_trades_split_a_saved_wallet_the_same_way():
    """Ruling (R1 final review): senpi-improve-trades owns the REVIEW of a saved wallet ("review my
    trades", "master my week"); the desk owns leaks and "what did I miss" on it."""
    sk = _skill()
    desc = sk.split("license:", 1)[0]
    assert ('leaks and "what did I miss" are always this skill; its trade review and "master my week" '
            'belong to senpi-improve-trades') in desc
    assert ('On a saved wallet the desk owns leaks and "what did I miss"; the trade review and '
            '"master my week" go to `senpi-improve-trades`') in sk


def test_readme_row_matches_the_skill_version():
    import re
    version = re.search(r'version: "([0-9.]+)"', (HERE.parent / "SKILL.md").read_text()).group(1)
    assert f"| [`quant-desk`](quant-desk/) | {version} |" in (HERE.parent.parent / "README.md").read_text()


def test_my_wallets_uses_none_of_the_retired_words(tmp_path, capsys):
    """Invariant I1 (amendment A1): `--my-wallets` names the reader's saved wallets `external_wallets`;
    nothing it prints uses the word connect(ed) or verified."""
    import re
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"user_get_me": {"success": True, "data": _me()},
                              "strategy_list": {"success": True, "data": {"strategies": []}}}))
    assert desk.main(["--my-wallets", "--fixture", str(fx), "--state-dir", str(tmp_path)]) == 0
    s = capsys.readouterr().out
    assert not re.search(r"(?i)connect(?!ion)", s), s
    assert not re.search(r"(?i)(?<![a-z])verified", s), s


def _analyzed_then_saved(tmp_path, capsys, saved=True, status="ok"):
    """Read as a stranger's (`--other` records `analyzed`), then the fixture's user_get_me is what the
    reader's Your wallets says now. Returns (argv for a bare re-run, addr)."""
    fx = HERE / "fixtures" / "sample_trader.json"
    rec = json.loads(fx.read_text())
    addr = rec["address"].lower()
    me = _me(status=status, wallets=((addr, "Followed"),) if saved else ())
    fx2 = tmp_path / "fx2.json"
    fx2.write_text(json.dumps(dict(rec, user_get_me={"success": True, "data": me})))
    argv = [addr, "--fixture", str(fx2), "--no-rank", "--no-cohort", "--state-dir", str(tmp_path / "sd"),
            "--cache", str(tmp_path / "cache")]
    assert desk.main(argv + ["--other", "--json"]) == 0
    capsys.readouterr()
    return argv, addr


def test_a_stranger_then_saved_is_told_once_with_the_way_back(tmp_path, capsys):
    """Dev E2E 2026-10-07, F2: the "Find my leaks on 0x…" run on a wallet the desk had read as a
    stranger's spoke to the reader as its owner without ever saying why, or how to undo it."""
    argv, addr = _analyzed_then_saved(tmp_path, capsys)
    sec = desk.render.SECTIONS[0]
    assert desk.main(argv + ["--section", sec]) == 0
    md = capsys.readouterr().out
    first = md.split("\n\n", 1)[0]
    assert f"{addr[:6]}…{addr[-4:]}" in first and "because you added it to Your wallets" in first
    assert "remove it in Your wallets on senpi.ai (web)" in first
    # Once per add: the next section does not repeat it, and the stranger mark is kept for a removal.
    assert desk.main(argv + ["--section", sec, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "whose_changed" not in out and out["whose"] == "mine"
    assert ab.relationship(ab.load(str(tmp_path / "sd")), addr) == ab.ANALYZED


def test_the_json_carries_whose_changed_for_this_run_only(tmp_path, capsys):
    argv, addr = _analyzed_then_saved(tmp_path, capsys)
    assert desk.main(argv + ["--json"]) == 0
    wc = json.loads(capsys.readouterr().out)["whose_changed"]
    assert wc["from"] == "analyzed" and wc["to"] == "saved" and "Your wallets" in wc["say"]
    cached = json.loads((tmp_path / "sd" / f"desk-{addr}.json").read_text())
    assert "whose_changed" not in cached


def test_a_deep_dive_on_a_just_added_wallet_says_it_and_speaks_to_the_reader(tmp_path, capsys):
    """The re-voice used to run after --deep, so a deep dive inside the cache window read a just-added
    wallet in the third person (skills final-review residual)."""
    argv, addr = _analyzed_then_saved(tmp_path, capsys)
    assert desk.main(argv + ["--deep", "rules"]) == 0
    md = capsys.readouterr().out
    assert md.startswith(f"I'm reading {addr[:6]}…{addr[-4:]} as your book now")
    assert not ab.saved_note_due(ab.load(str(tmp_path / "sd")), addr)


def test_a_removed_wallet_is_a_strangers_again_and_a_readd_is_told_again(tmp_path, capsys):
    argv, addr = _analyzed_then_saved(tmp_path, capsys)
    assert desk.main(argv + ["--json"]) == 0
    assert "whose_changed" in json.loads(capsys.readouterr().out)
    # removed: a successful read without it → stranger's book again, and the note re-arms
    fx2 = pathlib.Path(argv[argv.index("--fixture") + 1])
    rec = json.loads(fx2.read_text())
    fx2.write_text(json.dumps(dict(rec, user_get_me={"success": True, "data": _me(wallets=())})))
    assert desk.main(argv + ["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["whose"] == "other" and "whose_changed" not in out
    assert ab.saved_note_due(ab.load(str(tmp_path / "sd")), addr)


def test_an_unavailable_saved_list_neither_says_it_nor_rearms_it(tmp_path, capsys):
    argv, addr = _analyzed_then_saved(tmp_path, capsys)
    book = ab.load(str(tmp_path / "sd"))
    ab.mark_saved_noted(book, addr)
    ab.save(str(tmp_path / "sd"), book)
    fx2 = pathlib.Path(argv[argv.index("--fixture") + 1])
    rec = json.loads(fx2.read_text())
    fx2.write_text(json.dumps(dict(rec, user_get_me={"success": True, "data": _me(status="unavailable")})))
    assert desk.main(argv + ["--json"]) == 0
    assert "whose_changed" not in json.loads(capsys.readouterr().out)
    assert not ab.saved_note_due(ab.load(str(tmp_path / "sd")), addr)     # unknown is never "removed"


def test_the_skill_relays_the_note_on_any_path():
    sk = _skill()
    for needle in ("say it once, on any path", "`whose_changed.say`", "\"Find my leaks on 0x…\" button",
                   "that sentence is the first thing you say, word for word, before the score"):
        assert needle in sk, needle
