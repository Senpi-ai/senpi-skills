"""MAGPIE · GRADUATION — guards for conversion detection + marginPct-percent invariant.

1. marginPct must be a PERCENT in (0,100], never a v2 fraction (silent ~100x undersize).
2. The IPOP-vs-STANDARD classification + the IPOP->STANDARD conversion flip must
   match the v2 producer's classify_instrument / detect_conversion VERBATIM.
3. Post-conversion momentum scoring must reproduce the v2 build_thesis_graduation math.
"""

import os

import scoring
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME_YAML = os.path.join(_HERE, "..", "runtime.yaml")

_GR_CONF = {"momentumLookbackBars": 6, "minMomentumPct": 3.0, "strongMomentumPct": 8.0,
            "smTiltMinPct": 55, "smStrongTiltPct": 70}


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


def test_classify_ipop_vs_standard():
    assert scoring.classify_instrument(1e-9, 3, 1e-7, 5) == "IPOP"
    assert scoring.classify_instrument(1e-7, 5, 1e-7, 5) == "IPOP"      # boundary
    assert scoring.classify_instrument(5.68e-5, 20, 1e-7, 5) == "STANDARD"
    assert scoring.classify_instrument(0.0, 50, 1e-7, 5) == "STANDARD"  # zero funding but lev cap fails


def test_detect_conversion_only_on_known_prior_flip():
    assert scoring.detect_conversion("IPOP", "STANDARD") is True       # the graduation event
    assert scoring.detect_conversion("STANDARD", "STANDARD") is False
    assert scoring.detect_conversion("IPOP", "IPOP") is False
    assert scoring.detect_conversion(None, "STANDARD") is False        # first-seen: no false flip
    assert scoring.detect_conversion("STANDARD", "IPOP") is False      # reverse is not a graduation


def test_graduation_thesis_post_conversion_momentum():
    # 8 1h bars, strong +mom, SM aligned strong, rising volume -> LONG, high score
    c1h = ([{"close": 100, "volume": 1000}, {"close": 100, "volume": 1000}]
           + [{"close": 100 + i * 2, "volume": 1000 + i * 300} for i in range(6)])
    th = scoring.build_thesis_graduation("xyz:GRADME", c1h, 20, "LONG", 75.0, _GR_CONF)
    assert th["direction"] == "LONG"
    assert th["momentum_pct"] == 10.0
    # base 3 + strong-mom 2 + sm_confirms 1 + sm_strong 1 + vol_rising 1 = 8
    assert th["score"] == 8


def test_graduation_thesis_blocks_below_min_momentum():
    flat = [{"close": 100, "volume": 1000} for _ in range(8)]
    assert scoring.build_thesis_graduation("xyz:GRADME", flat, 20, "LONG", 75.0, _GR_CONF) is None


def test_leverage_clamps_to_lifted_venue_cap():
    assert scoring.clamp_leverage(5, 20) == 5   # desired 5x under the lifted cap
    assert scoring.clamp_leverage(5, 3) == 3    # cap still below desired -> clamp


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("ALL GRADUATION TESTS PASS")
