"""The emit path must survive a candidate — the gap that shipped a live KeyError.

WHAT HAPPENED (2026-10-12). Moving the condor family off the 4h gain board changed the
scorer's return keys from `sm_gain_share` / `sm_traders` to `cohort_conviction` /
`cohort_positioned`, but `scan.py`'s emit block still read the old names. Every test passed:
they exercised `scoring.evaluate_trend_continuation` directly and never ran `scan()` far
enough to BUILD a signal, because the emit block only executes once a candidate clears the
score floor. The failure therefore could not appear until the strategy was about to open a
position — and it appeared on a live $600 wallet as

    scaffold_tick_error  error_type: KeyError  error: 'sm_gain_share'

six ticks in a row, on the first ticks where the new cohort actually produced a candidate.
The irony is exact: the cohort fix worked, and that is what reached the broken line.

So this file drives `scan()` end to end with a stubbed MCP shaped like the live responses,
and asserts a signal comes out whole. wild-condor is covered by proxy —
`strategies/tests/test_condor_family_cohort.py` pins its scanner as condor's byte for byte
apart from the log prefix.

Run: python3 -m pytest strategies/condor/tests/test_emit_path.py -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PKG, "main", "scanners"))

import scan  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(PKG, "main", "runtime.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s.get("type") == "external_scanner")
INPUTS = SIGNALS["inputs"]
DECLARED = set(SIGNALS["signal_data_schema"])


def _board_row(token, p4h, p1h=None, c15m=2.5):
    return {"token": token, "dex": "", "is_dominant_direction": True, "direction": "LONG",
            "trader_count": 120, "pct_of_top_traders_gain": 7.0,
            "token_price_change_pct_4h": p4h,
            "token_price_change_pct_1h": p4h / 4 if p1h is None else p1h,
            "contribution_pct_change_15m": c15m, "contribution_pct_change_1h": 1.0}


def _instrument(coin, px=100.0, oi=100000.0):
    return {"name": coin, "context": {"openInterest": str(oi), "markPx": str(px),
                                      "prevDayPx": str(px * 0.95), "dayNtlVlm": "500000000",
                                      "funding": "0.0003"}}


class _MCP:
    """Shaped like the live responses: a board with a strong mover, a deep cohort holding it."""
    def __init__(self):
        self.calls = []

    def call_tool(self, tool, args):
        self.calls.append(tool)
        if tool == "market_list_instruments":
            return {"data": {"instruments": [_instrument("BTC"), _instrument("ETH", 50.0)]}}
        if tool == "leaderboard_get_markets":
            return {"data": {"markets": [_board_row("BTC", 5.0), _board_row("ETH", 4.0)]}}
        if tool == "discovery_get_top_traders":
            return {"data": {"traders": [
                {"address": f"0x{i:040x}", "realizedProfitAndLoss": 5_000_000} for i in range(60)]}}
        if tool == "discovery_get_trader_state":
            return {"data": {"traders": [
                {"address": a, "openPositions": [{"coin": "BTC", "szi": "1.0"}]}
                for a in args["trader_addresses"]]}}
        if tool == "strategy_get_clearinghouse_state":
            return {"data": {"main": {"assetPositions": [], "withdrawable": "5000",
                                      "marginSummary": {"accountValue": "5000"}},
                             "xyz": {"assetPositions": [], "withdrawable": "5000",
                                     "marginSummary": {"accountValue": "5000"}}}}
        if tool == "strategy_get_asset_trading_limits":
            return {"data": {"max_leverage": 20}}
        return {}


class _State:
    def __init__(self): self._l = []
    def last(self): return self._l[-1] if self._l else None
    def append(self, d): self._l.append(d)


def _run():
    ctx = type("C", (), {"senpi_mcp": _MCP(), "wallet": "0xw", "state": _State()})()
    return scan.scan(dict(INPUTS), ctx)


def test_a_qualifying_candidate_emits_without_raising():
    """The regression. Before the fix this raised KeyError('sm_gain_share')."""
    out = _run()
    assert out, "no signal emitted from a board + cohort that should qualify"
    assert out[0]["asset"] in ("BTC", "ETH")
    assert out[0]["direction"] == "LONG"


def test_every_emitted_key_is_declared_in_the_signal_schema():
    """An emitted key the runtime does not know about is dropped silently; a declared key
    that is never emitted is a lie in the manifest. Both directions are checked."""
    out = _run()
    emitted = set(out[0]["data"])
    undeclared = emitted - DECLARED
    assert not undeclared, f"emitted but NOT declared in signal_data_schema: {sorted(undeclared)}"
    required = {k for k, v in SIGNALS["signal_data_schema"].items()
                if isinstance(v, dict) and v.get("required", True)}
    missing = required - emitted
    assert not missing, f"declared as required but never emitted: {sorted(missing)}"


def test_the_signal_carries_the_cohort_numbers_the_scorer_produced():
    """The specific keys that broke: the emit block must read what the scorer returns."""
    data = _run()[0]["data"]
    assert data["cohortConvictionPct"] > 0, "cohort conviction did not reach the signal"
    assert data["cohortPositioned"] >= INPUTS["minPositionedCount"]
    assert "smGainSharePct" not in data, "a board-era key survived the cohort migration"
    assert "smTraders" not in data


def test_the_scorer_and_the_emit_block_agree_on_their_key_names():
    """Catch the mismatch at the source rather than only through a full run."""
    import inspect

    import scoring
    returned = set(re.findall(r'"([a-z_0-9]+)":',
                              inspect.getsource(scoring.evaluate_trend_continuation).split(
                                  "return {")[-1]))
    consumed = set(re.findall(r'best\["([a-z_0-9]+)"\]', inspect.getsource(scan.scan)))
    missing = consumed - returned
    assert not missing, (
        f"scan() reads {sorted(missing)} off the scorer's result, which does not return it — "
        "this is exactly the shape of the KeyError this file exists for")


import re  # noqa: E402  (used by the test above; kept local to avoid shadowing in other suites)
