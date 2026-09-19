"""What scan.py owns: when NOT to trade, what a direction it cannot name means, and the ring.

The engine's own maths is tested in senpi-signals. These are the decisions this scanner adds, and
each one is a failure this codebase has actually shipped before:

  - a read that failed reported as a market with nothing in it;
  - a partial gather scored as if it were whole;
  - an unnamed direction defaulted to LONG;
  - a diff detector with no baseline looking like a quiet market rather than a first tick.

Run: python3 -m pytest strategies/signals-hunter/tests -q
"""
import datetime
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import score  # noqa: E402

COHORT_OK = {f"A{i}": {"notional_vol": 50_000_000, "smart_dir": "long", "crowd_dir": "short",
                       "smart_share": 70.0, "smart_source": "proven_cohort",
                       "smart_positions": {"0xw": 1.0}} for i in range(5)}
COHORT_DARK = {f"A{i}": {"notional_vol": 50_000_000} for i in range(5)}


class _State:
    def __init__(self, last=None):
        self._last, self.records = last or {}, []

    def last(self):
        return self.records[-1] if self.records else self._last

    def append(self, rec):
        self.records.append(rec)


def _ctx(state=None, positions=()):
    return types.SimpleNamespace(state=state if state is not None else _State(),
                                 positions=list(positions),
                                 senpi_mcp=types.SimpleNamespace(call_tool=lambda *a, **k: None))


def _gather(monkeypatch, metrics, events=()):
    monkeypatch.setattr(scan.sweep, "gather",
                        lambda *a, **k: {"asset_metrics": metrics, "events": list(events),
                                         "wallets": {}})


def _ranked(monkeypatch, signals):
    monkeypatch.setattr(scan.score, "rank", lambda sigs, key, lo, n, cap: list(signals))


def _sig(asset, direction, ts, vol=50_000_000):
    return {"asset": asset, "direction": direction, "trade_score": ts, "detector": "sm_divergence",
            "notional_vol": vol, "numbers": ["cohort 70% long"], "smart_share": 70.0}


# ── when not to trade ──

def test_an_empty_universe_is_a_failed_read_not_an_empty_market(monkeypatch, capsys):
    _gather(monkeypatch, {})
    assert scan.scan({}, _ctx()) == []
    assert "not an empty market" in capsys.readouterr().err


def test_a_dark_cohort_lens_skips_the_tick_and_names_it(monkeypatch, capsys):
    """Scoring without the cohort is OI/funding/price — a different number under the same name."""
    _gather(monkeypatch, COHORT_DARK)
    assert scan.scan({}, _ctx()) == []
    err = capsys.readouterr().err
    assert "SKIP" in err and "proven-cohort lens went dark" in err


def test_a_gather_that_raises_never_crashes_the_runtime(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(scan.sweep, "gather", boom)
    assert scan.scan({}, _ctx()) == []
    assert "gather failed" in capsys.readouterr().err


# ── what it emits ──

def test_a_direction_the_engine_could_not_name_is_skipped_not_defaulted_long(monkeypatch, capsys):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [_sig("ETH", None, 90.0), _sig("SOL", "", 90.0)])
    assert scan.scan({}, _ctx()) == []
    assert "unnamed_direction=2" in capsys.readouterr().err


def test_both_directions_are_traded(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [_sig("ETH", "LONG", 70.0), _sig("SOL", "SHORT", 70.0)])
    out = scan.scan({}, _ctx())
    assert sorted((s["asset"], s["direction"]) for s in out) == [("ETH", "LONG"), ("SOL", "SHORT")]


def test_conviction_sizes_up_above_the_high_threshold(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [_sig("ETH", "LONG", 79.9), _sig("SOL", "LONG", 80.0)])
    got = {s["asset"]: s["marginPct"] for s in scan.scan({}, _ctx())}
    assert got == {"ETH": 20.0, "SOL": 25.0}


def test_a_held_asset_is_not_re_opened(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [_sig("ETH", "LONG", 90.0)])
    assert scan.scan({}, _ctx(positions=[{"coin": "eth"}])) == []


def test_the_same_name_does_not_re_fire_inside_the_ttl(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [_sig("ETH", "LONG", 90.0)])
    st = _State()
    assert len(scan.scan({}, _ctx(st))) == 1
    assert scan.scan({}, _ctx(st)) == []        # second tick, same name, inside the 2h TTL


# ── the ring ──

def test_the_first_tick_says_it_has_no_baseline_rather_than_looking_quiet(monkeypatch, capsys):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [])
    scan.scan({}, _ctx())
    err = capsys.readouterr().err
    assert "ring=0" in err and "diff detectors cannot fire yet" in err


def test_the_ring_persists_and_is_capped(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [])
    st = _State()
    for _ in range(6):
        scan.scan({"ringMax": 3}, _ctx(st))
    ring = st.records[-1]["ring"]
    assert len(ring) == 3
    assert all(r["asset_metrics"] and r["ts"] for r in ring)


def test_a_state_failure_warns_about_the_consequence(monkeypatch, capsys):
    _gather(monkeypatch, COHORT_OK)
    _ranked(monkeypatch, [])

    class _Bad(_State):
        def append(self, rec):
            raise OSError("disk full")
    scan.scan({}, _ctx(_Bad()))
    assert "loses its diff baseline" in capsys.readouterr().err
