"""Kingfisher 1.3.0: the stop outside the 2% stop-out zone, 3 slots of 25% at a constant 3x, no clock,
a provisional score floor of 7, and a card that says what the code does.

Replayed as written (1.2.0: 8% ROE stop = 2.0% of price at 4x, floor 6): profit factor 0.81 over 416
trades, 71% of them ended at the stop. These tests pin the shipped numbers, the fake-desk tick (flat
margin, constant leverage, schema-clean data, a failed read degrades instead of crashing), the raised
floor, and the two card corrections (the RSI extreme docks a point, it never vetoed; the ladder's first
rung is +15% ROE, not +8%).

Run: python3 -B -m pytest strategies/kingfisher/tests -q
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "kingfisher_main_signals")
INPUTS = dict(SIGNALS["inputs"])
SCHEMA = SIGNALS["signal_data_schema"]


# ── fixtures: a fresh MACD cross built from the package's own math ───────────────────────

def _closes(direction):
    """40 flat bars, 8 bars of 0.05 against the trade, then 0.15 a bar with it, cut at the bar where the
    package's own MACD(12/26/9) histogram first flips sign — so the LAST bar is a fresh signal-line cross.
    At that bar RSI(14) sits just past 50 (confirms), the MACD line is still on the far side of zero and
    the histogram is under the 0.03%-of-price strength bonus: a fresh cross + RSI and nothing else."""
    sign = -1 if direction == "LONG" else 1
    px, out = 100.0, [100.0] * 40
    for _ in range(8):
        px += sign * 0.05
        out.append(px)
    for _ in range(40):
        px -= sign * 0.15
        out.append(px)
    _, _, hist = scoring.calc_macd(out, 12, 26, 9)
    off = len(out) - len(hist)
    for i in range(1, len(hist)):
        flipped = (hist[i - 1] <= 0 < hist[i]) if direction == "LONG" else (hist[i - 1] >= 0 > hist[i])
        if off + i >= 48 and flipped:
            return out[: off + i + 1]
    raise AssertionError("fixture never crossed")


def _candles(direction="LONG", trend4h="FLAT"):
    """A market_get_asset_data `candles` dict in the feed's shape (o/h/l/c/v strings). `trend4h` BULL makes
    the 4h lows climb (BULLISH structure), BEAR makes the highs fall, FLAT is NEUTRAL."""
    one = [{"t": i, "T": i + 1, "s": "X", "i": "1h", "o": f"{c}", "h": f"{c}", "l": f"{c}", "c": f"{c}", "v": "1", "n": 1}
           for i, c in enumerate(_closes(direction))]
    four = []
    for i in range(8):
        lo, hi = {"BULL": (90 + i, 110 + i), "BEAR": (90 - i, 110 - i)}.get(trend4h, (90, 110))
        four.append({"t": i, "T": i + 1, "s": "X", "i": "4h", "o": "100", "h": f"{hi}", "l": f"{lo}", "c": "100", "v": "1", "n": 1})
    return {"1h": one, "4h": four}


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
    """Answers the two reads the scan makes: the clearinghouse (held names) and per-coin candles. A coin
    with no entry in `candles` gets an empty feed (no thesis), so a test controls exactly which names fire."""
    def __init__(self, candles=None, held=(), fail=()):
        self.candles, self.held, self.fail, self.calls = candles or {}, held, set(fail), []

    def call_tool(self, tool, args):
        self.calls.append((tool, args.get("asset")))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "strategy_get_clearinghouse_state":
            positions = [{"position": {"coin": c, "szi": "0.01", "marginUsed": "50"}} for c in self.held]
            return {"data": {"main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": f"{50 * len(self.held)}"},
                                      "assetPositions": positions}}}
        if tool == "market_get_asset_data":
            if args["asset"] in self.fail:
                raise RuntimeError("asset data down")
            return {"success": True, "data": {"candles": self.candles.get(args["asset"], {"1h": [], "4h": []})}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0xkingfisher", state, False
        self.scanner_name, self.interval_seconds = "kingfisher_main_signals", 900


# ── the shipped numbers ───────────────────────────────────────────────────────────────────

def test_the_stop_is_twelve_roe_four_of_price_and_there_is_no_clock():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["enabled"] is False
    assert pre["phase1"]["max_loss_pct"] == 12.0
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverage"] == 4.0      # 4.0% of price at 3x; 1.2.0 was 8 = 2.0%
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0] == {"trigger_pct": 15, "lock_hw_pct": 40}
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_sizing_leverage_cap_rails_cadence_and_version():
    strat = RUNTIME["strategy"]
    assert INPUTS["marginPct"] == strat["margin_pct"] == 25
    assert INPUTS["maxSlots"] == strat["slots"] == MANIFEST["catalog"]["max_slots"] == 3
    assert strat["slots"] * strat["margin_pct"] <= 100
    assert INPUTS["leverage"] == strat["default_leverage"] == MANIFEST["catalog"]["leverage_max"] == 3
    for gone in ("marginPctBase", "leverageDefault", "leverageMin", "leverageMax"):
        assert gone not in INPUTS, gone                                    # 1.2.0's tiered margin and 3-5x clamp
    assert INPUTS["minScore"] == 7
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > strat["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert MANIFEST["version"] == "1.3.0"


def test_the_card_and_the_runtime_prose_say_what_the_code_does():
    card = " ".join(str(MANIFEST["catalog"][f]) for f in ("tagline", "belief_plain", "thesis"))
    for stale in ("3-5x", "3x to 5x", "+8%", "~8%", "veto"):
        assert stale not in card.lower(), stale
    assert "3x" in card and "+15% ROE" in card and "12% ROE" in card
    prose = RUNTIME["description"] + open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8").read()
    for stale in ("3-5x", "+8%", "10% base margin", "max_loss 20", "x1.25 (>=6)"):
        assert stale not in prose, stale


# ── scan(): one tick against a fake desk ─────────────────────────────────────────────────

def test_a_tick_emits_the_flat_margin_at_three_x_inside_the_schema():
    candles = {"ETH": _candles("LONG", "BULL"), "SOL": _candles("SHORT", "BEAR")}
    mcp, st = _MCP(candles, held=("BTC",)), _State()
    out = scan.scan(dict(INPUTS), _Ctx(mcp, st))
    by = {s["asset"]: s for s in out}
    assert set(by) == {"ETH", "SOL"}
    assert by["ETH"]["direction"] == "LONG" and by["SOL"]["direction"] == "SHORT"
    for s in out:
        assert s["marginPct"] == 25 and s["leverage"] == 3 and s["data"]["leverage"] == 3
        assert s["data"]["score"] == 8                                    # fresh cross 4 + RSI 2 + aligned 4h 2
        assert set(s["data"]) <= set(SCHEMA) and all(v is not None for v in s["data"].values())
    assert by["ETH"]["data"]["heldAssets"] == ["BTC"]
    reads = [a for t, a in mcp.calls if t == "market_get_asset_data"]
    assert "BTC" not in reads and len(reads) == 7                          # a held name costs no candle read
    assert st.last()["result"]["emitted"] == 2 and {"ETH", "SOL"} <= set(st.last()["signaled"])


def test_a_failed_read_degrades_the_tick_instead_of_crashing(capsys):
    candles = {"ETH": _candles("LONG", "BULL"), "SOL": _candles("LONG", "BULL")}
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(candles, fail=("ETH",)), _State()))
    assert [s["asset"] for s in out] == ["SOL"]                            # the unreadable name is skipped, the tick goes on
    assert "market_get_asset_data(ETH) failed" in capsys.readouterr().err
    out = scan.scan(dict(INPUTS), _Ctx(_MCP(candles, fail=("strategy_get_clearinghouse_state",)), _State()))
    assert out == []                                                       # no account read: WAITING, not a crash


def test_a_pasted_fraction_margin_becomes_a_percent():
    out = scan.scan(dict(INPUTS, marginPct=0.25), _Ctx(_MCP({"ETH": _candles("LONG", "BULL")}), _State()))
    assert out and out[0]["marginPct"] == 25.0


# ── 1.3.0: the floor is 7, and the RSI extreme docks a point ─────────────────────────────

def test_the_floor_is_seven_so_a_fresh_cross_plus_rsi_alone_no_longer_enters():
    """At 6 (1.2.0) a fresh cross (4) + RSI confirming (2) entered with nothing else agreeing."""
    flat = _candles("LONG", "FLAT")
    th = scoring.build_thesis("ETH", flat["1h"], flat["4h"], INPUTS)
    assert th["score"] == 6 and th["directionSource"] == "macd_bull_cross"
    assert any(r.startswith("rsi_confirms") for r in th["reasons"]) and th["trend_4h"] == "NEUTRAL"
    assert scan.scan(dict(INPUTS), _Ctx(_MCP({"ETH": flat}), _State())) == []
    out = scan.scan(dict(INPUTS), _Ctx(_MCP({"ETH": _candles("LONG", "BULL")}), _State()))   # + the 4h trend = 8
    assert [s["asset"] for s in out] == ["ETH"] and out[0]["data"]["score"] == 8
    short = _candles("SHORT", "FLAT")
    assert scoring.build_thesis("ETH", short["1h"], short["4h"], INPUTS)["score"] == 6         # the mirror


def test_an_rsi_extreme_docks_a_point_it_does_not_veto():
    """The 1.2.0 card promised a veto. The code subtracts one point and keeps the direction."""
    c = _candles("LONG", "FLAT")
    confirmed = scoring.build_thesis("ETH", c["1h"], c["4h"], INPUTS)
    extreme = scoring.build_thesis("ETH", c["1h"], c["4h"], dict(INPUTS, rsiOverbought=50.0))  # the same RSI now reads as the extreme
    assert extreme is not None and extreme["direction"] == "LONG"
    assert extreme["score"] == confirmed["score"] - 3                      # +2 confirm becomes -1 extreme
    assert any(r.startswith("rsi_extreme") for r in extreme["reasons"])
