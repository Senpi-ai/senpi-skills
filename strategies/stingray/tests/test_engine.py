"""Stingray scanner tests. Pure helpers + a scan() smoke run against a fake ctx (no network).
Run: python3 -m pytest strategies/stingray/tests -q  (or plain `python3 .../test_engine.py`)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))
import scan     # noqa: E402
import scoring  # noqa: E402


# ── venue-name + dedup helpers (the XYZ-prefix fix) ──

def test_venue_asset_reattaches_xyz_prefix():
    # the leaderboard returns BARE tokens with the DEX in a separate field; market reads + emits need xyz:
    assert scan._venue_asset("NVDA", "xyz") == "xyz:NVDA"
    assert scan._venue_asset("BRENTOIL", "xyz") == "xyz:BRENTOIL"
    assert scan._venue_asset("BTC", "") == "BTC"          # main-DEX passes through
    assert scan._venue_asset("xyz:GOLD", "xyz") == "xyz:GOLD"   # already prefixed → no double-prefix
    assert scan._venue_asset("SILVER", None) == "SILVER"  # missing dex → treated as main (safe fallback)


def test_bare_strips_prefix_for_dedup():
    assert scan._bare("xyz:NVDA") == "NVDA"
    assert scan._bare("NVDA") == "NVDA"
    assert scan._bare("btc") == "BTC"


# ── scan() smoke: an XYZ board asset must emit + be price-gated under its xyz: name ──

class _State:
    def __init__(self):
        self._log = []

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)

    def __len__(self):
        return len(self._log)


class _MCP:
    """Board carries a strong LONG on the XYZ asset NVDA (bare token, dex 'xyz') and on crypto BTC.
    Captures every market_get_asset_data call so the test can assert the venue-correct name was used."""
    def __init__(self):
        self.asset_data_calls = []

    def call_tool(self, tool, args):
        args = args or {}
        if tool == "strategy_get_clearinghouse_state":
            return {"main": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []},
                    "xyz":  {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}}
        if tool == "leaderboard_get_markets":
            return {"markets": [
                {"token": "NVDA", "dex": "xyz", "direction": "long",  "pct_of_top_traders_gain": 80, "volume": 1000, "trader_count": 50},
                {"token": "NVDA", "dex": "xyz", "direction": "short", "pct_of_top_traders_gain": 20, "volume": 1000, "trader_count": 50},
                {"token": "BTC",  "dex": "",    "direction": "long",  "pct_of_top_traders_gain": 75, "volume": 2000, "trader_count": 60},
                {"token": "BTC",  "dex": "",    "direction": "short", "pct_of_top_traders_gain": 25, "volume": 2000, "trader_count": 60},
            ]}
        if tool == "market_get_asset_data":
            self.asset_data_calls.append({"asset": args.get("asset"), "dex": args.get("dex")})
            return {"data": {"candles": {"4h": [{"high": 100, "low": 100} for _ in range(6)]}}}  # NEUTRAL → gate passes
        return {}


class _Ctx:
    def __init__(self, mcp, state):
        self.senpi_mcp = mcp
        self.wallet = "0xWALLET0000000000000000000000000000wallet"
        self.state = state


def test_xyz_asset_emits_and_is_read_under_its_xyz_prefix():
    mcp = _MCP()
    inputs = {"minTiltLong": 58, "minTiltShort": 42, "marginPctBase": 12,
              "maxLong": 2, "maxShort": 2, "leverageDefault": 4,
              "leaderboardLimit": 100, "convictionVolFloor": 1.0}
    out = scan.scan(inputs, _Ctx(mcp, _State()))
    assets = {e["asset"] for e in out}
    # THE FIX: the XYZ asset is emitted WITH its venue prefix, never the bare token
    assert "xyz:NVDA" in assets, assets
    assert "NVDA" not in assets, assets
    assert "BTC" in assets, assets      # main-DEX asset is unaffected
    # and the price-confirmation read hit the venue-correct name + dex — not bare NVDA on the main DEX
    xyz_reads = [c for c in mcp.asset_data_calls if c["asset"] == "xyz:NVDA"]
    assert xyz_reads and xyz_reads[0]["dex"] == "xyz", mcp.asset_data_calls
    assert all(c["asset"] != "NVDA" for c in mcp.asset_data_calls), mcp.asset_data_calls


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
