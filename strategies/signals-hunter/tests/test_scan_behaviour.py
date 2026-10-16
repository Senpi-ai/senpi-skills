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


def _detected(monkeypatch, signals):
    """Inject BEFORE the non-tradeable filter, which is where detector parking happens."""
    monkeypatch.setattr(scan.score, "detect_from_metrics", lambda *a, **k: list(signals))
    monkeypatch.setattr(scan.score, "trade_score", lambda s, cred: s.get("trade_score", 0.0))
    monkeypatch.setattr(scan.score, "rank", lambda sigs, key, lo, n, cap: list(sigs))


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


# ── what the wallet funds, and what this package will not trade ──

def test_whale_open_is_never_traded(monkeypatch, capsys):
    """It is the only detector whose freshness is asserted by a third-party field we have caught
    being wrong — `startTime` reading 4592s of age as 17s. In the feed that is a discountable
    sentence; here it would be a 5x entry in the direction the field got wrong."""
    _gather(monkeypatch, COHORT_OK)
    whale = _sig("ETH", "LONG", 90.0); whale["detector"] = "whale_open"
    _detected(monkeypatch, [whale])
    assert scan.scan({}, _ctx()) == []
    assert "parked 1 signal" in capsys.readouterr().err


def test_a_tradeable_detector_beside_a_parked_one_still_fires(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    whale = _sig("ETH", "LONG", 90.0); whale["detector"] = "whale_open"
    _detected(monkeypatch, [whale, _sig("SOL", "SHORT", 88.0)])
    out = scan.scan({}, _ctx())
    assert [s["asset"] for s in out] == ["SOL"]


def test_emits_stop_at_what_the_wallet_funds(monkeypatch, capsys):
    """5 slots x 20% is exactly 100% of withdrawable with zero headroom, and every signal in a tick
    is sized off the SAME balance read — so over-emitting fails the later opens outright rather
    than shrinking them, and the ones it fails are the lowest-ranked."""
    _gather(monkeypatch, COHORT_OK)
    monkeypatch.setattr(scan, "_wallet", lambda ctx: (1000.0, 0.0))   # funded, nothing open yet
    _ranked(monkeypatch, [_sig(f"A{i}", "LONG", 70.0) for i in range(6)])
    out = scan.scan({}, _ctx())
    assert sum(s["marginPct"] for s in out) <= 80.0      # the default cap
    assert len(out) == 4                                  # 4 x 20% = 80; the 5th and 6th do not fit
    err = capsys.readouterr().err
    assert "2 qualifying signal(s) not emitted" in err and "Lowest-ranked dropped first" in err


def test_the_cap_drops_the_worst_ranked_not_the_best(monkeypatch):
    _gather(monkeypatch, COHORT_OK)
    monkeypatch.setattr(scan, "_wallet", lambda ctx: (1000.0, 0.0))   # funded, nothing open yet
    _ranked(monkeypatch, [_sig("BEST", "LONG", 95.0), _sig("MID", "LONG", 85.0),
                          _sig("WORST", "LONG", 66.0)])
    out = scan.scan({"maxTotalMarginPct": 50}, _ctx())
    assert [s["asset"] for s in out] == ["BEST", "MID"]   # 25 + 25 = 50; WORST drops


def test_a_failed_wallet_read_does_not_stop_trading(monkeypatch):
    """None means the read failed, which is not a wallet with nothing in it. The cap alone still
    governs — refusing to trade on a missing read would be the same bug in the other direction."""
    _gather(monkeypatch, COHORT_OK)
    monkeypatch.setattr(scan, "_wallet", lambda ctx: (None, 0.0))     # the read FAILED
    _ranked(monkeypatch, [_sig("ETH", "LONG", 70.0)])
    assert len(scan.scan({}, _ctx())) == 1


# ── the signal payload the runtime will actually accept ──
# `signal_data_schema` declares `oneSidedness: {type: number, required: false}`. The runtime reads
# that as "absent is fine, null is not", so a key present with None fails the type and the SIGNAL is
# rejected — while this scanner's own tick reports itself healthy. Shipped in 1.1.0: `oneSidedness`
# was read off the SIGNAL (`s.get("smart_share")`), but `smart_share` is a field on the asset
# METRICS and none of score.py's four signal-construction sites copies it across, so it was None on
# 100% of emits and nothing this strategy produced could ever open a position.
#
# Note the old `_sig()` helper above still sets `smart_share` on the signal. That is precisely how
# this passed review: the fixture was kinder than the engine. These tests use the real shape.

def _engine_sig(asset, direction, ts, vol=50_000_000):
    """A signal shaped the way score.py actually builds one — no `smart_share` key."""
    return {"asset": asset, "direction": direction, "trade_score": ts, "detector": "sm_divergence",
            "notional_vol": vol, "numbers": ["cohort 70% long"]}


def test_no_emitted_field_is_ever_null(monkeypatch):
    """`required: false` means OMIT the key. A null fails `type: number` and loses the signal."""
    _gather(monkeypatch, COHORT_OK)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    out = scan.scan({}, _ctx())
    assert out, "the fix must not cost us the emit"
    nulls = {k for k, v in out[0]["data"].items() if v is None}
    assert not nulls, f"null-valued keys the schema forbids: {sorted(nulls)}"


def test_one_sidedness_comes_from_the_metrics_row_not_the_signal(monkeypatch):
    """COHORT_OK carries smart_share 70.0 on each asset; the emit must carry that number through."""
    _gather(monkeypatch, COHORT_OK)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    out = scan.scan({}, _ctx())
    assert out[0]["data"]["oneSidedness"] == 70.0


def test_an_asset_with_no_smart_share_omits_the_key_rather_than_sending_none(monkeypatch):
    """An asset the metrics have no cohort reading for still trades — it just says nothing it cannot."""
    metrics = dict(COHORT_OK)
    metrics["A0"] = {k: v for k, v in COHORT_OK["A0"].items() if k != "smart_share"}
    _gather(monkeypatch, metrics)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    out = scan.scan({}, _ctx())
    assert out and "oneSidedness" not in out[0]["data"]


# ── the margin cap is a PORTFOLIO cap, not a per-tick one ──
# Shipped through 1.1.2: `committed_pct` started at 0 on EVERY tick and counted only that tick's
# emits, so an hourly clock could commit another maxTotalMarginPct on top of an already-full book —
# five ticks, 400%. What actually held the line was `slots` and the held-set, not this guard.
#
# The payload below is the verbatim shape of a live wallet (majutsushi-signals-hunter, 2026-09-20)
# holding three `main` and two `xyz` positions. It is the reason the two numbers must aggregate in
# opposite directions: withdrawable reads identically in both sections (one wallet), while the
# positions are genuinely different and their margin sums.

LIVE_STATE = {"data": {
    "main": {
        "withdrawable": "149.90865699999995",
        "marginSummary": {"accountValue": "342.389729", "totalMarginUsed": "199.340585"},
        "assetPositions": [
            {"position": {"coin": "BTC", "marginUsed": "110.299345", "szi": "-0.00678"}},
            {"position": {"coin": "SOL", "marginUsed": "39.53024", "szi": "-1.78"}},
            {"position": {"coin": "XRP", "marginUsed": "49.511", "szi": "-175.0"}},
        ]},
    "xyz": {
        "withdrawable": "149.90865699999995",
        "marginSummary": {"accountValue": "343.762276", "totalMarginUsed": "193.853619"},
        "assetPositions": [
            {"position": {"coin": "xyz:XYZ100", "marginUsed": "82.969042", "szi": "-0.0142"}},
            {"position": {"coin": "xyz:SP500", "marginUsed": "110.884577", "szi": "0.072"}},
        ]},
}}

EMPTY_STATE = {"data": {"main": {"withdrawable": "550.0", "assetPositions": []},
                        "xyz": {"withdrawable": "550.0", "assetPositions": []}}}


def _ctx_wallet(state, positions=()):
    return types.SimpleNamespace(
        state=_State(), positions=list(positions), wallet="0xtest",
        senpi_mcp=types.SimpleNamespace(call_tool=lambda *a, **k: state))


def test_wallet_maxes_free_and_sums_committed_margin():
    """Opposite directions. Sum the free and you double-count one wallet; max the margin and you
    lose every position outside the section that happened to read highest."""
    free, used = scan._wallet(_ctx_wallet(LIVE_STATE))
    assert round(free, 2) == 149.91, "free is a max across the two views of one wallet"
    assert round(used, 2) == 393.19, "committed margin sums across sections"
    # and it agrees with what the sections report about themselves
    assert round(used, 2) == round(199.340585 + 193.853619, 2)


def test_a_full_book_refuses_a_new_slot(monkeypatch, capsys):
    """72% of capital already at risk + a 20% slot is over the 80% cap. This is the case that used
    to pass: every tick started the count at zero and saw only its own emits."""
    _gather(monkeypatch, COHORT_OK)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    assert scan.scan({}, _ctx_wallet(LIVE_STATE)) == []
    err = capsys.readouterr().err
    assert "not emitted" in err and "already at risk in open positions" in err


def test_an_empty_book_still_takes_the_same_signal(monkeypatch):
    """The guard must refuse a FULL book, not refuse to trade. Same signal, nothing open."""
    _gather(monkeypatch, COHORT_OK)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    out = scan.scan({}, _ctx_wallet(EMPTY_STATE))
    assert len(out) == 1 and out[0]["asset"] == "A0"


def test_a_failed_wallet_read_does_not_block_trading(monkeypatch):
    """A read that FAILED is not a wallet with nothing in it — but it must not wedge the strategy
    shut either. With no reading, fall back to the cap alone, as before."""
    def boom(*a, **k):
        raise RuntimeError("clearinghouse down")
    ctx = types.SimpleNamespace(state=_State(), positions=[], wallet="0xtest",
                                senpi_mcp=types.SimpleNamespace(call_tool=boom))
    _gather(monkeypatch, COHORT_OK)
    _detected(monkeypatch, [_engine_sig("A0", "LONG", 90.0)])
    assert len(scan.scan({}, ctx)) == 1
