"""
Mathematical correctness tests for tiger_lib.py.

All expected values are hand-computed from indicator definitions.
If a test fails, the code has a bug — do NOT adjust expected values
to match code output.
"""

import math
import pytest
import tiger_lib as lib


# ─── SMA ─────────────────────────────────────────────────────

class TestSMA:
    def test_basic(self):
        # SMA(3) of [1,2,3,4,5]:
        # idx 2: (1+2+3)/3 = 2.0, idx 3: (2+3+4)/3 = 3.0, idx 4: (3+4+5)/3 = 4.0
        result = lib.sma([1, 2, 3, 4, 5], 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_constant_input(self):
        # SMA of constant series = that constant
        result = lib.sma([42.0] * 10, 5)
        for v in result[4:]:
            assert v == pytest.approx(42.0)

    def test_length_preserved(self):
        data = [10, 20, 30, 40, 50]
        assert len(lib.sma(data, 3)) == len(data)

    def test_empty(self):
        assert lib.sma([], 3) == []

    def test_fewer_than_period(self):
        result = lib.sma([1, 2], 5)
        assert result == [None, None]

    def test_period_one(self):
        # SMA(1) = identity
        data = [5, 10, 15]
        result = lib.sma(data, 1)
        assert result == [pytest.approx(5), pytest.approx(10), pytest.approx(15)]


# ─── EMA ─────────────────────────────────────────────────────

class TestEMA:
    def test_seed_is_sma(self):
        # First EMA value = SMA of first `period` values
        # EMA(3) of [1,2,3,...]: first value = SMA([1,2,3]) = 2.0
        result = lib.ema([1, 2, 3, 4, 5], 3)
        assert result[2] == pytest.approx(2.0)

    def test_subsequent_values(self):
        # EMA(3): k = 2/(3+1) = 0.5
        # seed = SMA([1,2,3]) = 2.0
        # idx 3: 4 * 0.5 + 2.0 * 0.5 = 3.0
        # idx 4: 5 * 0.5 + 3.0 * 0.5 = 4.0
        result = lib.ema([1, 2, 3, 4, 5], 3)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_constant_input(self):
        result = lib.ema([100.0] * 10, 3)
        for v in result[2:]:
            assert v == pytest.approx(100.0)

    def test_insufficient_data(self):
        result = lib.ema([1, 2], 5)
        assert all(v is None for v in result)


# ─── RSI ─────────────────────────────────────────────────────

class TestRSI:
    def test_all_gains_is_100(self):
        # 15 values, 14 consecutive gains → avg_loss = 0 → RSI = 100
        closes = [100 + i for i in range(16)]
        result = lib.rsi(closes, 14)
        assert result[14] == pytest.approx(100.0)

    def test_all_losses_is_0(self):
        # 15 values, 14 consecutive losses → avg_gain = 0 → RSI = 0
        closes = [200 - i for i in range(16)]
        result = lib.rsi(closes, 14)
        assert result[14] == pytest.approx(0.0)

    def test_equal_gains_losses(self):
        # Alternating +2, -2 for 14 periods
        # Gains: 7 gains of 2 → avg_gain = 7*2/14 = 1.0
        # Losses: 7 losses of 2 → avg_loss = 7*2/14 = 1.0
        # RS = 1.0, RSI = 100 - 100/(1+1) = 50
        closes = [100]
        for i in range(14):
            closes.append(closes[-1] + (2 if i % 2 == 0 else -2))
        result = lib.rsi(closes, 14)
        assert result[14] == pytest.approx(50.0)

    def test_known_computation(self):
        # 7 gains of 2, 7 losses of 1 over 14 periods
        # avg_gain = 7*2/14 = 1.0, avg_loss = 7*1/14 = 0.5
        # RS = 2.0, RSI = 100 - 100/3 = 66.667
        closes = [100]
        for i in range(14):
            closes.append(closes[-1] + (2 if i % 2 == 0 else -1))
        result = lib.rsi(closes, 14)
        assert result[14] == pytest.approx(100 - 100 / 3, abs=0.01)

    def test_always_in_range(self):
        # RSI must always be in [0, 100]
        import random
        random.seed(42)
        closes = [100]
        for _ in range(50):
            closes.append(closes[-1] + random.uniform(-5, 5))
        result = lib.rsi(closes, 14)
        for v in result:
            if v is not None:
                assert 0 <= v <= 100, f"RSI {v} outside [0, 100]"

    def test_insufficient_data(self):
        result = lib.rsi([100, 101, 102], 14)
        assert all(v is None for v in result)

    def test_output_length(self):
        closes = list(range(100, 130))
        result = lib.rsi(closes, 14)
        assert len(result) == len(closes)

    def test_first_valid_index(self):
        # With period=14, first valid RSI is at index 14 (need 15 values)
        closes = list(range(100, 120))  # 20 values
        result = lib.rsi(closes, 14)
        assert all(v is None for v in result[:14])
        assert result[14] is not None


# ─── Bollinger Bands ─────────────────────────────────────────

class TestBollingerBands:
    def test_constant_series_zero_width(self):
        # Constant series: stdev = 0, upper = middle = lower
        closes = [100.0] * 25
        upper, middle, lower = lib.bollinger_bands(closes, period=20)
        assert upper[19] == pytest.approx(100.0)
        assert middle[19] == pytest.approx(100.0)
        assert lower[19] == pytest.approx(100.0)

    def test_band_symmetry(self):
        # Bands should be symmetric around middle
        closes = [100 + (i % 5) for i in range(25)]
        upper, middle, lower = lib.bollinger_bands(closes, period=20)
        for i in range(19, 25):
            if upper[i] is not None:
                assert upper[i] - middle[i] == pytest.approx(middle[i] - lower[i], abs=1e-10)

    def test_upper_above_lower(self):
        closes = [100 + i * 0.5 for i in range(25)]
        upper, middle, lower = lib.bollinger_bands(closes, period=20)
        for i in range(19, 25):
            assert upper[i] >= lower[i]

    def test_output_lengths(self):
        closes = [100.0] * 25
        upper, middle, lower = lib.bollinger_bands(closes, period=20)
        assert len(upper) == len(middle) == len(lower) == 25


class TestBBWidth:
    def test_constant_zero(self):
        closes = [100.0] * 25
        widths = lib.bb_width(closes, period=20)
        assert widths[19] == pytest.approx(0.0)

    def test_wider_with_more_volatility(self):
        # Tight series vs wide series
        tight = [100.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(25)]
        wide = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(25)]
        tight_w = lib.bb_width(tight, period=20)
        wide_w = lib.bb_width(wide, period=20)
        assert wide_w[-1] > tight_w[-1]


class TestBBWidthPercentile:
    def test_returns_none_insufficient_data(self):
        closes = [100.0] * 5
        assert lib.bb_width_percentile(closes, period=20) is None

    def test_range_0_to_100(self):
        import random
        random.seed(99)
        closes = [100 + random.uniform(-3, 3) for _ in range(150)]
        pctl = lib.bb_width_percentile(closes, period=20, lookback=100)
        if pctl is not None:
            assert 0 <= pctl <= 100


# ─── ATR ─────────────────────────────────────────────────────

class TestATR:
    def test_constant_zero_range(self):
        # H=L=C → true range = 0, ATR = 0
        n = 20
        highs = [100.0] * n
        lows = [100.0] * n
        closes = [100.0] * n
        result = lib.atr(highs, lows, closes, period=14)
        assert result[13] == pytest.approx(0.0)

    def test_constant_range(self):
        # H=102, L=98, C=100 → true range always = 4
        # ATR(14) = average of 14 ranges of 4 = 4.0
        n = 20
        highs = [102.0] * n
        lows = [98.0] * n
        closes = [100.0] * n
        result = lib.atr(highs, lows, closes, period=14)
        assert result[13] == pytest.approx(4.0)

    def test_true_range_uses_previous_close(self):
        # True Range = max(H-L, |H-prevC|, |L-prevC|)
        # If gap up: H=110, L=108, C=109, prevC=100
        # TR = max(110-108, |110-100|, |108-100|) = max(2, 10, 8) = 10
        highs = [100, 110]
        lows = [100, 108]
        closes = [100, 109]
        result = lib.atr(highs, lows, closes, period=1)
        # ATR(1) at index 1 should be 10 (single TR)
        # But first TR at index 0 is H[0]-L[0] = 0
        # The ATR at period-1=0 is just trs[0]=0, then at 1: (0*0 + 10)/1 = 10
        assert result[1] == pytest.approx(10.0)

    def test_output_length(self):
        result = lib.atr([100] * 20, [98] * 20, [99] * 20, 14)
        assert len(result) == 20

    def test_insufficient_data(self):
        result = lib.atr([100], [98], [99], 14)
        assert all(v is None for v in result)


# ─── Volume Ratio ────────────────────────────────────────────

class TestVolumeRatio:
    def test_equal_volumes(self):
        volumes = [1000] * 20
        assert lib.volume_ratio(volumes, 5, 20) == pytest.approx(1.0)

    def test_increasing_volume(self):
        # Recent 5 are 2000, rest are 1000
        volumes = [1000] * 15 + [2000] * 5
        # short_avg = 2000, long_avg = (15*1000 + 5*2000)/20 = 25000/20 = 1250
        assert lib.volume_ratio(volumes, 5, 20) == pytest.approx(2000 / 1250)

    def test_insufficient_data(self):
        assert lib.volume_ratio([100, 200], 5, 20) is None

    def test_zero_long_avg(self):
        assert lib.volume_ratio([0] * 20, 5, 20) is None


# ─── OI Change ───────────────────────────────────────────────

class TestOIChange:
    def test_basic_change(self):
        # 13 values, periods=12: compare index -13 to index -1
        # 100 → 120 = +20%
        history = [100] + [100] * 11 + [120]
        assert lib.oi_change_pct(history, 12) == pytest.approx(20.0)

    def test_decrease(self):
        history = [200] + [200] * 11 + [150]
        assert lib.oi_change_pct(history, 12) == pytest.approx(-25.0)

    def test_zero_base(self):
        history = [0] + [0] * 12
        assert lib.oi_change_pct(history, 12) is None

    def test_insufficient_data(self):
        assert lib.oi_change_pct([100, 110], 12) is None


# ─── Confluence Score ────────────────────────────────────────

class TestConfluenceScore:
    def test_all_true(self):
        factors = {
            "a": (True, 0.3),
            "b": (True, 0.3),
            "c": (True, 0.4),
        }
        assert lib.confluence_score(factors) == pytest.approx(1.0)

    def test_all_false(self):
        factors = {
            "a": (False, 0.5),
            "b": (False, 0.5),
        }
        assert lib.confluence_score(factors) == pytest.approx(0.0)

    def test_partial(self):
        factors = {
            "a": (True, 0.3),
            "b": (False, 0.3),
            "c": (True, 0.4),
        }
        # Only a and c are true: 0.3 + 0.4 = 0.7
        assert lib.confluence_score(factors) == pytest.approx(0.7)

    def test_empty(self):
        assert lib.confluence_score({}) == pytest.approx(0.0)

    def test_weights_must_be_summed_not_averaged(self):
        # Score = sum of weights where true, NOT average
        factors = {"x": (True, 0.1)}
        assert lib.confluence_score(factors) == pytest.approx(0.1)


# ─── Kelly Fraction ──────────────────────────────────────────

class TestKellyFraction:
    def test_positive_edge(self):
        # win_rate=0.6, avg_win=2, avg_loss=1
        # b = 2/1 = 2, f = (0.6*2 - 0.4)/2 = (1.2-0.4)/2 = 0.4
        # Half-kelly: 0.4 * 0.5 = 0.2
        assert lib.kelly_fraction(0.6, 2.0, 1.0) == pytest.approx(0.2)

    def test_no_edge(self):
        # win_rate=0.5, avg_win=1, avg_loss=1
        # b = 1, f = (0.5 - 0.5)/1 = 0, half = 0
        assert lib.kelly_fraction(0.5, 1.0, 1.0) == pytest.approx(0.0)

    def test_negative_edge_returns_zero(self):
        # Losing system: kelly < 0 → clamped to 0
        assert lib.kelly_fraction(0.3, 1.0, 1.0) == 0.0

    def test_capped_at_25_percent(self):
        # Very high edge should still cap at 0.25
        result = lib.kelly_fraction(0.95, 10.0, 1.0)
        assert result <= 0.25

    def test_zero_avg_loss(self):
        assert lib.kelly_fraction(0.6, 2.0, 0.0) == 0


# ─── Required Daily Return ──────────────────────────────────

class TestRequiredDailyReturn:
    def test_double_in_7_days(self):
        # Need 2x in 7 days: (2)^(1/7) - 1 = ~10.41%
        result = lib.required_daily_return(1000, 2000, 7)
        expected = (math.pow(2, 1 / 7) - 1) * 100
        assert result == pytest.approx(expected, abs=0.01)

    def test_already_at_target(self):
        # current = target: ratio = 1, daily return = 0%
        result = lib.required_daily_return(1000, 1000, 7)
        assert result == pytest.approx(0.0)

    def test_zero_days_remaining(self):
        assert lib.required_daily_return(1000, 2000, 0) is None

    def test_zero_current(self):
        assert lib.required_daily_return(0, 2000, 7) is None


# ─── Aggression Mode ─────────────────────────────────────────

class TestAggressionMode:
    def test_conservative(self):
        # ≤ 8% daily
        assert lib.aggression_mode(5.0) == "CONSERVATIVE"
        assert lib.aggression_mode(8.0) == "CONSERVATIVE"

    def test_normal(self):
        # 8% < rate ≤ 15%
        assert lib.aggression_mode(10.0) == "NORMAL"
        assert lib.aggression_mode(15.0) == "NORMAL"

    def test_elevated(self):
        # 15% < rate ≤ 25%
        assert lib.aggression_mode(20.0) == "ELEVATED"
        assert lib.aggression_mode(25.0) == "ELEVATED"

    def test_abort(self):
        # > 25%
        assert lib.aggression_mode(30.0) == "ABORT"

    def test_none_is_abort(self):
        assert lib.aggression_mode(None) == "ABORT"


# ─── Parse Candles ───────────────────────────────────────────

class TestParseCandles:
    def test_basic_extraction(self):
        candles = [
            {"o": 100, "h": 105, "l": 95, "c": 102, "v": 5000},
            {"o": 102, "h": 108, "l": 99, "c": 107, "v": 6000},
        ]
        o, h, l, c, v = lib.parse_candles(candles)
        assert o == [100.0, 102.0]
        assert h == [105.0, 108.0]
        assert l == [95.0, 99.0]
        assert c == [102.0, 107.0]
        assert v == [5000.0, 6000.0]

    def test_string_values_converted(self):
        candles = [{"o": "100", "h": "105", "l": "95", "c": "102", "v": "5000"}]
        o, h, l, c, v = lib.parse_candles(candles)
        assert c == [102.0]

    def test_empty(self):
        o, h, l, c, v = lib.parse_candles([])
        assert o == h == l == c == v == []


# ─── RSI Divergence ──────────────────────────────────────────

class TestRSIDivergence:
    def test_bullish_divergence(self):
        # Price makes lower low in second half, RSI makes higher low
        # First half: price min=90, RSI min=20
        # Second half: price min=85, RSI min=25
        closes = [100, 95, 90, 95, 100, 98, 95, 90, 93, 95,  # first 10
                  98, 93, 88, 85, 88, 90, 92, 93, 94, 95]    # second 10
        # RSI needs enough data, build synthetic RSI values
        rsi_vals = [None] * 5 + [50, 40, 20, 30, 45,  # first half min=20
                                  50, 35, 30, 25, 35, 40, 45, 48, 50, 52]  # second half min=25
        result = lib.detect_rsi_divergence(closes, rsi_vals, lookback=20)
        assert result == "bullish"

    def test_bearish_divergence(self):
        # Price makes higher high in second half, RSI makes lower high
        closes = [100, 105, 110, 108, 105, 103, 102, 100, 101, 102,
                  105, 108, 112, 115, 112, 110, 108, 106, 105, 104]
        rsi_vals = [None] * 5 + [50, 60, 80, 70, 55,  # first half max=80
                                  60, 65, 70, 75, 68, 60, 55, 50, 48, 45]  # second half max=75
        result = lib.detect_rsi_divergence(closes, rsi_vals, lookback=20)
        assert result == "bearish"

    def test_no_divergence(self):
        # Price and RSI move together — no divergence
        closes = list(range(100, 120))
        rsi_vals = [None] * 5 + list(range(40, 55))
        result = lib.detect_rsi_divergence(closes, rsi_vals, lookback=20)
        assert result is None

    def test_insufficient_rsi_data(self):
        closes = list(range(100, 120))
        rsi_vals = [None] * 15 + [50, 55, 60, 65, 70]
        result = lib.detect_rsi_divergence(closes, rsi_vals, lookback=20)
        assert result is None
