#!/usr/bin/env python3
"""Connected wallets in the trade review (External Wallets R1).

A connected wallet is one the user proved they own and trades by hand. It joins the review set, its
closed trades come from the same address-generic fetch_closed_trades (discovery first, HL userFills
as the fallback — the NORMAL path, since Senpi's index rarely has an arbitrary address), every exit is
a MANUAL_TRADE, and nothing Senpi-strategy-shaped (ratchet, telemetry, DSL coaching, a strategy pitch)
ever touches it. Unknown is never empty: a failed fills read is closed_trades_unknown, never "no trades".

    python3 -m pytest senpi-improve-trades/tests/test_review_connected_wallets.py
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


def _connected(fx, fills=None, state=None, status="ok", wallets=((CW, "MetaMask"),)):
    fx = copy.deepcopy(fx)
    user = {"wallets": [], "connected_wallets_status": status}
    if status == "ok":
        user["connected_wallets"] = [{"address": a, "label": l, "verified_at": "2026-10-01T15:31:02.000Z",
                                      "access": ACCESS} for a, l in wallets]
    fx["user_get_me"] = {"user": user}
    fx["account_get_connected_wallets"] = {"connected_wallets": [
        {"address": a, "label": l, "verified_at": "2026-10-01T15:31:02.000Z", "access": ACCESS,
         "state": state} for a, l in wallets]}
    for a, _l in wallets:
        fx[f"discovery_get_trader_history::{a}"] = {"closedPositions": []}   # not in Senpi's index
        if fills is not None:
            fx[f"hl::userFills::{a}"] = fills
    return fx


def _base():
    with open(FIXTURE) as f:
        return json.load(f)


def _connected_only(**kw):
    return _connected({"strategy_list": {"strategies": []}}, **kw)


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
def test_connected_only_user_gets_a_review_of_manual_trades():
    res, _ = _run(_connected_only(fills=_fills(), state=_state()))
    cw = res["connected_wallets"]
    assert len(cw) == 1 and cw[0]["address"] == CW and cw[0]["label"] == "MetaMask"
    assert cw[0]["closed_trade_count"] == 2 and cw[0]["realized_pnl"] == 140.0
    assert cw[0]["access"] == ACCESS
    rows = [t for t in res["trades"] if t.get("wallet_kind") == "connected"]
    assert len(rows) == 2
    for t in rows:
        assert t["exit_reason"] == {"terminal": "MANUAL_TRADE", "tier_reached": None, "high_water_roe": None,
                                    "source": "connected_wallet"}
        assert t["source"] == "connected_wallet" and t["mandate"] is None
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "has_trades"
    assert "pitch" in out["meta"]["next_action"] and "discover" not in out["meta"]["next_action"]


def test_connected_wallets_never_touch_ratchet_or_telemetry():
    _res, client = _run(_connected_only(fills=_fills(), state=_state()), Recording)
    tools = [t for t, _ in client.calls]
    assert "ratchet_stop_list" not in tools
    assert "strategy_get_clearinghouse_state" not in tools        # open book comes from state
    assert tools.count("account_get_connected_wallets") == 1


def test_senpi_aggregates_stay_byte_identical_for_a_mixed_user():
    base, _ = _run(_base())
    mixed, _ = _run(_connected(_base(), fills=_fills(), state=_state()))
    for key in ("timing_summary", "pnl_summary", "strategies", "closed_strategies", "dsl_close_reason_mix",
                "telemetry_availability"):
        assert json.dumps(mixed[key], sort_keys=True) == json.dumps(base[key], sort_keys=True), key
    assert mixed["meta"]["strategy_count"] == base["meta"]["strategy_count"]
    assert mixed["meta"]["trade_count"] == base["meta"]["trade_count"]
    assert mixed["meta"]["connected_trade_count"] == 2


def test_the_open_book_is_the_state_verbatim_with_protection():
    res, _ = _run(_connected_only(fills=_fills(), state=_state("PARTIAL")))
    pos = res["connected_wallets"][0]["open_positions"]
    assert pos == _state("PARTIAL")["positions"]
    assert pos[0]["protection"] == "PARTIAL" and pos[0]["coveredSize"] == "0.5"


def test_a_null_state_is_an_unknown_open_book():
    res, _ = _run(_connected_only(fills=_fills(), state=None))
    w = res["connected_wallets"][0]
    assert w["state_read"] == "unavailable" and w["open_positions"] is None


# ── unknown is never empty ──────────────────────────────────────────────────────────────────────────
def test_empty_discovery_with_fills_uses_the_userfills_fallback():
    meta = {}
    fx = _connected_only(fills=_fills())
    trades = review.fetch_closed_trades(review._FixtureClient(fx), CW, None, None, None, meta, strict=True)
    assert len(trades) == 2 and all(t["source"] == "onchain_fills" for t in trades)
    assert "closed_trades_unknown" not in meta


def test_a_failed_userfills_read_is_unknown_never_no_trades():
    res, _ = _run(_connected_only(fills=None, state=_state()))   # no hl::userFills key → the read failed
    w = res["connected_wallets"][0]
    assert w["closed_trades_unknown"] is True
    assert w["closed_trade_count"] is None and w["realized_pnl"] is None and w["timing_summary"] is None
    assert CW in res["meta"]["closed_trades_unknown"]
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "unknown"                # never connected_no_trades / "no trades"
    assert "never 'no trades'" in out["meta"]["next_action"]


def test_an_empty_fills_answer_is_a_real_zero():
    res, _ = _run(_connected_only(fills=[], state=_state()))
    w = res["connected_wallets"][0]
    assert w["closed_trades_unknown"] is False and w["closed_trade_count"] == 0
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "connected_no_trades"
    assert "do NOT pitch a strategy" in out["meta"]["next_action"]


def test_the_2000_fill_cap_marks_the_totals_as_at_least():
    pad = [{"coin": "BTC", "dir": "Open Long", "sz": "0.001", "px": "60000", "closedPnl": "0", "fee": "0.01",
            "time": T0 - 10 * 86400000 - i, "oid": 100 + i} for i in range(review.HL_FILLS_CAP - 4)]
    res, _ = _run(_connected_only(fills=pad + _fills(), state=_state()))
    w = res["connected_wallets"][0]
    assert w["fills_capped"] is True and w["closed_trade_count"] == 2
    assert CW in res["meta"]["fills_capped"]


def test_an_older_mcp_with_no_connected_key_is_unavailable_not_none():
    fx = {"strategy_list": {"strategies": []}, "user_get_me": {"user": {"wallets": []}}}
    res, _ = _run(fx)
    assert res["meta"]["connected_wallets_status"] == "unavailable"
    assert res["connected_wallets"] is None                          # unknown, never []
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "unknown"                    # never "no_strategies" → pitch
    assert "couldn't load" in out["meta"]["next_action"]


def test_a_mixed_user_on_an_older_mcp_is_told_the_connected_wallets_did_not_load():
    fx = copy.deepcopy(_base())
    fx["user_get_me"] = {"user": {"wallets": []}}                    # no connected_wallets keys at all
    res, _ = _run(fx)
    assert res["connected_wallets"] is None
    out = review._slim_for_context(res)
    assert out["meta"]["book_state"] == "has_trades"
    assert "connected wallets couldn't be loaded" in out["meta"]["next_action"]


def test_a_connected_only_review_leads_with_the_connected_read_and_hands_leaks_to_quant_desk():
    res, _ = _run(_connected_only(fills=_fills(), state=_state()))
    nxt = review._slim_for_context(res)["meta"]["next_action"]
    assert "lead with connected_wallets[]" in nxt and "quant-desk" in nxt and "Senpi-only and empty" in nxt


# ── book_state ──────────────────────────────────────────────────────────────────────────────────────
def test_book_state_never_pitches_a_user_with_connected_wallets():
    assert review._book_state(0, 0, False, 1, 3)[0] == "has_trades"
    st, nxt = review._book_state(0, 0, False, 1, 0)
    assert st == "connected_no_trades" and "discover" not in nxt and "market-pulse" not in nxt
    assert review._book_state(0, 0, False, 0, 0, "unavailable")[0] == "unknown"
    assert review._book_state(0, 0, False)[0] == "no_strategies"           # unchanged default
    assert review._book_state(2, 0, False, 1, 4)[0] == "has_trades"
    assert review._book_state(0, 0, True, 1, 4)[0] == "unknown"            # token problem still wins
    assert review._book_state(0, 0, False, 1, 0, "ok", 1)[0] == "unknown"  # fills unread ≠ no trades


def test_a_manual_trade_is_never_a_premature_dsl_exit():
    assert review._is_premature_exit({"terminal": "MANUAL_TRADE", "tier_reached": 0,
                                      "high_water_roe": 1.0}) is False


# ── steps ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_timing_step_carries_the_connected_read_and_matches_all():
    fx = _connected(_base(), fills=_fills(), state=_state())
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
    assert t["connected_wallets"] == allr["connected_wallets"]
    assert s["meta"]["strategy_count"] == allr["meta"]["strategy_count"]
    assert all(r["exit_reason"]["terminal"] == "MANUAL_TRADE"
               for r in te["trades"] if r.get("wallet_kind") == "connected")


# ── fix round 1 ─────────────────────────────────────────────────────────────────────────────────────
def test_last_n_is_capped_per_kind_so_senpi_aggregates_match_the_senpi_only_run():
    saved = _env()
    try:
        base = review.run(review._FixtureClient(_base()), window_days=WINDOW_DAYS, last_n=1,
                          want_market=False, now_ms=NOW_MS)
        fx = _connected(_base(), fills=_fills(), state=_state())
        mixed = review.run(review._FixtureClient(fx), window_days=WINDOW_DAYS, last_n=1,
                           want_market=False, now_ms=NOW_MS)
    finally:
        _restore(saved)
    assert base["meta"]["trade_count"] >= 1
    for key in ("timing_summary", "pnl_summary", "strategies", "closed_strategies"):
        assert json.dumps(mixed[key], sort_keys=True) == json.dumps(base[key], sort_keys=True), key
    assert mixed["meta"]["trade_count"] == base["meta"]["trade_count"]
    assert mixed["meta"]["connected_trade_count"] == 1


def test_degraded_does_not_claim_no_strategies_when_connected_wallets_did_not_load():
    meta = {"connected_wallets_status": "unavailable", "warnings": []}
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
    sec = sk.split("## Connected wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    for needle in ("`MANUAL_TRADE`", "manual trade", "no DSL-preset or strategy-tuning coaching",
                   "`closed_trades_unknown: true`", "`fills_capped: true`", "at least", "`connected_no_trades`",
                   "`close.py`", "`edit_position`", "`strategy_*`", "`ratchet_stop_*`", "`protection`",
                   "Wallets on senpi.ai (web)", "no open positions on the Hyperliquid main and xyz dexes",
                   "never a bare \"no positions\"", "`connected_wallets: null`", "A pasted address is never described as saved"):
        assert needle in sec, needle


def test_skill_never_prints_a_read_error_code():
    sec = " ".join(open(SKILL, encoding="utf-8").read().split()).split(
        "## Connected wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    assert "never print the code" in sec and "`state: null`" in sec


def test_description_splits_leak_routing_with_quant_desk():
    desc = _skill().split("license:", 1)[0]
    assert "a CONNECTED wallet" in desc and "quant-desk" in desc
    assert "Senpi strategies" in desc
    for words in ('"my MetaMask"', '"my own Hyperliquid wallet"', '"my connected wallet"'):
        assert words in desc, words


def test_the_review_of_a_connected_wallet_stays_here_only_leaks_go_to_the_desk():
    """Ruling (R1 final review): this skill owns the REVIEW of a connected wallet ("review my trades",
    "master my week"); only leaks and "what did I miss" on a connected wallet go to quant-desk."""
    sk = _skill()
    desc = sk.split("license:", 1)[0]
    assert ('"where am I leaking" or "what did I miss" about a CONNECTED wallet the user trades by hand') in desc
    assert '"master my week" about a CONNECTED wallet' not in desc
    assert ('Connected wallets are reviewed here too, read-only, as manual trades — "review my trades" '
            'and "master my week" on a connected wallet stay in this skill') in desc
    sec = sk.split("## Connected wallets (read-only, traded by hand)", 1)[1].split("## ", 1)[0]
    assert ('The review itself — "review my trades", "master my week" on a connected wallet — stays '
            'here') in sec


def test_its_the_strategy_rule_is_scoped_to_senpi_strategy_trades():
    """Guardrail 5 used to say "Route every fix to the strategy config" unscoped, contradicting the
    connected-wallet rule (coach the user's own process, no strategy pitch)."""
    sk = _skill()
    assert ("**It's the strategy, not the user — on Senpi strategy trades.** Route every fix on a Senpi "
            "strategy trade to the strategy config") in sk
    assert "On a connected wallet there is no strategy: coach the user's own process" in sk
    desc = sk.split("license:", 1)[0]
    assert "it's the STRATEGY not the user (on Senpi strategy trades)" in desc
    assert "This section covers **Senpi strategy** trades. A connected wallet's trades are the user's own" in sk


def test_more_gains_never_pitches_a_connected_only_user():
    row = next(l for l in open(SKILL, encoding="utf-8").read().splitlines() if "How could I make more gains?" in l)
    assert "never for a connected-only user" in row


def test_readme_row_matches_the_skill_version():
    version = re.search(r'version: "([0-9.]+)"', open(SKILL, encoding="utf-8").read()).group(1)
    assert f"| [`senpi-improve-trades`](senpi-improve-trades/) | {version} |" in open(README, encoding="utf-8").read()
