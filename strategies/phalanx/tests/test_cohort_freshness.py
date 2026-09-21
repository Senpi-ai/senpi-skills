"""The membership list has to be datable, and stale has to have a limit.

`discovery_get_top_traders` decides WHO counts as smart money, refreshed every 24h and cached in
state. On a failed refresh the scanner keeps the cached list — correct, an empty cohort is worse
than an old one — but that fallback was UNBOUNDED and INVISIBLE:

  - `refreshed_at` was written on every tick and read by nothing, so a list pulled 30 minutes ago
    and one pulled 30 days ago both printed as `cohort=100`;
  - nothing refused. A broken upstream meant one warning line per tick, forever, while the
    strategy kept opening on a membership list of unknown age.

Their POSITIONS stay live every tick either way — a stale cohort is the right behaviour watched
from a possibly-outdated set of people, not stale data. That is why the ceiling is generous (72h,
three refresh attempts) and why it gates OPENS only: held positions keep their DSL.

Run: python3 -m pytest strategies/phalanx/tests/test_cohort_freshness.py -q
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

_WALLET = "0x" + "1" * 40
_HOT = {"ETH": {"long_n": 5, "short_n": 15, "raw_coin": "ETH"}}   # 75% one-sided SHORT


class _State:
    def __init__(self, last=None):
        self._last, self.records = last or {}, []

    def last(self):
        return self.records[-1] if self.records else self._last

    def append(self, rec):
        self.records.append(rec)


def _run(monkeypatch, age_h, sustain=11, inputs=None):
    refreshed = time.time() - age_h * 3600.0
    monkeypatch.setattr(scan, "_get_account", lambda ctx: (1000.0, []))
    monkeypatch.setattr(scan, "_get_cohort", lambda ctx, i, prev: ([_WALLET], refreshed))
    monkeypatch.setattr(scan, "_cohort_headcount", lambda ctx, c, i: _HOT)
    monkeypatch.setattr(scan, "_crowd_lean", lambda ctx, limit: {})
    monkeypatch.setattr(scan, "_asset_data", lambda ctx, coin: ([], []))
    last = {"cohort": {"addresses": [_WALLET], "refreshed_at": refreshed},
            "prev_tilts": {"ETH": {"long_n": 5, "short_n": 15, "dir": "SHORT",
                                   "sustain": sustain}}}
    st = _State(last)
    out = scan.scan(inputs or {}, types.SimpleNamespace(state=st, wallet=_WALLET, senpi_mcp=None))
    return out, st.records[-1]


def test_a_fresh_cohort_trades_normally(monkeypatch):
    """The control: 2h old is well inside the ceiling, so the standing consensus opens."""
    out, _ = _run(monkeypatch, age_h=2)
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]


def test_a_cohort_past_the_ceiling_stops_opening(monkeypatch, capsys):
    """Same signal, same everything — only the list's age differs."""
    out, rec = _run(monkeypatch, age_h=100)
    assert out == []
    assert rec["result"]["gate"] == "cohort_stale"
    err = capsys.readouterr().err
    assert "proven cohort is 100h old" in err
    assert "No new opens" in err and "keep their DSL" in err


def test_the_ceiling_is_configurable_and_can_be_disabled(monkeypatch):
    """0 turns it off — an operator who would rather run on any list than stop can."""
    out, _ = _run(monkeypatch, age_h=100, inputs={"cohortMaxAgeHours": 0})
    assert [(s["asset"], s["direction"]) for s in out] == [("ETH", "SHORT")]
    tight, _ = _run(monkeypatch, age_h=5, inputs={"cohortMaxAgeHours": 4})
    assert tight == []


def test_the_age_is_on_the_status_line(monkeypatch, capsys):
    """`cohort=100` alone cannot say whether those wallets were chosen today or last month."""
    _run(monkeypatch, age_h=7, sustain=0)                 # sustain 0 -> WAITING, not EMIT
    assert "age=7h" in capsys.readouterr().err


def test_an_undatable_cohort_is_reported_not_assumed_fresh(monkeypatch, capsys):
    """refreshed_at 0 means it could never be dated. Say so; do not silently call it new."""
    monkeypatch.setattr(scan, "_get_account", lambda ctx: (1000.0, []))
    monkeypatch.setattr(scan, "_get_cohort", lambda ctx, i, prev: ([_WALLET], 0.0))
    monkeypatch.setattr(scan, "_cohort_headcount", lambda ctx, c, i: _HOT)
    monkeypatch.setattr(scan, "_crowd_lean", lambda ctx, limit: {})
    monkeypatch.setattr(scan, "_asset_data", lambda ctx, coin: ([], []))
    last = {"cohort": {"addresses": [_WALLET], "refreshed_at": 0.0},
            "prev_tilts": {"ETH": {"long_n": 5, "short_n": 15, "dir": "SHORT", "sustain": 0}}}
    scan.scan({}, types.SimpleNamespace(state=_State(last), wallet=_WALLET, senpi_mcp=None))
    assert "age=?" in capsys.readouterr().err
