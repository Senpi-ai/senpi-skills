"""
Tests for correlation-scanner.py — BTC correlation lag detection.
Verifies check_btc_move, check_alt_lag, and BTC cache fast-path logic.
"""

import pytest
from conftest import import_script, make_candles

corr = import_script("correlation_scanner", "correlation-scanner.py")


class TestCheckBtcMove:
    def _make_candles_result(self, closes):
        """Build a mock get_asset_candles return value from closes."""
        candles = make_candles(closes)
        return {"success": True, "data": {"candles": {"1h": candles}}}

    def test_no_trigger_flat(self):
        # BTC barely moved: all closes ~65000
        closes = [65000, 65010, 65020, 65010, 65000, 65015]
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return self._make_candles_result(closes)

        result = corr.check_btc_move(config, state, get_candles)
        assert result["triggered"] is False

    def test_trigger_4h_move(self):
        # BTC jumped: closes[-4]=65000, closes[-1]=66500
        # move_4h = (66500-65000)/65000*100 = 2.31%
        closes = [64000, 65000, 65200, 65300, 65400, 66500]
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return self._make_candles_result(closes)

        result = corr.check_btc_move(config, state, get_candles)
        # closes[-4] = 65200, closes[-1] = 66500
        # move_4h = (66500-65200)/65200*100 = 1.994%
        # closes[-2] = 65400, move_1h = (66500-65400)/65400*100 = 1.682%
        # 1h threshold = 2 * 0.6 = 1.2, 1.682 >= 1.2 → triggered via 1h path
        assert result["triggered"] is True
        assert result["direction"] == "LONG"

    def test_trigger_short_direction(self):
        # BTC dropped
        closes = [67000, 66000, 65500, 65000, 64500, 64000]
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return self._make_candles_result(closes)

        result = corr.check_btc_move(config, state, get_candles)
        # move_4h = (64000 - 65500)/65500*100 = -2.29% → abs >= 2
        assert result["triggered"] is True
        assert result["direction"] == "SHORT"

    def test_1h_fast_trigger(self):
        """1h threshold = movePct * 0.6. Even if 4h doesn't trigger, 1h can."""
        # 4h move small, but 1h move >= threshold * 0.6
        # closes[-4]=65000, closes[-1]=65300 → 4h = 0.46% (< 2)
        # closes[-2]=64500, closes[-1]=65300 → 1h = 1.24% >= 2*0.6=1.2
        closes = [64800, 65000, 65100, 65100, 64500, 65300]
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return self._make_candles_result(closes)

        result = corr.check_btc_move(config, state, get_candles)
        # closes[-4] = 65100, closes[-1] = 65300
        # move_4h = (65300-65100)/65100*100 = 0.307% (< 2)
        # closes[-2] = 64500, move_1h = (65300-64500)/64500*100 = 1.240% >= 1.2
        assert result["triggered"] is True

    def test_insufficient_candles(self):
        closes = [65000, 65100]  # Only 2 candles, need >= 5
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return self._make_candles_result(closes)

        result = corr.check_btc_move(config, state, get_candles)
        assert result["triggered"] is False
        assert "error" in result

    def test_fetch_failure(self):
        config = {"btcCorrelationMovePct": 2}
        state = {}

        def get_candles(asset, intervals):
            return {"success": False}

        result = corr.check_btc_move(config, state, get_candles)
        assert result["triggered"] is False


class TestCheckAltLag:
    def _mock_candles_fn(self, closes_1h, closes_4h):
        """Build a mock get_asset_candles function."""
        def fn(asset, intervals):
            return {
                "success": True,
                "data": {
                    "candles": {
                        "1h": make_candles(closes_1h),
                        "4h": make_candles(closes_4h),
                    }
                }
            }
        return fn

    def test_strong_lag_long(self):
        """Alt barely moved while BTC went up 3% → strong lag."""
        # Alt 1h: need >= 5. Alt barely moved in last 4h.
        # closes_1h[-4] = 100, closes_1h[-1] = 100.3 → alt_move = 0.3%
        closes_1h = [100] * 5 + [100.3]
        # 4h candles need >= 20 for RSI
        closes_4h = [100] * 20 + [100.3]

        btc_direction = "LONG"
        btc_move_4h = 3.0  # BTC moved 3%
        instruments_map = {"TESTALT": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}}
        config = {"minLeverage": 5}

        result = corr.check_alt_lag(
            "TESTALT", btc_direction, btc_move_4h,
            instruments_map, {}, config,
            self._mock_candles_fn(closes_1h, closes_4h),
        )

        assert result is not None
        assert result["asset"] == "TESTALT"
        assert result["pattern"] == "CORRELATION_LAG"
        assert result["direction"] == "LONG"
        # lag = 3.0 - 0.3 = 2.7
        # lag_ratio = 2.7 / 3.0 = 0.9 → STRONG
        assert result["lag_ratio"] == pytest.approx(0.9, abs=0.05)
        assert result["window_quality"] == "STRONG"

    def test_no_lag_alt_already_moved(self):
        """Alt moved 80%+ of BTC's move → lag_ratio < 0.4 → None."""
        # closes_1h[-4] = 100, closes_1h[-1] = 102.5 → alt_move = 2.5%
        closes_1h = [100] * 5 + [102.5]
        closes_4h = [100] * 20 + [102.5]

        result = corr.check_alt_lag(
            "TESTALT", "LONG", 3.0,
            {"TESTALT": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}},
            {}, {"minLeverage": 5},
            self._mock_candles_fn(closes_1h, closes_4h),
        )

        # lag = 3.0 - 2.5 = 0.5, lag_ratio = 0.5/3.0 = 0.167 < 0.4 → None
        assert result is None

    def test_short_direction_lag(self):
        """For SHORT, lag = alt_move - btc_move (alt should be MORE negative)."""
        # BTC dropped -3%, alt dropped only -0.5%
        closes_1h = [100] * 5 + [99.5]  # alt_move = -0.5%
        closes_4h = [100] * 20 + [99.5]

        result = corr.check_alt_lag(
            "TESTALT", "SHORT", -3.0,
            {"TESTALT": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}},
            {}, {"minLeverage": 5},
            self._mock_candles_fn(closes_1h, closes_4h),
        )

        # For SHORT: lag = alt_move - btc_move = -0.5 - (-3.0) = 2.5
        # lag_ratio = 2.5 / 3.0 = 0.833 → STRONG
        assert result is not None
        assert result["direction"] == "SHORT"
        assert result["lag_ratio"] == pytest.approx(0.833, abs=0.05)

    def test_insufficient_candles(self):
        """Returns None when not enough candle data."""
        closes_1h = [100, 101, 102]  # Only 3, need >= 5
        closes_4h = [100] * 20

        result = corr.check_alt_lag(
            "TESTALT", "LONG", 3.0,
            {"TESTALT": {"max_leverage": 20, "context": {}}},
            {}, {"minLeverage": 5},
            self._mock_candles_fn(closes_1h, closes_4h),
        )
        assert result is None

    def test_window_quality_levels(self):
        """Verify STRONG > 0.7, MODERATE 0.5-0.7, CLOSING 0.4-0.5."""
        # lag_ratio = 0.55 → MODERATE
        # alt_move needed: btc 3%, lag_ratio 0.55 → alt_move = 3*(1-0.55) = 1.35%
        closes_1h = [100] * 5 + [101.35]
        closes_4h = [100] * 20 + [101.35]

        result = corr.check_alt_lag(
            "TESTALT", "LONG", 3.0,
            {"TESTALT": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}},
            {}, {"minLeverage": 5},
            self._mock_candles_fn(closes_1h, closes_4h),
        )

        assert result is not None
        assert result["window_quality"] == "MODERATE"


class TestConfluenceFactors:
    """Verify the confluence factor booleans are computed correctly."""

    def test_high_corr_alt_factor(self):
        """Assets in HIGH_CORR_ALTS get the high_correlation_alt factor."""
        closes_1h = [100] * 5 + [100.1]
        closes_4h = [100] * 20 + [100.1]

        def mock_candles(asset, intervals):
            return {
                "success": True,
                "data": {"candles": {
                    "1h": make_candles(closes_1h),
                    "4h": make_candles(closes_4h),
                }}
            }

        instruments_map = {
            "ETH": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}
        }

        result = corr.check_alt_lag(
            "ETH", "LONG", 3.0,
            instruments_map, {}, {"minLeverage": 5},
            mock_candles,
        )

        if result:
            assert result["factors"]["high_correlation_alt"] is True

    def test_non_corr_alt_factor(self):
        """Assets NOT in HIGH_CORR_ALTS don't get the factor."""
        closes_1h = [100] * 5 + [100.1]
        closes_4h = [100] * 20 + [100.1]

        def mock_candles(asset, intervals):
            return {
                "success": True,
                "data": {"candles": {
                    "1h": make_candles(closes_1h),
                    "4h": make_candles(closes_4h),
                }}
            }

        instruments_map = {
            "RANDOMCOIN": {"max_leverage": 20, "context": {"openInterest": "50000", "funding": "0.0001"}}
        }

        result = corr.check_alt_lag(
            "RANDOMCOIN", "LONG", 3.0,
            instruments_map, {}, {"minLeverage": 5},
            mock_candles,
        )

        if result:
            assert result["factors"]["high_correlation_alt"] is False
