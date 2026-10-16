#!/usr/bin/env python3
"""Saved wallets ("Your wallets") in the portfolio read (External Wallets R1, amendment A1).

A saved wallet is one the user added in Your wallets by pasting its address — their claim, not proof of
control — and trades by hand. Senpi can read it and cannot
place, change or cancel orders on it. These tests hold the four things that make that safe to narrate:
it never enters the Senpi money map (idle / grand_total / reconciles stay byte-identical), unknown is
never empty (an absent key or a failed read is "unavailable", never []), its state is quoted verbatim
from `account_get_external_wallets` (the single producer — `protection`, never `protected`), and a user
with only saved wallets gets a read, not a fault and not a strategy pitch.

    python3 -m pytest senpi-portfolio/tests/test_portfolio_external_wallets.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "portfolio_fixture.json")
SKILL = os.path.join(HERE, "..", "SKILL.md")
ACCESS = "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it."
CW_A = "0x" + "a1" * 20
CW_B = "0x" + "b2" * 20
EMBED = "0xembed00000000000000000000000000000000ed"
RUNTIMES_FIXTURE = os.path.join(HERE, "fixtures", "runtimes_list.json")

# Hermetic: the engine shells out to `openclaw senpi runtime list/status`. Serve the runtime list from the
# recorded fixture and empty PATH for this module only (restored after), so a host that has the CLI
# never answers these tests — same pattern as test_portfolio.py.
_SAVED = {}


def setup_module(module):
    _SAVED.update({k: os.environ.get(k) for k in ("PATH", "SENPI_RUNTIMES_FIXTURE", "SENPI_STATUS_FIXTURE")})
    os.environ["PATH"] = tempfile.mkdtemp(prefix="senpi-portfolio-cw-empty-bin-")
    os.environ["SENPI_RUNTIMES_FIXTURE"] = RUNTIMES_FIXTURE
    os.environ.pop("SENPI_STATUS_FIXTURE", None)


def teardown_module(module):
    for k, v in _SAVED.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _base():
    with open(FIXTURE) as f:
        return json.load(f)


def _state_ok(total="5234.10", mode="unifiedAccount", positions=None, unpriced=None):
    return {"readAt": "2026-10-03T12:00:00Z", "readError": None, "role": "USER", "accountMode": mode,
            "accountValueUsd": "4100.00",
            "spotBalances": [{"coin": "USDC", "total": "1500.0", "hold": "400.0", "usdValue": "1500.00"}],
            "totalValueUsd": total, "unpricedCoins": unpriced if unpriced is not None else [],
            "positions": positions if positions is not None else [{
                "coin": "BTC", "dex": "", "side": "LONG", "size": "0.05", "entryPx": "62000.0",
                "markPx": "64000.0", "positionValueUsd": "3200.00", "unrealizedPnlUsd": "100.00",
                "leverage": 5, "marginType": "cross", "liquidationPx": "51000.0",
                "stopOrders": [{"oid": "564213649294", "cloid": None, "triggerPx": "60000.0",
                                "kind": "STOP_MARKET", "status": "ARMED", "isPositionTpsl": True,
                                "size": "0.0"}],
                "coveredSize": "0.05", "protection": "FULL"}],
            "openOrders": []}


def _state_error(code="PERPS_UNAVAILABLE"):
    """L10: a readError nulls every field except readAt / readError / role / accountMode."""
    return {"readAt": "2026-10-03T12:00:00Z", "readError": code, "role": "USER", "accountMode": None,
            "accountValueUsd": None, "spotBalances": None, "totalValueUsd": None, "unpricedCoins": None,
            "positions": None, "openOrders": None}


def _with_external(fixture, wallets, states=None, status="ok"):
    """Turn the canonical fixture's flat user_get_me into the MCP's real `{user: {...}}` shape, with
    saved wallets inside `user` (C3), and record `account_get_external_wallets`."""
    fx = copy.deepcopy(fixture)
    user = {"wallets": fx["user_get_me"]["wallets"], "external_wallets_status": status}
    if status == "ok":
        user["external_wallets"] = [{"address": a, "label": lbl, "added_at": "2026-10-01T15:31:02.000Z",
                                      "access": ACCESS} for a, lbl in wallets]
    fx["user_get_me"] = {"user": user}
    if states is not None:
        fx["account_get_external_wallets"] = {"external_wallets": [
            {"address": a, "label": lbl, "added_at": "2026-10-01T15:31:02.000Z", "access": ACCESS,
             "state": states.get(a)} for a, lbl in wallets]}
    return fx


def _external_only():
    """No Senpi strategy at all: an embedded wallet with $0 and one saved wallet."""
    return _with_external({
        "user_get_me": {"wallets": [{"walletType": "embedded", "walletAddress": EMBED}]},
        "account_get_portfolio": {"total_balance_usd": 0, "total_withdrawable": 0, "total_in_hyperliquid": 0,
                                  "token_balances": []},
        "strategy_list": {"strategies": []},
    }, [(CW_A, "Main")], {CW_A: _state_ok()})


def _run(fx):
    return portfolio.run(portfolio._FixtureClient(fx), want_market=False)


def _dump(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


# ── never in the Senpi money map ────────────────────────────────────────────────────────────────────
def test_external_wallets_never_enter_the_totals_or_the_reconciliation():
    base = _run(_base())
    mixed = _run(_with_external(_base(), [(CW_A, "Main"), (CW_B, None)],
                                {CW_A: _state_ok(total="99999.99"), CW_B: _state_ok(total="12345.67")}))
    for key in ("totals", "exposure", "signals", "strategy_groups", "strategies", "embedded_wallet"):
        assert _dump(mixed[key]) == _dump(base[key]), key
    assert mixed["totals"]["reconciles"] == base["totals"]["reconciles"]


def test_the_money_step_keeps_the_same_buckets_and_adds_the_section():
    sp = os.path.join(tempfile.mkdtemp(), "state.json")
    base = portfolio.step_money(portfolio._FixtureClient(_base()), state_path=sp)
    sp2 = os.path.join(tempfile.mkdtemp(), "state.json")
    mixed = portfolio.step_money(portfolio._FixtureClient(
        _with_external(_base(), [(CW_A, "Main")], {CW_A: _state_ok()})), state_path=sp2)
    assert _dump(mixed["totals"]) == _dump(base["totals"])
    assert mixed["external_wallets"]["status"] == "ok"
    assert [w["address"] for w in mixed["external_wallets"]["wallets"]] == [CW_A]


def test_all_stays_byte_identical_to_run_with_external_wallets():
    fx = _with_external(_base(), [(CW_A, "Main")], {CW_A: _state_ok()})
    direct = portfolio.run(portfolio._FixtureClient(fx), want_market=True)
    allres = portfolio._all_and_persist(portfolio._FixtureClient(fx), want_market=True,
                                        state_path=os.path.join(tempfile.mkdtemp(), "s.json"))
    assert _dump(direct) == _dump(allres)


# ── state quoted verbatim (single producer) ─────────────────────────────────────────────────────────
def test_mixed_user_state_is_quoted_verbatim_and_protection_is_not_protected():
    st = _state_ok()
    out = _run(_with_external(_base(), [(CW_A, "Main")], {CW_A: st}))
    cw = out["external_wallets"]
    assert cw["status"] == "ok"
    w = cw["wallets"][0]
    assert w["state"] == st                                     # verbatim, camelCase, decimal strings
    assert w["state_read"] == "ok" and w["kind"] == "external"
    assert w["access"] == ACCESS
    assert w["state"]["positions"][0]["protection"] == "FULL"
    assert "protected" not in w and "protected" not in w["state"]["positions"][0]
    assert w["not_applicable"] == ["dsl", "runtime_health", "mandate", "funded_drained", "telemetry"]


def test_a_unified_account_total_is_quoted_not_recomputed():
    st = _state_ok(total="5234.10", mode="unifiedAccount")
    w = _run(_with_external(_base(), [(CW_A, "Main")], {CW_A: st}))["external_wallets"]["wallets"][0]
    assert w["state"]["accountMode"] == "unifiedAccount"
    assert w["state"]["totalValueUsd"] == "5234.10"             # moxie's computeTotals(), not ours


def test_unpriced_coins_are_carried_so_the_total_can_say_what_it_excludes():
    st = _state_ok(total="5234.10", unpriced=["STHYPE"])
    w = _run(_with_external(_base(), [(CW_A, "Main")], {CW_A: st}))["external_wallets"]["wallets"][0]
    assert w["state"]["unpricedCoins"] == ["STHYPE"] and w["state"]["totalValueUsd"] == "5234.10"


def test_a_read_error_wallet_is_couldnt_load_and_the_others_survive():
    out = _run(_with_external(_base(), [(CW_A, "Main"), (CW_B, "Cold")],
                              {CW_A: _state_error("ORDERS_UNAVAILABLE:xyz"), CW_B: _state_ok()}))
    a, b = out["external_wallets"]["wallets"]
    assert a["state_read"] == "error" and a["state"]["readError"] == "ORDERS_UNAVAILABLE:xyz"
    assert a["state"]["totalValueUsd"] is None and a["state"]["positions"] is None    # never 0 / []
    assert b["state_read"] == "ok"


def test_a_wallet_whose_state_came_back_null_is_unavailable_not_empty():
    out = _run(_with_external(_base(), [(CW_A, "Main"), (CW_B, "Cold")], {CW_A: None, CW_B: _state_ok()}))
    a, b = out["external_wallets"]["wallets"]
    assert a["state_read"] == "unavailable" and a["state"] is None
    assert b["state_read"] == "ok"


def test_a_failed_state_read_keeps_the_list_and_warns():
    fx = _with_external(_base(), [(CW_A, "Main")], states=None)    # no account_get_external_wallets
    out = _run(fx)
    w = out["external_wallets"]["wallets"][0]
    assert out["external_wallets"]["status"] == "ok"
    assert w["state_read"] == "unavailable" and w["state"] is None
    assert any("account_get_external_wallets failed" in x for x in out["meta"]["warnings"])


# ── unknown is never empty ──────────────────────────────────────────────────────────────────────────
def test_an_older_mcp_with_no_external_key_reads_unavailable_not_none():
    out = _run(_base())                                          # the canonical fixture predates the key
    assert out["external_wallets"] == {"status": "unavailable", "wallets": None}


def test_status_unavailable_reads_unavailable_not_none():
    out = _run(_with_external(_base(), [(CW_A, "Main")], status="unavailable"))
    assert out["external_wallets"] == {"status": "unavailable", "wallets": None}


def test_a_failed_user_get_me_is_unavailable():
    fx = _base()
    del fx["user_get_me"]

    class Boom(portfolio._FixtureClient):
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "user_get_me":
                raise RuntimeError("HTTP 503")
            return super().mcp_call(tool, timeout=timeout, **kw)
    out = portfolio.run(Boom(fx), want_market=False)
    assert out["external_wallets"]["status"] == "unavailable"
    assert any("user_get_me failed" in x for x in out["meta"]["warnings"])


def test_ok_with_no_wallets_is_a_real_empty_list():
    out = _run(_with_external(_base(), [], {}))
    assert out["external_wallets"] == {"status": "ok", "wallets": []}


# ── the no-strategy path ────────────────────────────────────────────────────────────────────────────
def test_external_only_user_gets_a_read_not_a_fault():
    out = _run(_external_only())
    assert out["meta"].get("no_strategy_path") is True
    assert "degraded" not in out["meta"]
    assert out["strategies"] == [] and out["strategy_groups"] == []
    assert out["totals"]["grand_total_usd"] == 0.0               # saved money is not Senpi money
    assert out["external_wallets"]["wallets"][0]["state"]["totalValueUsd"] == "5234.10"


class _Counting(portfolio._FixtureClient):
    def __init__(self, fx):
        super().__init__(fx)
        self.calls = []

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append(tool)
        return super().mcp_call(tool, timeout=timeout, **kw)


def _assert_external_only_read(out):
    assert out["meta"].get("no_strategy_path") is True
    assert "degraded" not in out["meta"]
    assert out["external_wallets"]["status"] == "ok"
    assert [w["address"] for w in out["external_wallets"]["wallets"]] == [CW_A]
    assert out["external_wallets"]["wallets"][0]["state"]["totalValueUsd"] == "5234.10"


def test_external_only_strategies_and_positions_steps_carry_the_section_from_state():
    """"Are my positions protected?" routes to the `strategies` step. For a saved-only user that step
    must carry the saved wallets + `no_strategy_path` the `money` step persisted, or the agent reads an
    empty strategy list as "no positions" and pitches a strategy."""
    sp = os.path.join(tempfile.mkdtemp(), "state.json")
    portfolio.step_money(portfolio._FixtureClient(_external_only()), state_path=sp)
    for step in (portfolio.step_strategies, portfolio.step_positions):
        client = _Counting(_external_only())
        out = step(client, want_market=False, state_path=sp)
        _assert_external_only_read(out)
        assert "account_get_external_wallets" not in client.calls, step.__name__   # reused, not re-read


def test_external_only_strategies_and_positions_steps_rebuild_the_section_standalone():
    for step in (portfolio.step_strategies, portfolio.step_positions):
        missing = os.path.join(tempfile.mkdtemp(), "absent.json")
        client = _Counting(_external_only())
        out = step(client, want_market=False, state_path=missing)
        _assert_external_only_read(out)
        assert client.calls.count("user_get_me") == 1, step.__name__      # one read shared by both readers


def test_a_state_without_the_external_section_is_rebuilt_not_read_as_empty():
    """A state file written before the section existed (strategies cached, no `external_wallets`) is
    rebuilt with the same fetch, never read as "no saved wallets"."""
    for step in (portfolio.step_strategies, portfolio.step_positions):
        sp = os.path.join(tempfile.mkdtemp(), "state.json")
        portfolio.step_money(portfolio._FixtureClient(_external_only()), state_path=sp)
        st = json.load(open(sp))
        st.pop("external_wallets")
        st["strategies_full"] = []
        json.dump(st, open(sp, "w"))
        _assert_external_only_read(step(portfolio._FixtureClient(_external_only()),
                                         want_market=False, state_path=sp))


def test_the_steps_pass_the_section_through_for_a_user_with_strategies():
    fx = _with_external(_base(), [(CW_A, "Main")], {CW_A: _state_ok()})
    sp = os.path.join(tempfile.mkdtemp(), "state.json")
    portfolio.step_money(portfolio._FixtureClient(fx), state_path=sp)
    for step in (portfolio.step_strategies, portfolio.step_positions):
        out = step(portfolio._FixtureClient(fx), want_market=False, state_path=sp)
        assert out["external_wallets"]["wallets"][0]["address"] == CW_A
        assert "no_strategy_path" not in out["meta"]


def test_a_user_with_strategies_is_not_on_the_no_strategy_path():
    out = _run(_with_external(_base(), [(CW_A, "Main")], {CW_A: _state_ok()}))
    assert "no_strategy_path" not in out["meta"]


# ── the narration rules (SKILL.md) ──────────────────────────────────────────────────────────────────
def _skill():
    return " ".join(open(SKILL, encoding="utf-8").read().split())


def test_skill_quotes_the_access_line_verbatim():
    assert ACCESS in _skill()


def test_skill_keeps_external_wallets_out_of_idle_and_the_total():
    sk = _skill()
    for needle in ("## One wallet list — every wallet first-class", "never in `grand_total_usd`",
                   "never idle", "`external_wallets.status: \"unavailable\"`",
                   "I couldn't load your saved wallets", "`protection`", "not `protected`",
                   "no Hyperliquid activity yet", "Not applicable, not a fault",
                   "never call `accountValueUsd` \"account value\"", "total excludes",
                   "`state: null`", "couldn't load this wallet", "never print the code",
                   "add it in Your wallets on senpi.ai (web)", "no open positions on the Hyperliquid main and xyz dexes",
                   "never a bare \"no positions\"", "balances, positions and protection are all unknown",
                   "A non-zero `totalValueUsd` wins — quote the value",
                   "never imply Senpi checked who controls them"):
        assert needle in sk, needle


def test_skill_forbids_write_suggestions_on_external_wallets():
    sec = _skill().split("## One wallet list — every wallet first-class", 1)[1].split("## ", 1)[0]
    for needle in ("`close.py`", "`edit_position`", "`close_position`", "`strategy_*`", "`ratchet_stop_*`"):
        assert needle in sec, needle
    assert "senpi-improve-trades" in sec and "quant-desk" in sec      # trade history is handed off


def test_cta_one_never_routes_a_saved_wallet_to_a_write_tool():
    sk = _skill()
    assert ("a genuinely ad-hoc position the user placed by hand on a Senpi wallet — never a saved "
            "wallet, which is read-only (quote its `access` line)") in sk
    assert "the saved-wallets closing replaces them" in sk


def test_skill_has_no_strategy_pitch_on_the_no_strategy_path():
    sec = _skill().split("## One wallet list — every wallet first-class", 1)[1].split("## ", 1)[0]
    assert "`meta.no_strategy_path`" in sec
    assert "from every step (`money`, `strategies`, `positions`) and from `all`" in sec
    assert "Never pitch a strategy" in sec
    assert "**Want me to review the trades on it, or score it on the quant desk?**" in sec


def test_readme_row_matches_the_skill_version():
    import re
    version = re.search(r'version: "([0-9.]+)"', open(SKILL, encoding="utf-8").read()).group(1)
    readme = open(os.path.join(HERE, "..", "..", "README.md"), encoding="utf-8").read()
    assert f"| [`senpi-portfolio`](senpi-portfolio/) | {version} |" in readme


# ── invariant I1: the engine never calls a saved wallet connect(ed) or verified (amendment A1) ─────
def test_a_saved_wallet_read_uses_none_of_the_retired_words():
    """A saved wallet is the user's claim, not proof of control. Whatever the engine prints about it —
    keys, values, warnings — never uses the word connect(ed) or verified (a socket "connection" is another word)."""
    import re
    for fx in (_external_only(),
               _with_external(_base(), [(CW_A, "Main")], {CW_A: _state_ok()}),
               _with_external(_base(), [(CW_A, "Main")]),                    # state read fails → warning text
               _with_external(_base(), [(CW_A, "Main")], status="unavailable")):
        s = _dump(_run(fx))
        assert not re.search(r"(?i)connect(?!ion)", s), re.findall(r".{0,40}onnect.{0,40}", s)[:3]
        assert not re.search(r"(?i)(?<![a-z])verified", s), re.findall(r".{0,40}erified.{0,20}", s)[:3]
