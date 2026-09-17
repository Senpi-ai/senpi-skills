"""Chimp / Gibbon / Gorilla / Orangutan: an entry scores against the cohort the recalibration read, never the 4h board.

The four share one scan.py (byte-identical — asserted below; scoring.py is shared pairwise). build_posture
ranks the candidates against the proven cohort, but the slot-fill loop handed score_candidate a stub
`{"available": True}` with no cohort in it, so smart_lean fell through to the leaderboard board: every
entry was scored against the 4h crowd the thesis is built to disagree with. The cohort now travels with
the posture in ctx.state and the entry scorer reads it from there.

Payload shapes follow the tool guides: discovery_get_trader_state open positions carry coin / szi
(signed) / entryPx / positionValue / ...; discovery_get_top_traders rows carry address /
realizedProfitAndLoss / ...; leaderboard_get_markets rows carry token / dex / direction /
pct_of_top_traders_gain; market_list_instruments rows carry name / is_delisted / context.dayNtlVlm /
context.markPx / context.prevDayPx; candles are o/h/l/c/v strings.

Run: python3 -m pytest strategies/tests/test_chimp_family_topup_cohort.py -q
"""
import os
import sys

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAMILY = ("chimp", "gibbon", "gorilla", "orangutan")


def _load(pkg):
    d = os.path.join(_ROOT, pkg, "main", "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


def _inputs(pkg):
    ry = yaml.safe_load(open(os.path.join(_ROOT, pkg, "main", "runtime.yaml")))
    return dict(next(s for s in ry["scanners"] if s.get("type") == "external_scanner")["inputs"])


class _State:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def recent(self, n):
        return self.rows[-n:]

    def __len__(self):
        return len(self.rows)


class _Mcp:
    """Canned reads keyed by tool name; a callable answers from the call's args."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        r = self.responses[name]
        return r(args) if callable(r) else r


class _Ctx:
    def __init__(self, responses):
        self.senpi_mcp = _Mcp(responses)
        self.wallet = "0x" + "0" * 40
        self.state = _State()
        self.scanner_name = "test"
        self.interval_seconds = 3600
        self.dry_run = False


# ── payloads ──────────────────────────────────────────────────────────────────
def _inst(name, vol, mark, prev):
    xyz = name.startswith("xyz:")
    return {"name": name, "dex": "xyz" if xyz else "", "is_delisted": False, "max_leverage": 40,
            "only_isolated": xyz, "context": {"dayNtlVlm": str(vol), "markPx": str(mark),
                                              "prevDayPx": str(prev), "funding": "0.0000125"}}


# a risk-on tape: every group up, BTC the strongest 24h move
MAIN = [_inst("BTC", 9.1e8, 103.0, 100.0), _inst("ETH", 6.2e8, 102.0, 100.0), _inst("SOL", 3.1e8, 101.5, 100.0),
        _inst("HYPE", 1.5e8, 101.0, 100.0), _inst("XRP", 1.2e8, 100.8, 100.0), _inst("DOGE", 1.0e8, 100.6, 100.0)]
XYZ = [_inst("xyz:NVDA", 8.0e7, 101.5, 100.0), _inst("xyz:SP500", 6.0e7, 100.9, 100.0),
       _inst("xyz:GOLD", 4.0e7, 100.3, 100.0)]

SMART = ["0x" + "0" * 39 + str(i) for i in range(1, 6)]          # >= $1M realized: all long BTC
CROWD = ["0x" + "0" * 38 + "1" + str(i) for i in range(0, 5)]    # $10k-100k realized: all short BTC


def _trader(addr, realized):
    return {"address": addr, "realizedProfitAndLoss": realized, "unRealizedProfitAndLoss": 0.0,
            "profitAndLoss": realized, "returnOnInvestment": 40.0, "tcsLabel": "ELITE"}


def _top_traders(args):
    if args.get("offset", 0):
        return {"data": {"traders": []}}
    return {"data": {"traders": [_trader(a, 2_400_000.0) for a in SMART] + [_trader(a, 45_000.0) for a in CROWD]}}


def _pos(coin, szi):
    return {"coin": coin, "coinDisplayName": coin, "szi": str(szi), "entryPx": "100.0",
            "positionValue": str(abs(szi) * 100.0), "unrealizedPnl": "300.0", "marginUsed": str(abs(szi) * 20.0),
            "leverage": {"type": "cross", "value": 5}, "returnOnEquity": "0.15", "liquidationPx": "80.0",
            "cumFunding": {"allTime": "0.0"}}


def _trader_state(args):
    return {"data": {"traders": [{"address": a, "openPositions": [_pos("BTC", 12.0 if a in SMART else -0.4)]}
                                 for a in args["trader_addresses"]]}}


def _row(token, direction, pct, traders, dominant):
    return {"token": token, "dex": "", "direction": direction, "max_leverage": 40, "pct_of_top_traders_gain": pct,
            "contribution_pct_change_15m": 0.4, "contribution_pct_change_1h": 0.9, "contribution_pct_change_4h": 1.6,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5, "token_price_change_pct_4h": 1.3,
            "day_notional_volume": 9.1e8, "trader_count": traders, "is_dominant_direction": dominant}


# the 4h crowd is SHORT BTC (87.5% of the board's BTC gains) — the opposite of the proven cohort
BOARD = {"data": {"markets": [_row("BTC", "short", 70.0, 38, True), _row("BTC", "long", 10.0, 9, False)],
                  "source_trader_count": 100, "window": "4h", "timestamp": 1789000000}}


def _c(px, i):
    return {"t": 1789000000000 + i * 3600000, "T": 1789000000000 + (i + 1) * 3600000 - 1, "o": str(px - 0.3),
            "h": str(px + 0.4), "l": str(px - 0.5), "c": str(px), "v": "8200", "n": 310}


C1 = [_c(100 + i, i) for i in range(12)]   # 1h: 11 rising closes -> 'up', +11% 24h mom
C4 = [_c(96 + i, i) for i in range(8)]     # 4h: 7 rising closes -> 'up', strength 1.0


def _asset_data(args):
    return {"data": {"asset": args["asset"], "candles": {"1h": C1, "4h": C4},
                     "asset_context": {"max_leverage": 40, "funding": "0.0000125", "dayNtlVlm": "910000000"}}}


CLEARINGHOUSE = {"data": {
    "main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                               "totalRawUsd": "1000.0"}, "withdrawable": "1000.0", "assetPositions": []},
    "xyz": {"marginSummary": {"accountValue": "0.0", "totalMarginUsed": "0.0", "totalNtlPos": "0.0",
                              "totalRawUsd": "0.0"}, "withdrawable": "0.0", "assetPositions": []}}}


def _ctx():
    return _Ctx({
        "market_list_instruments": lambda a: {"data": {"instruments": XYZ if a.get("dex") == "xyz" else MAIN + XYZ}},
        "discovery_get_top_traders": _top_traders,
        "discovery_get_trader_state": _trader_state,
        "leaderboard_get_markets": BOARD,
        "market_get_funding_regime": {"data": {"regime": "LONG_CROWDED"}},
        "strategy_get_clearinghouse_state": CLEARINGHOUSE,
        "market_get_asset_data": _asset_data,
    })


@pytest.mark.parametrize("pkg", FAMILY)
def test_entry_scores_against_the_recalibration_cohort(pkg):
    """One free slot; the top-ranked long is BTC. Its lean must be the proven cohort's +1.0 (five
    members long), not the board's (which reads the crowd SHORT and would score it -0.75)."""
    scan = _load(pkg)
    signals = scan.scan(dict(_inputs(pkg), maxSlots=1), _ctx())
    assert [(s["asset"], s["direction"]) for s in signals] == [("BTC", "LONG")]
    data = signals[0]["data"]
    assert data["lean"] == 1.0, f"scored against the board lean ({data['lean']}), not the cohort's +1.0"
    assert "lean +1.00" in data["reasons"] and data["score"] == 10.0


def test_scan_py_is_byte_identical_across_the_family():
    """The shared test above is valid because the four scanners are one file."""
    blobs = {pkg: open(os.path.join(_ROOT, pkg, "main", "scanners", "scan.py"), "rb").read() for pkg in FAMILY}
    assert len(set(blobs.values())) == 1, "chimp-family scan.py drifted — fix all four together"
    assert b'posture.get("cohort")' in blobs["chimp"]
