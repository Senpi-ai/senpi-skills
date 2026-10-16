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
    assert mw["senpi_wallets"] == [{"address": STRAT, "name": "aegis", "status": "CLOSED", "skill_name": None}]


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


def test_the_skill_resolves_mine_from_one_list_of_saved_and_senpi_wallets():
    sk = _skill()
    for needle in ("desk.py --my-wallets", "largest first", "it isn't saved",
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


# ── Every wallet first-class (R1 review §02/§04): one list, by value, kind as a column ──────────────
SAVED_B = "0x" + "c3" * 20
SAVED_C = "0x" + "a1" * 20
STRAT_B = "0x" + "f6" * 20


def _portfolio(value):
    """Hyperliquid's `portfolio` reply: [window, {accountValueHistory: [[t, "v"], …]}], day first."""
    return [["day", {"accountValueHistory": [[1, "1.0"], [2, str(value)]], "pnlHistory": [], "vlm": "0"}],
            ["week", {"accountValueHistory": [[0, "9.0"], [2, str(value)]], "pnlHistory": [], "vlm": "0"}]]


def _state(total, unpriced=(), error=None):
    if error:
        return {"readAt": "2026-10-07T18:00:00Z", "readError": error, "totalValueUsd": None,
                "unpricedCoins": None, "positions": None}
    return {"readAt": "2026-10-07T18:00:00Z", "readError": None, "totalValueUsd": total,
            "unpricedCoins": list(unpriced), "positions": []}


class ValuedMCP(FakeMCP):
    def __init__(self, me=None, strategies=None, fail=(), states=None):
        super().__init__(me, strategies, fail)
        self.states, self.calls = states, []

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append(tool)
        if tool == "account_get_external_wallets":
            if tool in self.fail:
                raise RuntimeError(f"{tool} HTTP 503")
            assert "address" not in kw, "one call for every saved wallet, not one per wallet"
            return {"success": True, "data": {"external_wallets": [
                {"address": a, "label": None, "added_at": None, "access": ACCESS, "state": s}
                for a, s in (self.states or {}).items()]}}
        return super().mcp_call(tool, timeout=timeout, **kw)


def _hl(values):
    """An HLFixture whose `portfolio` reads answer `values` {addr: value}; an address left out FAILS."""
    from hl_api import HLFixture
    return HLFixture({f"hl::portfolio::{a}": _portfolio(v) for a, v in values.items()}, now_ms=1)


def _mixed(states=None, hl_values=None, fail=()):
    me = _me(wallets=((CONN, "MetaMask"), (SAVED_B, "Ledger")))
    strategies = [{"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE"},
                  {"strategyWalletAddress": STRAT_B, "strategyName": "Phalanx", "status": "CLOSED"}]
    mcp = ValuedMCP(me, strategies, fail=fail,
                    states={CONN: _state("500.50"), SAVED_B: _state("12000")} if states is None else states)
    hl = _hl({STRAT: 3000.25, STRAT_B: 0.0} if hl_values is None else hl_values)
    return desk.my_wallets_listed(mcp, hl), mcp, hl


def test_one_list_ordered_by_value_never_by_origin():
    mw, _, _ = _mixed()
    rows = mw["wallets"]
    assert [r["address"] for r in rows] == [SAVED_B, STRAT, CONN, STRAT_B]   # 12,000 · 3,000 · 500 · 0
    assert [r["kind"] for r in rows] == ["saved", "strategy", "saved", "strategy"]
    assert [r["value_usd"] for r in rows] == [12000.0, 3000.25, 500.5, 0.0]
    assert all(r["value_status"] == "ok" for r in rows)
    assert mw["order"] == "value_desc" and mw["wallets_complete"] is True


def test_each_row_carries_its_kind_and_a_closed_strategy_stays_labeled_closed():
    rows = {r["address"]: r for r in _mixed()[0]["wallets"]}
    assert rows[SAVED_B]["kind_label"] == "read-only — you added it" and rows[SAVED_B]["label"] == "Ledger"
    assert rows[SAVED_B]["access"] == ACCESS and "access" not in rows[STRAT]
    assert rows[STRAT]["kind_label"] == "Senpi strategy" and rows[STRAT]["label"] == "Aegis"
    assert rows[STRAT_B]["closed"] is True and rows[STRAT_B]["status"] == "CLOSED"
    assert rows[STRAT_B]["kind_label"] == "Senpi strategy — closed"
    assert rows[STRAT]["closed"] is False and rows[SAVED_B]["closed"] is False
    assert rows[SAVED_B]["value_source"] == "account_get_external_wallets state.totalValueUsd"
    assert rows[STRAT]["value_source"] == "Hyperliquid portfolio (account value)"


def test_a_wallet_that_could_not_load_sorts_last_and_is_never_zero():
    mw, _, _ = _mixed(states={CONN: None, SAVED_B: _state(None, error="hyperliquid timeout")},
                      hl_values={STRAT_B: 0.0})                                 # STRAT's HL read fails
    rows = mw["wallets"]
    assert rows[0]["address"] == STRAT_B and rows[0]["value_usd"] == 0.0     # a real $0 is a value
    unknown = rows[1:]
    assert {r["address"] for r in unknown} == {CONN, SAVED_B, STRAT}
    assert all(r["value_usd"] is None and r["value_status"] == "couldnt_load" for r in unknown)
    # ties among the unknown: by label, then address — deterministic, never arbitrary
    assert [r["label"] for r in unknown] == ["Aegis", "Ledger", "MetaMask"]
    assert "couldn't load" in mw["text"] and "$0" in mw["text"].split("couldn't load")[0]


def test_ties_break_by_label_then_address():
    mw, _, _ = _mixed(states={CONN: _state("100"), SAVED_B: _state("100")}, hl_values={STRAT: 100, STRAT_B: 100})
    assert [r["label"] for r in mw["wallets"]] == ["Aegis", "Ledger", "MetaMask", "Phalanx"]


def test_a_failed_state_read_keeps_the_saved_wallets_listed_with_unknown_values():
    mw, _, _ = _mixed(fail=("account_get_external_wallets",))
    assert mw["external_wallets_status"] == "ok"
    saved = [r for r in mw["wallets"] if r["kind"] == "saved"]
    assert len(saved) == 2 and all(r["value_usd"] is None for r in saved)
    assert [r["kind"] for r in mw["wallets"]] == ["strategy", "strategy", "saved", "saved"]


def test_unpriced_coins_are_carried_and_the_text_says_the_value_excludes_them():
    mw, _, _ = _mixed(states={CONN: _state("500.50", unpriced=("PURR", "HFUN")), SAVED_B: _state("12000")})
    row = next(r for r in mw["wallets"] if r["address"] == CONN)
    assert row["unpriced_coins"] == ["PURR", "HFUN"] and row["value_usd"] == 500.5
    assert "MetaMask's value excludes PURR, HFUN" in mw["text"]


def test_an_unavailable_source_is_visible_and_never_reads_as_no_wallets():
    mw, _, _ = _mixed(fail=("user_get_me",))
    assert mw["external_wallets_status"] == "unavailable" and mw["wallets_complete"] is False
    assert [r["kind"] for r in mw["wallets"]] == ["strategy", "strategy"]
    assert "I couldn't load your saved wallets" in mw["text"]
    mw, _, _ = _mixed(fail=("strategy_list",))
    assert mw["senpi_wallets_status"] == "unavailable" and mw["wallets_complete"] is False
    assert "I couldn't load your Senpi strategy wallets" in mw["text"]
    both = desk.my_wallets_listed(None, None)
    assert both["wallets"] is None and both["wallets_complete"] is False   # unknown, never []
    for mw in (_mixed(fail=("user_get_me",))[0], both):
        assert not re_search(r"(?i)no (saved )?wallets", mw["text"]), mw["text"]


def re_search(p, s):
    import re
    return re.search(p, s)


def test_the_value_reads_are_one_mcp_call_and_one_hl_read_per_strategy_wallet():
    mw, mcp, _ = _mixed()
    assert mcp.calls.count("account_get_external_wallets") == 1
    # no saved wallet → no state read at all
    mcp2 = ValuedMCP(_me(wallets=()), [{"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE"}])
    desk.my_wallets_listed(mcp2, _hl({STRAT: 1}))
    assert "account_get_external_wallets" not in mcp2.calls


def test_the_portfolio_read_is_the_same_request_the_desk_makes_so_the_run_after_reuses_it():
    """The list primes the desk's own cache: `HL.trader` asks for {"type": "portfolio", "user": addr},
    and so does the list — same body, same cache key (TTL 600 s)."""
    import hl_api

    class Spy(hl_api.HL):
        def __init__(self):
            super().__init__(cache_dir=None, now_ms=1)
            self.bodies = []

        def info(self, body):
            self.bodies.append(body)
            return _portfolio(7)
    s = Spy()
    assert s.portfolios([STRAT, STRAT_B]) == {STRAT: _portfolio(7), STRAT_B: _portfolio(7)}
    assert sorted(b["user"] for b in s.bodies) == sorted([STRAT, STRAT_B])
    assert all(b == {"type": "portfolio", "user": b["user"]} for b in s.bodies)


def test_current_account_value_reads_zero_as_a_value_and_no_series_as_unknown():
    from metrics import current_account_value
    assert current_account_value(_portfolio(0)) == 0.0
    assert current_account_value(_portfolio("1234.5")) == 1234.5
    assert current_account_value([["day", {"accountValueHistory": []}], ["allTime", {"accountValueHistory": [[1, "5"]]}]]) == 5.0
    for bad in (None, [], [["day", {"accountValueHistory": []}]], {"oops": 1}, [["day", None]], [["day", {"accountValueHistory": [[1, "x"]]}]]):
        assert current_account_value(bad) is None, bad


def test_the_question_names_the_wallets_largest_first():
    mw, _, _ = _mixed()
    ask = mw["ask"]
    assert ask.startswith("Which one do you want me to run the desk on")
    order = [ask.index(n) for n in ("Ledger", "Aegis", "MetaMask", "Phalanx")]
    assert order == sorted(order), ask
    assert "($12,000)" in ask and "($0, closed)" in ask
    one = desk.my_wallets_listed(ValuedMCP(_me(), []), _hl({}))
    assert one["ask"] is None                                       # one wallet: nothing to choose


def test_the_text_is_one_table_in_the_engine_order_with_a_kind_column():
    mw, _, _ = _mixed()
    t = mw["text"]
    assert "| # | Wallet | Kind | Value |" in t
    assert t.index("Ledger") < t.index("Aegis") < t.index("MetaMask") < t.index("Phalanx")
    assert "read-only — you added it" in t and "Senpi strategy — closed" in t
    assert ACCESS in t
    assert "Saved wallets" not in t and "Strategy wallets" not in t       # no section by origin


def test_the_my_wallets_flag_prints_the_ordered_list_with_values(tmp_path, capsys):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "user_get_me": {"success": True, "data": _me(wallets=((CONN, "MetaMask"),))},
        "strategy_list": {"success": True, "data": {"strategies": [
            {"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE"}]}},
        "account_get_external_wallets": {"success": True, "data": {"external_wallets": [
            {"address": CONN, "label": "MetaMask", "access": ACCESS, "state": _state("50")}]}},
        f"hl::portfolio::{STRAT}": _portfolio(900)}))
    assert desk.main(["--my-wallets", "--fixture", str(fx), "--state-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [(r["label"], r["value_usd"]) for r in out["wallets"]] == [("Aegis", 900.0), ("MetaMask", 50.0)]
    assert out["text"].index("Aegis") < out["text"].index("MetaMask")
    assert desk.main(["--my-wallets", "--json", "--fixture", str(fx), "--state-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["wallets"] == out["wallets"]


def test_the_listed_text_uses_none_of_the_retired_words():
    import re
    for mw in (_mixed()[0], _mixed(fail=("user_get_me",))[0], desk.my_wallets_listed(None, None),
               desk.my_wallets_listed(ValuedMCP(_me(wallets=()), []), _hl({}))):
        s = json.dumps(mw)
        assert not re.search(r"(?i)connect(?!ion)", s), s
        assert not re.search(r"(?i)(?<![a-z])verified|you own|proven", s), s


def test_no_wallets_at_all_points_at_your_wallets():
    mw = desk.my_wallets_listed(ValuedMCP(_me(wallets=()), []), _hl({}))
    assert mw["wallets"] == [] and mw["wallets_complete"] is True
    assert "add it in Your wallets on senpi.ai (web)" in mw["text"]


def test_the_skill_orders_by_value_and_never_by_origin():
    sk = _skill()
    assert "saved wallets first" not in sk.lower()
    assert "then strategy wallets" not in sk.lower()
    for needle in ("largest first", "`wallets`", "keep that order", "never split it by origin",
                   "`ask`", "`value_status: \"couldnt_load\"`", "never as $0"):
        assert needle in sk, needle


def test_skill_names_the_kind_fields_the_script_emits():
    """`kind` is the machine value (`saved` / `strategy`); the words a reader sees are `kind_label` —
    SKILL.md must not tell the agent that `kind` carries the label text (R1 re-review)."""
    render = desk.render
    sk = _skill()
    assert "its `kind` (read-only — you added it / Senpi strategy)" not in sk
    assert "`kind` (`saved` / `strategy` / `main`)" in sk
    assert "`kind_label`" in sk
    for v in render.KIND_LABEL.values():
        assert v in sk, v
    assert {desk.KIND_SAVED, desk.KIND_STRATEGY, desk.KIND_MAIN} == {"saved", "strategy", "main"}


# ── one strategy unit: a Senpi strategy is one row with all its wallets (senpi-portfolio's key) ─────
CAMEL_P, CAMEL_H = "0x" + "b1" * 20, "0x" + "b2" * 20


def _camel(p_status="ACTIVE", h_status="ACTIVE", hl_values=None, stamped=True):
    meta = {"strategyMetadata": {"skillName": "camel"}} if stamped else {}
    strategies = [{"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE"},
                  dict({"strategyWalletAddress": CAMEL_P, "strategyName": "camel-concentrated-payout",
                        "tradingStrategyName": "camel", "status": p_status}, **meta),
                  dict({"strategyWalletAddress": CAMEL_H, "strategyName": "camel-harvest",
                        "tradingStrategyName": "camel", "status": h_status}, **meta)]
    mcp = ValuedMCP(_me(wallets=((CONN, "MetaMask"),)), strategies, states={CONN: _state("500.50")})
    hl = _hl({STRAT: 3000.25, CAMEL_P: 600.0, CAMEL_H: 400.5} if hl_values is None else hl_values)
    return desk.my_wallets_listed(mcp, hl)


def _row(mw, label):
    return next(r for r in mw["wallets"] if r["label"] == label)


def test_my_wallets_carries_the_package_each_strategy_wallet_was_deployed_under():
    mw = desk.my_wallets(FakeMCP(_me(), [
        {"strategyWalletAddress": CAMEL_P, "strategyName": "camel-harvest", "status": "ACTIVE",
         "strategyMetadata": {"skillName": "camel"}},
        {"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE", "skillName": "aegis"}]))
    assert [w["skill_name"] for w in mw["senpi_wallets"]] == ["camel", "aegis"]


def test_a_package_deployed_as_two_instances_is_one_row_with_both_wallets():
    mw = _camel()
    assert [r["label"] for r in mw["wallets"]] == ["Aegis", "camel", "MetaMask"]   # 3000 · 1000.50 · 500.50
    row = _row(mw, "camel")
    assert row["kind"] == "strategy" and row["strategy_group"] == "camel"
    assert row["address"] is None                            # a strategy of several wallets has no one address
    assert row["value_usd"] == 1000.5 and row["value_status"] == "ok"
    assert row["wallet_count"] == 2 and row["wallets_loaded"] == 2
    assert [(w["address"], w["label"], w["value_usd"]) for w in row["wallets"]] == [
        (CAMEL_P, "camel-concentrated-payout", 600.0), (CAMEL_H, "camel-harvest", 400.5)]
    assert row["run"] == f"--book {CAMEL_P} {CAMEL_H}"
    assert row["closed"] is False and row["kind_label"] == "Senpi strategy"


def test_a_single_wallet_strategy_runs_the_plain_desk_and_a_saved_wallet_stays_one_row():
    mw = _camel()
    aegis, saved = _row(mw, "Aegis"), _row(mw, "MetaMask")
    assert aegis["address"] == STRAT and aegis["wallet_count"] == 1 and aegis["run"] == STRAT
    assert [w["address"] for w in aegis["wallets"]] == [STRAT]
    assert saved["address"] == CONN and saved["run"] == CONN and "wallet_count" not in saved


def test_the_book_run_on_a_strategy_row_is_a_form_the_desk_accepts():
    import shlex
    row = _row(_camel(), "camel")
    args = desk.argparse.ArgumentParser(add_help=False)
    args.add_argument("--book", nargs="+")
    assert args.parse_args(shlex.split(row["run"])).book == [CAMEL_P, CAMEL_H]


def test_a_strategy_with_one_wallet_unread_says_one_of_two_and_never_counts_it_as_zero():
    mw = _camel(hl_values={STRAT: 3000.25, CAMEL_P: 600.0})            # camel-harvest's HL read fails
    row = _row(mw, "camel")
    assert row["value_usd"] == 600.0 and row["value_status"] == "partial" and row["wallets_loaded"] == 1
    assert [w["value_usd"] for w in row["wallets"]] == [600.0, None]
    assert "$600 (1 of 2 wallets)" in mw["text"]
    assert "camel ($600, 1 of 2 wallets)" in mw["ask"]


def test_a_strategy_with_no_wallet_read_is_couldnt_load_and_sorts_last():
    mw = _camel(hl_values={STRAT: 3000.25})
    row = mw["wallets"][-1]
    assert row["label"] == "camel" and row["value_usd"] is None and row["value_status"] == "couldnt_load"


def test_closed_is_all_its_wallets_closed_and_mixed_says_so():
    mw = _camel(p_status="CLOSED", h_status="CLOSED", hl_values={STRAT: 1, CAMEL_P: 0, CAMEL_H: 0})
    row = _row(mw, "camel")
    assert row["closed"] is True and row["kind_label"] == "Senpi strategy — closed"
    mw = _camel(h_status="CLOSED")
    row = _row(mw, "camel")
    assert row["closed"] is False and row["closed_wallets"] == 1
    assert row["kind_label"] == "Senpi strategy — some wallets closed"
    assert "camel ($1,000, 1 of 2 wallets closed)" in mw["ask"]


def test_unstamped_lookalike_wallets_stay_their_own_rows():
    mw = _camel(stamped=False)
    assert [r["label"] for r in mw["wallets"]] == ["Aegis", "camel-concentrated-payout", "MetaMask",
                                                   "camel-harvest"]
    assert all(r.get("wallet_count") == 1 for r in mw["wallets"] if r["kind"] == "strategy")


def test_the_text_shows_a_strategy_row_by_its_wallet_count():
    t = _camel()["text"]
    assert "| camel (2 wallets) | Senpi strategy | $1,000 |" in t
    assert t.index("Aegis") < t.index("camel (2 wallets)") < t.index("MetaMask")


def test_the_ask_lists_strategies_largest_first_never_their_instances():
    ask = _camel()["ask"]
    assert ask.index("Aegis") < ask.index("camel") < ask.index("MetaMask")
    assert "camel-harvest" not in ask and "camel-concentrated-payout" not in ask


def test_skill_says_a_strategy_is_one_row_and_how_to_run_it():
    sk = _skill()
    for needle in ("**A Senpi strategy is one row with all its wallets.**", "`run`", "`wallets[]`",
                   "`value_status: \"partial\"`", "(1 of 2 wallets)", "some wallets closed"):
        assert needle in sk, needle


def test_a_packaged_one_wallet_strategy_is_called_by_its_package_like_portfolio():
    mcp = ValuedMCP(_me(wallets=()), [{"strategyWalletAddress": STRAT, "strategyName": "cub-main",
                                       "status": "ACTIVE", "strategyMetadata": {"skillName": "cub"}}])
    row = desk.my_wallets_listed(mcp, _hl({STRAT: 10}))["wallets"][0]
    assert row["label"] == "cub" and row["wallets"][0]["label"] == "cub-main" and row["run"] == STRAT


# ── the Senpi main wallet is a row too (R1 dev E2E round 2, A3) ─────────────────────────────────────
MAIN = "0x" + "e1" * 20
PORTFOLIO_REPLY = {"success": True, "data": {"portfolio": {
    "total_in_hyperliquid": "120.50", "total_spot_usd_in_hyperliquid": "39.90",
    "token_balances": [{"symbol": "USDC", "balanceInUSD": "0.05"}, {"symbol": "HYPE", "balanceInUSD": "999"}]}}}


class MainMCP(ValuedMCP):
    def __init__(self, *a, portfolio=PORTFOLIO_REPLY, **kw):
        super().__init__(*a, **kw)
        self.portfolio, self.portfolio_kw = portfolio, []

    def mcp_call(self, tool, timeout=12, **kw):
        if tool == "account_get_portfolio":
            self.calls.append(tool)
            self.portfolio_kw.append(kw)
            if isinstance(self.portfolio, Exception):
                raise self.portfolio
            return self.portfolio
        return super().mcp_call(tool, timeout=timeout, **kw)


def _with_main(portfolio=PORTFOLIO_REPLY, fail=()):
    me = _me(wallets=((CONN, "MetaMask"),))
    me["user"]["wallets"] = [{"walletType": "embedded", "walletAddress": MAIN.upper().replace("0X", "0x")}]
    mcp = MainMCP(me, [{"strategyWalletAddress": STRAT, "strategyName": "Aegis", "status": "ACTIVE"}],
                  fail=fail, states={CONN: _state("500.50")}, portfolio=portfolio)
    return desk.my_wallets_listed(mcp, _hl({STRAT: 100.0})), mcp


def test_the_main_wallet_is_a_row_valued_by_its_idle_cash_and_sorted_by_value():
    mw, mcp = _with_main()
    rows = mw["wallets"]
    assert [(r["kind"], r["value_usd"]) for r in rows] == [("saved", 500.5), ("main", 160.45), ("strategy", 100.0)]
    m = rows[1]
    assert m["label"] == "Senpi main wallet" and m["address"] == MAIN and m["run"] == MAIN
    assert m["kind_label"] == "Senpi main wallet — idle cash" and m["value_status"] == "ok"
    # the read senpi-portfolio makes: one account_get_portfolio, forceFetch
    assert mcp.portfolio_kw == [{"forceFetch": True, "strategyStatus": "ALL"}]
    assert "Senpi main wallet `0xe1e1…e1e1` | Senpi main wallet — idle cash | $160" in mw["text"]
    assert "Senpi main wallet ($160" in mw["ask"]


def test_the_main_wallet_value_is_the_one_senpi_portfolio_reads():
    import importlib.util
    path = HERE.parent.parent / "senpi-portfolio" / "scripts" / "portfolio.py"
    spec = importlib.util.spec_from_file_location("portfolio_for_quant_desk", path)
    pf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pf)

    class C:
        def mcp_call(self, tool, timeout=12, **kw):
            return PORTFOLIO_REPLY
    emb, _ = pf.fetch_embedded(C(), {}, me={"user": {"wallets": [{"walletType": "embedded",
                                                                   "walletAddress": MAIN}]}})
    mw, _ = _with_main()
    assert emb["idle_total"] == next(r for r in mw["wallets"] if r["kind"] == "main")["value_usd"]


def test_a_failed_main_wallet_read_is_couldnt_load_never_zero_and_still_runnable():
    for bad in ({"success": False, "error": "UNAVAILABLE"}, RuntimeError("HTTP 503")):
        mw, _ = _with_main(portfolio=bad)
        m = mw["wallets"][-1]
        assert m["kind"] == "main" and m["value_usd"] is None and m["value_status"] == "couldnt_load", bad
        assert m["run"] == MAIN


def test_no_main_wallet_named_adds_no_row_and_an_unread_user_get_me_says_so():
    mw, _, _ = _mixed()
    assert all(r["kind"] != "main" for r in mw["wallets"])
    assert mw["main_wallet_status"] == "ok" and mw["main_wallet"] is None
    mw, _, _ = _mixed(fail=("user_get_me",))
    assert mw["main_wallet_status"] == "unavailable"
    assert "I couldn't load your Senpi main wallet" in mw["text"]


def test_the_skill_offers_the_choices_in_plain_words_never_flags():
    sk = _skill()
    assert "Never show the user a flag or a command" in sk
    assert "Offer to run them together (`--book`) or side by side (`--compare`)" not in sk
