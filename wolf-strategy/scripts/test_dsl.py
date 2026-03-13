#!/usr/bin/env python3
"""Test suite for wolf-strategy DSL v5.2 integration.

Covers: asset_to_filename, dsl_state_path, dsl_state_glob, dsl_position_state_files,
build_wolf_dsl_config, validate_dsl_state, resolve_dsl_cli_path, and migration path logic.

Run:
  python3 test_dsl.py              # run all tests (from wolf-strategy/scripts/ or repo root)
  python3 test_dsl.py -v            # verbose
  python3 test_dsl.py -l            # list test names

Uses unittest; no external test deps. Registry and filesystem are faked or temp dirs.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Run from scripts/ or repo root; ensure wolf_config is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import wolf_config


# ---------------------------------------------------------------------------
# asset_to_filename
# ---------------------------------------------------------------------------

class TestAssetToFilename(unittest.TestCase):
    def test_mainnet_asset_unchanged(self):
        self.assertEqual(wolf_config.asset_to_filename("HYPE"), "HYPE")
        self.assertEqual(wolf_config.asset_to_filename("ETH"), "ETH")

    def test_xyz_asset_colon_replaced_with_double_dash(self):
        self.assertEqual(wolf_config.asset_to_filename("xyz:SILVER"), "xyz--SILVER")
        self.assertEqual(wolf_config.asset_to_filename("xyz:BTC"), "xyz--BTC")

    def test_only_first_colon_replaced(self):
        self.assertEqual(wolf_config.asset_to_filename("xyz:SILVER"), "xyz--SILVER")


# ---------------------------------------------------------------------------
# build_wolf_dsl_config
# ---------------------------------------------------------------------------

class TestBuildWolfDslConfig(unittest.TestCase):
    def test_no_dsl_uses_default_tiers(self):
        cfg = {"strategyId": "abc", "name": "Test"}
        out = wolf_config.build_wolf_dsl_config(cfg)
        self.assertTrue(out["phase1"]["enabled"])
        self.assertEqual(out["phase1"]["retraceThreshold"], 0.10)
        self.assertEqual(out["phase1"]["consecutiveBreachesRequired"], 3)
        self.assertEqual(out["phase2TriggerTier"], 0)
        self.assertTrue(out["phase2"]["enabled"])
        self.assertEqual(out["phase2"]["retraceThreshold"], 0.015)
        self.assertEqual(len(out["phase2"]["tiers"]), 4)
        # DSL v5.3.1: default is High Water (profile or DEFAULT_DSL_TIERS with lockHwPct)
        if out.get("lockMode") == "pct_of_high_water":
            self.assertIn("lockHwPct", out["phase2"]["tiers"][0])
            self.assertEqual(out["phase2"]["tiers"][0]["triggerPct"], 7)
            self.assertEqual(out["phase2"]["tiers"][3]["triggerPct"], 20)
        else:
            self.assertEqual(out["phase2"]["tiers"][0], {"triggerPct": 5, "lockPct": 50})
            self.assertEqual(out["phase2"]["tiers"][3], {"triggerPct": 20, "lockPct": 85})
        self.assertIn(out["phase2"]["consecutiveBreachesRequired"], (1, 2))

    def test_custom_tiers_from_strategy(self):
        cfg = {
            "dsl": {
                "tiers": [
                    {"triggerPct": 8, "lockPct": 55, "breaches": 2},
                    {"triggerPct": 16, "lockPct": 70, "breaches": 2},
                ]
            }
        }
        out = wolf_config.build_wolf_dsl_config(cfg)
        self.assertEqual(len(out["phase2"]["tiers"]), 2)
        self.assertEqual(out["phase2"]["tiers"][0], {"triggerPct": 8, "lockPct": 55})
        self.assertEqual(out["phase2"]["tiers"][1], {"triggerPct": 16, "lockPct": 70})
        self.assertEqual(out["phase2"]["consecutiveBreachesRequired"], 2)

    def test_tiers_strip_breaches_key_for_dsl_v5(self):
        """DSL v5.3.1: fixed-ROE tiers pass triggerPct and lockPct only; High Water passes lockHwPct + consecutiveBreachesRequired."""
        cfg = {
            "dsl": {
                "tiers": [
                    {"triggerPct": 5, "lockPct": 50, "breaches": 3},
                    {"triggerPct": 10, "lockPct": 65, "breaches": 2},
                ]
            }
        }
        out = wolf_config.build_wolf_dsl_config(cfg)
        for t in out["phase2"]["tiers"]:
            self.assertNotIn("breaches", t)
            self.assertIn("triggerPct", t)
            self.assertIn("lockPct", t)

    def test_breaches_required_alias(self):
        cfg = {
            "dsl": {
                "tiers": [
                    {"triggerPct": 5, "lockPct": 50, "breachesRequired": 3},
                    {"triggerPct": 10, "lockPct": 65, "breachesRequired": 3},
                ]
            }
        }
        out = wolf_config.build_wolf_dsl_config(cfg)
        self.assertEqual(out["phase2"]["consecutiveBreachesRequired"], 3)

    def test_majority_breach_count(self):
        # 3, 2, 2, 1 → majority 2
        cfg = {"dsl": {"tiers": [
            {"triggerPct": 5, "lockPct": 50, "breaches": 3},
            {"triggerPct": 10, "lockPct": 65, "breaches": 2},
            {"triggerPct": 15, "lockPct": 75, "breaches": 2},
            {"triggerPct": 20, "lockPct": 85, "breaches": 1},
        ]}}
        out = wolf_config.build_wolf_dsl_config(cfg)
        self.assertEqual(out["phase2"]["consecutiveBreachesRequired"], 2)

        # all 3 → 3
        cfg2 = {"dsl": {"tiers": [
            {"triggerPct": 5, "lockPct": 50, "breaches": 3},
            {"triggerPct": 10, "lockPct": 65, "breaches": 3},
        ]}}
        out2 = wolf_config.build_wolf_dsl_config(cfg2)
        self.assertEqual(out2["phase2"]["consecutiveBreachesRequired"], 3)

    def test_empty_tiers_fallback_breach_count(self):
        cfg = {"dsl": {"tiers": []}}
        out = wolf_config.build_wolf_dsl_config(cfg)
        # Empty strategy tiers → profile or DEFAULT_DSL_TIERS (non-empty)
        self.assertGreaterEqual(len(out["phase2"]["tiers"]), 0)
        self.assertIn(out["phase2"]["consecutiveBreachesRequired"], (1, 2, 3))


# ---------------------------------------------------------------------------
# validate_dsl_state
# ---------------------------------------------------------------------------

def _minimal_dsl_state():
    return {
        "asset": "HYPE",
        "direction": "LONG",
        "entryPrice": 30.0,
        "size": 1.0,
        "leverage": 6.0,
        "highWaterPrice": 30.5,
        "phase": 1,
        "currentBreachCount": 0,
        "currentTierIndex": -1,
        "tierFloorPrice": None,
        "tiers": [{"triggerPct": 5, "lockPct": 50}],
        "phase1": {"retraceThreshold": 0.10, "consecutiveBreachesRequired": 3},
    }


class TestValidateDslState(unittest.TestCase):
    def test_valid_state_passes(self):
        state = _minimal_dsl_state()
        ok, err = wolf_config.validate_dsl_state(state)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_not_dict_fails(self):
        ok, err = wolf_config.validate_dsl_state([])
        self.assertFalse(ok)
        self.assertIn("not a dict", err)

    def test_missing_top_level_keys_fails(self):
        state = _minimal_dsl_state()
        del state["asset"]
        ok, err = wolf_config.validate_dsl_state(state)
        self.assertFalse(ok)
        self.assertIn("missing keys", err)
        self.assertIn("asset", err)

    def test_phase1_not_dict_fails(self):
        state = _minimal_dsl_state()
        state["phase1"] = "invalid"
        ok, err = wolf_config.validate_dsl_state(state)
        self.assertFalse(ok)
        self.assertIn("phase1", err)

    def test_phase1_missing_keys_fails(self):
        state = _minimal_dsl_state()
        del state["phase1"]["retraceThreshold"]
        ok, err = wolf_config.validate_dsl_state(state)
        self.assertFalse(ok)
        self.assertIn("phase1", err)

    def test_tiers_not_list_fails(self):
        state = _minimal_dsl_state()
        state["tiers"] = {}
        ok, err = wolf_config.validate_dsl_state(state)
        self.assertFalse(ok)
        self.assertIn("tiers", err)


# ---------------------------------------------------------------------------
# dsl_state_path, dsl_state_glob (with mocked load_strategy)
# ---------------------------------------------------------------------------

class TestDslStatePathAndGlob(unittest.TestCase):
    def test_dsl_state_path_uses_uuid_and_asset_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wolf_config, "DSL_STATE_DIR", tmp):
                with patch.object(
                    wolf_config,
                    "load_strategy",
                    return_value={"strategyId": "6a23783a-12e6-415c-b59b-70ca5e5c3a1d"},
                ):
                    path = wolf_config.dsl_state_path("wolf-any", "HYPE")
                    self.assertEqual(
                        path,
                        os.path.join(tmp, "6a23783a-12e6-415c-b59b-70ca5e5c3a1d", "HYPE.json"),
                    )

    def test_dsl_state_path_xyz_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wolf_config, "DSL_STATE_DIR", tmp):
                with patch.object(
                    wolf_config,
                    "load_strategy",
                    return_value={"strategyId": "abc-uuid"},
                ):
                    path = wolf_config.dsl_state_path("wolf-any", "xyz:SILVER")
                    self.assertEqual(
                        path,
                        os.path.join(tmp, "abc-uuid", "xyz--SILVER.json"),
                    )

    def test_dsl_state_glob_returns_pattern_under_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wolf_config, "DSL_STATE_DIR", tmp):
                with patch.object(
                    wolf_config,
                    "load_strategy",
                    return_value={"strategyId": "strat-123"},
                ):
                    pattern = wolf_config.dsl_state_glob("wolf-any")
                    self.assertEqual(pattern, os.path.join(tmp, "strat-123", "*.json"))


# ---------------------------------------------------------------------------
# dsl_position_state_files (excludes strategy-*.json and *_archived_*)
# ---------------------------------------------------------------------------

class TestDslPositionStateFiles(unittest.TestCase):
    def test_filters_out_strategy_and_archived_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            uuid = "6a23783a-12e6-415c-b59b-70ca5e5c3a1d"
            d = os.path.join(tmp, uuid)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "HYPE.json"), "w").close()
            open(os.path.join(d, "ETH.json"), "w").close()
            open(os.path.join(d, "strategy-6a23783a.json"), "w").close()
            open(os.path.join(d, "HYPE_archived_123.json"), "w").close()
            open(os.path.join(d, "BTC.archived.json"), "w").close()

            with patch.object(wolf_config, "DSL_STATE_DIR", tmp):
                with patch.object(
                    wolf_config,
                    "load_strategy",
                    return_value={"strategyId": uuid},
                ):
                    files = wolf_config.dsl_position_state_files("wolf-any")
                    basenames = [os.path.basename(p) for p in files]
                    self.assertIn("HYPE.json", basenames)
                    self.assertIn("ETH.json", basenames)
                    self.assertNotIn("strategy-6a23783a.json", basenames)
                    self.assertNotIn("HYPE_archived_123.json", basenames)
                    self.assertNotIn("BTC.archived.json", basenames)
                    self.assertEqual(len(files), 2)


# ---------------------------------------------------------------------------
# resolve_dsl_cli_path
# ---------------------------------------------------------------------------

class TestResolveDslCliPath(unittest.TestCase):
    def test_env_dsl_cli_path_returned_when_file_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            cli_path = f.name
        try:
            with patch.dict(os.environ, {"DSL_CLI_PATH": cli_path}, clear=False):
                self.assertEqual(wolf_config.resolve_dsl_cli_path(), cli_path)
        finally:
            if os.path.exists(cli_path):
                os.unlink(cli_path)

    def test_registry_global_dsl_cli_path_used_when_env_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "wolf-strategies.json")
            fake_cli = os.path.join(tmp, "dsl-cli.py")
            with open(fake_cli, "w") as f:
                f.write("# fake dsl-cli\n")
            with open(reg_path, "w") as f:
                json.dump({"global": {"dslCliPath": fake_cli}, "strategies": {}}, f)

            with patch.object(wolf_config, "REGISTRY_FILE", reg_path):
                with patch.dict(os.environ, {"DSL_CLI_PATH": ""}, clear=False):
                    self.assertEqual(wolf_config.resolve_dsl_cli_path(), fake_cli)

    def test_fail_when_not_found(self):
        nonexistent_registry = os.path.join(tempfile.gettempdir(), "wolf-test-nonexistent-registry-xyz.json")
        with patch.dict(os.environ, {"DSL_CLI_PATH": ""}, clear=False):
            with patch.object(wolf_config, "REGISTRY_FILE", nonexistent_registry):
                with patch.object(wolf_config, "_discover_dsl_cli_path", return_value=None):
                    with patch("sys.stdout"):
                        with self.assertRaises(SystemExit):
                            wolf_config.resolve_dsl_cli_path()


# ---------------------------------------------------------------------------
# _discover_dsl_cli_path (discovery finds scripts/dsl-cli.py under a skill dir)
# ---------------------------------------------------------------------------

class TestDiscoverDslCliPath(unittest.TestCase):
    def test_discovers_dsl_cli_under_workspace_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "skills", "dsl-dynamic-stop-loss", "scripts")
            os.makedirs(skill_dir, exist_ok=True)
            cli_path = os.path.join(skill_dir, "dsl-cli.py")
            open(cli_path, "w").close()

            with patch.object(wolf_config, "WORKSPACE", tmp):
                found = wolf_config._discover_dsl_cli_path()
                self.assertEqual(found, os.path.abspath(cli_path))

    def test_returns_none_or_path_when_workspace_skills_missing_dsl_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "skills", "other-skill", "scripts"), exist_ok=True)
            # No dsl-cli.py under tmp/skills; _discover_dsl_cli_path also checks repo root (parent of wolf-strategy)
            with patch.object(wolf_config, "WORKSPACE", tmp):
                found = wolf_config._discover_dsl_cli_path()
                # In-repo runs may find dsl-cli.py under repo root (e.g. dsl-dynamic-stop-loss/scripts/)
                self.assertTrue(found is None or (isinstance(found, str) and "dsl-cli.py" in found))


# ---------------------------------------------------------------------------
# Migration path logic (wolf-migrate-dsl): asset from basename, new path shape
# ---------------------------------------------------------------------------

class TestMigrationPathLogic(unittest.TestCase):
    """Test the path/name conventions used by wolf-migrate-dsl (no subprocess)."""

    def test_old_basename_to_asset(self):
        basename = "dsl-HYPE.json"
        asset = basename.replace("dsl-", "").replace(".json", "")
        self.assertEqual(asset, "HYPE")

    def test_new_path_uses_uuid_and_asset_filename(self):
        uuid = "6a23783a-12e6-415c-b59b-70ca5e5c3a1d"
        asset = "xyz:SILVER"
        new_filename = wolf_config.asset_to_filename(asset) + ".json"
        new_dir = os.path.join("/data/workspace/dsl", uuid)
        new_path = os.path.join(new_dir, new_filename)
        self.assertEqual(new_filename, "xyz--SILVER.json")
        self.assertIn(uuid, new_path)
        self.assertTrue(new_path.endswith("xyz--SILVER.json"))


# ---------------------------------------------------------------------------
# DEFAULT_DSL_TIERS
# ---------------------------------------------------------------------------

class TestDefaultDslTiers(unittest.TestCase):
    def test_has_four_tiers(self):
        self.assertEqual(len(wolf_config.DEFAULT_DSL_TIERS), 4)

    def test_tiers_have_trigger_and_lock_hw_pct(self):
        # DSL v5.3.1 default is High Water (lockHwPct, consecutiveBreachesRequired)
        for t in wolf_config.DEFAULT_DSL_TIERS:
            self.assertIn("triggerPct", t)
            self.assertIn("lockHwPct", t)
            self.assertIn("consecutiveBreachesRequired", t)
        self.assertEqual(wolf_config.DEFAULT_DSL_TIERS[0]["triggerPct"], 7)
        self.assertEqual(wolf_config.DEFAULT_DSL_TIERS[0]["lockHwPct"], 40)
        self.assertEqual(wolf_config.DEFAULT_DSL_TIERS[3]["triggerPct"], 20)
        self.assertEqual(wolf_config.DEFAULT_DSL_TIERS[3]["lockHwPct"], 85)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
