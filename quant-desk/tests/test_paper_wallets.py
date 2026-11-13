#!/usr/bin/env python3
"""A simulation's wallet is never sent to Hyperliquid.

The desk reads any address straight from Hyperliquid's public `/info`. A simulation trades on the
simulated venue, so its wallet has no Hyperliquid book: `hl_api` refuses it before any request leaves,
the desk refuses it up front with a clear answer, and the reader's own wallet list leaves out a row
marked `paper: true`.

    python3 -m pytest quant-desk/tests/test_paper_wallets.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import desk  # noqa: E402
import hl_api  # noqa: E402

PAPER_WALLET = "0x0000000000000000" + "1762500000000" + "0a1b2c3d4e5"
LIVE_WALLET = "0x" + "ab" * 20


def test_hl_info_refuses_a_paper_address_before_any_request(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(hl_api.urllib.request, "urlopen", lambda *a, **k: sent.append(a))
    hl = hl_api.HL(cache_dir=None)
    for body in ({"type": "clearinghouseState", "user": PAPER_WALLET},
                 {"type": "userFillsByTime", "user": PAPER_WALLET.upper().replace("0X", "0x"), "startTime": 0},
                 {"type": "vaultDetails", "vaultAddress": PAPER_WALLET}):
        with pytest.raises(hl_api.PaperWalletRefused):
            hl.info(body)
    with pytest.raises(hl_api.PaperWalletRefused):
        hl.trader(PAPER_WALLET)
    assert sent == []
    assert hl.calls == 0


def test_the_zero_address_is_live_not_a_simulation():
    zero = "0x" + "0" * 40
    for value in (zero, zero.upper().replace("0X", "0x")):
        assert not hl_api.is_paper_address(value)
        assert desk._simulation_refusal([value]) is None
    assert hl_api.HLFixture({f"hl::clearinghouseState::{zero}": {"ok": 1}}).info(
        {"type": "clearinghouseState", "user": zero}) is not None


def test_the_fixture_client_refuses_too():
    with pytest.raises(hl_api.PaperWalletRefused):
        hl_api.HLFixture({f"hl::clearinghouseState::{PAPER_WALLET}": {}}).info(
            {"type": "clearinghouseState", "user": PAPER_WALLET})


def _desk(argv, monkeypatch, tmp_path):
    monkeypatch.setattr(hl_api.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = desk.main([*argv, "--state-dir", str(tmp_path)])
    return rc, json.loads(buf.getvalue())


@pytest.mark.parametrize("argv", [[PAPER_WALLET], ["--book", LIVE_WALLET, PAPER_WALLET],
                                  ["--compare", LIVE_WALLET, PAPER_WALLET]])
def test_the_desk_refuses_a_simulation_wallet_up_front(argv, monkeypatch, tmp_path):
    rc, out = _desk(argv, monkeypatch, tmp_path)
    assert rc == 2
    assert out["simulation_wallet"] == PAPER_WALLET
    assert "simulation" in out["error"]


def test_my_wallets_leaves_out_simulations():
    class _MCP:
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "user_get_me":
                return {"success": True, "data": {"wallets": [], "external_wallets_status": "ok",
                                                  "external_wallets": []}}
            assert "paper" not in kw
            return {"success": True, "data": {"strategies": [
                {"strategyWalletAddress": LIVE_WALLET, "status": "ACTIVE"},
                {"strategyWalletAddress": PAPER_WALLET, "status": "ACTIVE", "paper": True}]}}
    out = desk.my_wallets(_MCP())
    assert [w["address"] for w in out["senpi_wallets"]] == [LIVE_WALLET]
