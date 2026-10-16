#!/usr/bin/env python3
"""Saved wallets ("Your wallets") in the trade review (External Wallets R1, amendment A1).

A saved wallet is one the user added in Your wallets by pasting its address — their claim, not proof of
control — and trades by hand. It joins the review set, its
closed trades come from the same address-generic fetch_closed_trades (discovery first, HL userFills
as the fallback — the NORMAL path, since Senpi's index rarely has an arbitrary address), every exit is
a MANUAL_TRADE, and nothing Senpi-strategy-shaped (ratchet, telemetry, DSL coaching, a strategy pitch)
ever touches it. Unknown is never empty: a failed fills read is closed_trades_unknown, never "no trades".

    python3 -m pytest senpi-improve-trades/tests/test_review_external_wallets.py
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

import review  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "review_fixture.json")
REGISTRY_DIR = os.path.join(HERE, "fixtures", "registry")
EVENTS_FIXTURE = os.path.join(HERE, "fixtures", "events_fixture.json")   # hermetic: never spawn openclaw
SKILL = os.path.join(HERE, "..", "SKILL.md")
README = os.path.join(HERE, "..", "..", "README.md")
NOW_MS = 1782800100000
WINDOW_DAYS = 30
ACCESS = "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it."
CW = "0x" + "c3" * 20
T0 = NOW_MS - 5 * 86400000


def _fills():
    """Two manual round trips inside the window: HYPE long +40, SOL short +100."""
    return [
        {"coin": "HYPE", "dir": "Open Long", "sz": "2", "px": "100", "closedPnl": "0", "fee": "0.1", "time": T0, "oid": 1},
        {"coin": "HYPE", "dir": "Close Long", "sz": "2", "px": "120", "closedPnl": "40", "fee": "0.12", "time": T0 + 1000, "oid": 2},
        {"coin": "SOL", "dir": "Open Short", "sz": "10", "px": "200", "closedPnl": "0", "fee": "0.2", "time": T0 + 2000, "oid": 3},
        {"coin": "SOL", "dir": "Close Short", "sz": "10", "px": "190", "closedPnl": "100", "fee": "0.19", "time": T0 + 3000, "oid": 4},
    ]


def _state(protection="PARTIAL"):
    return {"readAt": "2026-10-03T12:00:00Z", "readError": None, "role": "USER", "accountMode": "unifiedAccount",
            "accountValueUsd": "900.00", "spotBalances": [], "totalValueUsd": "900.00", "openOrders": [],
            "positions": [{"coin": "ETH", "dex": "", "side": "LONG", "size": "1.0", "entryPx": "2500.0",
                           "markPx": "2600.0", "positionValueUsd": "2600.00", "unrealizedPnlUsd": "100.00",
                           "leverage": 5, "marginType": "cross", "liquidationPx": "2100.0",
                           "stopOrders": [{"oid": "11", "cloid": None, "triggerPx": "2400.0", "kind": "STOP_MARKET",
                                           "status": "ARMED", "isPositionTpsl": False, "size": "0.5"}],
                           "coveredSize": "0.5", "protection": protection}]}


def _external(fx, fills=None, state=None, status="ok", wallets=((CW, "MetaMask"),)):
    fx = copy.deepcopy(fx)
    user = {"wallets": [], "external_wallets_status": status}
    if status == "ok":
        user["external_wallets"] = [{"address": a, "label": l, "added_at": "2026-10-01T15:31:02.000Z",
                                      "access": ACCESS} for a, l in wallets]
    fx["user_get_me"] = {"user": user}
    fx["account_get_external_wallets"] = {"external_wallets": [
        {"address": a, "label": l, "added_at": "2026-10-01T15:31:02.000Z", "access": ACCESS,
         "state": state} for a, l in wallets]}
    for a, _l in wallets:
        fx[f"discovery_get_trader_history::{a}"] = {"closedPositions": []}   # not in Senpi's index
        if fills is not None:
            fx[f"hl::userFills::{a}"] = fills
    return fx


def _base():
    with open(FIXTURE) as f:
        return json.load(f)


def _external_only(**kw):
    return _external({"strategy_list": {"strategies": []}}, **kw)


class Recording(review._FixtureClient):
    def __init__(self, recorded):
        super().__init__(recorded)
        self.calls = []

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append((tool, kw))
        return super().mcp_call(tool, timeout=timeout, **kw)


def _env():
    saved = {k: os.environ.get(k) for k in ("SENPI_STATE_DIR", "SENPI_EVENTS_FIXTURE")}
    os.environ["SENPI_STATE_DIR"] = REGISTRY_DIR
    os.environ["SENPI_EVENTS_FIXTURE"] = EVENTS_FIXTURE
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _run(fx, client_cls=review._FixtureClient):
    saved = _env()
    try:
        client = client_cls(fx)
        return review.run(client, window_days=WINDOW_DAYS, want_market=False, now_ms=NOW_MS), client
    finally:
        _restore(saved)


# ── the review set ──────────────────────────────────────────────────────────────────────────────────
def test_external_only_user_gets_a_review_of_manual_trades():
    res, _ = _run(_external_only(fills=_fills(), state=_state()))
    cw = res["external_wallets"]
    assert len(cw) == 1 and cw[0]["address"] == CW and cw[0]["label"] == "MetaMask"
    assert cw[0]["closed_trade_count"] == 2 and cw[0]["realized_pnl"] == 140.0
    assert cw[0]["access"] == ACCESS
    rows = [t for t in res["trades"] if t.get("wallet_kind") == "external"]
    assert len(rows) == 2
    for t in rows:
        assert t["exit_reason"] == {"terminal": "MANUAL_TRADE", "tier_reached": None, "high_water_roe": None,
                                    "source": "external_wallet"}
        assert t["source"] == "external_wallet" and t["mandate"] is None
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "has_trades"
    assert "pitch" in out["meta"]["next_action"] and "discover" not in out["meta"]["next_action"]


def test_external_wallets_never_touch_ratchet_or_telemetry():
    _res, client = _run(_external_only(fills=_fills(), state=_state()), Recording)
    tools = [t for t, _ in client.calls]
    assert "ratchet_stop_list" not in tools
    assert "strategy_get_clearinghouse_state" not in tools        # open book comes from state
    assert tools.count("account_get_external_wallets") == 1


def test_senpi_aggregates_stay_byte_identical_for_a_mixed_user():
    base, _ = _run(_base())
    mixed, _ = _run(_external(_base(), fills=_fills(), state=_state()))
    for key in ("timing_summary", "pnl_summary", "strategies", "closed_strategies", "dsl_close_reason_mix",
                "telemetry_availability"):
        assert json.dumps(mixed[key], sort_keys=True) == json.dumps(base[key], sort_keys=True), key
    assert mixed["meta"]["strategy_count"] == base["meta"]["strategy_count"]
    assert mixed["meta"]["trade_count"] == base["meta"]["trade_count"]
    assert mixed["meta"]["external_trade_count"] == 2


def test_the_open_book_is_the_state_verbatim_with_protection():
    res, _ = _run(_external_only(fills=_fills(), state=_state("PARTIAL")))
    pos = res["external_wallets"][0]["open_positions"]
    assert pos == _state("PARTIAL")["positions"]
    assert pos[0]["protection"] == "PARTIAL" and pos[0]["coveredSize"] == "0.5"


def test_a_null_state_is_an_unknown_open_book():
    res, _ = _run(_external_only(fills=_fills(), state=None))
    w = res["external_wallets"][0]
    assert w["state_read"] == "unavailable" and w["open_positions"] is None


# ── unknown is never empty ──────────────────────────────────────────────────────────────────────────
def test_empty_discovery_with_fills_uses_the_userfills_fallback():
    meta = {}
    fx = _external_only(fills=_fills())
    trades = review.fetch_closed_trades(review._FixtureClient(fx), CW, None, None, None, meta, strict=True)
    assert len(trades) == 2 and all(t["source"] == "onchain_fills" for t in trades)
    assert "closed_trades_unknown" not in meta


def test_a_failed_userfills_read_is_unknown_never_no_trades():
    res, _ = _run(_external_only(fills=None, state=_state()))   # no hl::userFills key → the read failed
    w = res["external_wallets"][0]
    assert w["closed_trades_unknown"] is True
    assert w["closed_trade_count"] is None and w["realized_pnl"] is None and w["timing_summary"] is None
    assert CW in res["meta"]["closed_trades_unknown"]
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "unknown"                # never external_no_trades / "no trades"
    assert "never 'no trades'" in out["meta"]["next_action"]


def test_an_empty_fills_answer_is_a_real_zero():
    res, _ = _run(_external_only(fills=[], state=_state()))
    w = res["external_wallets"][0]
    assert w["closed_trades_unknown"] is False and w["closed_trade_count"] == 0
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "external_no_trades"
    assert "do NOT pitch a strategy" in out["meta"]["next_action"]


def test_the_2000_fill_cap_marks_the_totals_as_at_least():
    pad = [{"coin": "BTC", "dir": "Open Long", "sz": "0.001", "px": "60000", "closedPnl": "0", "fee": "0.01",
            "time": T0 - 10 * 86400000 - i, "oid": 100 + i} for i in range(review.HL_FILLS_CAP - 4)]
    res, _ = _run(_external_only(fills=pad + _fills(), state=_state()))
    w = res["external_wallets"][0]
    assert w["fills_capped"] is True and w["closed_trade_count"] == 2
    assert CW in res["meta"]["fills_capped"]


def test_an_older_mcp_with_no_external_key_is_unavailable_not_none():
    fx = {"strategy_list": {"strategies": []}, "user_get_me": {"user": {"wallets": []}}}
    res, _ = _run(fx)
    assert res["meta"]["external_wallets_status"] == "unavailable"
    assert res["external_wallets"] is None                          # unknown, never []
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "unknown"                    # never "no_strategies" → pitch
    assert "couldn't load" in out["meta"]["next_action"]


def test_a_mixed_user_on_an_older_mcp_is_told_the_external_wallets_did_not_load():
    fx = copy.deepcopy(_base())
    fx["user_get_me"] = {"user": {"wallets": []}}                    # no external_wallets keys at all
    res, _ = _run(fx)
    assert res["external_wallets"] is None
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "has_trades"
    assert "saved wallets couldn't be loaded" in out["meta"]["next_action"]


def test_a_saved_only_review_leads_with_the_external_read_and_hands_leaks_to_quant_desk():
    res, _ = _run(_external_only(fills=_fills(), state=_state()))
    nxt = review._slim_for_context(res)["meta"]["next_action"]
    assert "lead with external_wallets[]" in nxt and "quant-desk" in nxt and "Senpi-only and empty" in nxt


# ── book_state ──────────────────────────────────────────────────────────────────────────────────────
def test_book_state_never_pitches_a_user_with_external_wallets():
    assert review._book_state(0, 0, False, 1, 3)[0] == "has_trades"
    st, nxt = review._book_state(0, 0, False, 1, 0)
    assert st == "external_no_trades" and "discover" not in nxt and "market-pulse" not in nxt
    assert review._book_state(0, 0, False, 0, 0, "unavailable")[0] == "unknown"
    assert review._book_state(0, 0, False)[0] == "no_strategies"           # unchanged default
    assert review._book_state(2, 0, False, 1, 4)[0] == "has_trades"
    assert review._book_state(0, 0, True, 1, 4)[0] == "unknown"            # token problem still wins
    assert review._book_state(0, 0, False, 1, 0, "ok", 1)[0] == "unknown"  # fills unread ≠ no trades


def test_a_manual_trade_is_never_a_premature_dsl_exit():
    assert review._is_premature_exit({"terminal": "MANUAL_TRADE", "tier_reached": 0,
                                      "high_water_roe": 1.0}) is False


# ── steps ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_timing_step_carries_the_external_read_and_matches_all():
    fx = _external(_base(), fills=_fills(), state=_state())
    saved = _env()
    try:
        sp = os.path.join(tempfile.mkdtemp(), "s.json")
        t = review.step_timing(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                               state_path=sp, now_ms=NOW_MS)
        s = review.step_strategies(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                                   state_path=sp, now_ms=NOW_MS)
        te = review.step_telemetry(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                                   state_path=sp, now_ms=NOW_MS)
    finally:
        _restore(saved)
    allr, _ = _run(fx)
    assert t["external_wallets"] == allr["external_wallets"]
    assert s["meta"]["strategy_count"] == allr["meta"]["strategy_count"]
    assert all(r["exit_reason"]["terminal"] == "MANUAL_TRADE"
               for r in te["trades"] if r.get("wallet_kind") == "external")


# ── fix round 1 ─────────────────────────────────────────────────────────────────────────────────────
def test_last_n_is_capped_per_kind_so_senpi_aggregates_match_the_senpi_only_run():
    saved = _env()
    try:
        base = review.run(review._FixtureClient(_base()), window_days=WINDOW_DAYS, last_n=1,
                          want_market=False, now_ms=NOW_MS)
        fx = _external(_base(), fills=_fills(), state=_state())
        mixed = review.run(review._FixtureClient(fx), window_days=WINDOW_DAYS, last_n=1,
                           want_market=False, now_ms=NOW_MS)
    finally:
        _restore(saved)
    assert base["meta"]["trade_count"] >= 1
    for key in ("timing_summary", "pnl_summary", "strategies", "closed_strategies"):
        assert json.dumps(mixed[key], sort_keys=True) == json.dumps(base[key], sort_keys=True), key
    assert mixed["meta"]["trade_count"] == base["meta"]["trade_count"]
    assert mixed["meta"]["external_trade_count"] == 1


def test_degraded_does_not_claim_no_strategies_when_external_wallets_did_not_load():
    meta = {"external_wallets_status": "unavailable", "warnings": []}
    msg = review._degraded([], [], [], meta)
    assert "not a fault" not in msg and "couldn't be loaded" in msg
    res, _ = _run({"strategy_list": {"strategies": []}, "user_get_me": {"user": {"wallets": []}}})
    assert "not a fault" not in (res["meta"]["degraded"] or "")


# ── narration rules ─────────────────────────────────────────────────────────────────────────────────
def _skill():
    return " ".join(open(SKILL, encoding="utf-8").read().split())


def test_skill_quotes_the_access_line_and_forbids_writes():
    sk = _skill()
    assert ACCESS in sk
    sec = sk.split("## Your wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    for needle in ("`MANUAL_TRADE`", "manual trade", "no DSL-preset or strategy-tuning coaching",
                   "`closed_trades_unknown: true`", "`fills_capped: true`", "at least", "`external_no_trades`",
                   "`close.py`", "`edit_position`", "`strategy_*`", "`ratchet_stop_*`", "`protection`",
                   "add it in Your wallets on senpi.ai (web)", "no open positions on the Hyperliquid main and xyz dexes",
                   "never a bare \"no positions\"", "`external_wallets: null`", "An address pasted in chat is never described as saved",
                   "never imply Senpi checked who controls them"):
        assert needle in sec, needle


def test_skill_never_prints_a_read_error_code():
    sec = " ".join(open(SKILL, encoding="utf-8").read().split()).split(
        "## Your wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    assert "never print the code" in sec and "`state: null`" in sec


def test_description_splits_leak_routing_with_quant_desk():
    desc = _skill().split("license:", 1)[0]
    assert "a SAVED wallet" in desc and "quant-desk" in desc
    assert "Senpi strategies" in desc
    for words in ('"my MetaMask"', '"my own Hyperliquid wallet"', '"my saved wallet"'):
        assert words in desc, words


def test_the_review_of_a_saved_wallet_stays_here_only_leaks_go_to_the_desk():
    """Ruling (R1 final review): this skill owns the REVIEW of a saved wallet ("review my trades",
    "master my week"); only leaks and "what did I miss" on a saved wallet go to quant-desk."""
    sk = _skill()
    desc = sk.split("license:", 1)[0]
    assert ('"where am I leaking" or "what did I miss" about a SAVED wallet the user trades by hand') in desc
    assert '"master my week" about a SAVED wallet' not in desc
    assert ('Saved wallets are reviewed here too, read-only, as manual trades — "review my trades" '
            'and "master my week" on a saved wallet stay in this skill') in desc
    sec = sk.split("## Your wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    assert ('The review itself — "review my trades", "master my week" on a saved wallet — stays '
            'here') in sec


def test_its_the_strategy_rule_is_scoped_to_senpi_strategy_trades():
    """Guardrail 5 used to say "Route every fix to the strategy config" unscoped, contradicting the
    saved-wallet rule (coach the user's own process, no strategy pitch)."""
    sk = _skill()
    assert ("**It's the strategy, not the user — on Senpi strategy trades.** Route every fix on a Senpi "
            "strategy trade to the strategy config") in sk
    assert "On a saved wallet there is no strategy: coach the user's own process" in sk
    desc = sk.split("license:", 1)[0]
    assert "it's the STRATEGY not the user (on Senpi strategy trades)" in desc
    assert "This section covers **Senpi strategy** trades. A saved wallet's trades are the user's own" in sk


def test_more_gains_never_pitches_a_saved_only_user():
    row = next(l for l in open(SKILL, encoding="utf-8").read().splitlines() if "How could I make more gains?" in l)
    assert "never for a saved-only user" in row


def test_readme_row_matches_the_skill_version():
    version = re.search(r'version: "([0-9.]+)"', open(SKILL, encoding="utf-8").read()).group(1)
    assert f"| [`senpi-improve-trades`](senpi-improve-trades/) | {version} |" in open(README, encoding="utf-8").read()


# ── invariant I1: the engine never calls a saved wallet connect(ed) or verified (amendment A1) ─────
def test_a_saved_wallet_review_uses_none_of_the_retired_words():
    """A saved wallet is the user's claim, not proof of control. Whatever the engine prints about it —
    keys, values, book_state, next_action, warnings — never uses the word connect(ed) or verified."""
    for fx in (_external_only(fills=_fills(), state=_state()),
               _external_only(fills=[], state=_state()),                     # external_no_trades
               _external_only(status="unavailable"),
               _external(_base(), fills=_fills(), state=_state())):
        res, _ = _run(fx)
        s = json.dumps(review._slim_for_context(res), sort_keys=True, ensure_ascii=False)
        assert not re.search(r"(?i)connect(?!ion)", s), re.findall(r".{0,40}onnect.{0,40}", s)[:3]
        assert not re.search(r"(?i)(?<![a-z])verified", s), re.findall(r".{0,40}erified.{0,20}", s)[:3]


# ── every wallet first-class: one book, rows by value (reviewer §02/§04, release R1) ────────────────
LW = "0x" + "d4" * 20


def _valued_base():
    """The Senpi fixture with Kodiak's clearinghouse carrying marginSummary — value 200 idle (shared by
    both views, counted once) + 1300 main equity + 0 xyz equity = 1500."""
    fx = _base()
    key = "strategy_get_clearinghouse_state::0xkodiak00000000000000000000000000000kdk"
    fx[key]["main"].update({"marginSummary": {"accountValue": "1500.00"}, "withdrawable": "200.00"})
    fx[key]["xyz"].update({"marginSummary": {"accountValue": "200.00"}, "withdrawable": "200.00"})
    return fx


def _two_saved(base, cw_state, lw_state):
    fx = _external(base, fills=_fills(), state=cw_state, wallets=((CW, "MetaMask"), (LW, "Ledger")))
    for row in fx["account_get_external_wallets"]["external_wallets"]:
        row["state"] = cw_state if row["address"] == CW else lw_state
    return fx


def _valued_state(total, protection="PARTIAL", unpriced=None):
    st = _state(protection)
    st["totalValueUsd"] = total
    st["unpricedCoins"] = unpriced if unpriced is not None else []
    return st


def test_book_rows_are_one_list_by_value_and_kind_is_a_column():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00", "FULL")))
    rows = res["book"]["rows"]
    assert [(r["label"], r["kind"], r["value_usd"]) for r in rows] == [
        ("Ledger", "read_only", 5000.0), ("kodiak", "managed", 1500.0), ("MetaMask", "read_only", 900.0)]
    k = rows[1]
    assert k["closed_trade_count"] == res["strategies"][0]["closed_trade_count"]
    assert k["realized_pnl"] == res["strategies"][0]["realized_pnl"]
    assert k["protection"] == {"kind": "runtime_exit"} and "access" not in k
    m = rows[2]
    assert m["access"] == ACCESS and m["closed_trade_count"] == 2 and m["realized_pnl"] == 140.0
    assert m["fees"] == 0.61 and m["open_position_count"] == 1
    assert m["protection"] == {"kind": "live_stops", "FULL": 0, "PARTIAL": 1, "NONE": 0}
    assert rows[0]["protection"] == {"kind": "live_stops", "FULL": 1, "PARTIAL": 0, "NONE": 0}


def test_ties_break_by_label_then_address_and_unknown_sorts_last():
    res, _ = _run(_two_saved(_valued_base(), None, _valued_state("1500.00")))
    rows = res["book"]["rows"]
    assert [(r["label"], r["value_usd"]) for r in rows] == [
        ("kodiak", 1500.0), ("Ledger", 1500.0), ("MetaMask", None)]     # casefold tie-break; unknown LAST
    assert rows[2]["value_read"] == "unavailable" and rows[2]["open_position_count"] is None


def test_a_wallet_that_could_not_load_is_never_summed_as_zero():
    res, _ = _run(_two_saved(_valued_base(), None, _valued_state("5000.00")))
    tot = res["book"]["total"]
    assert tot["value_usd"] == 6500.0                                  # 1500 managed + 5000 read-only
    assert tot["managed"]["value_usd"] == 1500.0 and tot["read_only"]["value_usd"] == 5000.0
    assert tot["excludes"]["couldnt_load"] == ["MetaMask"]
    assert "total excludes 1 wallet that couldn't load (MetaMask)" in tot["line"]


def test_a_read_error_state_is_couldnt_load_too():
    err = {"readAt": "2026-10-03T12:00:00Z", "readError": "ORDERS_UNAVAILABLE:xyz", "role": "USER",
           "accountMode": "default", "accountValueUsd": None, "spotBalances": None, "totalValueUsd": None,
           "unpricedCoins": None, "positions": None, "openOrders": None}
    res, _ = _run(_two_saved(_valued_base(), err, _valued_state("5000.00")))
    m = [r for r in res["book"]["rows"] if r["label"] == "MetaMask"][0]
    assert m["value_usd"] is None and m["value_read"] == "error" and res["book"]["rows"][-1] is m
    assert "ORDERS_UNAVAILABLE" not in json.dumps(res["book"])          # never print the code


def test_unpriced_coins_carry_their_note_to_the_book_total():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00", unpriced=["PURR", "#4210"]),
                             _valued_state("5000.00")))
    m = [r for r in res["book"]["rows"] if r["label"] == "MetaMask"][0]
    assert m["unpriced_coins"] == ["PURR", "#4210"] and m["value_usd"] == 900.0
    tot = res["book"]["total"]
    assert tot["excludes"]["unpriced_coins"] == ["PURR", "#4210"]
    assert "total excludes PURR, #4210" in tot["line"]


def test_the_managed_subtotal_is_the_senpi_numbers_and_they_do_not_move():
    base, _ = _run(_valued_base())
    mixed, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00")))
    for key in ("timing_summary", "pnl_summary", "strategies", "closed_strategies", "dsl_close_reason_mix",
                "telemetry_availability"):
        assert json.dumps(mixed[key], sort_keys=True) == json.dumps(base[key], sort_keys=True), key
    man = mixed["book"]["total"]["managed"]
    assert man == base["book"]["total"]["managed"]                       # the pin, re-pointed at the subtotal
    assert man["realized_pnl"] == mixed["pnl_summary"]["realized"]
    assert man["fees"] == mixed["pnl_summary"]["fees"]
    assert man["value_usd"] == 1500.0 and man["wallet_count"] == 1
    ro = mixed["book"]["total"]["read_only"]
    assert ro["value_usd"] == 5900.0 and ro["wallet_count"] == 2 and ro["realized_pnl"] == 280.0
    assert mixed["book"]["total"]["realized_pnl"] == round(man["realized_pnl"] + 280.0, 2)


def test_the_book_line_never_reads_a_saved_balance_as_deployable_or_as_senpi_performance():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00")))
    line = res["book"]["total"]["line"]
    assert "managed by Senpi $1,500.00" in line and "read-only $5,900.00" in line
    assert "Senpi can't deploy it" in line
    assert "gross of fees" in line and "across the book" in line
    assert not re.search(r"(?i)combined|deployable|idle", line)
    assert "not Senpi performance" in res["book"]["total"]["note"]


def test_saved_wallets_unavailable_never_read_as_none_in_the_book():
    res, _ = _run(_valued_base())                                      # no user_get_me → unavailable
    tot = res["book"]["total"]
    assert tot["read_only"]["value_usd"] is None and tot["read_only"]["wallet_count"] is None
    assert "your saved wallets couldn't be loaded" in tot["line"]
    assert "The total excludes your saved wallets (couldn't be read)." in tot["line"]
    assert "across the wallets that could be read" in tot["line"] and "across the book" not in tot["line"]
    assert [r["kind"] for r in res["book"]["rows"]] == ["managed"]


def test_an_unreadable_strategy_list_is_an_unknown_managed_subtotal_never_zero():
    fx = _external({"strategy_list": None}, fills=_fills(), state=_valued_state("900.00"))

    class Failing(review._FixtureClient):
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "strategy_list":
                raise RuntimeError("401 unauthorized")
            return super().mcp_call(tool, timeout=timeout, **kw)
    res, _ = _run(fx, Failing)
    man = res["book"]["total"]["managed"]
    assert man["value_usd"] is None and man["realized_pnl"] is None and man["wallet_count"] is None
    assert "your Senpi strategies couldn't be read" in res["book"]["total"]["line"]
    assert res["book"]["total"]["value_usd"] == 900.0


def test_a_saved_only_user_has_a_real_zero_managed_subtotal():
    res, _ = _run(_external_only(fills=_fills(), state=_valued_state("900.00")))
    man = res["book"]["total"]["managed"]
    assert man["wallet_count"] == 0 and man["value_usd"] == 0.0
    assert [r["kind"] for r in res["book"]["rows"]] == ["read_only"]
    assert res["book"]["deep_dive"] is None                              # one wallet: no narrow offer


def test_the_deep_dive_question_names_the_largest_first_for_more_than_one_wallet():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00")))
    dd = res["book"]["deep_dive"]
    assert dd["question"] == "Which wallet do you want me to go deeper on: Ledger, kodiak or MetaMask?"
    assert dd["order"] == ["Ledger", "kodiak", "MetaMask"]


def test_duplicate_labels_are_told_apart_by_address():
    fx = _external({"strategy_list": {"strategies": []}}, fills=_fills(), state=_valued_state("900.00"),
                   wallets=((CW, "Main"), (LW, "Main")))
    res, _ = _run(fx)
    q = res["book"]["deep_dive"]["question"]
    assert "Main (0xc3c3…c3c3)" in q and "Main (0xd4d4…d4d4)" in q


def _row(label, kind, measurable, ahead, unknown=False, capped=False, value=100.0):
    return {"label": label, "display_label": label, "kind": kind, "value_usd": value,
            "trades_unknown": unknown, "trades_capped": capped,
            "closed_trade_count": None if unknown else measurable,
            "timing": None if unknown else {"measurable_closes": measurable, "exits_ahead": ahead,
                                             "exits_ahead_share": round(ahead / measurable, 2) if measurable else None}}


def test_the_comparison_needs_two_wallets_with_eight_measurable_closes():
    assert review.MIN_COMPARE_CLOSES == 8
    c = review._book_comparison([_row("Aegis", "managed", 10, 7), _row("MetaMask", "read_only", 9, 3)])
    assert c["line"] == ("On closes measurable against today's price, Aegis got out ahead of the later move "
                         "on 7 of 10 (70%), MetaMask on 3 of 9 (33%).")
    assert [w["label"] for w in c["wallets"]] == ["Aegis", "MetaMask"] and c["min_closes"] == 8
    thin = review._book_comparison([_row("Aegis", "managed", 10, 7), _row("MetaMask", "read_only", 7, 3)])
    assert thin["line"] is None and "fewer than 2 wallets" in thin["reason"]
    assert thin["not_compared"] == [{"label": "MetaMask", "why": "7 measurable closes, under 8"}]
    assert review._book_comparison([_row("Solo", "read_only", 20, 5)])["line"] is None


def test_the_comparison_names_a_wallet_it_could_not_read_or_that_was_capped():
    c = review._book_comparison([_row("Aegis", "managed", 10, 7), _row("MetaMask", "read_only", 9, 3, capped=True),
                                 _row("Ledger", "read_only", 0, 0, unknown=True)])
    assert "MetaMask on at least 3 of 9" not in c["line"]
    assert "MetaMask's closes are only its most recent (Hyperliquid's 2,000-fill ceiling)" in c["line"]
    assert "Ledger isn't compared: its trades couldn't be read" in c["line"]
    assert {"label": "Ledger", "why": "its trades couldn't be read"} in c["not_compared"]


def test_the_comparison_is_deterministic_and_ordered_like_the_rows():
    rows = [_row("B", "read_only", 8, 2, value=50.0), _row("A", "managed", 12, 6, value=900.0)]
    rows.sort(key=review._row_sort_key)
    a = review._book_comparison(rows)
    assert a == review._book_comparison(rows) and a["line"].startswith(
        "On closes measurable against today's price, A got out ahead")


def test_the_engine_emits_no_comparison_on_a_thin_sample_and_says_why():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00")))
    c = res["book"]["comparison"]
    assert c["line"] is None and "8" in c["reason"]                      # 2 closes each, no prices: thin


def test_the_strategies_step_emits_the_same_book_as_all():
    fx = _two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00"))
    saved = _env()
    try:
        sp = os.path.join(tempfile.mkdtemp(), "s.json")
        review.step_timing(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                           state_path=sp, now_ms=NOW_MS)
        s = review.step_strategies(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                                   state_path=sp, now_ms=NOW_MS)
        sp2 = os.path.join(tempfile.mkdtemp(), "s.json")                   # standalone: self-heals
        s2 = review.step_strategies(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=False,
                                    state_path=sp2, now_ms=NOW_MS)
    finally:
        _restore(saved)
    allr, _ = _run(fx)
    assert s["book"] == allr["book"] == s2["book"]
    assert list(allr.keys())[:2] == ["window", "book"]                   # the book comes first, then the detail


def test_the_book_uses_none_of_the_retired_words():
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), None))
    s = json.dumps(res["book"], ensure_ascii=False)
    assert not re.search(r"(?i)connect(?!ion)|(?<![a-z])verified|you own|proven", s)
    said = " ".join(x or "" for x in (res["book"]["total"]["line"], res["book"]["comparison"]["line"],
                                      res["book"]["deep_dive"]["question"]))
    assert not re.search(r"(?i)combined|deployable|\bidle\b", said)   # the user-facing lines


# ── SKILL.md: one book, comparison, narrow offer, fix depth on a saved wallet ───────────────────────
def _book_section():
    return _skill().split("## One book — every wallet first-class", 1)[1].split(" ## ", 1)[0]


def test_skill_narrates_one_book_in_the_engines_order():
    sec = _book_section()
    for needle in ("`book.rows`", "in the order the engine gives", "never re-section by origin",
                   "`kind`", "managed", "read-only", "`book.total.line` verbatim",
                   "Never build a \"Combined\" row", "never present it as Senpi performance",
                   "`book.comparison.line`", "never compare wallets yourself",
                   "`book.deep_dive.question`", "Which wallet do you want me to go deeper on",
                   "couldn't load", "never $0",
                   "runtime exit", "live stops", "never merge"):
        assert needle in sec, needle


def test_skill_offers_the_three_depths_on_a_saved_wallet_as_advice():
    sec = _book_section()
    for needle in ("**Explain only**", "**Turn it into a routine**", "**Draft the exact orders**",
                   "places them on Hyperliquid themselves", "fewer than ~8 closes",
                   "no write tool", "no template or strategy pitch"):
        assert needle in sec, needle
    assert not re.search(r"(?i)\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\b(you|they) own\b|\bowned by\b", sec)


def test_output_shape_documents_the_book():
    shape = " ".join(open(os.path.join(HERE, "..", "references", "output-shape.md"), encoding="utf-8").read().split())
    for needle in ("book", "rows[]", "kind: managed | read_only", "value_usd", "total", "comparison",
                   "deep_dive", "MIN_COMPARE_CLOSES"):
        assert needle in shape, needle


def _round_trips(coin, n, close_px, t0):
    out = []
    for i in range(n):
        t = t0 + i * 10000
        out += [{"coin": coin, "dir": "Open Long", "sz": "2", "px": "100", "closedPnl": "0", "fee": "0.1",
                 "time": t, "oid": 1000 + 2 * i},
                {"coin": coin, "dir": "Close Long", "sz": "2", "px": str(close_px), "closedPnl": "10", "fee": "0.1",
                 "time": t + 1000, "oid": 1001 + 2 * i}]
    return out


def test_the_engine_emits_one_comparison_line_end_to_end():
    """MetaMask: 8 HYPE longs closed at 120, HYPE now 110 → every exit got out ahead. Ledger: 9 SOL longs
    closed at 120, SOL now 130 → none did. Kodiak has 3 closes: under the gate, never compared."""
    fx = _two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00"))
    fx[f"hl::userFills::{CW}"] = _round_trips("HYPE", 8, 120, T0)
    fx[f"hl::userFills::{LW}"] = _round_trips("SOL", 9, 120, T0)
    fx["market_get_asset_data::hype"] = {"asset_context": {"markPx": "110.00"}}
    saved = _env()
    try:
        res = review.run(review._FixtureClient(fx), window_days=WINDOW_DAYS, want_market=True, now_ms=NOW_MS)
    finally:
        _restore(saved)
    c = res["book"]["comparison"]
    assert c["line"] == ("On closes measurable against today's price, Ledger got out ahead of the later move "
                         "on 0 of 9 (0%), MetaMask on 8 of 8 (100%).")
    assert {"label": "kodiak", "why": "3 measurable closes, under 8"} in c["not_compared"]


# ── R1 re-review: a subtotal says how many of its wallets it covers; unknown is never 0 ────────────
MIXED_FIXTURE = os.path.join(HERE, "fixtures", "review_mixed_status_fixture.json")


def test_a_partly_loaded_subtotal_counts_only_the_wallets_that_loaded_and_names_the_rest():
    """One saved wallet couldn't load, the other did: the read-only subtotal is the loaded one's value,
    says "1 of 2 wallets", and names the unknown one — an unknown wallet is never added in as $0."""
    res, _ = _run(_two_saved(_valued_base(), None, _valued_state("5000.00")))
    ro = res["book"]["total"]["read_only"]
    assert ro["value_usd"] == 5000.0 and ro["wallet_count"] == 2 and ro["wallets_loaded"] == 1
    assert ro["couldnt_load"] == ["MetaMask"]
    line = res["book"]["total"]["line"]
    assert "read-only $5,000.00 (1 of 2 wallets, traded by hand — Senpi can't deploy it)" in line
    assert "The total excludes 1 wallet that couldn't load (MetaMask)." in line
    # every wallet loaded: no "N of M"
    res, _ = _run(_two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00")))
    assert "read-only $5,900.00 (2 wallets, traded by hand" in res["book"]["total"]["line"]
    assert res["book"]["total"]["read_only"]["wallets_loaded"] == 2
    assert res["book"]["total"]["managed"]["wallets_loaded"] == 1
    assert "managed by Senpi $1,500.00 (1 wallet)" in res["book"]["total"]["line"]


def test_a_partly_loaded_managed_subtotal_says_how_many_wallets_it_covers():
    rows = [{"display_label": "kodiak", "label": "kodiak", "kind": "managed", "value_usd": 1500.0},
            {"display_label": "grizzly", "label": "grizzly", "kind": "managed", "value_usd": None}]
    for r in rows:
        r.update({"trades_unknown": False, "trades_capped": False, "realized_pnl": 0.0, "fees": None,
                  "unpriced_coins": []})
    tot = review._book_total(rows, {"realized": 0.0, "fees": None}, "ok", "ok")
    assert tot["managed"]["value_usd"] == 1500.0 and tot["managed"]["wallets_loaded"] == 1
    assert "managed by Senpi $1,500.00 (1 of 2 wallets)" in tot["line"]
    assert tot["excludes"]["couldnt_load"] == ["grizzly"]


def test_every_wallet_of_a_kind_unloaded_is_an_unknown_subtotal_never_zero():
    res, _ = _run(_two_saved(_valued_base(), None, None))
    ro = res["book"]["total"]["read_only"]
    assert ro["value_usd"] is None and ro["wallets_loaded"] == 0 and ro["couldnt_load"] == ["Ledger", "MetaMask"]
    assert " · read-only unknown." in res["book"]["total"]["line"]
    assert res["book"]["total"]["value_usd"] == 1500.0


def test_no_saved_wallets_omits_the_read_only_clause_like_portfolio():
    fx = _external(_valued_base(), wallets=())
    res, _ = _run(fx)
    ro = res["book"]["total"]["read_only"]
    assert ro["state"] == "ok" and ro["wallet_count"] == 0
    line = res["book"]["total"]["line"]
    assert "read-only" not in line and line.startswith("Book value $1,500.00: managed by Senpi $1,500.00 (1 wallet).")
    # unavailable is still said
    res, _ = _run(_valued_base())
    assert "read-only unknown (your saved wallets couldn't be loaded)" in res["book"]["total"]["line"]


# ── R1 re-review: read-only realized never sums a wallet whose trades couldn't be read ──────────────
def test_read_only_realized_is_unknown_when_no_saved_wallets_trades_could_be_read():
    res, _ = _run(_external(_valued_base(), fills=None, state=_valued_state("900.00")))
    ro = res["book"]["total"]["read_only"]
    assert ro["trades_unknown"] == ["MetaMask"]
    assert ro["realized_pnl"] is None and ro["fees"] is None              # never $0
    tot = res["book"]["total"]
    assert tot["realized_pnl"] == res["pnl_summary"]["realized"]
    assert "across the wallets that could be read" in tot["line"] and "· read-only unknown." in tot["line"]
    assert "Realized excludes MetaMask (trades couldn't be read)." in tot["line"]


def test_read_only_realized_sums_only_the_wallets_whose_trades_were_read():
    fx = _two_saved(_valued_base(), _valued_state("900.00"), _valued_state("5000.00"))
    fx.pop(f"hl::userFills::{LW}")                                        # Ledger's fills unreadable
    res, _ = _run(fx)
    ro = res["book"]["total"]["read_only"]
    assert ro["trades_unknown"] == ["Ledger"] and ro["realized_pnl"] == 140.0 and ro["fees"] == 0.61
    assert "Realized excludes Ledger (trades couldn't be read)." in res["book"]["total"]["line"]


# ── R1 re-review: the managed realized subtotal is pnl_summary, closed strategies included ─────────
def test_managed_realized_is_pnl_summary_including_closed_strategies_never_the_rows():
    with open(MIXED_FIXTURE) as f:
        fx = json.load(f)
    res, _ = _run(_external(fx, fills=_fills(), state=_valued_state("900.00")))
    man = res["book"]["total"]["managed"]
    rows_sum = sum(r["realized_pnl"] or 0.0 for r in res["book"]["rows"] if r["kind"] == "managed")
    assert res["pnl_summary"]["realized_by_book"]["closed"] == 220.0
    assert man["realized_pnl"] == res["pnl_summary"]["realized"] == 360.0 != rows_sum
    assert man["realized_pnl_closed_strategies"] == 220.0
    assert res["book"]["total"]["realized_pnl"] == 500.0                  # 360 managed + 140 read-only
    assert ("managed by Senpi $360.00 (incl. $220.00 from closed strategies) · read-only $140.00."
            in res["book"]["total"]["line"])


# ── R1 re-review: the deep-dive offers only wallets with something to go deeper on ─────────────────
def _empty_state():
    st = _valued_state("0")
    st["role"], st["totalValueUsd"], st["positions"] = "MISSING", None, []
    return st


def test_the_deep_dive_skips_an_empty_wallet_with_nothing_to_review():
    """A saved wallet read fine with no Hyperliquid activity, no position and no trades: a row of the
    list, never an option of the question. A $0 wallet that closed trades in the window still is."""
    fx = _two_saved(_valued_base(), _valued_state("900.00"), _empty_state())
    fx[f"hl::userFills::{LW}"] = []
    res, _ = _run(fx)
    assert [r["label"] for r in res["book"]["rows"]] == ["kodiak", "MetaMask", "Ledger"]
    dd = res["book"]["deep_dive"]
    assert dd["order"] == ["kodiak", "MetaMask"]
    assert dd["question"] == "Which wallet do you want me to go deeper on: kodiak or MetaMask?"
    # Ledger closed trades in the window: offered even at $0
    fx[f"hl::userFills::{LW}"] = _fills()
    res, _ = _run(fx)
    assert res["book"]["deep_dive"]["order"] == ["kodiak", "MetaMask", "Ledger"]
    # one wallet with something in it + an empty one: nothing to choose between
    fx = _external({"strategy_list": {"strategies": []}}, fills=_fills(), state=_valued_state("900.00"),
                   wallets=((CW, "MetaMask"), (LW, "Ledger")))
    for row in fx["account_get_external_wallets"]["external_wallets"]:
        if row["address"] == LW:
            row["state"] = _empty_state()
    fx[f"hl::userFills::{LW}"] = []
    res, _ = _run(fx)
    assert res["book"]["deep_dive"] is None


# ── R1 re-review: ONE placement for the deep-dive question, ONE opening rule ────────────────────────
def test_skill_asks_the_deep_dive_once_at_the_end_and_never_stops_the_steps():
    sk = _skill()
    sec = _book_section()
    assert "asked once, at the end of the whole answer" in sec
    assert "never stops the remaining steps" in sec
    assert "and stop. Go deep" not in sec and "end the overview with" not in sk
    steps = sk.split("## Run it in steps", 1)[1].split("## ", 1)[0]
    step2 = steps.split("2. `review.py strategies`", 1)[1].split("3. `review.py telemetry`", 1)[0]
    assert "deep-dive" not in step2 and "deep_dive" not in step2
    assert "**After the last step the request needs**, end the answer with `book.deep_dive.question`" in steps
    contract = sk.split("## The output contract", 1)[1]
    item0 = contract.split("0. **One book**", 1)[1].split("1. **", 1)[0]
    assert "comes last of all" in item0 and "then `book.deep_dive.question`" not in item0


def test_skill_has_one_opening_rule_book_first_then_pnl_summary_within_the_detail():
    sk = _skill()
    assert "lead with `external_wallets[]`" not in sk
    assert "open with `book.rows` as always" in sk
    gives = sk.split("## What the engine gives you", 1)[1].split("## ", 1)[0]
    assert gives.lstrip().startswith("The engine prints one JSON dict. **Open with `book`**")
    assert "within the Senpi detail, **lead with `pnl_summary.total`**" in gives


# ── one strategy unit: a Senpi strategy is one row with all its wallets (same key as senpi-portfolio) ──
CAMEL_P = "0x" + "a1" * 20
CAMEL_H = "0x" + "a2" * 20


def _ch(value):
    return {"main": {"assetPositions": [], "marginSummary": {"accountValue": value}, "withdrawable": value},
            "xyz": {"assetPositions": [], "marginSummary": {"accountValue": value}, "withdrawable": value}}


def _closed(oid, coin, pnl, close_time):
    return {"closedOrderId": oid, "coin": coin, "coinDisplayName": coin, "szi": "1.0", "type": "Close Long",
            "entryPx": "100.00", "exitPx": "110.00", "leverage": {"type": "cross", "value": 2},
            "realizedPnl": pnl, "marginUsed": "50.00", "openTime": close_time - 100000, "closeTime": close_time}


def _camel_base(harvest_value="400.50"):
    """kodiak (no package stamp — its own row) + the camel package deployed as two instances, each on its
    own wallet: camel-concentrated-payout and camel-harvest, both stamped skillName "camel"."""
    fx = _valued_base()
    fx["strategy_list"]["strategies"] += [
        {"strategyName": "camel-concentrated-payout", "tradingStrategyName": "camel", "id": "strat-camel-p",
         "strategyWalletAddress": CAMEL_P, "status": "ACTIVE", "strategyMetadata": {"skillName": "camel"}},
        {"strategyName": "camel-harvest", "tradingStrategyName": "camel", "id": "strat-camel-h",
         "strategyWalletAddress": CAMEL_H, "status": "ACTIVE", "strategyMetadata": {"skillName": "camel"}}]
    fx[f"strategy_get_clearinghouse_state::{CAMEL_P}"] = _ch("600.00")
    fx[f"strategy_get_clearinghouse_state::{CAMEL_H}"] = _ch(harvest_value)
    fx[f"discovery_get_trader_history::{CAMEL_P}"] = {"closedPositions": [
        _closed("0xP1", "SOL", "30.00", 1782710000000), _closed("0xP2", "ETH", "-10.00", 1782720000000)]}
    fx[f"discovery_get_trader_history::{CAMEL_H}"] = {"closedPositions": [
        _closed("0xH1", "BTC", "25.00", 1782730000000)]}
    fx[f"ratchet_stop_list::{CAMEL_P}"] = {"configs": []}
    fx[f"ratchet_stop_list::{CAMEL_H}"] = {"configs": []}
    return fx


def _camel(res):
    return next(r for r in res["book"]["rows"] if r["label"] == "camel")


def test_a_package_deployed_as_two_instances_is_one_book_row_with_both_wallets():
    res, _ = _run(_camel_base())
    managed = [r for r in res["book"]["rows"] if r["kind"] == "managed"]
    assert [r["label"] for r in managed] == ["kodiak", "camel"]          # 1500 > 1000.50; kodiak unstamped
    row = _camel(res)
    assert row["strategy_group"] == "camel" and row["wallet_count"] == 2 and row["wallets_loaded"] == 2
    assert row["value_usd"] == 1000.5 and row["value_read"] == "ok" and row["wallets_couldnt_load"] == 0
    by = {s["label"]: s for s in res["strategies"]}
    assert row["closed_trade_count"] == by["camel-concentrated-payout"]["closed_trade_count"] + \
        by["camel-harvest"]["closed_trade_count"] == 3
    assert row["realized_pnl"] == round(by["camel-concentrated-payout"]["realized_pnl"]
                                        + by["camel-harvest"]["realized_pnl"], 2) == 45.0
    assert row["open_position_count"] == 0
    assert row["protection"] == {"kind": "runtime_exit"}
    assert [(w["label"], w["wallet"], w["value_usd"]) for w in row["strategy_wallets"]] == [
        ("camel-concentrated-payout", CAMEL_P, 600.0), ("camel-harvest", CAMEL_H, 400.5)]
    # the detail stays per instance: two strategies[] entries, each its own verdict surface
    assert {"camel-concentrated-payout", "camel-harvest", "kodiak"} == set(by)
    # the managed subtotal still counts wallets, and still quotes pnl_summary
    man = res["book"]["total"]["managed"]
    assert man["wallet_count"] == 3 and man["wallets_loaded"] == 3 and man["value_usd"] == 2500.5
    assert man["realized_pnl"] == res["pnl_summary"]["realized"]
    assert "managed by Senpi $2,500.50 (3 wallets)" in res["book"]["total"]["line"]


def _unstamped(fx):
    for s in fx["strategy_list"]["strategies"]:
        s.pop("strategyMetadata", None)
    return fx


def test_the_senpi_aggregates_do_not_move_when_rows_group_by_strategy():
    """Grouping is a book-layer view over the per-wallet reads: the same wallets with and without the
    package stamp give the same Senpi aggregates and the same managed subtotal — only the rows differ."""
    grouped, _ = _run(_camel_base())
    flat, _ = _run(_unstamped(_camel_base()))
    for key in ("timing_summary", "pnl_summary", "closed_strategies", "dsl_close_reason_mix",
                "telemetry_availability"):
        assert json.dumps(grouped[key], sort_keys=True) == json.dumps(flat[key], sort_keys=True), key
    assert grouped["book"]["total"]["managed"] == flat["book"]["total"]["managed"]
    assert grouped["book"]["total"]["line"] == flat["book"]["total"]["line"]
    assert len(grouped["strategies"]) == len(flat["strategies"]) == 3     # the detail stays per instance
    # the strategy row's timing is over ALL its wallets' closes
    parts = [r for r in flat["book"]["rows"] if r["label"].startswith("camel-")]
    assert _camel(grouped)["timing"]["measurable_closes"] == sum(r["timing"]["measurable_closes"] for r in parts)
    assert _camel(grouped)["closed_trade_count"] == sum(r["closed_trade_count"] for r in parts)


def test_a_strategy_row_with_one_wallet_unread_says_one_of_two_and_names_it():
    fx = _camel_base()
    fx.pop(f"strategy_get_clearinghouse_state::{CAMEL_H}")
    res, _ = _run(fx)
    row = _camel(res)
    assert row["value_usd"] == 600.0 and row["wallets_loaded"] == 1 and row["wallets_couldnt_load"] == 1
    assert row["value_read"] == "partial"
    assert [w["value_usd"] for w in row["strategy_wallets"]] == [600.0, None]
    tot = res["book"]["total"]
    assert tot["managed"]["wallet_count"] == 3 and tot["managed"]["wallets_loaded"] == 2
    assert tot["managed"]["value_usd"] == 2100.0
    assert "managed by Senpi $2,100.00 (2 of 3 wallets)" in tot["line"]
    assert tot["excludes"]["couldnt_load"] == ["camel-harvest"]
    assert "The total excludes 1 wallet that couldn't load (camel-harvest)." in tot["line"]


def test_a_strategy_row_with_no_wallet_read_is_unknown_and_sorts_last():
    fx = _camel_base()
    fx.pop(f"strategy_get_clearinghouse_state::{CAMEL_H}")
    fx.pop(f"strategy_get_clearinghouse_state::{CAMEL_P}")
    res, _ = _run(fx)
    row = _camel(res)
    assert row["value_usd"] is None and row["value_read"] == "unavailable" and row["open_position_count"] is None
    assert res["book"]["rows"][-1] is row
    assert res["book"]["total"]["excludes"]["couldnt_load"] == ["camel-concentrated-payout", "camel-harvest"]


def test_the_deep_dive_names_the_strategy_once_never_its_instances():
    res, _ = _run(_camel_base())
    dd = res["book"]["deep_dive"]
    assert dd["order"] == ["kodiak", "camel"]
    assert dd["question"] == "Which wallet do you want me to go deeper on: kodiak or camel?"


def test_unstamped_lookalike_wallets_stay_their_own_rows():
    """Created outside deploy.py: no skillName, nothing says they are one strategy — one row each."""
    res, _ = _run(_unstamped(_camel_base()))
    managed = [r["label"] for r in res["book"]["rows"] if r["kind"] == "managed"]
    assert managed == ["kodiak", "camel-concentrated-payout", "camel-harvest"]
    assert all(r["wallet_count"] == 1 for r in res["book"]["rows"] if r["kind"] == "managed")


def test_the_comparison_compares_strategies_not_instances():
    res, _ = _run(_camel_base())
    labels = {w["label"] for w in res["book"]["comparison"]["wallets"]} | \
        {n["label"] for n in res["book"]["comparison"]["not_compared"]}
    assert "camel" in labels and not labels & {"camel-concentrated-payout", "camel-harvest"}


def test_a_strategy_row_sharing_a_label_is_told_apart_by_its_wallet_count():
    """A user-named, unstamped "camel" next to the camel package: two rows, two display labels."""
    fx = _camel_base()
    fx["strategy_list"]["strategies"][0]["strategyName"] = "camel"         # kodiak renamed, no stamp
    res, _ = _run(fx)
    q = res["book"]["deep_dive"]["question"]
    assert "camel (0xKODI…0kdk)" in q and "camel (2 wallets)" in q


def test_skill_says_the_row_is_the_strategy_and_the_detail_goes_per_instance():
    sec = _book_section()
    for needle in ("**A Senpi strategy is one row with all its wallets.**", "The row is the strategy",
                   "the review detail goes per instance", "its own runtime.yaml", "`strategy_wallets[]`",
                   "`value_read: \"partial\"`", "(1 of 2 wallets)"):
        assert needle in sec, needle
