"""Two readings the engine acted on without having made them. Athena's phalanx leg is byte-identical
(the athena drift test pins that).

1. The first tick. The conviction gate measures growth against the last tick's headcount; with no last
   tick it measured against zero, so every standing one-sided name read as growing conviction and a new
   deployment opened on its first tick. The first read is the baseline, as it already is in Starling,
   Whalehunter and Pilotfish.
2. A name missing from the 4h board. The crowd read defaulted to 50, which counts as SHORT, so every
   cohort long on an off-board name scored as divergent and took the 1.5x booster. No board row means no
   crowd read, so no divergence."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

_WALLET = "0x" + "1" * 40
_COHORT = {"addresses": [_WALLET], "refreshed_at": 1.0}


class _State:
    def __init__(self, last=None):
        self._last = last or {}
        self.records = []

    def last(self):
        return self.records[-1] if self.records else self._last

    def append(self, rec):
        self.records.append(rec)


def _run(monkeypatch, headcount, crowd_lean, last=None):
    monkeypatch.setattr(scan, "_get_account", lambda ctx: (1000.0, []))
    monkeypatch.setattr(scan, "_get_cohort", lambda ctx, inputs, prev: ([_WALLET], 1.0))
    monkeypatch.setattr(scan, "_cohort_headcount", lambda ctx, cohort, inputs: headcount)
    monkeypatch.setattr(scan, "_crowd_lean", lambda ctx, limit: crowd_lean)
    monkeypatch.setattr(scan, "_asset_data", lambda ctx, coin: ([], []))
    st = _State(last)
    out = scan.scan({}, types.SimpleNamespace(state=st, wallet=_WALLET, senpi_mcp=None))
    return out, st.records[-1]


def test_the_first_tick_seeds_the_baseline_and_opens_nothing(monkeypatch):
    # 15 of 20 short (75% one-sided): against a zero baseline that read as +10 growth and opened.
    out, rec = _run(monkeypatch, {"ETH": {"long_n": 5, "short_n": 15, "raw_coin": "ETH"}}, {})
    assert out == []
    assert rec["result"]["gate"] == "baseline"
    # sustain starts at 0: a cold start must not inherit a run it never observed, or the
    # standing-consensus path would qualify an asset the scanner has seen exactly once.
    assert rec["prev_tilts"] == {"ETH": {"long_n": 5, "short_n": 15, "sustain": 0}}


def test_growth_after_the_baseline_still_opens(monkeypatch):
    last = {"cohort": _COHORT, "prev_tilts": {"ETH": {"long_n": 5, "short_n": 12}}}
    out, _ = _run(monkeypatch, {"ETH": {"long_n": 5, "short_n": 15, "raw_coin": "ETH"}}, {}, last)
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]


def test_a_name_missing_from_the_4h_board_is_not_divergent(monkeypatch):
    last = {"cohort": _COHORT, "prev_tilts": {"SOL": {"long_n": 12, "short_n": 5}}}
    out, _ = _run(monkeypatch, {"SOL": {"long_n": 15, "short_n": 5, "raw_coin": "SOL"}}, {}, last)
    assert [(s["asset"], s["direction"]) for s in out] == [("SOL", "LONG")]
    assert out[0]["data"]["divergence"] is False
    assert out[0]["data"]["conviction"] == 75.0        # one-sidedness x 1.0 price x 1.0 divergence
    assert not any("divergence" in r for r in out[0]["data"]["reasons"])


def test_a_board_crowd_on_the_other_side_still_boosts(monkeypatch):
    last = {"cohort": _COHORT, "prev_tilts": {"SOL": {"long_n": 12, "short_n": 5}}}
    out, _ = _run(monkeypatch, {"SOL": {"long_n": 15, "short_n": 5, "raw_coin": "SOL"}}, {"SOL": 30.0}, last)
    assert out[0]["data"]["divergence"] is True
    assert out[0]["data"]["conviction"] == 112.5       # 75 x 1.5
