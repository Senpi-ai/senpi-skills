"""Wire-shape guard for both spider sleeves' emitted candidates.

The v3.0.4 supervised scaffold strips any top-level candidate key it does not
recognize, then validates marginPct against (0, 100]. These tests drive the real
scan() emit/sizing path for each sleeve through a fake ctx and assert:
  (a) NO `marginUsd` key is emitted (it would be silently dropped on the wire);
  (b) emitted `marginPct` is a percent in [1, 100], never a fraction.

Each sleeve's scan.py imports a sibling `scoring` module, so we load each from
its own scanners/ dir under a distinct module name and inject the dir on
sys.path for the duration of the import.
"""

import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_scan(sleeve):
    scanners_dir = os.path.join(_HERE, sleeve, "scanners")
    sys.path.insert(0, scanners_dir)
    try:
        # Fresh `scoring` per sleeve (the two dirs hold different scoring.py).
        for name in ("scoring", f"spider_{sleeve}_scan"):
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(
            f"spider_{sleeve}_scan", os.path.join(scanners_dir, "scan.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(scanners_dir)


class _FakeState:
    def __init__(self):
        self._records = []

    def __len__(self):
        return len(self._records)

    def last(self):
        return self._records[-1] if self._records else {}

    def append(self, rec):
        self._records.append(rec)


class _FakeMcp:
    """Returns canned tool payloads driving exactly one qualifying candidate."""

    def __init__(self, sleeve):
        self._sleeve = sleeve

    def call_tool(self, tool, args):
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"marginSummary": {"accountValue": 10000.0},
                             "assetPositions": []}}
        if tool == "market_list_instruments":
            return {"instruments": [
                {"name": "BTC", "max_leverage": 50,
                 "context": {"coin": "BTC", "dayNtlVlm": 1e9}},
                {"name": "xyz:NVDA", "max_leverage": 20,
                 "context": {"coin": "xyz:NVDA", "dayNtlVlm": 1e9}},
            ]}
        if tool == "leaderboard_get_markets":
            return {"markets": [
                {"token": "BTC", "direction": "long", "longPct": 80},
                {"token": "NVDA", "direction": "long", "longPct": 80},
            ]}
        if tool == "market_get_asset_data":
            return {"data": {"candles": self._candles(), "asset_context": {}}}
        return None

    def _candles(self):
        if self._sleeve == "scalp":
            # 15m: deep-oversold RSI + large negative stretch (fade long); 1h bullish.
            c15 = [{"close": 100.0, "high": 100, "low": 100} for _ in range(20)]
            c15 += [{"close": 100 - i, "high": 101 - i, "low": 99 - i}
                    for i in range(1, 11)]  # straight downtrend → low RSI, neg stretch
            c1 = [{"close": 90 + i, "high": 90 + i, "low": 89 + i} for i in range(8)]
            return {"15m": c15, "1h": c1}
        # swing: 1h + 4h strong uptrend → bullish structure, positive RS.
        c1 = [{"close": 100 + i, "high": 100 + i, "low": 99 + i} for i in range(30)]
        c4 = [{"close": 100 + 2 * i, "high": 100 + 2 * i, "low": 99 + 2 * i}
              for i in range(30)]
        return {"1h": c1, "4h": c4}


class _FakeCtx:
    def __init__(self, sleeve):
        self.senpi_mcp = _FakeMcp(sleeve)
        self.state = _FakeState()
        self.wallet = "0xtest"


_SLEEVE_INPUTS = {
    "scalp": {
        "allowedAssets": ["BTC"],
        "minScore": 1,
        "marginPct": 15,
        "maxLeverage": 5,
        "rsiOversold": 30,
        "rsiOverbought": 70,
        "stretchThresholdPct": 0.8,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "recentSignalTtlSeconds": 180,
    },
    "swing": {
        "cryptoAlts": [],
        "xyzIncludeSet": ["NVDA"],
        "xyzExcludeSet": [],
        "allowedAssets": ["xyz:NVDA"],
        "minScore": 1,
        "marginPct": 28,
        "maxLeverage": 10,
        "rsiMaxLong": 78,
        "useSmBonus": True,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "recentSignalTtlSeconds": 180,
        "xyzVolFloorUsd": 1000,
        "xyzMaxNames": 20,
        "xyzFreshDays": 21,
    },
}

_EXPECTED_PCT = {"scalp": 15.0, "swing": 28.0}


@pytest.mark.parametrize("sleeve", ["scalp", "swing"])
def test_emits_no_marginUsd_and_valid_marginPct(sleeve):
    mod = _load_scan(sleeve)
    out = mod.scan(_SLEEVE_INPUTS[sleeve], _FakeCtx(sleeve))

    assert out, f"{sleeve}: expected at least one emitted candidate"
    for sig in out:
        assert "marginUsd" not in sig, (
            f"{sleeve}: candidate must not emit top-level 'marginUsd' "
            f"(scaffold drops it; sizing intent lost)"
        )
        assert "marginPct" in sig, f"{sleeve}: candidate must emit 'marginPct'"
        mp = sig["marginPct"]
        assert isinstance(mp, (int, float)) and not isinstance(mp, bool)
        assert 1 <= mp <= 100, (
            f"{sleeve}: marginPct={mp} out of [1,100] — likely a fraction, "
            f"not a percent"
        )
        assert mp == pytest.approx(_EXPECTED_PCT[sleeve]), (
            f"{sleeve}: marginPct={mp}, expected {_EXPECTED_PCT[sleeve]}"
        )
