"""A cohort that is already short and STAYS short has to be tradeable.

The conviction gate only ever measured growth: `delta >= deltaMin` against the previous tick. That
catches the moment a consensus forms and nothing after it. Once the cohort is heavily one-sided and
simply holds, delta sits at ~0 and the strategy is locked out — at exactly the point the setup is
most established.

2026-09-19, on a live book: the cohort held a standing short through a rally, the user was stopped out, and
waited for Phalanx to add shorts into it. It never did — **359 consecutive ticks, zero candidates**.
The only thing that worked was closing the strategy and redeploying, which reseeds the baseline so
the next read looks like a jump. That is a user hand-executing a bug, and it is what this path
removes.

Run: python3 -m pytest strategies/phalanx/tests/test_standing_consensus.py -q
"""
import os
import sys
import types

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_WALLET = "0x" + "1" * 40
_COHORT = {"addresses": [_WALLET], "refreshed_at": 1.0}
# 5 long / 15 short = 75% one-sided SHORT, and identical to the previous tick -> delta 0.
_FLAT = {"ETH": {"long_n": 5, "short_n": 15, "raw_coin": "ETH"}}


class _State:
    def __init__(self, last=None):
        self._last = last or {}
        self.records = []

    def last(self):
        return self.records[-1] if self.records else self._last

    def append(self, rec):
        self.records.append(rec)


def _run(monkeypatch, headcount, last, inputs=None):
    monkeypatch.setattr(scan, "_get_account", lambda ctx: (1000.0, []))
    monkeypatch.setattr(scan, "_get_cohort", lambda ctx, inputs_, prev: ([_WALLET], 1.0))
    monkeypatch.setattr(scan, "_cohort_headcount", lambda ctx, cohort, inputs_: headcount)
    monkeypatch.setattr(scan, "_crowd_lean", lambda ctx, limit: {})
    monkeypatch.setattr(scan, "_asset_data", lambda ctx, coin: ([], []))
    st = _State(last)
    out = scan.scan(inputs or {}, types.SimpleNamespace(state=st, wallet=_WALLET, senpi_mcp=None))
    return out, st.records[-1]


def _prev(sustain, direction="SHORT", long_n=5, short_n=15):
    return {"cohort": _COHORT,
            "prev_tilts": {"ETH": {"long_n": long_n, "short_n": short_n,
                                   "dir": direction, "sustain": sustain}}}


def test_a_standing_consensus_opens_even_with_a_flat_delta(monkeypatch):
    """The Jason case: nothing is growing, but the cohort has held for an hour."""
    out, _ = _run(monkeypatch, _FLAT, _prev(11))          # 11 + this tick = 12 = the floor
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]
    why = " ".join(out[0]["data"]["reasons"])
    assert "standing consensus" in why and "12 consecutive ticks" in why


def test_a_short_run_with_a_flat_delta_still_waits(monkeypatch):
    out, rec = _run(monkeypatch, _FLAT, _prev(10))        # 10 + 1 = 11, one short
    assert out == []
    assert rec["prev_tilts"]["ETH"]["sustain"] == 11      # the run keeps counting


def test_a_direction_flip_restarts_the_run(monkeypatch):
    """A cohort that just flipped has not held anything — it starts from one."""
    out, rec = _run(monkeypatch, _FLAT, _prev(40, direction="LONG"))
    assert out == []
    assert rec["prev_tilts"]["ETH"]["sustain"] == 1


def test_a_tick_below_the_threshold_resets_the_run_to_zero(monkeypatch):
    """11 long / 9 short is 55% — under the 65 floor, so the run breaks."""
    weak = {"ETH": {"long_n": 11, "short_n": 9, "raw_coin": "ETH"}}
    out, rec = _run(monkeypatch, weak, _prev(40))
    assert out == []
    assert rec["prev_tilts"]["ETH"]["sustain"] == 0


def test_growth_still_qualifies_on_its_own_with_no_run(monkeypatch):
    """The original path is untouched: conviction that IS growing needs no sustained run."""
    last = {"cohort": _COHORT,
            "prev_tilts": {"ETH": {"long_n": 5, "short_n": 12, "dir": "SHORT", "sustain": 1}}}
    out, _ = _run(monkeypatch, _FLAT, last)               # short_n 12 -> 15 = delta +3
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]
    assert "standing consensus" not in " ".join(out[0]["data"]["reasons"])


def test_the_floor_is_configurable(monkeypatch):
    out, _ = _run(monkeypatch, _FLAT, _prev(2), inputs={"sustainMinTicks": 3})
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]


def test_the_dedup_ttl_outlives_the_scan_interval():
    """Was 240s against a 300s scan — it expired before the next tick could consult it, so a
    sustained name would have re-signalled on EVERY tick once the standing path opened up.
    Same defect chameleon carried (#669)."""
    for rt_path in (os.path.join(HERE, "..", "main", "runtime.yaml"),
                    os.path.join(HERE, "..", "..", "athena", "phalanx", "runtime.yaml")):
        rt = yaml.safe_load(open(rt_path, encoding="utf-8"))
        sc = next(s for s in rt["scanners"] if s.get("inputs", {}).get("deltaMin"))
        assert sc["inputs"]["recentSignalTtlSeconds"] > sc["interval_seconds"], rt_path


def test_the_sustain_floor_is_an_hour_of_real_time():
    """12 ticks only means an hour because the interval is 300s — pin them together."""
    rt = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
    sc = next(s for s in rt["scanners"] if s.get("inputs", {}).get("sustainMinTicks"))
    assert sc["inputs"]["sustainMinTicks"] * sc["interval_seconds"] >= 3600
