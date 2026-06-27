"""MAGPIE · PRE-LISTING — guards for the IPOP detection + marginPct-percent invariant.

1. marginPct must be a PERCENT in (0,100], never a v2 fraction. The v3.x runtime
   sizes (marginPct/100)*withdrawable, so a fraction like 0.12 would size ~0.12% —
   a silent ~100x undersize. marginPct is a pass-through config read at scan.py.
2. The IPOP funding-signature detection must match the v2 producer's
   fetch_ipop_universe predicate VERBATIM on representative instruments.
"""

import os

import scoring
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")

_CONF = {"ipopFundingMaxAbs": 1e-7, "ipopMaxLeverageCap": 5, "ipopMinDailyVolUsd": 100000}


def _runtime_margin_pct():
    with open(_RUNTIME_YAML) as f:
        spec = yaml.safe_load(f)
    for s in spec["scanners"]:
        if s.get("type") == "external_scanner":
            return float(s["inputs"]["marginPct"])
    raise AssertionError("no external_scanner in runtime.yaml")


def test_margin_pct_is_percent_not_fraction():
    mp = _runtime_margin_pct()
    assert 0 < mp <= 100, f"marginPct {mp} must be a percent in (0,100]"
    assert mp >= 1, f"marginPct {mp} looks like a v2 fraction (<1) — should be a percent"


def test_ipop_signature_accepts_pre_ipo_perp():
    # low funding + low leverage cap + enough volume = IPOP in the pre-listing universe
    assert scoring.ipop_passes_universe("xyz:NEWIPO", False, 1e-9, 3, 5_000_000, _CONF)


def test_ipop_signature_rejects_standard_equity():
    # converted equity perp: funding jumped ~100x, cap lifted -> NOT an IPOP
    assert not scoring.ipop_passes_universe("xyz:SPCX", False, 5.68e-5, 20, 3e8, _CONF)


def test_ipop_signature_rejects_zero_funding_high_leverage_fx():
    # JPY/EUR: funding 0 but max_leverage 50 -> fails the leverage cap, not an IPOP
    assert not scoring.ipop_passes_universe("xyz:JPY", False, 0.0, 50, 2_900_000, _CONF)


def test_ipop_signature_rejects_low_volume():
    # right funding/leverage signature but below the daily-volume floor
    assert not scoring.ipop_passes_universe("xyz:THINIPO", False, 1e-9, 3, 50_000, _CONF)


def test_ipop_signature_rejects_non_xyz_and_delisted():
    assert not scoring.ipop_passes_universe("BTC", False, 1e-9, 3, 5_000_000, _CONF)
    assert not scoring.ipop_passes_universe("xyz:NEWIPO", True, 1e-9, 3, 5_000_000, _CONF)


def test_pre_listing_thesis_bullish_ramp_trend_only_fallback():
    # 4h + 1h bullish ramp, SM absent -> trend-only fallback, LONG, score 7
    c4 = [{"low": 10 + i, "high": 11 + i, "close": 10.5 + i} for i in range(6)]
    c1 = [{"low": 10 + i * 0.5, "high": 11 + i * 0.5, "close": 10.5 + i * 0.5} for i in range(6)]
    th = scoring.build_thesis_pre_listing("xyz:NEWIPO", c1, c4, None, 0.0,
                                          {"smTiltMinPct": 55, "smStrongTiltPct": 70})
    assert th["direction"] == "LONG" and th["score"] == 7


def test_leverage_clamps_to_venue_cap():
    assert scoring.clamp_leverage(3, 3) == 3
    assert scoring.clamp_leverage(5, 3) == 3   # desired above venue cap -> clamped
    assert scoring.clamp_leverage(3, 0) == 3   # bad cap -> fall back to desired


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("ALL PRE-LISTING TESTS PASS")
