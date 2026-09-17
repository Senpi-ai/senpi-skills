"""Unit tests for Aegis scoring module — pure math, no mocks."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scoring

def test_funding_regime_score():
    assert scoring.funding_regime_score("LONG_CROWDED") == -1.0
    assert scoring.funding_regime_score("SHORT_CROWDED") == 1.0
    assert scoring.funding_regime_score("NEUTRAL") == 0.0
    assert scoring.funding_regime_score(None) == 0.0
    print("✓ funding_regime_score")

def test_haven_trend_score():
    # Gold bid + yen bid (USD/JPY falling) = strong risk-off
    s = scoring.haven_trend_score(("BULLISH", 1.0), ("BEARISH", 1.0))
    assert s == -1.0, f"expected -1.0, got {s}"

    # Gold sold + yen sold (USD/JPY rising) = strong risk-on
    s = scoring.haven_trend_score(("BEARISH", 1.0), ("BULLISH", 1.0))
    assert s == 1.0, f"expected 1.0, got {s}"

    # Mixed
    s = scoring.haven_trend_score(("BULLISH", 1.0), ("NEUTRAL", 0.0))
    assert s == -0.5, f"expected -0.5, got {s}"

    # Neutral
    s = scoring.haven_trend_score(("NEUTRAL", 0.0), ("NEUTRAL", 0.0))
    assert s == 0.0, f"expected 0.0, got {s}"
    print("✓ haven_trend_score")

def test_compute_regime_score():
    # Severe risk-off: long crowded + gold bid + yen bid (USD/JPY falling)
    s = scoring.compute_regime_score("LONG_CROWDED", ("BULLISH", 1.0), ("BEARISH", 1.0))
    assert s == -2.0, f"expected -2.0, got {s}"

    # Strong risk-on: short crowded + gold sold + yen sold (USD/JPY rising)
    s = scoring.compute_regime_score("SHORT_CROWDED", ("BEARISH", 1.0), ("BULLISH", 1.0))
    assert s == 2.0, f"expected 2.0, got {s}"

    # Neutral funding, havens disagree (gold bid, yen sold)
    s = scoring.compute_regime_score("NEUTRAL", ("BULLISH", 1.0), ("BULLISH", 1.0))
    assert s == 0.0, f"expected 0.0, got {s}"

    # Clamped
    s = scoring.compute_regime_score("LONG_CROWDED", ("BULLISH", 1.0), ("BEARISH", 1.0))
    assert s == -2.0  # already at edge
    print("✓ compute_regime_score")

def test_regime_to_direction():
    # Risk-off: risk assets shorted, defensives longed
    assert scoring.regime_to_direction("BTC", -1.0) == "SHORT"
    assert scoring.regime_to_direction("xyz:GOLD", -1.0) == "LONG"
    assert scoring.regime_to_direction("xyz:SP500", -1.5) == "SHORT"
    assert scoring.regime_to_direction("xyz:JPY", -0.8) == "SHORT"   # short USD/JPY = buy the yen

    # Risk-on: risk assets longed, defensives shorted
    assert scoring.regime_to_direction("BTC", 1.0) == "LONG"
    assert scoring.regime_to_direction("xyz:GOLD", 1.0) == "SHORT"
    assert scoring.regime_to_direction("ETH", 1.5) == "LONG"
    assert scoring.regime_to_direction("xyz:NATGAS", 0.8) == "SHORT"

    # Neutral: cash
    assert scoring.regime_to_direction("BTC", 0.3) is None
    assert scoring.regime_to_direction("xyz:GOLD", -0.4) is None
    assert scoring.regime_to_direction("ETH", 0.0) is None

    # Unknown asset
    assert scoring.regime_to_direction("DOGE", -1.0) is None
    print("✓ regime_to_direction")

def test_trend_alignment():
    assert scoring.trend_alignment("BULLISH", "LONG") == 1.2
    assert scoring.trend_alignment("BEARISH", "SHORT") == 1.2
    assert scoring.trend_alignment("BULLISH", "SHORT") == 0.7
    assert scoring.trend_alignment("BEARISH", "LONG") == 0.7
    assert scoring.trend_alignment("NEUTRAL", "LONG") == 1.0
    assert scoring.trend_alignment("NEUTRAL", "SHORT") == 1.0
    print("✓ trend_alignment")

def test_oi_confirmation():
    assert scoring.oi_confirmation({"oi_trend": "BUILDING", "oi_acceleration": "INCREASING"}) == 1.15
    assert scoring.oi_confirmation({"oi_trend": "BUILDING", "oi_acceleration": "STABLE"}) == 1.10
    assert scoring.oi_confirmation({"oi_trend": "FLAT"}) == 1.0
    assert scoring.oi_confirmation({"oi_trend": "DECLINING"}) == 0.8
    assert scoring.oi_confirmation(None) == 1.0
    print("✓ oi_confirmation")

def test_funding_edge():
    # Positive funding: longs pay shorts
    assert scoring.funding_edge(0.0000125, "SHORT") == 1.1   # short collects
    assert scoring.funding_edge(0.0000125, "LONG") == 0.9    # long pays
    # Negative funding: shorts pay longs
    assert scoring.funding_edge(-0.0000125, "LONG") == 1.1   # long collects
    assert scoring.funding_edge(-0.0000125, "SHORT") == 0.9  # short pays
    # Zero funding
    assert scoring.funding_edge(0.0, "LONG") == 1.0
    print("✓ funding_edge")

def test_conviction_for():
    # Strong risk-off, aligned trend, building OI, collecting funding
    c = scoring.conviction_for(-1.5, "BEARISH", "SHORT",
                                {"oi_trend": "BUILDING", "oi_acceleration": "INCREASING"},
                                0.0000125)
    # base = 1.5 * 40 = 60, * 1.2 (aligned) * 1.15 (OI building) * 1.1 (funding edge) = 90.9
    assert 85 < c < 100, f"expected ~91, got {c}"

    # Weak signal: low regime, counter trend, declining OI
    c = scoring.conviction_for(-0.6, "BULLISH", "SHORT",
                                {"oi_trend": "DECLINING"}, 0.0000125)
    # base = 0.6 * 40 = 24, * 0.7 (counter) * 0.8 (declining) * 1.1 (collecting) = 14.8
    assert 10 < c < 20, f"expected ~15, got {c}"
    print("✓ conviction_for")

def test_margin_pct_for():
    # High conviction + strong regime
    pct = scoring.margin_pct_for(80, 12.0, -2.0, 15.0)
    # 12 * 1.5 * (2.0/2.0=1.0) = 18, capped at 15
    assert pct == 15.0, f"expected 15.0, got {pct}"

    # Mid conviction + moderate regime
    pct = scoring.margin_pct_for(55, 12.0, -1.0, 15.0)
    # 12 * 1.25 * (1.0/2.0=0.5) = 7.5
    assert pct == 7.5, f"expected 7.5, got {pct}"

    # Low conviction + mild regime
    pct = scoring.margin_pct_for(30, 12.0, -0.6, 15.0)
    # 12 * 1.0 * (0.6/2.0=0.3) = 12 * 0.3 = 3.6
    assert 3.5 < pct < 3.7, f"expected ~3.6, got {pct}"
    print("✓ margin_pct_for")

def test_trend_structure():
    # Build bullish candles (higher lows)
    bullish = [
        {"l": 100, "h": 110}, {"l": 102, "h": 112}, {"l": 104, "h": 114},
        {"l": 106, "h": 116}, {"l": 108, "h": 118}, {"l": 110, "h": 120},
    ]
    label, strength = scoring.trend_structure(bullish)
    assert label == "BULLISH", f"expected BULLISH, got {label}"

    # Build bearish candles (lower highs)
    bearish = [
        {"h": 120, "l": 110}, {"h": 118, "l": 108}, {"h": 116, "l": 106},
        {"h": 114, "l": 104}, {"h": 112, "l": 102}, {"h": 110, "l": 100},
    ]
    label, strength = scoring.trend_structure(bearish)
    assert label == "BEARISH", f"expected BEARISH, got {label}"

    # Too few candles
    label, strength = scoring.trend_structure([{"l": 100, "h": 110}])
    assert label == "NEUTRAL"
    print("✓ trend_structure")

def test_asset_role():
    assert scoring.asset_role("BTC") == "risk"
    assert scoring.asset_role("ETH") == "risk"
    assert scoring.asset_role("xyz:GOLD") == "defensive"
    assert scoring.asset_role("xyz:JPY") == "risk"   # USD/JPY rises with risk appetite
    assert scoring.asset_role("DOGE") is None
    print("✓ asset_role")

def test_rank_signals():
    candidates = [
        {"conviction": 50, "asset": "BTC"},
        {"conviction": 90, "asset": "ETH"},
        {"conviction": 70, "asset": "SOL"},
    ]
    ranked = scoring.rank_signals(candidates)
    assert ranked[0]["asset"] == "ETH"
    assert ranked[1]["asset"] == "SOL"
    assert ranked[2]["asset"] == "BTC"
    print("✓ rank_signals")

if __name__ == "__main__":
    test_funding_regime_score()
    test_haven_trend_score()
    test_compute_regime_score()
    test_regime_to_direction()
    test_trend_alignment()
    test_oi_confirmation()
    test_funding_edge()
    test_conviction_for()
    test_margin_pct_for()
    test_trend_structure()
    test_asset_role()
    test_rank_signals()
    print("\n✅ All scoring tests passed")


def test_floor_to_venue_min():
    # a big wallet is never touched
    assert scoring.floor_to_venue_min(3.6, 1000.0, 3, 15.0) == (3.6, False)
    # a small wallet is raised to the smallest fill the venue accepts: 12 / (40 * 3) = 10%
    pct, floored = scoring.floor_to_venue_min(3.6, 40.0, 3, 15.0)
    assert floored and abs(pct - 10.0) < 1e-9
    # 12 / (30 * 3) = 13.333% is stored to 2 dp: rounded UP so the order still clears
    assert scoring.floor_to_venue_min(3.6, 30.0, 3, 15.0) == (13.34, True)
    # too small even at the cap: 12 / (20 * 3) = 20% > 15% -> the caller skips
    assert scoring.floor_to_venue_min(3.6, 20.0, 3, 15.0) == (None, True)
    # no account read -> untouched
    assert scoring.floor_to_venue_min(3.6, 0, 3, 15.0) == (3.6, False)

def test_the_venue_floor_is_the_same_number_the_catalog_minimum_is_built_from():
    # min_budget.py computes aegis's advertised min_budget from BUMPED_NOTIONAL; the scanner sizes to
    # VENUE_MIN_NOTIONAL. The claim that a wallet at the catalog minimum can place its smallest entry holds
    # only while these are equal — a scanner cannot import the runtime script, so the coupling lives here.
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "senpi-trading-runtime", "scripts", "min_budget.py")
    spec = importlib.util.spec_from_file_location("min_budget", path)
    min_budget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(min_budget)
    assert scoring.VENUE_MIN_NOTIONAL == min_budget.BUMPED_NOTIONAL
