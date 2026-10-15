"""Raptor must not emit when it cannot prove an asset is not already held.

The copied-trader origin is strategy-local scanner state. Emitting a second signal for an
already-held asset can replace that origin even though OPEN_POSITION later skips the signal as
position_already_exists. A failed clearinghouse read is indistinguishable from an empty book, so
entry scanning must fail closed instead of allowing that overwrite.

Run: python3 -m pytest strategies/tests/test_raptor_held_read_fail_closed.py -q
"""
import os
import sys

import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TRADER = "0x" + "0" * 39 + "1"


def _load():
    directory = os.path.join(_ROOT, "raptor", "main", "scanners")
    sys.path.insert(0, directory)
    try:
        for module in ("scoring", "scan"):
            sys.modules.pop(module, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(directory)


def _inputs():
    runtime = yaml.safe_load(open(os.path.join(_ROOT, "raptor", "main", "runtime.yaml")))
    return dict(next(s for s in runtime["scanners"] if s.get("type") == "external_scanner")["inputs"])


class _State:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None


class _Mcp:
    def __init__(self, clearinghouse_error=True):
        self.clearinghouse_error = clearinghouse_error

    def call_tool(self, name, _args):
        if name == "strategy_get_clearinghouse_state":
            if self.clearinghouse_error:
                raise RuntimeError("clearinghouse unavailable")
            return {"unexpected": "shape"}
        if name == "discovery_get_top_traders":
            return {"data": {"traders": [{
                "address": _TRADER,
                "tcsLabel": "ELITE",
                "tcsValue": 90,
                "unRealizedProfitAndLoss": 1_000_000,
                "realizedProfitAndLoss": 100_000,
                "returnOnInvestment": 60,
            }]}}
        if name == "leaderboard_get_markets":
            return {"data": {"markets": [{
                "token": "BTC",
                "dex": "",
                "direction": "LONG",
                "pct_of_top_traders_gain": 10,
                "trader_count": 20,
                "token_price_change_pct_4h": 1,
                "token_price_change_pct_1h": 0.5,
                "contribution_pct_change_15m": 1,
                "contribution_pct_change_1h": 1,
                "is_dominant_direction": True,
            }]}}
        if name == "leaderboard_get_trader_positions":
            return {"data": {"positions": {"positions": [{
                "market": "BTC",
                "direction": "LONG",
                "size": 10,
                "notional_size": 1_000_000,
                "account_value": 2_000_000,
                "leverage": 10,
                "delta_pnl": 500_000,
            }]}}}
        raise AssertionError(f"unexpected tool call {name!r}")


class _Ctx:
    wallet = "0x" + "0" * 40
    scanner_name = "test"
    interval_seconds = 180
    dry_run = False

    def __init__(self, clearinghouse_error=True):
        self.senpi_mcp = _Mcp(clearinghouse_error)
        self.state = _State()


def test_clearinghouse_read_failure_emits_no_candidate():
    assert _load().scan(_inputs(), _Ctx()) == []


def test_malformed_clearinghouse_payload_emits_no_candidate():
    assert _load().scan(_inputs(), _Ctx(clearinghouse_error=False)) == []
