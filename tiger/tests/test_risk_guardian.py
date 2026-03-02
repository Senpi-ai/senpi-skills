"""
Tests for risk-guardian.py — risk limit checks and alert generation.
Verifies daily loss, drawdown, OI shifts, deadline, funding reversal,
and position P&L checks with hand-computed expected values.
"""

import pytest

from conftest import import_script

risk = import_script("risk_guardian", "risk-guardian.py")


class TestCheckDailyLoss:
    def test_no_breach(self):
        config = {"maxDailyLossPct": 12}
        state = {"dayStartBalance": 1000}
        result = risk.check_daily_loss(config, state, 950)
        assert result["breach"] is False
        # loss = 50, pct = 50/1000*100 = 5.0%
        assert result["loss_pct"] == 5.0

    def test_breach(self):
        config = {"maxDailyLossPct": 12}
        state = {"dayStartBalance": 1000}
        result = risk.check_daily_loss(config, state, 870)
        # loss = 130, pct = 13.0%
        assert result["breach"] is True
        assert result["loss_pct"] == 13.0
        assert result["type"] == "DAILY_LOSS"
        assert result["loss_usd"] == 130.0

    def test_at_exact_limit(self):
        config = {"maxDailyLossPct": 10}
        state = {"dayStartBalance": 1000}
        result = risk.check_daily_loss(config, state, 900)
        # loss = 100, pct = 10.0% == limit
        assert result["breach"] is True

    def test_uses_budget_if_no_day_start(self):
        config = {"maxDailyLossPct": 12, "budget": 500}
        state = {}
        result = risk.check_daily_loss(config, state, 450)
        # dayStartBalance defaults to config["budget"] = 500
        # loss = 50, pct = 10.0%
        assert result["breach"] is False
        assert result["loss_pct"] == 10.0

    def test_zero_day_start_falls_back_to_budget(self):
        config = {"maxDailyLossPct": 12, "budget": 500}
        state = {"dayStartBalance": 0}
        result = risk.check_daily_loss(config, state, 450)
        # dayStartBalance=0 is falsy → falls back to budget=500
        # loss = 50, pct = 10.0%
        assert result["loss_pct"] == 10.0

    def test_no_budget_no_day_start(self):
        """Defensive: neither dayStartBalance nor budget → no crash, pct=0."""
        config = {"maxDailyLossPct": 12}
        state = {}
        result = risk.check_daily_loss(config, state, 100)
        assert result["loss_pct"] == 0


class TestCheckDrawdown:
    def test_no_breach(self):
        config = {"maxDrawdownPct": 20}
        state = {"peakBalance": 1000}
        result = risk.check_drawdown(config, state, 850)
        assert result["breach"] is False
        # dd = (1000-850)/1000*100 = 15.0%
        assert result["drawdown_pct"] == 15.0

    def test_breach(self):
        config = {"maxDrawdownPct": 20}
        state = {"peakBalance": 1000}
        result = risk.check_drawdown(config, state, 790)
        # dd = (1000-790)/1000*100 = 21.0%
        assert result["breach"] is True
        assert result["drawdown_pct"] == 21.0
        assert result["type"] == "MAX_DRAWDOWN"

    def test_at_peak(self):
        config = {"maxDrawdownPct": 20}
        state = {"peakBalance": 1000}
        result = risk.check_drawdown(config, state, 1000)
        assert result["breach"] is False
        assert result["drawdown_pct"] == 0.0

    def test_peak_zero(self):
        config = {"maxDrawdownPct": 20}
        state = {"peakBalance": 0}
        result = risk.check_drawdown(config, state, 100)
        assert result["breach"] is False


class TestCheckDeadline:
    def test_deadline_reached(self):
        result = risk.check_deadline({}, {}, lambda c: 0)
        assert len(result) == 1
        assert result[0]["type"] == "DEADLINE_REACHED"
        assert result[0]["action"] == "CLOSE_ALL"

    def test_deadline_imminent(self):
        result = risk.check_deadline({}, {}, lambda c: 0.4)
        # 0.4 days = 9.6h, <= 0.5 days
        assert len(result) == 1
        assert result[0]["type"] == "DEADLINE_IMMINENT"
        assert result[0]["action"] == "TIGHTEN_STOPS"

    def test_deadline_approaching(self):
        result = risk.check_deadline({}, {}, lambda c: 0.8)
        # 0.8 days = 19.2h, > 0.5 but <= 1
        assert len(result) == 1
        assert result[0]["type"] == "DEADLINE_APPROACHING"

    def test_no_alert_days_remaining(self):
        result = risk.check_deadline({}, {}, lambda c: 5)
        assert result == []

    def test_negative_days(self):
        result = risk.check_deadline({}, {}, lambda c: -1)
        # -1 <= 0 -> DEADLINE_REACHED
        assert any(a["type"] == "DEADLINE_REACHED" for a in result)


class TestCheckOiShifts:
    def test_oi_collapse(self):
        config = {"oiCollapseThresholdPct": 25, "oiReduceThresholdPct": 10, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # OI dropped from 100000 to 70000 = -30%
        oi_history = {
            "ETH": [{"oi": 100000, "price": 3500}] * 12 + [{"oi": 70000, "price": 3400}]
        }
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        assert len(result) == 1
        assert result[0]["type"] == "OI_COLLAPSE"
        assert result[0]["action"] == "CLOSE"
        assert result[0]["oi_change_pct"] == -30.0

    def test_oi_drop(self):
        config = {"oiCollapseThresholdPct": 25, "oiReduceThresholdPct": 10, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # OI dropped from 100000 to 85000 = -15%
        oi_history = {
            "ETH": [{"oi": 100000, "price": 3500}] * 12 + [{"oi": 85000, "price": 3400}]
        }
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        assert len(result) == 1
        assert result[0]["type"] == "OI_DROP"
        assert result[0]["action"] == "REDUCE"

    def test_oi_building(self):
        config = {"oiCollapseThresholdPct": 25, "oiReduceThresholdPct": 10, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # OI grew from 100000 to 115000 = +15%
        oi_history = {
            "ETH": [{"oi": 100000, "price": 3500}] * 12 + [{"oi": 115000, "price": 3500}]
        }
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        assert len(result) == 1
        assert result[0]["type"] == "OI_BUILDING"
        assert result[0]["action"] == "HOLD"

    def test_no_alert_small_change(self):
        config = {"oiCollapseThresholdPct": 25, "oiReduceThresholdPct": 10, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # OI changed by 5% — below all thresholds
        oi_history = {
            "ETH": [{"oi": 100000, "price": 3500}] * 12 + [{"oi": 105000, "price": 3500}]
        }
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        assert result == []

    def test_reduce_threshold_guard(self):
        """If oiReduceThresholdPct >= oiCollapseThresholdPct, reduce is halved."""
        config = {"oiCollapseThresholdPct": 10, "oiReduceThresholdPct": 15, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # OI dropped 8%
        oi_history = {
            "ETH": [{"oi": 100000, "price": 3500}] * 12 + [{"oi": 92000, "price": 3400}]
        }
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        # reduce_threshold = max(1.0, 10/2) = 5
        # oi_change = -8%, -8 < -5 → OI_DROP
        assert any(a["type"] == "OI_DROP" for a in result)

    def test_insufficient_history(self):
        config = {"oiCollapseThresholdPct": 25, "oiReduceThresholdPct": 10, "minOiChangePct": 10}
        state = {"activePositions": {"ETH": {"direction": "LONG"}}}
        # Only 5 data points — need at least 12
        oi_history = {"ETH": [{"oi": 100000, "price": 3500}] * 5}
        result = risk.check_oi_shifts(config, state, lambda: oi_history)
        assert result == []


class TestCheckFundingReversal:
    def test_funding_flipped_short_to_negative(self):
        config = {}
        state = {
            "activePositions": {
                "ETH": {"pattern": "FUNDING_ARB", "direction": "SHORT"}
            }
        }
        # We went SHORT to collect positive funding. Now funding is negative.
        instruments = [
            {"name": "ETH", "is_delisted": False, "context": {"funding": "-0.001"}}
        ]
        result = risk.check_funding_reversal(config, state, lambda: instruments)
        assert len(result) == 1
        assert result[0]["type"] == "FUNDING_REVERSED"
        assert result[0]["action"] == "CLOSE"

    def test_funding_weak(self):
        config = {}
        state = {
            "activePositions": {
                "ETH": {"pattern": "FUNDING_ARB", "direction": "SHORT"}
            }
        }
        # Funding still positive but weak:
        # annualized = 0.000005 * 3 * 365 * 100 = 0.5475% < 10%
        instruments = [
            {"name": "ETH", "is_delisted": False, "context": {"funding": "0.000005"}}
        ]
        result = risk.check_funding_reversal(config, state, lambda: instruments)
        assert len(result) == 1
        assert result[0]["type"] == "FUNDING_WEAK"

    def test_non_funding_arb_ignored(self):
        config = {}
        state = {
            "activePositions": {
                "ETH": {"pattern": "COMPRESSION_BREAKOUT", "direction": "SHORT"}
            }
        }
        instruments = [
            {"name": "ETH", "is_delisted": False, "context": {"funding": "-0.001"}}
        ]
        result = risk.check_funding_reversal(config, state, lambda: instruments)
        assert result == []

    def test_healthy_funding(self):
        config = {}
        state = {
            "activePositions": {
                "ETH": {"pattern": "FUNDING_ARB", "direction": "SHORT"}
            }
        }
        # Funding healthy: 0.0005 * 3 * 365 * 100 = 54.75% > 10%
        instruments = [
            {"name": "ETH", "is_delisted": False, "context": {"funding": "0.0005"}}
        ]
        result = risk.check_funding_reversal(config, state, lambda: instruments)
        assert result == []


class TestCheckPositionPnl:
    def test_single_loss_limit(self):
        config = {"maxSingleLossPct": 5}
        state = {"currentBalance": 1000}
        positions = [
            {"coin": "ETH", "unrealizedPnl": -60, "marginUsed": 200}
        ]
        result = risk.check_position_pnl(config, state, positions)
        # loss_of_balance = 60 / 1000 * 100 = 6% > 5%
        assert len(result) == 1
        assert result[0]["type"] == "SINGLE_LOSS_LIMIT"
        assert result[0]["action"] == "CLOSE"

    def test_daily_target_hit(self):
        config = {"maxSingleLossPct": 5}
        state = {"currentBalance": 1000, "dailyRateNeeded": 10}
        positions = [
            {"coin": "ETH", "unrealizedPnl": 120, "marginUsed": 200}
        ]
        result = risk.check_position_pnl(config, state, positions)
        # daily_target_usd = 1000 * 10/100 = 100
        # pnl 120 >= 100
        assert any(a["type"] == "DAILY_TARGET_HIT" for a in result)

    def test_no_alert_small_loss(self):
        config = {"maxSingleLossPct": 5}
        state = {"currentBalance": 1000, "dailyRateNeeded": 10}
        positions = [
            {"coin": "ETH", "unrealizedPnl": -10, "marginUsed": 200}
        ]
        result = risk.check_position_pnl(config, state, positions)
        # loss_of_balance = 10/1000*100 = 1% < 5%
        # pnl -10 not positive, no daily target check
        assert result == []

    def test_zero_balance_guard(self):
        config = {"maxSingleLossPct": 5}
        state = {"currentBalance": 0, "dailyRateNeeded": 10}
        positions = [
            {"coin": "ETH", "unrealizedPnl": -10, "marginUsed": 200}
        ]
        # Should not crash on division by zero
        result = risk.check_position_pnl(config, state, positions)
        assert isinstance(result, list)
