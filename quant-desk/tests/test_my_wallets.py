#!/usr/bin/env python3
"""Whose wallet it is (External Wallets R1): "my wallets" = the reader's CONNECTED wallets (proved with
a signature, read from user_get_me) plus their Senpi strategy wallets. A typed "that one's mine" is this
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
    user = {"wallets": [], "connected_wallets_status": status}
    if status == "ok":
        user["connected_wallets"] = [{"address": a.upper().replace("0X", "0x"), "label": l,
                                      "verified_at": "2026-10-01T15:31:02.000Z", "access": ACCESS}
                                     for a, l in wallets]
    return {"user": user}


def test_my_wallets_lists_connected_then_senpi():
    mw = desk.my_wallets(FakeMCP(_me(), [{"strategyWalletAddress": STRAT, "strategyName": "aegis",
                                          "status": "CLOSED"}]))
    assert mw["connected_wallets_status"] == "ok"
    assert mw["connected_wallets"] == [{"address": CONN, "label": "MetaMask",
                                        "verified_at": "2026-10-01T15:31:02.000Z", "access": ACCESS}]
    assert mw["senpi_wallets_status"] == "ok"
    assert mw["senpi_wallets"] == [{"address": STRAT, "name": "aegis", "status": "CLOSED"}]


def test_an_older_mcp_is_unavailable_never_none():
    mw = desk.my_wallets(FakeMCP({"user": {"wallets": []}}, []))
    assert mw["connected_wallets_status"] == "unavailable" and mw["connected_wallets"] is None


def test_failed_reads_are_unavailable_each_on_its_own():
    mw = desk.my_wallets(FakeMCP(_me(), [], fail=("strategy_list",)))
    assert mw["connected_wallets_status"] == "ok"
    assert mw["senpi_wallets_status"] == "unavailable" and mw["senpi_wallets"] is None
    mw = desk.my_wallets(FakeMCP(_me(), [], fail=("user_get_me",)))
    assert mw["connected_wallets_status"] == "unavailable" and mw["senpi_wallets_status"] == "ok"


def test_no_token_means_both_unknown():
    mw = desk.my_wallets(None)
    assert mw["connected_wallets"] is None and mw["senpi_wallets"] is None and "token" in mw["error"]


def test_a_connected_wallet_is_mine_even_if_once_read_as_a_strangers(tmp_path):
    book = ab.load(str(tmp_path))
    ab.record(book, CONN, relationship=ab.ANALYZED)
    assert desk.resolve_whose(book, CONN) == "other"
    assert desk.resolve_whose(book, CONN, connected=[CONN.upper().replace("0X", "0x")]) == "mine"
    assert desk.resolve_whose(book, CONN, other=True, connected=[CONN]) == "other"   # an explicit flag wins


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


def test_the_my_wallets_flag_prints_the_json(tmp_path, capsys):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"user_get_me": {"success": True, "data": _me()},
                              "strategy_list": {"success": True, "data": {"strategies": []}}}))
    assert desk.main(["--my-wallets", "--fixture", str(fx), "--state-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["connected_wallets"][0]["access"] == ACCESS and out["senpi_wallets"] == []
