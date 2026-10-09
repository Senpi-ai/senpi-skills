#!/usr/bin/env python3
"""Every wallet first-class — the ONE wallet list (`book`), External Wallets R1 product rule.

Ordering goes by value, never by origin: the Senpi main wallet, each Senpi strategy (all its wallets) and
each wallet the user added sit in ONE list, largest first, with the origin as a `kind` column (managed /
read-only), never as a section break. One book total, with a managed subtotal that IS `grand_total_usd`
(the money map's three buckets ride on it, byte-identical) and a read-only subtotal that is never
deployable. Unknown is never zero: a wallet that couldn't load sorts last, is never summed, and the
total says it excludes it. The order and the totals are computed here, so the agent narrates an order it
was given.

    python3 -m pytest senpi-portfolio/tests/test_portfolio_one_wallet_list.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import copy
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import portfolio  # noqa: E402
import test_portfolio_external_wallets as ew  # noqa: E402

CW_A, CW_B, ACCESS = ew.CW_A, ew.CW_B, ew.ACCESS
CW_C = "0x" + "c3" * 20
SKILL = os.path.join(HERE, "..", "SKILL.md")


def setup_module(module):
    ew.setup_module(module)


def teardown_module(module):
    ew.teardown_module(module)


def _labels(book):
    return [r["label"] for r in book["wallets"]]


def _mixed(**states):
    """The canonical fixture (embedded $1.51 + cub across 3 wallets ≈ $3,101) plus saved wallets."""
    wallets = [(CW_A, "Main"), (CW_B, "Cold"), (CW_C, "Spare")]
    wallets = [w for w in wallets if w[0] in states]
    return ew._with_external(ew._base(), wallets, states)


class _Failing(portfolio._FixtureClient):
    """A read that raises (HTTP 503) for the calls `fails(tool, kw)` picks — a real failed read, not an
    empty reply."""
    def __init__(self, fx, fails):
        super().__init__(fx)
        self._fails = fails

    def mcp_call(self, tool, timeout=12, **kw):
        if self._fails(tool, kw):
            raise RuntimeError("HTTP 503")
        return super().mcp_call(tool, timeout=timeout, **kw)


def _sp():
    return os.path.join(tempfile.mkdtemp(), "state.json")


# ── one list, ordered by value, kind as a column ────────────────────────────────────────────────────
def test_every_wallet_is_one_list_ordered_by_value_with_kind_as_a_column():
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: ew._state_ok(total="12.00")}))
    book = out["book"]
    assert _labels(book) == ["Main", "cub", "Cold", "Senpi main wallet"]
    assert [r["kind"] for r in book["wallets"]] == ["read-only", "managed", "read-only", "managed"]
    assert [r["origin"] for r in book["wallets"]] == ["saved", "strategy", "saved", "embedded"]
    values = [r["value_usd"] for r in book["wallets"]]
    assert values == sorted(values, reverse=True)
    assert book["order_rule"].startswith("by value, largest first")


def test_saved_wallets_are_not_a_separate_section_after_the_money_map():
    """A saved wallet larger than the Senpi book leads the list — origin is never a rank."""
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="99999.99")}))
    assert out["book"]["wallets"][0]["origin"] == "saved"
    assert out["book"]["wallets"][0]["address"] == CW_A
    # and a small one sits below the Senpi strategy, above the Senpi main wallet — interleaved, not appended
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="12.00")}))
    assert [r["origin"] for r in out["book"]["wallets"]] == ["strategy", "saved", "embedded"]


def test_a_wallet_that_couldnt_load_sorts_last_and_is_never_zero():
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: None,
                            CW_C: ew._state_error("PERPS_UNAVAILABLE")}))
    rows = out["book"]["wallets"]
    assert _labels(out["book"])[-2:] == ["Cold", "Spare"]                  # unknown last, ties by label
    for r in rows[-2:]:
        assert r["loaded"] is False and r["value_usd"] is None
        assert r["open_positions"] is None and r["unrealized_pnl_usd"] is None and r["protection"] is None
    assert out["book"]["totals"]["read_only_usd"] == 5234.10            # never summed as 0 — excluded


def test_ties_break_by_label_then_address():
    fx = ew._with_external(ew._base(), [(CW_B, "same"), (CW_A, "same"), (CW_C, "alpha")],
                           {CW_A: ew._state_ok(total="50.00"), CW_B: ew._state_ok(total="50.00"),
                            CW_C: ew._state_ok(total="50.00")})
    rows = [r for r in ew._run(fx)["book"]["wallets"] if r["origin"] == "saved"]
    assert [(r["label"], r["address"]) for r in rows] == [("alpha", CW_C), ("same", CW_A), ("same", CW_B)]


def test_an_unlabelled_saved_wallet_is_called_by_its_short_address():
    out = ew._run(ew._with_external(ew._base(), [(CW_A, None)], {CW_A: ew._state_ok()}))
    row = next(r for r in out["book"]["wallets"] if r["origin"] == "saved")
    assert row["label"] == portfolio._short_wallet(CW_A)


def test_a_strategy_is_one_row_across_all_its_wallets():
    out = ew._run(_mixed(**{CW_A: ew._state_ok()}))
    cub = next(r for r in out["book"]["wallets"] if r["origin"] == "strategy")
    assert cub["wallet_count"] == 3 and len(cub["strategy_wallets"]) == 3
    assert cub["strategy_group"] == "cub"
    group = next(g for g in out["strategy_groups"] if g["label"] == "cub")
    assert cub["value_usd"] == group["totals"]["account_value"]
    assert cub["open_positions"] == sum(len(i["positions"]) for i in group["instances"])
    assert cub["unrealized_pnl_usd"] == group["totals"]["upnl"]


# ── the grouping invariant: one package = one strategy = one row (`_book_group_key`) ────────────────
_CP, _CH = "0x" + "a1" * 20, "0x" + "a2" * 20


def _wallet(name, wallet, value, skill=None):
    return {"name": name, "name_source": "strategyName", "wallet": wallet, "skill_name": skill,
            "account_value": value}


def test_two_instances_of_one_package_are_one_row_with_both_wallets_summed():
    """deploy.py stamps skillName = the package id on every instance's wallet (`<id>-<instance>`) and a
    re-run adopts the wallet by that name — so a package is one strategy, its instances its wallets."""
    rows = portfolio._strategy_rows([_wallet("camel-concentrated-payout", _CP, 600.0, "camel"),
                                     _wallet("camel-harvest", _CH, 400.5, "camel")], groups=None)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "camel" and row["strategy_group"] == "camel"
    assert row["wallet_count"] == 2 and row["value_usd"] == 1000.5 and row["wallets_couldnt_load"] == 0
    assert [w["name"] for w in row["strategy_wallets"]] == ["camel-concentrated-payout", "camel-harvest"]


def test_an_unstamped_wallet_is_its_own_row():
    """A strategy created outside deploy.py carries no skillName: nothing says which wallets are one
    strategy, so each is its own row — never merged by a lookalike name."""
    rows = portfolio._strategy_rows([_wallet("camel-concentrated-payout", _CP, 600.0, "camel"),
                                     _wallet("camel-harvest", _CH, 400.0)], groups=None)
    assert [(r["label"], r["wallet_count"]) for r in rows] == [("camel", 1), ("camel-harvest", 1)]
    assert rows[1]["strategy_group"] == _CH


def test_two_packages_are_two_rows():
    """A fork is a new package id, so a new row — two packages never share one."""
    rows = portfolio._strategy_rows([_wallet("camel-harvest", _CP, 600.0, "camel"),
                                     _wallet("camel-fork-harvest", _CH, 400.0, "camel-fork")], groups=None)
    assert [(r["label"], r["wallet_count"], r["value_usd"]) for r in rows] == [
        ("camel", 1, 600.0), ("camel-fork", 1, 400.0)]


def test_a_duplicate_wallet_from_a_deploy_race_lands_on_the_same_row():
    """A second wallet under the same stamp (a deploy race) is an extra wallet on the strategy's row —
    visible in wallet_count / strategy_wallets[], never a second strategy."""
    rows = portfolio._strategy_rows([_wallet("camel-harvest", _CP, 600.0, "camel"),
                                     _wallet("camel-harvest", _CH, 10.0, "camel")], groups=None)
    assert len(rows) == 1 and rows[0]["wallet_count"] == 2 and rows[0]["value_usd"] == 610.0


def test_the_money_step_gives_the_same_one_list_with_detail_left_to_the_strategies_step():
    fx = _mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: ew._state_ok(total="12.00")})
    money = portfolio.step_money(portfolio._FixtureClient(fx), state_path=_sp())
    assert _labels(money["book"]) == ["Main", "cub", "Cold", "Senpi main wallet"]
    cub = next(r for r in money["book"]["wallets"] if r["origin"] == "strategy")
    assert cub["wallet_count"] == 3
    assert cub["open_positions"] is None and cub["protected"] is None
    assert cub["not_read_this_step"] == ["open_positions", "protected", "unrealized_pnl_usd"]
    assert cub["detail_step"] == "strategies"
    saved = next(r for r in money["book"]["wallets"] if r["label"] == "Main")
    assert saved["open_positions"] == 1                       # a saved wallet's state is read in full here


# ── protection vocabulary never merged ──────────────────────────────────────────────────────────────
def test_saved_rows_say_protection_and_strategy_rows_say_protected():
    out = ew._run(_mixed(**{CW_A: ew._state_ok()}))
    saved = next(r for r in out["book"]["wallets"] if r["origin"] == "saved")
    cub = next(r for r in out["book"]["wallets"] if r["origin"] == "strategy")
    assert saved["protection"] == {"FULL": 1} and "protected" not in saved
    assert "protection" not in cub and "protected" in cub and "runtime_health" in cub
    assert saved["access"] == ACCESS
    assert saved["positions_scope"] == "the Hyperliquid main and xyz dexes"
    assert saved["not_applicable"] == list(portfolio.EXTERNAL_NOT_APPLICABLE)


def test_the_senpi_main_wallet_row_holds_cash_only():
    row = next(r for r in ew._run(ew._base())["book"]["wallets"] if r["origin"] == "embedded")
    assert row["kind"] == "managed" and row["value_usd"] == 1.51 and row["holds"] == "cash"
    assert row["not_applicable"] == ["open_positions", "protection", "pnl"]


# ── one book total; managed subtotal IS grand_total_usd ─────────────────────────────────────────────
def test_the_managed_subtotal_is_grand_total_usd_exactly_in_every_step():
    fx = _mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: ew._state_ok(total="12.00")})
    base = ew._run(ew._base())
    out = ew._run(fx)
    t = out["book"]["totals"]
    assert t["managed_usd"] == out["totals"]["grand_total_usd"] == base["totals"]["grand_total_usd"]
    assert ew._dump(out["totals"]) == ew._dump(base["totals"])           # the money map does not move
    assert t["read_only_usd"] == 5246.10
    assert t["total_usd"] == round(t["managed_usd"] + t["read_only_usd"], 2)
    br = t["managed_breakdown"]
    for k in ("idle_in_embedded", "idle_in_strategies", "deployed_in_positions", "reconciles"):
        assert br[k] == out["totals"][k], k
    sp = _sp()
    money = portfolio.step_money(portfolio._FixtureClient(fx), state_path=sp)
    assert money["book"]["totals"]["managed_usd"] == money["totals"]["grand_total_usd"]
    strat = portfolio.step_strategies(portfolio._FixtureClient(fx), want_market=False, state_path=sp)
    assert strat["book"]["totals"]["managed_usd"] == money["totals"]["grand_total_usd"]
    pos = portfolio.step_positions(portfolio._FixtureClient(fx), want_market=False, state_path=sp)
    assert pos["book"]["totals"]["managed_usd"] == pos["totals"]["grand_total_usd"]
    for res in (money, strat, pos):
        assert _labels(res["book"]) == ["Main", "cub", "Cold", "Senpi main wallet"]


def test_the_read_only_subtotal_is_labelled_never_deployable():
    t = ew._run(_mixed(**{CW_A: ew._state_ok()}))["book"]["totals"]
    assert "never deployable" in t["read_only_note"] and "wallets you added" in t["read_only_note"]
    assert "read-only $5,234.10" in t["line"]
    assert t["line"].startswith("Book total $")
    assert "managed by Senpi $" in t["line"] and "idle in strategies $" in t["line"]


def test_no_saved_wallets_is_a_real_zero_subtotal():
    t = ew._run(ew._with_external(ew._base(), [], {}))["book"]["totals"]
    assert t["read_only_usd"] == 0.0 and t["excludes_note"] is None
    assert t["total_usd"] == t["managed_usd"]


# ── unknown is never zero: the total says what it excludes ──────────────────────────────────────────
def test_the_total_says_it_excludes_a_wallet_that_couldnt_load():
    t = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: None}))["book"]["totals"]
    assert t["excludes"]["wallets"] == ["Cold"]
    assert t["excludes_note"] == "total excludes 1 wallet that couldn't load (Cold)"
    assert t["excludes_note"] in t["line"]


def test_unpriced_coins_carry_to_the_book_total():
    t = ew._run(_mixed(**{CW_A: ew._state_ok(unpriced=["STHYPE"])}))["book"]["totals"]
    assert t["excludes"]["coins"] == ["STHYPE"]
    assert t["excludes_note"] == "total excludes STHYPE (no price)"
    row = next(r for r in ew._run(_mixed(**{CW_A: ew._state_ok(unpriced=["STHYPE"])}))["book"]["wallets"]
               if r["origin"] == "saved")
    assert row["excludes_coins"] == ["STHYPE"]


def test_an_unavailable_saved_wallets_read_is_excluded_never_none():
    out = ew._run(ew._with_external(ew._base(), [(CW_A, "Main")], status="unavailable"))
    t = out["book"]["totals"]
    assert t["read_only_usd"] is None
    assert t["excludes"]["saved_wallets_unavailable"] is True
    assert t["excludes_note"] == "total excludes the wallets you added (couldn't load them)"
    assert t["total_usd"] == t["managed_usd"]
    assert not re.search(r"(?i)no (saved )?wallets", ew._dump(out["book"]))
    # an MCP older than saved wallets reads the same way
    assert ew._run(ew._base())["book"]["totals"]["excludes"]["saved_wallets_unavailable"] is True


def test_a_senpi_wallet_that_couldnt_load_is_named_and_the_managed_subtotal_still_matches():
    out = portfolio.run(_Failing(_mixed(**{CW_A: ew._state_ok()}),
                                 lambda tool, kw: tool == "strategy_get_clearinghouse_state"
                                 and str(kw.get("strategy_wallet", "")).startswith("0xshort")),
                        want_market=False)
    cub = next(r for r in out["book"]["wallets"] if r["origin"] == "strategy")
    assert cub["wallets_couldnt_load"] == 1 and cub["loaded"] is True     # partly loaded: 2 of 3 wallets
    t = out["book"]["totals"]
    assert t["managed_usd"] == out["totals"]["grand_total_usd"]
    assert t["excludes"]["wallets"] == ["cub-short"]
    assert t["excludes_note"] == "total excludes 1 wallet that couldn't load (cub-short)"


def test_the_senpi_main_wallet_that_couldnt_load_sorts_last():
    out = portfolio.run(_Failing(_mixed(**{CW_A: ew._state_ok()}),
                                 lambda tool, kw: tool == "account_get_portfolio"), want_market=False)
    last = out["book"]["wallets"][-1]
    assert last["origin"] == "embedded" and last["loaded"] is False and last["value_usd"] is None
    assert "Senpi main wallet" in out["book"]["totals"]["excludes"]["wallets"]


def test_an_unreconciled_managed_side_is_said_on_the_total_line():
    fx = ew._base()
    fx["account_get_portfolio"]["portfolio"]["total_balance_usd"] = 9999.0
    t = ew._run(fx)["book"]["totals"]
    assert t["managed_breakdown"]["reconciles"] is False
    assert "do not reconcile" in t["line"]


def test_no_activity_is_a_read_zero_not_an_unknown():
    st = ew._state_ok(total=None, positions=[])
    st["role"] = "MISSING"
    row = next(r for r in ew._run(_mixed(**{CW_A: st}))["book"]["wallets"] if r["origin"] == "saved")
    assert row["loaded"] is True and row["value_usd"] == 0.0 and row["no_hyperliquid_activity"] is True


# ── one deep-dive question for every user with more than one wallet ─────────────────────────────────
def test_the_deep_dive_question_is_offered_to_everyone_with_more_than_one_wallet_largest_first():
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10")}))          # a user WITH a strategy
    dd = out["book"]["deep_dive"]
    assert dd["offer"] is True and dd["question"] == "Which one do you want me to go deeper on?"
    assert dd["order"] == _labels(out["book"]) and dd["order"][0] == "Main"
    assert "no_strategy_path" not in out["meta"]
    only = ew._run(ew._external_only())["book"]["deep_dive"]                 # no strategy, saved + $0 Senpi
    assert only["offer"] is False and only["order"] == ["Main"]              # an empty main wallet isn't an option
    alone = {"user_get_me": {"wallets": [{"walletType": "embedded", "walletAddress": ew.EMBED}]},
             "account_get_portfolio": {"total_balance_usd": 5, "total_withdrawable": 0,
                                       "total_in_hyperliquid": 5, "token_balances": []},
             "strategy_list": {"strategies": []}}
    assert ew._run(alone)["book"]["deep_dive"]["offer"] is False             # one wallet: nothing to pick


# ── determinism: `all` is still byte-identical to run() ─────────────────────────────────────────────
def test_all_stays_byte_identical_to_run_with_the_book():
    fx = _mixed(**{CW_A: ew._state_ok(), CW_B: None})
    direct = portfolio.run(portfolio._FixtureClient(copy.deepcopy(fx)), want_market=True)
    allres = portfolio._all_and_persist(portfolio._FixtureClient(copy.deepcopy(fx)), want_market=True,
                                        state_path=_sp())
    assert json.dumps(direct, sort_keys=True) == json.dumps(allres, sort_keys=True)
    assert "book" in direct


# ── SKILL.md: the agent keeps the engine's order and never re-sections by origin ────────────────────
def _skill():
    return " ".join(open(SKILL, encoding="utf-8").read().split())


def test_skill_presents_one_list_and_never_a_separate_saved_section():
    sk = _skill()
    assert "## Your wallets (read-only)" not in sk
    assert "## One wallet list — every wallet first-class" in sk
    assert "its own \"Your wallets (read-only)\" section" not in sk
    assert "after the money map, never summed into it" not in sk
    for needle in ("Keep the engine's order", "never re-section by origin", "`book.wallets`",
                   "`book.totals.line`", "`book.totals.managed_usd` is `grand_total_usd`",
                   "`book.totals.excludes_note`", "kind column"):
        assert needle in sk, needle


def test_skill_steps_and_output_contract_lead_with_the_one_list():
    sk = _skill()
    steps = sk.split("## Run it in steps", 1)[1].split("## How to run the engine", 1)[0]
    assert "`book`" in steps
    contract = sk.split("## Output contract", 1)[1].split("## Mandatory closing", 1)[0]
    assert "1. **One wallet list + the book total.**" in contract
    ret = sk.split("## How to run the engine", 1)[1].split("## Output contract", 1)[0]
    assert "`book`" in ret and "`external_wallets`" in ret


def test_skill_closing_offers_the_deep_dive_and_keeps_the_ctas_managed():
    closing = _skill().split("## Mandatory closing (verbatim)", 1)[1].split("## Resilience", 1)[0]
    assert "**Which one do you want me to go deeper on?**" in closing
    assert "`book.deep_dive.offer`" in closing and "largest first" in closing
    assert "never `book.totals.read_only_usd`" in closing
    assert "CTA 1 and CTA 2 are about managed wallets only" in closing


# ── R1 re-review: the read-only subtotal never reads unloaded wallets as $0 ─────────────────────────
def test_every_saved_wallet_unloaded_is_an_unknown_read_only_subtotal_never_zero():
    """account_get_external_wallets 503 with two saved wallets: none loaded → `read_only_usd: null` and
    "read-only unknown", never "read-only $0.00"."""
    fx = _mixed(**{CW_A: ew._state_ok(), CW_B: ew._state_ok()})
    out = portfolio.run(_Failing(fx, lambda tool, kw: tool == "account_get_external_wallets"),
                        want_market=False)
    t = out["book"]["totals"]
    assert t["read_only_usd"] is None
    assert t["read_only_wallets"] == {"total": 2, "loaded": 0}
    assert " · read-only unknown · " in t["line"] and "read-only $0.00" not in t["line"]
    assert t["excludes"]["wallets"] == ["Cold", "Main"] and t["excludes_note"] in t["line"]
    assert t["total_usd"] == t["managed_usd"]
    # a single saved wallet whose state is a readError reads the same way
    t = ew._run(ew._with_external(ew._base(), [(CW_A, "Main")], {CW_A: ew._state_error()}))["book"]["totals"]
    assert t["read_only_usd"] is None and "read-only unknown" in t["line"]
    assert t["read_only_wallets"] == {"total": 1, "loaded": 0}


def test_a_partly_loaded_read_only_subtotal_says_how_many_wallets_it_covers():
    t = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: None}))["book"]["totals"]
    assert t["read_only_usd"] == 5234.10
    assert t["read_only_wallets"] == {"total": 2, "loaded": 1}
    assert "read-only $5,234.10 (1 of 2 wallets you added — Senpi cannot trade or deploy it)" in t["line"]
    assert "total excludes 1 wallet that couldn't load (Cold)" in t["line"]
    # all loaded: no "N of M"
    t = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: ew._state_ok(total="12.00")}))["book"]["totals"]
    assert "read-only $5,246.10 (wallets you added — Senpi cannot trade or deploy it)" in t["line"]
    assert t["read_only_wallets"] == {"total": 2, "loaded": 2}


def test_no_saved_wallets_omits_the_read_only_clause_from_the_line():
    t = ew._run(ew._with_external(ew._base(), [], {}))["book"]["totals"]
    assert t["read_only_usd"] == 0.0 and t["read_only_wallets"] == {"total": 0, "loaded": 0}
    assert "read-only" not in t["line"] and "wallets you added" not in t["line"]
    # unavailable is still said — "couldn't load", never silence and never "none"
    t = ew._run(ew._base())["book"]["totals"]
    assert "the wallets you added (couldn't load them)" in t["line"]
    assert t["read_only_wallets"] == {"total": None, "loaded": None}


# ── R1 re-review: an unreadable strategy list is named, never a complete managed subtotal ──────────
def _strategy_list_fails(tool, kw):
    return tool == "strategy_list"


def test_an_unreadable_strategy_list_is_named_and_never_the_no_strategy_path():
    fx = ew._with_external(ew._base(), [(CW_A, "Main")], {CW_A: ew._state_ok()})
    outs = {"all": portfolio.run(_Failing(fx, _strategy_list_fails), want_market=False),
            "money": portfolio.step_money(_Failing(fx, _strategy_list_fails), state_path=_sp()),
            "strategies": portfolio.step_strategies(_Failing(fx, _strategy_list_fails), want_market=False,
                                                    state_path=_sp()),
            "positions": portfolio.step_positions(_Failing(fx, _strategy_list_fails), want_market=False,
                                                  state_path=_sp())}
    for step, out in outs.items():
        t = out["book"]["totals"]
        assert t["managed_complete"] is False, step
        assert t["excludes"]["strategies_unreadable"] is True, step
        assert "your Senpi strategies couldn't be read" in t["line"], step
        assert "your Senpi strategies (couldn't be read)" in t["excludes_note"], step
        assert "idle in strategies $0.00" not in t["line"], step          # never a false-zero bucket
        if "totals" in out:
            assert t["managed_usd"] == out["totals"]["grand_total_usd"], step
        assert "no_strategy_path" not in out["meta"], step                 # a failed read is not "none"
    # the list read cleanly → complete, and nothing extra
    t = ew._run(fx)["book"]["totals"]
    assert t["managed_complete"] is True and t["excludes"]["strategies_unreadable"] is False
    # a strategies step that follows a money step whose list failed keeps the state
    sp = _sp()
    portfolio.step_money(_Failing(fx, _strategy_list_fails), state_path=sp)
    st = portfolio.step_positions(_Failing(fx, _strategy_list_fails), want_market=False, state_path=sp)
    assert st["book"]["totals"]["managed_complete"] is False and "no_strategy_path" not in st["meta"]


def test_a_saved_only_user_whose_list_read_cleanly_is_still_the_no_strategy_path():
    out = ew._run(ew._external_only())
    assert out["meta"].get("no_strategy_path") is True
    assert out["book"]["totals"]["managed_complete"] is True


# ── R1 re-review: the line's "(see meta.warnings)" points at a warning that is there ────────────────
def _unreconciled():
    fx = ew._base()
    fx["account_get_portfolio"]["portfolio"]["total_balance_usd"] = 9999.0
    return fx


def _reconcile_warnings(out):
    return [w for w in out["meta"]["warnings"] if "RECONCILE" in w]


def test_every_step_whose_line_points_at_meta_warnings_carries_the_warning():
    fx = _unreconciled()
    alone = portfolio.step_strategies(portfolio._FixtureClient(fx), want_market=False, state_path=_sp())
    assert "(see meta.warnings)" in alone["book"]["totals"]["line"]
    assert len(_reconcile_warnings(alone)) == 1
    sp = _sp()
    money = portfolio.step_money(portfolio._FixtureClient(fx), state_path=sp)
    strat = portfolio.step_strategies(portfolio._FixtureClient(fx), want_market=False, state_path=sp)
    pos = portfolio.step_positions(portfolio._FixtureClient(fx), want_market=False, state_path=sp)
    for out in (money, strat, pos):
        assert "(see meta.warnings)" in out["book"]["totals"]["line"]
        assert len(_reconcile_warnings(out)) == 1                        # there, and said once
    # the full fetch the strategies step makes never clobbers what the money step stored
    for w in money["meta"]["warnings"]:
        assert w in strat["meta"]["warnings"], w
    # a reconciled book points nowhere
    ok = portfolio.step_strategies(portfolio._FixtureClient(ew._base()), want_market=False, state_path=_sp())
    assert "meta.warnings" not in ok["book"]["totals"]["line"] and not _reconcile_warnings(ok)


# ── R1 re-review: every step groups a strategy's wallets by the same key ───────────────────────────
def test_every_step_groups_a_strategys_wallets_by_the_same_key():
    """The `money` step reads no runtime registry (no `profile.group`), so the book groups by the key both
    steps have — the package a wallet was deployed under (`skill_name`), else the wallet — and the list
    never depends on which step answered."""
    emb = {"address": "0xe", "idle_total": 10.0}
    w1, w2, w3 = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40
    money = [{"name": "alpha-long", "name_source": "strategyName", "skill_name": None, "wallet": w1,
              "account_value": 100.0, "idle_withdrawable": 100.0, "deployed": 0.0},
             {"name": "alpha-short", "name_source": "strategyName", "skill_name": None, "wallet": w2,
              "account_value": 90.0, "idle_withdrawable": 90.0, "deployed": 0.0},
             {"name": "cub-long", "name_source": "strategyName", "skill_name": "cub", "wallet": w3,
              "account_value": 50.0, "idle_withdrawable": 50.0, "deployed": 0.0}]
    full = [dict(s, profile={"group": "alpha" if s["skill_name"] is None else "cub"},
                 positions=[], protected=True, runtime_health="live",
                 closed={"realized_pnl": 1.0}) for s in money]
    saved = {"status": "ok", "wallets": []}
    bm = portfolio.build_book(emb, money, saved, portfolio._money_totals(emb, money, {}))
    bf = portfolio.build_book(emb, full, saved, portfolio._money_totals(emb, full, {}),
                              portfolio.group_strategies(full, {}))
    assert _labels(bm) == _labels(bf) == ["alpha-long", "alpha-short", "cub", "Senpi main wallet"]
    assert [r["value_usd"] for r in bm["wallets"]] == [r["value_usd"] for r in bf["wallets"]]
    cub = next(r for r in bf["wallets"] if r["label"] == "cub")
    assert cub["protected"] is True and cub["runtime_health"] == "live" and cub["realized_pnl_usd"] == 1.0
    assert cub["open_positions"] == 0


def test_skill_says_the_book_groups_by_the_same_key_in_every_step():
    sec = _skill().split("## One wallet list — every wallet first-class", 1)[1].split("## Run it in steps", 1)[0]
    assert "the same key in every step" in sec and "`skill_name`" in sec


# ── R1 re-review: the deep-dive offer counts only wallets with something to go deeper on ───────────
def _offered(book):
    return book["deep_dive"]["order"]


def test_an_empty_senpi_main_wallet_is_never_offered_for_a_deep_dive():
    out = ew._run(ew._external_only())                          # $0 Senpi main wallet + one saved wallet
    assert out["book"]["deep_dive"]["offer"] is False
    assert _offered(out["book"]) == ["Main"]
    assert "Senpi main wallet" in _labels(out["book"])         # still a row of the list
    # $0 main + one strategy: nothing to choose between
    fx = ew._base()
    fx["account_get_portfolio"]["portfolio"]["token_balances"] = []
    fx["account_get_portfolio"]["portfolio"]["total_balance_usd"] = 3101.43
    book = ew._run(fx)["book"]
    emb = next(r for r in book["wallets"] if r["origin"] == "embedded")
    assert emb["value_usd"] == 0.0
    assert book["deep_dive"]["offer"] is False and _offered(book) == ["cub"]


def test_the_deep_dive_offers_unknown_and_non_zero_wallets_and_skips_empty_ones():
    empty = ew._state_ok(total=None, positions=[])
    empty["role"] = "MISSING"                                  # read fine, no Hyperliquid activity: a real $0
    out = ew._run(_mixed(**{CW_A: ew._state_ok(total="5234.10"), CW_B: None, CW_C: empty}))
    dd = out["book"]["deep_dive"]
    assert dd["offer"] is True
    assert dd["order"] == ["Main", "cub", "Senpi main wallet", "Cold"]    # book order; Spare ($0, empty) left out
    # a $0 saved wallet that still has an open position is offered
    held = ew._state_ok(total="0")
    out = ew._run(_mixed(**{CW_A: held}))
    assert "Main" in out["book"]["deep_dive"]["order"]
