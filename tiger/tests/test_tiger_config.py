"""
Config, state management, and path resolution tests for tiger_config.py.
Verifies H5 fix (instance dir paths) and state I/O correctness.
"""

import json
import os
import pytest
from datetime import datetime, timezone

import tiger_config


class TestAliasDict:
    def test_camel_case_direct(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        assert d["maxSlots"] == 3

    def test_snake_case_resolves_to_camel(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        assert d["max_slots"] == 3

    def test_snake_case_in_operator(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        assert "max_slots" in d
        assert "maxSlots" in d

    def test_get_with_snake_case(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        assert d.get("max_slots") == 3
        assert d.get("nonexistent", 99) == 99

    def test_write_to_camel_when_exists(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        d["max_slots"] = 5
        assert d["maxSlots"] == 5

    def test_unknown_key_raises(self):
        d = tiger_config.AliasDict({"maxSlots": 3})
        with pytest.raises(KeyError):
            _ = d["nonexistent"]


class TestDeepMerge:
    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = tiger_config.deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = tiger_config.deep_merge(base, override)
        assert result["x"] == {"a": 1, "b": 3, "c": 4}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        tiger_config.deep_merge(base, override)
        assert base["a"]["b"] == 1


class TestNormalizeUserConfig:
    def test_snake_to_camel(self):
        user = {"max_slots": 5, "max_leverage": 10}
        result = tiger_config._normalize_user_config(user)
        assert result["maxSlots"] == 5
        assert result["maxLeverage"] == 10

    def test_camel_passes_through(self):
        user = {"maxSlots": 5}
        result = tiger_config._normalize_user_config(user)
        assert result["maxSlots"] == 5

    def test_dsl_retrace_normalization(self):
        user = {"dsl_retrace": {"phase_1": 0.015, "phase_2": 0.02}}
        result = tiger_config._normalize_user_config(user)
        assert result["dslRetrace"]["phase1"] == 0.015
        assert result["dslRetrace"]["phase2"] == 0.02


class TestInstanceDir:
    def test_uses_strategy_id(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        d = tiger_config._instance_dir(config, runtime=tmp_runtime)
        assert d.endswith(os.path.join("state", "test-strategy"))

    def test_default_fallback(self, tmp_runtime):
        config = tiger_config._to_alias_dict({"strategyId": None})
        d = tiger_config._instance_dir(config, runtime=tmp_runtime)
        assert d.endswith(os.path.join("state", "default"))


class TestStatePersistence:
    def test_save_load_roundtrip(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        state = tiger_config.deep_merge(tiger_config.DEFAULT_STATE, {
            "instanceKey": "test-strategy",
            "currentBalance": 1234.56,
            "aggression": "ELEVATED",
        })
        tiger_config.save_state(config, state, runtime=tmp_runtime)
        loaded = tiger_config.load_state(config=config, runtime=tmp_runtime)
        assert loaded["currentBalance"] == 1234.56
        assert loaded["aggression"] == "ELEVATED"

    def test_halt_flag_preserved_on_save(self, tmp_runtime):
        """If another cron set halted=True, saving state with halted=False must preserve it."""
        config = tiger_config.load_config(runtime=tmp_runtime)

        # First save: halted=True (simulating risk guardian)
        state1 = tiger_config.deep_merge(tiger_config.DEFAULT_STATE, {
            "instanceKey": "test-strategy",
            "safety": {"halted": True, "haltReason": "Daily loss limit"},
        })
        tiger_config.save_state(config, state1, runtime=tmp_runtime)

        # Second save: halted=False (simulating scanner that didn't know about halt)
        state2 = tiger_config.deep_merge(tiger_config.DEFAULT_STATE, {
            "instanceKey": "test-strategy",
            "safety": {"halted": False, "haltReason": None},
        })
        tiger_config.save_state(config, state2, runtime=tmp_runtime)

        # Load: halt flag should be preserved
        loaded = tiger_config.load_state(config=config, runtime=tmp_runtime)
        assert loaded["safety"]["halted"] is True
        assert loaded["safety"]["haltReason"] == "Daily loss limit"


class TestBtcCache:
    def test_save_load_roundtrip(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        tiger_config.save_btc_cache(67500.0, "2026-03-02T10:00:00+00:00", config=config, runtime=tmp_runtime)
        cache = tiger_config.load_btc_cache(config=config, runtime=tmp_runtime)
        assert cache["last_btc_price"] == 67500.0
        assert cache["last_btc_check"] == "2026-03-02T10:00:00+00:00"

    def test_scan_fields_persisted(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        tiger_config.save_btc_cache(
            67500.0, "2026-03-02T10:00:00+00:00",
            scan_price=67500.0, scan_ts="2026-03-02T10:00:00+00:00",
            config=config, runtime=tmp_runtime,
        )
        cache = tiger_config.load_btc_cache(config=config, runtime=tmp_runtime)
        assert cache["last_scan_price"] == 67500.0
        assert cache["last_scan_ts"] == "2026-03-02T10:00:00+00:00"

    def test_scan_fields_preserved_on_update(self, tmp_runtime):
        """Updating price should not erase existing scan fields."""
        config = tiger_config.load_config(runtime=tmp_runtime)
        # First save with scan fields
        tiger_config.save_btc_cache(
            67500.0, "2026-03-02T10:00:00+00:00",
            scan_price=67500.0, scan_ts="2026-03-02T10:00:00+00:00",
            config=config, runtime=tmp_runtime,
        )
        # Second save without scan fields
        tiger_config.save_btc_cache(67600.0, "2026-03-02T10:03:00+00:00", config=config, runtime=tmp_runtime)
        cache = tiger_config.load_btc_cache(config=config, runtime=tmp_runtime)
        assert cache["last_btc_price"] == 67600.0
        assert cache["last_scan_price"] == 67500.0  # preserved from first save

    def test_file_in_instance_dir(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        tiger_config.save_btc_cache(100, "ts", config=config, runtime=tmp_runtime)
        expected = os.path.join(tmp_runtime.state_dir, "test-strategy", "btc-cache.json")
        assert os.path.exists(expected)


class TestPrescreenedFilePath:
    def test_in_instance_dir(self, tmp_runtime):
        """H5 fix: prescreened.json must be in instance dir, not base state dir."""
        config = tiger_config.load_config(runtime=tmp_runtime)
        path = tiger_config._prescreened_file(config=config, runtime=tmp_runtime)
        assert "test-strategy" in path
        assert path.endswith("prescreened.json")
        # Must NOT be in base state dir
        assert path != os.path.join(tmp_runtime.state_dir, "prescreened.json")


class TestDisabledPatterns:
    def test_empty_when_no_file(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        result = tiger_config.get_disabled_patterns(config=config, runtime=tmp_runtime)
        assert result == set()

    def test_reads_disabled_patterns(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        # Write a roar-state.json with disabled patterns
        roar_state = {
            "disabled_patterns": {
                "COMPRESSION_BREAKOUT": (datetime.now(timezone.utc) + __import__("datetime").timedelta(hours=24)).isoformat(),
            }
        }
        instance_dir = os.path.join(tmp_runtime.state_dir, "test-strategy")
        with open(os.path.join(instance_dir, "roar-state.json"), "w") as f:
            json.dump(roar_state, f)

        result = tiger_config.get_disabled_patterns(config=config, runtime=tmp_runtime)
        assert "COMPRESSION_BREAKOUT" in result

    def test_expired_patterns_not_returned(self, tmp_runtime):
        config = tiger_config.load_config(runtime=tmp_runtime)
        roar_state = {
            "disabled_patterns": {
                "EXPIRED_PATTERN": (datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=1)).isoformat(),
            }
        }
        instance_dir = os.path.join(tmp_runtime.state_dir, "test-strategy")
        with open(os.path.join(instance_dir, "roar-state.json"), "w") as f:
            json.dump(roar_state, f)

        result = tiger_config.get_disabled_patterns(config=config, runtime=tmp_runtime)
        assert "EXPIRED_PATTERN" not in result


class TestHelpers:
    def test_is_halted(self):
        state = {"safety": {"halted": True, "haltReason": "test"}}
        assert tiger_config.is_halted(state) is True

    def test_is_not_halted(self):
        state = {"safety": {"halted": False}}
        assert tiger_config.is_halted(state) is False

    def test_get_active_positions_empty(self):
        state = {}
        result = tiger_config.get_active_positions(state)
        assert result == {}

    def test_get_pattern_min_confluence_override(self, sample_config, sample_state):
        config = tiger_config._to_alias_dict({
            **sample_config,
            "patternConfluenceOverrides": {"COMPRESSION_BREAKOUT": 0.65},
        })
        result = tiger_config.get_pattern_min_confluence(config, sample_state, "COMPRESSION_BREAKOUT")
        assert result == 0.65

    def test_get_pattern_min_confluence_aggression_fallback(self, sample_config, sample_state):
        # No override → falls back to minConfluenceScore for aggression level
        result = tiger_config.get_pattern_min_confluence(sample_config, sample_state, "SOME_PATTERN")
        expected = float(sample_config["minConfluenceScore"]["NORMAL"])
        assert result == expected
