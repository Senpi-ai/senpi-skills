"""The entry gates Athena 2.0.0 adds, run through the real scanners against a fake market.

Both sleeves skip an entry that price moved more than momBlockPct (1%) against over the last
momBars1h (2) hourly bars. Phalanx also opens no LONG while cohortShortBlockPct (65%) or more of
the cohort's positions across all assets are short, and raises conviction for a divergence from
the 4h board only while the cohort is actively shifting. Every test runs with the inputs shipped
in runtime.yaml.

Run: python3 -m pytest strategies/athena/tests/test_entry_gates.py -q
"""
import importlib.util
import os
import sys
import time
import types

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, ".."))
_WALLET = "0x" + "1" * 40


def _inputs(leg):
    with open(os.path.join(PKG, leg, "runtime.yaml"), encoding="utf-8") as f:
        rt = yaml.safe_load(f)
    return dict(next(s for s in rt["scanners"] if s.get("type") == "external_scanner")["inputs"])


def _scanner(leg):
    """This leg's scan.py bound to this leg's scoring.py; both legs ship modules of those names."""
    d = os.path.join(PKG, leg, "scanners")
    saved = {k: sys.modules.pop(k) for k in ("scan", "scoring") if k in sys.modules}
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location(f"_athena_{leg}_scan", os.path.join(d, "scan.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(d)
        sys.modules.pop("scoring", None)
        sys.modules.update(saved)
    return mod


PHALANX, AEGIS = _scanner("phalanx"), _scanner("aegis")


def _closes(*px):
    return [{"o": str(p), "h": str(p), "l": str(p), "c": str(p)} for p in px]


FLAT = _closes(100, 100, 100)
UP_HALF = _closes(100, 100, 100.5)        # +0.5% over two 1h bars: inside the band
UP = _closes(100, 100, 101.5)             # +1.5%: against a SHORT
DOWN = _closes(100, 100, 98.5)            # -1.5%: against a LONG


class _State:
    def __init__(self, last=None):
        self._last, self.records = last, []

    def last(self):
        return self.records[-1] if self.records else self._last

    def append(self, rec):
        self.records.append(rec)


# ── phalanx ───────────────────────────────────────────────────────────────────

def _phalanx(monkeypatch, headcount, prev_tilts, c1h, crowd=None):
    monkeypatch.setattr(PHALANX, "_get_account", lambda ctx: (1000.0, []))
    monkeypatch.setattr(PHALANX, "_get_cohort", lambda ctx, inputs_, prev: ([_WALLET], time.time()))
    monkeypatch.setattr(PHALANX, "_cohort_headcount", lambda ctx, cohort, inputs_: headcount)
    monkeypatch.setattr(PHALANX, "_crowd_lean", lambda ctx, limit: crowd or {})
    monkeypatch.setattr(PHALANX, "_asset_data", lambda ctx, coin: (c1h, []))
    last = {"cohort": {"addresses": [_WALLET], "refreshed_at": time.time()}, "prev_tilts": prev_tilts}
    ctx = types.SimpleNamespace(state=_State(last), wallet=_WALLET, senpi_mcp=None)
    return PHALANX.scan(_inputs("phalanx"), ctx)


def _side(sym, long_n, short_n):
    return {sym: {"long_n": long_n, "short_n": short_n, "raw_coin": sym}}


def _prev(sym, long_n, short_n, direction, sustain=1):
    return {sym: {"long_n": long_n, "short_n": short_n, "dir": direction, "sustain": sustain}}


# ETH: the cohort 5 long / 15 short (75% one-sided), up from 12 short last tick: delta +3, growing.
ETH_SHORT, ETH_PREV = _side("ETH", 5, 15), _prev("ETH", 5, 12, "SHORT")
# SOL: 15 long / 5 short, up from 12 long: delta +3, growing.
SOL_LONG, SOL_PREV = _side("SOL", 15, 5), _prev("SOL", 12, 5, "LONG")


def _sig(out):
    return [(s["asset"], s["direction"]) for s in out]


def test_phalanx_shorts_unless_price_bounced_against_it(monkeypatch, capsys):
    assert _sig(_phalanx(monkeypatch, ETH_SHORT, ETH_PREV, UP_HALF)) == [("ETH", "SHORT")]
    assert _phalanx(monkeypatch, ETH_SHORT, ETH_PREV, UP) == []
    assert "momentum=1" in capsys.readouterr().err


def test_phalanx_longs_unless_price_dumped_against_it(monkeypatch, capsys):
    assert _sig(_phalanx(monkeypatch, SOL_LONG, SOL_PREV, FLAT)) == [("SOL", "LONG")]
    assert _phalanx(monkeypatch, SOL_LONG, SOL_PREV, DOWN) == []
    assert "momentum=1" in capsys.readouterr().err


def test_no_long_while_the_cohort_is_65_pct_short_across_all_assets(monkeypatch, capsys):
    """SOL qualifies on its own; BTC (no change since last tick, so never a candidate itself)
    sets the cohort's overall lean. 35 long / 65 short is exactly the threshold."""
    btc = lambda l, s: (_side("BTC", l, s), _prev("BTC", l, s, "SHORT", sustain=0))
    head, prev = btc(20, 60)
    assert _phalanx(monkeypatch, {**SOL_LONG, **head}, {**SOL_PREV, **prev}, FLAT) == []
    err = capsys.readouterr().err
    assert "cohort regime gate: 65% short" in err
    assert "regime=1 max_delta=" in err          # the count and the next field are separated
    head, prev = btc(21, 59)                      # 36 long / 64 short: under the threshold
    assert _sig(_phalanx(monkeypatch, {**SOL_LONG, **head}, {**SOL_PREV, **prev}, FLAT)) == [("SOL", "LONG")]


def test_the_regime_gate_never_holds_back_a_short(monkeypatch):
    """ETH alone is 75% short, so the gate is on this tick, and the short still goes out."""
    assert _sig(_phalanx(monkeypatch, ETH_SHORT, ETH_PREV, FLAT)) == [("ETH", "SHORT")]


def test_divergence_raises_conviction_only_on_an_active_shift(monkeypatch):
    board_long = {"ETH": 80.0}                    # the 4h board leans long; the cohort is short
    shifting = _phalanx(monkeypatch, ETH_SHORT, ETH_PREV, FLAT, crowd=board_long)
    assert shifting[0]["data"]["conviction"] == 75.0 * 1.5
    assert any("active shift" in r for r in shifting[0]["data"]["reasons"])
    # unchanged for an hour (11 ticks + this one): standing consensus, delta 0
    standing = _phalanx(monkeypatch, ETH_SHORT, _prev("ETH", 5, 15, "SHORT", sustain=11), FLAT,
                        crowd=board_long)
    assert standing[0]["data"]["conviction"] == 75.0
    assert any("stale divergence" in r for r in standing[0]["data"]["reasons"])


# ── aegis ─────────────────────────────────────────────────────────────────────

_FALLING_4H = [{"h": str(120 - 2 * i), "l": str(110 - 2 * i)} for i in range(6)]


class _MCP:
    """Long-crowded funding and every market falling on 4h: risk-off, so aegis shorts risk."""

    def __init__(self, c1h):
        self.c1h = c1h

    def call_tool(self, name, args):
        if name == "strategy_get_clearinghouse_state":
            book = {"marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"}, "assetPositions": []}
            return {"data": {"main": book, "xyz": book}}
        if name == "market_get_funding_regime":
            return {"data": {"regime": "LONG_CROWDED"}}
        if name == "market_get_asset_data":
            candles = {"4h": _FALLING_4H}
            if "1h" in args.get("candle_intervals", []):       # served only when asked for
                candles["1h"] = self.c1h
            return {"data": {"candles": candles, "asset_context": {"funding": "0.0000125"},
                             "oi_velocity": {"oi_trend": "BUILDING", "oi_acceleration": "INCREASING"}}}
        raise AssertionError(f"unexpected tool {name}")


def _aegis(c1h):
    ctx = types.SimpleNamespace(senpi_mcp=_MCP(c1h), state=_State(), wallet="0x0")
    return AEGIS.scan(_inputs("aegis"), ctx)


def test_aegis_shorts_the_risk_off_tape_unless_price_bounced_against_it(capsys):
    out = _aegis(UP_HALF)
    assert len(out) == 2 and {s["direction"] for s in out} == {"SHORT"}
    assert not [s for s in _aegis(UP) if s["direction"] == "SHORT"]
    assert "1h momentum +1.50% against entry" in capsys.readouterr().err
