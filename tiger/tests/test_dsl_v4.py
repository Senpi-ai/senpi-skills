"""
Tests for dsl-v4.py — DSL trailing stop logic.
Verifies M5 fix (_as_decimal_pct threshold), floor calculations,
breach counting, and tier upgrades.
"""

import json
import os
import pytest

from conftest import import_script

dsl = import_script("dsl_v4", "dsl-v4.py")


class TestAsDecimalPct:
    """M5 fix: values >= 1 are whole-number percents."""

    def test_whole_number_5(self):
        # 5% whole number -> 0.05 decimal
        assert dsl._as_decimal_pct(5) == 0.05

    def test_whole_number_100(self):
        assert dsl._as_decimal_pct(100) == 1.0

    def test_already_decimal(self):
        assert dsl._as_decimal_pct(0.05) == 0.05

    def test_boundary_exactly_1(self):
        """M5 fix: v=1 means 1%, should return 0.01."""
        assert dsl._as_decimal_pct(1) == 0.01

    def test_boundary_just_under_1(self):
        # 0.99 is already a decimal pct (99%), stays as-is
        assert dsl._as_decimal_pct(0.99) == 0.99

    def test_string_input(self):
        assert dsl._as_decimal_pct("5") == 0.05

    def test_zero(self):
        assert dsl._as_decimal_pct(0) == 0.0

    def test_negative(self):
        # -5 is < 1 so stays as-is (edge case)
        assert dsl._as_decimal_pct(-5) == -5.0


class TestSafeFloat:
    def test_valid_number(self):
        assert dsl._safe_float("3.14") == 3.14

    def test_invalid_string(self):
        assert dsl._safe_float("abc", 0.0) == 0.0

    def test_none(self):
        assert dsl._safe_float(None, 5.0) == 5.0

    def test_zero_string(self):
        assert dsl._safe_float("0") == 0.0


class TestApplyDslRetraceOverrides:
    def test_applies_phase1_override(self):
        state = {"phase1": {"retraceThreshold": 0.015}}
        config = {"dslRetrace": {"phase1": 0.02}}
        dsl._apply_dsl_retrace_overrides(state, config)
        assert state["phase1"]["retraceThreshold"] == 0.02

    def test_applies_phase2_override(self):
        state = {"phase2": {"retraceThreshold": 0.012}}
        config = {"dslRetrace": {"phase2": 0.018}}
        dsl._apply_dsl_retrace_overrides(state, config)
        assert state["phase2"]["retraceThreshold"] == 0.018

    def test_no_override_if_empty(self):
        state = {"phase1": {"retraceThreshold": 0.015}}
        config = {}
        dsl._apply_dsl_retrace_overrides(state, config)
        assert state["phase1"]["retraceThreshold"] == 0.015

    def test_creates_phase_dict_if_missing(self):
        state = {}
        config = {"dslRetrace": {"phase1": 0.025}}
        dsl._apply_dsl_retrace_overrides(state, config)
        assert state["phase1"]["retraceThreshold"] == 0.025


class TestProcessStateFile:
    """Test _process_state_file floor calculation and breach logic."""

    def _make_state(self, **overrides):
        """Build a minimal DSL state dict."""
        base = {
            "active": True,
            "asset": "ETH",
            "wallet": "0xTEST",
            "direction": "LONG",
            "entryPrice": 3000.0,
            "size": 1.0,
            "leverage": 10,
            "highWaterPrice": 3100.0,
            "phase": 1,
            "currentBreachCount": 0,
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "pendingClose": False,
            "breachDecay": "hard",
            "maxFetchFailures": 10,
            "consecutiveFetchFailures": 0,
            "tiers": [
                {"triggerPct": 5, "lockPct": 2},    # Tier 1: 5% trigger, 2% lock
                {"triggerPct": 10, "lockPct": 5},   # Tier 2: 10% trigger, 5% lock
            ],
            "phase1": {
                "retraceThreshold": 0.015,
                "consecutiveBreachesRequired": 2,
                "absoluteFloor": 2900.0,
            },
            "phase2": {
                "retraceThreshold": 0.012,
                "consecutiveBreachesRequired": 3,
            },
            "phase2TriggerTier": 1,
        }
        base.update(overrides)
        return base

    def _make_deps(self, price=3050.0, close_success=True):
        """Build mock deps for _process_state_file."""
        writes = {}

        def mock_get_prices(assets=None):
            return {"prices": {"ETH": str(price)}}

        def mock_close_position(wallet, asset, reason=""):
            if close_success:
                return {"success": True}
            return {"error": "failed"}

        def mock_atomic_write(path, data):
            writes[path] = data

        return {
            "get_prices": mock_get_prices,
            "close_position": mock_close_position,
            "atomic_write": mock_atomic_write,
            "_writes": writes,
        }

    def test_upnl_calculation_long(self, tmp_path):
        """uPnL = (price - entry) * size for LONG."""
        state = self._make_state(
            entryPrice=3000.0, size=1.0, leverage=10, highWaterPrice=3000.0
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        price = 3100.0
        deps = self._make_deps(price=price)
        result, errored = dsl._process_state_file(state_file, {}, deps)

        # uPnL = (3100 - 3000) * 1.0 = 100
        # margin = 3000 * 1.0 / 10 = 300
        # upnl_pct = 100 / 300 * 100 = 33.33%
        assert result["upnl"] == 100.0
        assert result["upnl_pct"] == pytest.approx(33.33, abs=0.01)

    def test_upnl_calculation_short(self, tmp_path):
        """uPnL = (entry - price) * size for SHORT."""
        state = self._make_state(
            direction="SHORT", entryPrice=3000.0, size=1.0, leverage=10,
            highWaterPrice=3000.0
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        price = 2900.0
        deps = self._make_deps(price=price)
        result, errored = dsl._process_state_file(state_file, {}, deps)

        # uPnL = (3000 - 2900) * 1.0 = 100
        # margin = 3000 * 1.0 / 10 = 300
        # upnl_pct = 100 / 300 * 100 = 33.33%
        assert result["upnl"] == 100.0
        assert result["upnl_pct"] == pytest.approx(33.33, abs=0.01)

    def test_phase1_trailing_floor_long(self, tmp_path):
        """Phase 1 floor = max(absoluteFloor, hw * (1 - retrace))."""
        # Use tiers=[] to isolate phase 1 floor logic (avoid unintended tier upgrades)
        state = self._make_state(highWaterPrice=3100.0, phase=1, tiers=[])
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # retrace = 0.015
        # trailing_floor = 3100 * (1 - 0.015) = 3100 * 0.985 = 3053.5
        # absolute_floor = 2900
        # effective = max(2900, 3053.5) = 3053.5
        deps = self._make_deps(price=3050.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        expected_trailing = round(3100.0 * (1 - 0.015), 4)  # 3053.5
        assert result["trailing_floor"] == expected_trailing
        assert result["floor"] == expected_trailing  # > absoluteFloor (2900)

    def test_phase1_absolute_floor_used_when_higher(self, tmp_path):
        """If hw is low, absoluteFloor is used instead of trailing."""
        state = self._make_state(
            highWaterPrice=2920.0, phase=1,
            entryPrice=2900.0,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # trailing_floor = 2920 * (1 - 0.015) = 2920 * 0.985 = 2876.2
        # absolute_floor = 2900
        # effective = max(2900, 2876.2) = 2900
        deps = self._make_deps(price=2910.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["floor"] == 2900.0

    def test_breach_counting_hard_reset(self, tmp_path):
        """Hard decay: breach count resets to 0 when price is above floor."""
        state = self._make_state(
            highWaterPrice=3100.0, phase=1, currentBreachCount=1,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Floor = max(2900, 3100 * 0.985) = 3053.5. Price 3080 > 3053.5 → not breached.
        deps = self._make_deps(price=3080.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["breached"] is False
        assert result["breach_count"] == 0

    def test_breach_counting_soft_decay(self, tmp_path):
        """Soft decay: breach count decrements by 1 when price is above floor."""
        # Use tiers=[] to stay in phase 1 (avoids tier upgrades resetting breach count)
        state = self._make_state(
            highWaterPrice=3100.0, phase=1, currentBreachCount=2, breachDecay="soft",
            tiers=[],
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=3080.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["breached"] is False
        assert result["breach_count"] == 1  # 2 - 1 = 1

    def test_tier_upgrade_triggers_phase2(self, tmp_path):
        """Tier upgrade at phase2TriggerTier transitions to phase 2."""
        # phase2TriggerTier=1 means tier index 1 (second tier, triggerPct=10%)
        # Need upnl_pct >= 10% to trigger tier index 1
        # upnl_pct = (price - 3000) / 300 * 100 = 10 → price = 3030
        state = self._make_state(
            entryPrice=3000.0, size=1.0, leverage=10, highWaterPrice=3000.0,
            phase=1, currentTierIndex=-1, phase2TriggerTier=1,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=3030.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["phase"] == 2
        assert result["tier_changed"] is True

    def test_close_on_breach_threshold(self, tmp_path):
        """Position closes when breach count reaches consecutiveBreachesRequired."""
        # Use tiers=[] to stay in phase 1 with breaches_needed=2
        state = self._make_state(
            highWaterPrice=3100.0, phase=1, currentBreachCount=1, tiers=[],
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Floor = max(2900, 3100 * 0.985) = 3053.5. Price 3050 < 3053.5 → breached.
        # breach_count goes from 1 to 2, which == phase1 breaches_needed (2) → close.
        deps = self._make_deps(price=3050.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["breached"] is True
        assert result["breach_count"] == 2
        assert result["should_close"] is True
        assert result["closed"] is True

    def test_close_fails_gracefully(self, tmp_path):
        """If close_position fails, mark pendingClose=True."""
        # Use tiers=[] to stay in phase 1 with breaches_needed=2
        state = self._make_state(
            highWaterPrice=3100.0, phase=1, currentBreachCount=1, tiers=[],
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=3050.0, close_success=False)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["should_close"] is True
        assert result["closed"] is False
        assert result["pending_close"] is True

    def test_inactive_state_returns_inactive(self, tmp_path):
        state = self._make_state(active=False, pendingClose=False)
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps()
        result, errored = dsl._process_state_file(state_file, {}, deps)

        assert result["status"] == "inactive"
        assert errored is False

    def test_high_water_update_long(self, tmp_path):
        """High water updates when price exceeds current hw for LONG."""
        state = self._make_state(highWaterPrice=3050.0)
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=3200.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["hw"] == 3200.0

    def test_high_water_update_short(self, tmp_path):
        """High water updates when price is LOWER than hw for SHORT."""
        state = self._make_state(
            direction="SHORT", highWaterPrice=3000.0, entryPrice=3100.0,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=2900.0)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["hw"] == 2900.0

    def test_tier_floor_calculation_long(self, tmp_path):
        """Tier floor for LONG: entry * (1 + lockPct / leverage)."""
        state = self._make_state(
            entryPrice=3000.0, leverage=10, highWaterPrice=3000.0,
            currentTierIndex=-1,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        # Tier 1: lockPct=2 → _as_decimal_pct(2) = 0.02
        # tier_floor = 3000 * (1 + 0.02 / 10) = 3000 * 1.002 = 3006.0
        deps = self._make_deps(price=3015.0)  # triggers tier 1 (5% ROE)
        result, _ = dsl._process_state_file(state_file, {}, deps)

        expected_floor = round(3000 * (1 + 0.02 / 10), 4)
        assert result["tier_floor"] == expected_floor

    def test_pending_close_triggers_close(self, tmp_path):
        """pendingClose=True forces close regardless of breach count."""
        state = self._make_state(
            highWaterPrice=3100.0, phase=1, currentBreachCount=0,
            pendingClose=True,
        )
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        deps = self._make_deps(price=3080.0)  # No breach, but pendingClose
        result, _ = dsl._process_state_file(state_file, {}, deps)

        assert result["should_close"] is True
        assert result["closed"] is True

    def test_fetch_failure_counting(self, tmp_path):
        """Consecutive fetch failures deactivate after max."""
        state = self._make_state(consecutiveFetchFailures=9, maxFetchFailures=10)
        state_file = str(tmp_path / "dsl-ETH.json")
        with open(state_file, "w") as f:
            json.dump(state, f)

        writes = {}

        def failing_get_prices(assets=None):
            raise RuntimeError("network error")

        deps = {
            "get_prices": failing_get_prices,
            "close_position": lambda w, a, reason="": {"success": True},
            "atomic_write": lambda path, data: writes.update({path: data}),
        }

        result, errored = dsl._process_state_file(state_file, {}, deps)
        assert errored is True
        assert result["consecutive_failures"] == 10
        assert result["deactivated"] is True
