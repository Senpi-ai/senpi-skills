#!/usr/bin/env python3
"""Exit attribution from `ratchet_stop_list`, on rows shaped exactly as the MCP returns them.

2026-10-23, a real dev account: the MCP answers {success, data: {positions, count, query}}, and the engine
read `configs` / `ratchetStops` / `data` / `items`, so it found 0 of 25 rows and every exit read UNKNOWN.
Behind that, it kept the LAST row per asset, and the rows come newest first: with the key fixed alone,
every HYPE trade went to the 08-03 record and terrapin-u2's 09-23 exit got tier 3 where 0 was right.
Each trade now gets its own row: the stop order that filled, else the row alive during the trade.

Fixture: tests/fixtures/ratchet_stop_list_real.json (that account's rows, ids and wallets synthetic).
Trade times below are that account's on-chain round trips.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import review  # noqa: E402

with open(os.path.join(HERE, "fixtures", "ratchet_stop_list_real.json"), encoding="utf-8") as _f:
    REAL = json.load(_f)
HYPE = REAL["hype-momentum"]
TERRAPIN = REAL["terrapin-u2"]
HYPE_WALLET = HYPE["data"]["query"]["filter"]["strategyWalletAddress"]
TERRAPIN_WALLET = TERRAPIN["data"]["query"]["filter"]["strategyWalletAddress"]


def _trade(asset, open_ms, close_ms, oid=None, direction="long"):
    return {"asset": asset, "direction": direction, "open_time": open_ms, "close_time": close_ms,
            "closed_order_id": oid}


# real HYPE round trips (open, close, closing order id) from the same wallet's fills
HYPE_OCT = (1790996286430, 1791337877088, 566217646262)    # 10-03 02:58 → 10-07 01:51, row created 10-04
HYPE_AUG03 = (1785765449937, 1786017776628, 510859925446)  # 08-03 13:57 → 08-06 12:02, the OLDEST row
HYPE_AUG22 = (1787371498838, 1787375390396, 522717196608)  # 08-22 04:04 → 05:09, a scaled-in position
HYPE_AUG20 = (1787209997067, 1787214060093, 520621198613)  # 08-20 07:13 → 08:21, no row for it
TERRAPIN_SEP = (1789980546963, 1790179282223, 552189286958)  # 09-21 → 09-23, the NEWER BTC row (tier 0)
TERRAPIN_AUG = (1787152565676, 1789044963094, 536426410914)  # 08-19 → 09-10, the OLDER BTC row (tier 3)


class _Client:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append((tool, kw))
        if self.exc:
            raise self.exc
        return copy.deepcopy(self.resp)


def _records(resp, wallet=HYPE_WALLET, meta=None):
    strat = {"strategy_id": "strat-x", "wallet": wallet}
    return review._load_ratchet_records(_Client(resp), strat, {} if meta is None else meta)


def _exit(trade, resp, wallet=HYPE_WALLET):
    return review._exit_reason_for(trade, _records(resp, wallet))


def _no_oid(t):
    return (t[0], t[1], None)


def test_the_positions_envelope_is_read():
    recs = _records(HYPE)
    assert sum(len(v) for v in recs.values()) == 25, "the MCP's {positions, count} envelope must be read"
    er = _exit(_trade("HYPE", *HYPE_OCT), HYPE)
    assert er["terminal"] == "SL_TRIGGERED"


def test_newest_first_rows_give_each_trade_its_own_record_by_time():
    # without the closing order id, the time overlap alone must pick the right row of 25 newest-first rows
    oct_ = _exit(_trade("HYPE", *_no_oid(HYPE_OCT)), HYPE)
    aug = _exit(_trade("HYPE", *_no_oid(HYPE_AUG03)), HYPE)
    assert (oct_["terminal"], oct_["tier_reached"], oct_["high_water_roe"]) == ("SL_TRIGGERED", 2, 38.162205)
    assert (aug["terminal"], aug["tier_reached"], aug["high_water_roe"]) == ("SL_TRIGGERED", 2, 41.361222)


def test_the_stop_order_that_filled_is_an_exact_match():
    # the row's activeSLOrderId is the trade's closing order: decisive even with the times stripped
    t = _trade("HYPE", None, None, oid=HYPE_OCT[2])
    er = _exit(t, HYPE)
    assert (er["tier_reached"], er["high_water_roe"]) == (2, 38.162205)


def test_several_records_per_asset_over_time():
    # terrapin-u2 traded BTC twice: the newer row is tier 0, the older tier 3. Keeping the last row per
    # asset gave both trades tier 3.
    sep = _exit(_trade("BTC", *_no_oid(TERRAPIN_SEP)), TERRAPIN, TERRAPIN_WALLET)
    aug = _exit(_trade("BTC", *_no_oid(TERRAPIN_AUG)), TERRAPIN, TERRAPIN_WALLET)
    assert (sep["tier_reached"], sep["tier_number"]) == (0, 1)
    assert (aug["tier_reached"], aug["tier_number"]) == (3, 4)


def test_a_scaled_in_position_takes_the_row_that_ended_with_it():
    # 08-22: each add re-created the row (DELETED 02:11, 02:18, 02:39, 03:16, then SL_TRIGGERED 04:05 → 05:09)
    t = _trade("HYPE", 1787364000000, HYPE_AUG22[1])          # opened 02:00, before the first row
    er = _exit(t, HYPE)
    assert (er["terminal"], er["tier_reached"], er["high_water_roe"]) == ("SL_TRIGGERED", 0, 17.923844)


def test_a_trade_with_no_row_of_its_own_stays_unknown():
    # rows exist for HYPE before and after, none alive during this trade: never borrow a neighbour's
    er = _exit(_trade("HYPE", *_no_oid(HYPE_AUG20)), HYPE)
    assert er["terminal"] == "UNKNOWN" and er["tier_reached"] is None and er["tier_number"] is None


def test_a_quick_reentry_does_not_take_the_previous_positions_row():
    # 08-19: the position opened 16:01 ended on its stop at Tier 6 (row updated 19:22:28); the runtime
    # re-entered at 19:23 and closed at 19:28 with no row of its own
    first = _exit(_trade("HYPE", 1787155260000, 1787167320000), HYPE)
    assert (first["terminal"], first["tier_number"]) == ("SL_TRIGGERED", 6)
    again = _exit(_trade("HYPE", 1787167380000, 1787167680000), HYPE)
    assert again["terminal"] == "UNKNOWN"


def test_the_other_side_is_another_position():
    assert _exit(_trade("HYPE", *_no_oid(HYPE_OCT)), HYPE)["terminal"] == "SL_TRIGGERED"
    assert _exit(_trade("HYPE", *_no_oid(HYPE_OCT), direction="short"), HYPE)["terminal"] == "UNKNOWN"


def _xyz_resp():
    # an xyz row as the MCP returns it: asset carries the prefix, dex is "xyz"
    row = dict(HYPE["data"]["positions"][0], id="dsl-xyz-00", asset="xyz:MSTR", dex="xyz",
               activeSLOrderId=None, currentTierIndex=1, highWaterRoe=21.5)
    return {"success": True, "data": {"positions": [row], "count": 1}}


def test_xyz_assets_match_on_their_prefixed_symbol():
    er = _exit(_trade("xyz:MSTR", *_no_oid(HYPE_OCT)), _xyz_resp())
    assert (er["terminal"], er["tier_reached"], er["tier_number"]) == ("SL_TRIGGERED", 1, 2)
    # the bare symbol is a main-dex coin, not this row
    assert _exit(_trade("MSTR", *_no_oid(HYPE_OCT)), _xyz_resp())["terminal"] == "UNKNOWN"


def test_a_tier_minus_one_record_is_not_a_locked_tier():
    # -1 = the ladder never armed: the hard stop fired. No "Tier 0", and never a premature profit lock.
    row = dict(HYPE["data"]["positions"][0], currentTierIndex=-1, highWaterRoe=3.2, tierFloorPrice=None)
    er = _exit(_trade("HYPE", *_no_oid(HYPE_OCT)), {"success": True, "data": {"positions": [row], "count": 1}})
    assert (er["terminal"], er["tier_reached"], er["tier_number"]) == ("SL_TRIGGERED", -1, None)
    assert review._is_premature_exit(er) is False


def test_no_records_reads_unknown():
    resp = {"success": True, "data": {"positions": [], "count": 0}}
    meta = {}
    assert _records(resp, meta=meta) == {} and not meta.get("warnings")   # empty is a clean read
    er = _exit(_trade("HYPE", *HYPE_OCT), resp)
    assert er == {"terminal": "UNKNOWN", "tier_reached": None, "tier_number": None, "high_water_roe": None}


def test_a_failed_list_fails_open_with_a_warning():
    meta = {}
    recs = review._load_ratchet_records(_Client(exc=RuntimeError("timeout")), {"strategy_id": "s", "wallet": HYPE_WALLET}, meta)
    assert recs == {}
    assert any("ratchet_stop_list" in w and "UNKNOWN" in w for w in meta["warnings"])
    meta = {}
    _records({"success": False, "error": {"code": "UNAUTHORIZED"}}, meta=meta)
    assert any("ratchet_stop_list" in w for w in meta.get("warnings", [])), \
        "a success:false answer is a failed read, not an empty list"


def test_the_list_call_asks_for_history_and_the_largest_page():
    c = _Client({"success": True, "data": {"positions": [], "count": 0}})
    review._load_ratchet_records(c, {"strategy_id": "s", "wallet": HYPE_WALLET}, {})
    (tool, kw), = c.calls
    assert tool == "ratchet_stop_list" and kw["status"] == "ALL" and kw["limit"] == 100


# ── end to end: a closed strategy's trades through review.run ───────────────────────────────────────
NOW_MS = 1792800000000     # 2026-10-24


def _closed(open_ms, close_ms, oid, pnl):
    return {"closedOrderId": str(oid), "coin": "HYPE", "coinDisplayName": "HYPE", "szi": "1.0",
            "type": "Close Long", "entryPx": "80.0", "exitPx": "80.0", "leverage": {"type": "cross", "value": 3},
            "realizedPnl": pnl, "marginUsed": "30.0", "openTime": open_ms, "closeTime": close_ms}


def test_review_run_attributes_each_trade_to_its_own_record():
    fx = {
        "strategy_list": {"strategies": [
            {"strategyName": "hype-momentum", "tradingStrategyName": "hype-momentum", "id": "strat-hype",
             "strategyWalletAddress": HYPE_WALLET, "status": "CLOSED"}]},
        f"discovery_get_trader_history::{HYPE_WALLET}": {"closedPositions": [
            _closed(*HYPE_OCT, "1.61"), _closed(*HYPE_AUG22, "-2.18"),
            _closed(*HYPE_AUG20, "-10.29"), _closed(*HYPE_AUG03, "7.00")]},
        f"ratchet_stop_list::{HYPE_WALLET}": HYPE,
    }
    old = os.environ.get("SENPI_STATE_DIR")
    os.environ["SENPI_STATE_DIR"] = os.path.join(HERE, "fixtures", "registry")
    try:
        res = review.run(review._FixtureClient(fx), window_days=90, now_ms=NOW_MS, want_market=False)
    finally:
        if old is None:
            os.environ.pop("SENPI_STATE_DIR", None)
        else:
            os.environ["SENPI_STATE_DIR"] = old
    by_close = {t["close_time"]: t["exit_reason"] for t in res["trades"]}
    assert (by_close[HYPE_OCT[1]]["tier_number"], by_close[HYPE_OCT[1]]["high_water_roe"]) == (3, 38.162205)
    assert (by_close[HYPE_AUG03[1]]["tier_number"], by_close[HYPE_AUG03[1]]["high_water_roe"]) == (3, 41.361222)
    assert (by_close[HYPE_AUG22[1]]["tier_number"], by_close[HYPE_AUG22[1]]["source"]) == (1, "ratchet")
    assert (by_close[HYPE_AUG20[1]]["terminal"], by_close[HYPE_AUG20[1]]["source"]) == ("UNKNOWN", "unknown")
    assert res["telemetry_availability"]["exit_attribution"]["ratchet"] == 3


def test_the_contract_says_the_tier_one_based():
    # the engine's tier_reached / tier_index are 0-based; a reader is told tier_number ("Tier 1" = index 0)
    def norm(name):
        with open(os.path.join(HERE, "..", "references", name), encoding="utf-8") as f:
            return " ".join(f.read().split())
    shape, records = norm("output-shape.md"), norm("exit-records.md")
    assert '`tier_number` = the tier that locked, 1-based: say "Tier {tier_number}"' in shape
    assert "never print them as the tier" in shape
    assert "`currentTierIndex` is 0-based: say **Tier N = index + 1**" in records
