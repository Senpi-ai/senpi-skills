"""
Tests for tiger-exit.py — position exit management.
Verifies trailing lock, daily target, stagnation, deadline, false breakout,
and time stop logic with hand-computed expected values.
"""

import pytest
from datetime import datetime, timezone, timedelta

from conftest import import_script

exit_mod = import_script("tiger_exit", "tiger-exit.py")


def _make_pos_data(coin="ETH", entry_px="3000", pnl=30, margin=300, szi="1.0", leverage=10):
    """Build a minimal Hyperliquid position dict."""
    return {
        "coin": coin,
        "entryPx": entry_px,
        "unrealizedPnl": str(pnl),
        "marginUsed": str(margin),
        "szi": str(szi),
        "leverage": {"value": leverage},
    }


def _make_state(aggression="NORMAL", days_remaining=5, daily_rate=10, balance=1000):
    return {
        "aggression": aggression,
        "daysRemaining": days_remaining,
        "dailyRateNeeded": daily_rate,
        "currentBalance": balance,
    }


class TestDailyTargetHit:
    def test_triggers_when_pnl_exceeds_target(self):
        pos = _make_pos_data(pnl=120, margin=300)
        active_pos = {"ETH": {"highWaterRoe": 35, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=10, balance=1000)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 120/300*100 = 40%
        # daily_target_usd = 1000 * 10/100 = 100
        # pnl 120 >= 100
        assert result is not None
        assert any(a["type"] == "DAILY_TARGET_HIT" for a in result["actions"])

    def test_no_trigger_below_target(self):
        pos = _make_pos_data(pnl=80, margin=300)
        active_pos = {"ETH": {"highWaterRoe": 25, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=10, balance=1000)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # pnl 80 < 100 target, and ROE=26.7% hw=26.7 → no trailing lock (hw <= 5 check: 26.7 > 5,
        # but roe not < locked)
        if result:
            assert not any(a["type"] == "DAILY_TARGET_HIT" for a in result["actions"])


class TestTrailingLock:
    def test_triggers_when_roe_below_lock_pct(self):
        pos = _make_pos_data(pnl=30, margin=300)
        # hw was 40%, now ROE is 10%
        active_pos = {"ETH": {"highWaterRoe": 40, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state()

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 30/300*100 = 10%
        # high_water = max(40, 10) = 40
        # locked_level = 40 * 0.60 = 24
        # 10 < 24 AND 40 > 5 AND 10 > 0 → TRAILING_LOCK
        assert result is not None
        assert any(a["type"] == "TRAILING_LOCK" for a in result["actions"])

    def test_no_trigger_above_lock(self):
        pos = _make_pos_data(pnl=100, margin=300)
        active_pos = {"ETH": {"highWaterRoe": 30, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state()

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 100/300*100 = 33.3%
        # high_water = max(30, 33.3) = 33.3
        # locked_level = 33.3 * 0.6 = 20.0
        # 33.3 > 20.0 → no trailing lock
        if result:
            assert not any(a["type"] == "TRAILING_LOCK" for a in result["actions"])

    def test_no_trigger_insufficient_hw(self):
        """Trailing lock requires high_water_roe > 5."""
        pos = _make_pos_data(pnl=3, margin=300)
        active_pos = {"ETH": {"highWaterRoe": 0, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 1%, hw = max(0, 1) = 1 <= 5 → no trailing lock
        assert result is None

    def test_deadline_tightens_lock(self):
        """Last day: lock_pct = max(normal, 0.85)."""
        pos = _make_pos_data(pnl=60, margin=300)
        # hw 30%, ROE 20%. At 60% lock, 20 >= 30*0.6=18 → no trigger.
        # But at 85% lock (deadline), 20 < 30*0.85=25.5 → triggers.
        active_pos = {"ETH": {"highWaterRoe": 30, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(days_remaining=1)  # Last day

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 60/300*100 = 20%
        # lock_pct = max(0.60, 0.85) = 0.85
        # hw = max(30, 20) = 30
        # locked_level = 30 * 0.85 = 25.5
        # 20 < 25.5 AND 30 > 5 AND 20 > 0 → TRAILING_LOCK
        assert result is not None
        assert any(a["type"] == "TRAILING_LOCK" for a in result["actions"])
        assert result["lock_pct"] == 0.85


class TestStagnation:
    def test_triggers_after_24_checks(self):
        pos = _make_pos_data(pnl=12, margin=300)
        # ROE = 4%, hw will be max(4.5, 4) = 4.5
        # abs(4.5 - 4) = 0.5 < 1, roe > 3, stagnantChecks >= 24
        active_pos = {
            "ETH": {
                "highWaterRoe": 4.5, "openedAt": "", "pattern": "unknown",
                "stagnantChecks": 24, "prevHighWater": 4.5,
            }
        }
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 12/300*100 = 4%
        assert result is not None
        assert any(a["type"] == "STAGNATION" for a in result["actions"])

    def test_no_stagnation_if_moving(self):
        """High water well above current ROE means position is NOT stagnant."""
        pos = _make_pos_data(pnl=12, margin=300)
        # ROE = 4%, hw = max(10, 4) = 10
        # Stagnation check: abs(10 - 4) = 6 >= 1 → check fails, no STAGNATION
        active_pos = {
            "ETH": {
                "highWaterRoe": 10, "openedAt": "", "pattern": "unknown",
                "stagnantChecks": 24, "prevHighWater": 10,
            }
        }
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE=4%, hw=10, abs(10-4)=6 > 1 → no stagnation
        # But trailing lock: hw=10>5, roe=4>0, locked=10*0.6=6, 4<6 → TRAILING_LOCK triggers
        if result:
            assert not any(a["type"] == "STAGNATION" for a in result["actions"])


class TestDeadline:
    def test_deadline_reached(self):
        pos = _make_pos_data(pnl=10, margin=300)
        active_pos = {"ETH": {"openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(days_remaining=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        assert result is not None
        assert any(a["type"] == "DEADLINE" for a in result["actions"])
        # DEADLINE has CRITICAL priority, should be primary_action
        assert result["primary_action"]["type"] == "DEADLINE"

    def test_no_deadline_with_time(self):
        pos = _make_pos_data(pnl=1, margin=300)
        active_pos = {"ETH": {"openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(days_remaining=5)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        if result:
            assert not any(a["type"] == "DEADLINE" for a in result["actions"])


class TestFalseBreakout:
    def test_triggers_on_bb_reentry(self):
        pos = _make_pos_data(pnl=5, margin=300)
        active_pos = {
            "ETH": {
                "openedAt": "", "pattern": "COMPRESSION_BREAKOUT",
                "breakoutCandleIndex": 10,
                "candlesSinceBreakout": 1,
                "bbReentry": True,
                "highWaterRoe": 2,
            }
        }
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 5/300*100 = 1.67% < 3, candles_since <= 2, bb_reentry True
        assert result is not None
        assert any(a["type"] == "FALSE_BREAKOUT" for a in result["actions"])

    def test_no_trigger_high_roe(self):
        """No false breakout if ROE > 3% (position is working)."""
        pos = _make_pos_data(pnl=30, margin=300)
        active_pos = {
            "ETH": {
                "openedAt": "", "pattern": "COMPRESSION_BREAKOUT",
                "breakoutCandleIndex": 10,
                "candlesSinceBreakout": 1,
                "bbReentry": True,
                "highWaterRoe": 10,
            }
        }
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = 10% > 3 → false breakout check doesn't trigger
        if result:
            assert not any(a["type"] == "FALSE_BREAKOUT" for a in result["actions"])


class TestTimeStop:
    def test_triggers_after_30_min_negative(self):
        opened = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        pos = _make_pos_data(pnl=-10, margin=300)
        active_pos = {
            "ETH": {"openedAt": opened, "pattern": "unknown", "highWaterRoe": 0}
        }
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(daily_rate=0)

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        # ROE = -10/300*100 = -3.33% < -2 and < 0, elapsed 45min > 30
        assert result is not None
        assert any(a["type"] == "TIME_STOP" for a in result["actions"])


class TestMarginGuard:
    def test_zero_margin_returns_none(self):
        pos = _make_pos_data(margin=0)
        active_pos = {"ETH": {"openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state()

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        assert result is None


class TestActionPriority:
    def test_critical_outranks_high(self):
        """DEADLINE (CRITICAL) should be primary over TRAILING_LOCK (HIGH)."""
        pos = _make_pos_data(pnl=30, margin=300)
        active_pos = {"ETH": {"highWaterRoe": 40, "openedAt": "", "pattern": "unknown"}}
        config = {"trailingLockPct": {"NORMAL": 0.60}}
        state = _make_state(days_remaining=0)  # Deadline + trailing lock both trigger

        result = exit_mod.evaluate_position(pos, active_pos, config, state)
        assert result is not None
        # DEADLINE=CRITICAL should outrank TRAILING_LOCK=HIGH
        assert result["primary_action"]["type"] == "DEADLINE"
