#!/usr/bin/env python3
"""Test all testable methods in dsl-v5.py and dsl-cli.py.

Run:
  python3 test_all_methods.py           # run all tests (from scripts/ or repo root)
  python3 test_all_methods.py -l        # list all test names (--list)

Uses unittest; no external test deps. MCP/subprocess-dependent functions are not invoked (no live mcporter).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

# Load dsl-v5 and dsl-cli from this script's directory (hyphenated module names)
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

dsl_v5 = _load_module("dsl_v5", "dsl-v5.py")
dsl_cli = _load_module("dsl_cli", "dsl-cli.py")


# ---------------------------------------------------------------------------
# dsl-v5 tests
# ---------------------------------------------------------------------------

class TestDslV5PathHelpers(unittest.TestCase):
    def test_asset_to_filename(self):
        self.assertEqual(dsl_v5.asset_to_filename("xyz:SILVER"), "xyz--SILVER")
        self.assertEqual(dsl_v5.asset_to_filename("ETH"), "ETH")
        self.assertEqual(dsl_v5.asset_to_filename("xyz:BTC"), "xyz--BTC")

    def test_filename_to_asset(self):
        self.assertEqual(dsl_v5.filename_to_asset("xyz--SILVER.json"), "xyz:SILVER")
        self.assertEqual(dsl_v5.filename_to_asset("ETH.json"), "ETH")
        self.assertIsNone(dsl_v5.filename_to_asset("ETH.txt"))
        self.assertIsNone(dsl_v5.filename_to_asset("bad--middle.json"))  # -- not xyz--

    def test_resolve_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "strat-1"
            asset = "ETH"
            path = os.path.join(d, strat, "ETH.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
            p, err = dsl_v5.resolve_state_file(d, strat, asset)
            self.assertIsNone(err)
            self.assertEqual(p, path)
            p2, err2 = dsl_v5.resolve_state_file(d, strat, "MISSING")
            self.assertEqual(err2, "state_file_not_found")
            p3, err3 = dsl_v5.resolve_state_file(d, "", "ETH")
            self.assertEqual(err3, "strategy_id and asset required")

    def test_list_strategy_state_files(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            sd = os.path.join(d, strat)
            os.makedirs(sd, exist_ok=True)
            for name in ["ETH.json", "xyz--SILVER.json", "strategy-x.json", "ETH_archived_123.json"]:
                open(os.path.join(sd, name), "w").close()
            out = dsl_v5.list_strategy_state_files(d, strat)
            self.assertEqual(len(out), 2)  # ETH, xyz--SILVER
            assets = [a for _, a in out]
            self.assertIn("ETH", assets)
            self.assertIn("xyz:SILVER", assets)
        out_empty = dsl_v5.list_strategy_state_files("/nonexistent", "x")
        self.assertEqual(out_empty, [])

    def test_dex_and_lookup_symbol(self):
        self.assertEqual(dsl_v5.dex_and_lookup_symbol("xyz:SILVER"), ("xyz", "SILVER"))
        self.assertEqual(dsl_v5.dex_and_lookup_symbol("ETH"), ("", "ETH"))


class TestDslV5UnwrapMcporter(unittest.TestCase):
    def test_unwrap_mcporter_response(self):
        # Direct JSON object
        self.assertEqual(dsl_v5._unwrap_mcporter_response('{"a":1}'), {"a": 1})
        # Wrapped content[0].text
        raw = json.dumps({"content": [{"type": "text", "text": '{"b":2}'}]})
        self.assertEqual(dsl_v5._unwrap_mcporter_response(raw), {"b": 2})
        # Invalid
        self.assertIsNone(dsl_v5._unwrap_mcporter_response("not json"))
        self.assertIsNone(dsl_v5._unwrap_mcporter_response("[]"))


class TestDslV5NormalizeState(unittest.TestCase):
    def test_normalize_state_phase_config(self):
        state = {"entryPrice": 100.0, "leverage": 10, "direction": "LONG"}
        changed = dsl_v5.normalize_state_phase_config(state)
        self.assertTrue(changed)
        self.assertIn("phase1", state)
        self.assertIn("phase2", state)
        self.assertEqual(state["phase1"]["retraceThreshold"], dsl_v5.DEFAULT_PHASE1_RETRACE)
        self.assertEqual(state["phase1"]["consecutiveBreachesRequired"], dsl_v5.DEFAULT_PHASE1_BREACHES)
        self.assertAlmostEqual(state["phase1"]["absoluteFloor"], 100.0 * (1 - 0.03 / 10))
        # Already complete -> no change
        changed2 = dsl_v5.normalize_state_phase_config(state)
        self.assertFalse(changed2)


class TestDslV5TradingLogic(unittest.TestCase):
    def test_update_high_water(self):
        state = {"highWaterPrice": 100.0}
        hw = dsl_v5.update_high_water(state, 105.0, True)
        self.assertEqual(hw, 105.0)
        self.assertEqual(state["highWaterPrice"], 105.0)
        hw2 = dsl_v5.update_high_water(state, 103.0, True)
        self.assertEqual(hw2, 105.0)
        state_short = {"highWaterPrice": 100.0}
        hw3 = dsl_v5.update_high_water(state_short, 95.0, False)
        self.assertEqual(hw3, 95.0)

    def test_apply_tier_upgrades(self):
        state = {
            "tiers": [
                {"triggerPct": 10, "lockPct": 5},
                {"triggerPct": 20, "lockPct": 14},
            ],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 1,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
        }
        # upnl_pct=15 crosses tier 0 (trigger 10), not tier 1 (trigger 20)
        tier_idx, tier_floor, tier_changed, prev = dsl_v5.apply_tier_upgrades(
            state, 15.0, True, 110.0
        )
        self.assertEqual(tier_idx, 0)
        self.assertTrue(tier_changed)
        self.assertIsNotNone(tier_floor)
        self.assertEqual(state["currentTierIndex"], 0)
        # Phase stays 1 unless tier_idx >= phase2TriggerTier; tier 0 >= 0 so phase -> 2
        self.assertEqual(state["phase"], 2)
        # Cross tier 1 with upnl_pct=25
        state["currentTierIndex"] = 0
        tier_idx2, _, tier_changed2, _ = dsl_v5.apply_tier_upgrades(state, 25.0, True, 115.0)
        self.assertEqual(tier_idx2, 1)
        self.assertTrue(tier_changed2)

    def test_compute_effective_floor(self):
        state = {
            "phase1": {"retraceThreshold": 0.03, "consecutiveBreachesRequired": 3, "absoluteFloor": 97.0},
            "phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 1},
            "tiers": [{"triggerPct": 10, "lockPct": 5, "retrace": 0.012}],
            "leverage": 10,
        }
        eff, trail, needed, retrace = dsl_v5.compute_effective_floor(
            state, 1, 0, None, 105.0, True
        )
        self.assertGreater(eff, 0)
        self.assertGreater(needed, 0)
        self.assertEqual(retrace, 0.03)

    def test_update_breach_count(self):
        state = {"currentBreachCount": 0}
        c = dsl_v5.update_breach_count(state, True, "soft")
        self.assertEqual(c, 1)
        self.assertEqual(state["currentBreachCount"], 1)
        c2 = dsl_v5.update_breach_count(state, False, "soft")
        self.assertEqual(c2, 0)
        state["currentBreachCount"] = 2
        c3 = dsl_v5.update_breach_count(state, False, "hard")
        self.assertEqual(c3, 0)
        self.assertEqual(state["currentBreachCount"], 0)


class TestDslV5ArchivedFilename(unittest.TestCase):
    def test_archived_state_filename(self):
        with unittest.mock.patch.object(time, "time", return_value=1709722800.0):
            out = dsl_v5._archived_state_filename("/some/ETH.json", "2024-03-07T12:00:00.000Z", "archived")
            self.assertTrue(out.endswith(".json"))
            self.assertIn("_archived_1709722800", out)
            self.assertIn("ETH", out)
        with unittest.mock.patch.object(time, "time", return_value=1709722800.0):
            out2 = dsl_v5._archived_state_filename("/a/b/xyz--SILVER.json", "now", "external")
            self.assertIn("external", out2)
            self.assertIn("1709722800", out2)


class TestDslV5CleanupAndSave(unittest.TestCase):
    def test_cleanup_strategy_state_dir(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            sd = os.path.join(d, strat)
            os.makedirs(sd, exist_ok=True)
            open(os.path.join(sd, "ETH.json"), "w").close()
            open(os.path.join(sd, "BTC_archived_123.json"), "w").close()
            n = dsl_v5.cleanup_strategy_state_dir(d, strat)
            self.assertEqual(n, 1)
            self.assertFalse(os.path.isfile(os.path.join(sd, "ETH.json")))
            self.assertTrue(os.path.isfile(os.path.join(sd, "BTC_archived_123.json")))
        self.assertEqual(dsl_v5.cleanup_strategy_state_dir("/nonexistent", "x"), 0)

    def test_save_or_rename_state_not_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ETH.json")
            state = {"asset": "ETH", "lastPrice": 100.0}
            out = dsl_v5.save_or_rename_state(state, path, closed=False, now="2024-03-07T12:00:00.000Z", close_result=None)
            self.assertIsNone(out)
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["lastCheck"], "2024-03-07T12:00:00.000Z")

    def test_save_or_rename_state_closed_renames(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ETH.json")
            with open(path, "w") as f:
                json.dump({"asset": "ETH"}, f)
            state = {"asset": "ETH", "lastPrice": 100.0, "active": False}
            with unittest.mock.patch.object(time, "time", return_value=1709722800.0):
                out = dsl_v5.save_or_rename_state(state, path, closed=True, now="2024-03-07T12:00:00.000Z", close_result="ok")
            self.assertEqual(out, "ok")
            self.assertFalse(os.path.isfile(path))
            archived = [f for f in os.listdir(d) if "archived" in f]
            self.assertEqual(len(archived), 1)


class TestDslV5PriceHelpers(unittest.TestCase):
    def test_parse_price_from_response(self):
        self.assertEqual(dsl_v5._parse_price_from_response({"prices": {"ETH": "2000.5"}}, "ETH"), "2000.5")
        self.assertEqual(dsl_v5._parse_price_from_response({"ETH": "2000.5"}, "ETH"), "2000.5")
        self.assertIsNone(dsl_v5._parse_price_from_response({"prices": {}}, "ETH"))

    def test_unwrap_mcp_response(self):
        self.assertEqual(dsl_v5._unwrap_mcp_response({"data": {"a": 1}}), {"a": 1})
        self.assertEqual(dsl_v5._unwrap_mcp_response({"a": 1}), {"a": 1})
        self.assertIsNone(dsl_v5._unwrap_mcp_response(None))
        self.assertIsNone(dsl_v5._unwrap_mcp_response([]))


class TestDslV5BuildOutput(unittest.TestCase):
    def test_build_output(self):
        state = {
            "asset": "ETH",
            "entryPrice": 100.0,
            "size": 1.0,
            "createdAt": "2024-01-01T00:00:00.000Z",
        }
        out = dsl_v5.build_output(
            state,
            price=102.0,
            direction="LONG",
            upnl=2.0,
            upnl_pct=2.0,
            phase=1,
            hw=105.0,
            effective_floor=101.0,
            trailing_floor=101.5,
            tier_floor=None,
            tier_idx=-1,
            tiers=[{"triggerPct": 10, "lockPct": 5}],
            tier_changed=False,
            previous_tier_idx=-1,
            breach_count=0,
            breaches_needed=3,
            breached=False,
            should_close=False,
            closed=False,
            close_result=None,
            now="2024-03-07T12:00:00.000Z",
            sl_synced=False,
            sl_initial_sync=False,
        )
        self.assertEqual(out["asset"], "ETH")
        self.assertEqual(out["status"], "active")
        self.assertEqual(out["price"], 102.0)
        self.assertFalse(out["closed"])
        self.assertIn("tier_name", out)


# ---------------------------------------------------------------------------
# dsl-cli tests
# ---------------------------------------------------------------------------

class TestDslCliPathHelpers(unittest.TestCase):
    def test_asset_to_filename(self):
        self.assertEqual(dsl_cli.asset_to_filename("xyz:SILVER"), "xyz--SILVER")
        self.assertEqual(dsl_cli.asset_to_filename("ETH"), "ETH")

    def test_filename_to_asset(self):
        self.assertEqual(dsl_cli.filename_to_asset("xyz--SILVER.json"), "xyz:SILVER")
        self.assertEqual(dsl_cli.filename_to_asset("ETH.json"), "ETH")
        self.assertIsNone(dsl_cli.filename_to_asset("strategy-uuid.json"))

    def test_safe_path_component(self):
        self.assertTrue(dsl_cli._safe_path_component("strat-1"))
        self.assertFalse(dsl_cli._safe_path_component(""))
        self.assertFalse(dsl_cli._safe_path_component(".."))
        self.assertFalse(dsl_cli._safe_path_component("a/b"))

    def test_strategy_dir(self):
        self.assertEqual(
            dsl_cli.strategy_dir("/data/dsl", "s1"),
            os.path.join("/data/dsl", "s1"),
        )

    def test_strategy_config_filename(self):
        self.assertEqual(dsl_cli.strategy_config_filename("abc"), "strategy-abc.json")

    def test_strategy_json_path(self):
        self.assertEqual(
            dsl_cli.strategy_json_path("/d", "s1"),
            os.path.join("/d", "s1", "strategy-s1.json"),
        )

    def test_position_state_path(self):
        self.assertEqual(
            dsl_cli.position_state_path("/d", "s1", "ETH"),
            os.path.join("/d", "s1", "ETH.json"),
        )
        self.assertEqual(
            dsl_cli.position_state_path("/d", "s1", "xyz:SILVER"),
            os.path.join("/d", "s1", "xyz--SILVER.json"),
        )

    def test_list_position_state_files(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            sd = os.path.join(d, strat)
            os.makedirs(sd, exist_ok=True)
            open(os.path.join(sd, "ETH.json"), "w").close()
            open(os.path.join(sd, "strategy-s1.json"), "w").close()
            open(os.path.join(sd, "BTC_archived_1.json"), "w").close()
            out = dsl_cli.list_position_state_files(d, strat)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0][1], "ETH")


class TestDslCliHelpers(unittest.TestCase):
    def test_now_iso(self):
        s = dsl_cli._now_iso()
        self.assertIn("T", s)
        self.assertTrue(s.endswith("Z") or "+" in s)

    def test_safe_float(self):
        self.assertEqual(dsl_cli._safe_float(1.5), 1.5)
        self.assertEqual(dsl_cli._safe_float("2.5"), 2.5)
        self.assertEqual(dsl_cli._safe_float(None, 3.0), 3.0)
        self.assertEqual(dsl_cli._safe_float("x", 4.0), 4.0)

    def test_safe_int(self):
        self.assertEqual(dsl_cli._safe_int(1), 1)
        self.assertEqual(dsl_cli._safe_int("2"), 2)
        self.assertEqual(dsl_cli._safe_int(None, 3), 3)
        self.assertEqual(dsl_cli._safe_int("x", 4), 4)

    def test_unwrap_mcporter_response(self):
        self.assertEqual(dsl_cli._unwrap_mcporter_response('{"x":1}'), {"x": 1})
        raw = json.dumps({"content": [{"text": '{"y":2}'}]})
        self.assertEqual(dsl_cli._unwrap_mcporter_response(raw), {"y": 2})


class TestDslCliValidate(unittest.TestCase):
    def test_validate_cli_args(self):
        self.assertEqual(dsl_cli.validate_cli_args(strategy_id=""), ["strategy_id is required"])
        self.assertEqual(dsl_cli.validate_cli_args(strategy_id="s1"), [])
        self.assertEqual(dsl_cli.validate_cli_args(strategy_id="s/1"), ["strategy_id must be path-safe (no path separators or . / ..)"])
        self.assertEqual(
            dsl_cli.validate_cli_args(asset="ETH", dex=None),
            ["asset and dex must both be set or both omitted"],
        )
        self.assertEqual(
            dsl_cli.validate_cli_args(asset="ETH", dex="xyz"),
            [],
        )
        self.assertEqual(
            dsl_cli.validate_cli_args(asset="ETH", dex="invalid"),
            ["dex must be 'main' or 'xyz'"],
        )

    def test_validate_dsl_config(self):
        # Empty dict / partial patch (no phase blocks) is valid for update-dsl
        self.assertEqual(dsl_cli.validate_dsl_config({}), [])
        self.assertEqual(dsl_cli.validate_dsl_config({"phase2TriggerTier": 1}), [])
        self.assertEqual(dsl_cli.validate_dsl_config({"tiers": [{"triggerPct": 10, "lockPct": 5}]}), [])
        self.assertEqual(dsl_cli.validate_dsl_config({"phase1": {"enabled": True}}), [])
        self.assertEqual(dsl_cli.validate_dsl_config("x"), ["configuration must be a JSON object"])
        self.assertEqual(
            dsl_cli.validate_dsl_config({"phase1": {"retraceThreshold": -0.1}}),
            ["phase1.retraceThreshold must be a number between 0 and 1 (ROE fraction)"],
        )
        self.assertEqual(
            dsl_cli.validate_dsl_config({"phase1": {"enabled": False}, "phase2": {"enabled": False}}),
            ["at least one of phase1.enabled or phase2.enabled must be true"],
        )
        errs = dsl_cli.validate_dsl_config({
            "phase2": {"enabled": True},
            "phase1": {"enabled": False},
            "tiers": [],
        })
        self.assertIn("phase2 only mode requires a non-empty tiers array", errs)


class TestDslCliConfig(unittest.TestCase):
    def test_load_config_source_inline(self):
        cfg, err = dsl_cli.load_config_source('{"phase1":{"enabled":true}}')
        self.assertIsNone(err)
        self.assertEqual(cfg["phase1"]["enabled"], True)
        cfg2, err2 = dsl_cli.load_config_source("not json")
        self.assertIsNotNone(err2)
        self.assertIsNone(cfg2)

    def test_load_config_source_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"phase1": {"enabled": True, "retraceThreshold": 0.02}}, f)
            path = f.name
        try:
            cfg, err = dsl_cli.load_config_source("@" + path)
            self.assertIsNone(err)
            self.assertEqual(cfg["phase1"]["retraceThreshold"], 0.02)
        finally:
            os.unlink(path)
        cfg_miss, err_miss = dsl_cli.load_config_source("@/nonexistent/path.json")
        self.assertIsNotNone(err_miss)
        self.assertIsNone(cfg_miss)

    def test_resolve_config(self):
        base = {"phase1": {"enabled": True}}
        out = dsl_cli.resolve_config(base, {"phase2TriggerTier": 1})
        self.assertEqual(out.get("phase2TriggerTier"), 1)
        self.assertIn("tiers", out)

    def test_calc_absolute_floor(self):
        # LONG: entry * (1 - retrace/lev)
        f = dsl_cli.calc_absolute_floor(100.0, 10.0, 0.03, "LONG")
        self.assertAlmostEqual(f, 100.0 * (1 - 0.03 / 10))
        f2 = dsl_cli.calc_absolute_floor(100.0, 10.0, 0.03, "SHORT")
        self.assertAlmostEqual(f2, 100.0 * (1 + 0.03 / 10))

    def test_config_to_phase1_phase2_tiers(self):
        config = {
            "phase1": {"enabled": True, "retraceThreshold": 0.03},
            "phase2": {"enabled": True},
            "tiers": [{"triggerPct": 10, "lockPct": 5}],
        }
        p1, trigger, p2, tiers = dsl_cli.config_to_phase1_phase2_tiers(
            config, 100.0, 10.0, "LONG"
        )
        self.assertTrue(p1["enabled"])
        self.assertIn("absoluteFloor", p1)
        self.assertEqual(trigger, 0)
        self.assertEqual(len(tiers), 1)
        self.assertEqual(tiers[0]["triggerPct"], 10)
        with self.assertRaises(ValueError):
            dsl_cli.config_to_phase1_phase2_tiers(
                {"phase1": {"enabled": False}, "phase2": {"enabled": False}},
                100.0, 10.0, "LONG",
            )


class TestDslCliPositionState(unittest.TestCase):
    def test_write_read_position_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "ETH.json")
            state = {"asset": "ETH", "entryPrice": 100.0}
            err = dsl_cli.write_position_state(path, state)
            self.assertIsNone(err)
            self.assertTrue(os.path.isfile(path))
            data, err2 = dsl_cli.read_position_state(path)
            self.assertIsNone(err2)
            self.assertEqual(data["asset"], "ETH")
            self.assertEqual(data["entryPrice"], 100.0)
        data_miss, err_miss = dsl_cli.read_position_state(os.path.join(d, "missing.json"))
        self.assertIsNotNone(err_miss)
        self.assertIsNone(data_miss)

    def test_patch_config_into_state(self):
        state = {
            "phase1": {"enabled": True},
            "phase2": {},
            "entryPrice": 100.0,
            "leverage": 10,
            "direction": "LONG",
        }
        updated = dsl_cli.patch_config_into_state(state, {
            "phase1": {"retraceThreshold": 0.02},
            "phase2TriggerTier": 1,
            "tiers": [{"triggerPct": 10, "lockPct": 5}],
        })
        self.assertIn("phase1", updated)
        self.assertIn("tiers", updated)
        self.assertEqual(state["phase1"]["retraceThreshold"], 0.02)
        self.assertEqual(state["phase2TriggerTier"], 1)
        self.assertEqual(len(state["tiers"]), 1)

    def test_build_position_state(self):
        config = {
            "phase1": {"enabled": True, "retraceThreshold": 0.03},
            "phase2": {"enabled": True},
            "tiers": [{"triggerPct": 10, "lockPct": 5}],
        }
        state = dsl_cli.build_position_state(
            "ETH", "main", "0xwallet", "strat-1",
            100.0, 1.0, 10.0, "LONG",
            config, "2024-03-07T12:00:00.000Z",
        )
        self.assertEqual(state["asset"], "ETH")
        self.assertEqual(state["entryPrice"], 100.0)
        self.assertEqual(state["highWaterPrice"], 100.0)
        self.assertEqual(state["currentTierIndex"], -1)
        self.assertTrue(state["active"])
        self.assertIn("phase1", state)
        self.assertIn("tiers", state)


class TestDslCliStrategyJson(unittest.TestCase):
    def test_default_strategy_config(self):
        cfg = dsl_cli._default_strategy_config()
        self.assertIn("phase1", cfg)
        self.assertIn("phase2", cfg)
        self.assertIn("tiers", cfg)
        self.assertTrue(cfg["phase1"]["enabled"])

    def test_new_strategy_data(self):
        data = dsl_cli._new_strategy_data("strat-1", "0xwallet", "2024-03-07T12:00:00.000Z")
        self.assertEqual(data["strategyId"], "strat-1")
        self.assertEqual(data["wallet"], "0xwallet")
        self.assertEqual(data["status"], "active")
        self.assertIn("defaultConfig", data)

    def test_load_strategy_json(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            path = dsl_cli.strategy_json_path(d, strat)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump({"strategyId": strat, "wallet": "0x"}, f)
            data, err = dsl_cli.load_strategy_json(d, strat)
            self.assertIsNone(err)
            self.assertEqual(data["strategyId"], strat)
            data2, err2 = dsl_cli.load_strategy_json(d, "nonexistent")
            self.assertIsNone(data2)
            self.assertIsNone(err2)

    def test_save_strategy_json(self):
        with tempfile.TemporaryDirectory() as d:
            err = dsl_cli.save_strategy_json(d, "s1", {"strategyId": "s1"})
            self.assertIsNone(err)
            path = dsl_cli.strategy_json_path(d, "s1")
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertEqual(json.load(f)["strategyId"], "s1")

    def test_reconcile_strategy_positions_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            sd = os.path.join(d, strat)
            os.makedirs(sd, exist_ok=True)
            open(os.path.join(sd, "ETH.json"), "w").write(json.dumps({"asset": "ETH"}))
            data = {"positions": {"OLD": {"dex": "main"}}}
            dsl_cli.reconcile_strategy_positions_from_disk(d, strat, data)
            self.assertIn("ETH", data["positions"])
            self.assertNotIn("OLD", data["positions"])
            self.assertIn("dex", data["positions"]["ETH"])


class TestDslCliStatusAndCount(unittest.TestCase):
    def test_position_status_summary(self):
        state = {"active": True, "phase": 2, "currentTierIndex": 1, "highWaterPrice": 105.0, "floorPrice": 100.0, "lastCheck": "2024-01-01T00:00:00Z"}
        out = dsl_cli._position_status_summary(state, "ETH")
        self.assertEqual(out["status"], "active")
        self.assertEqual(out["phase"], 2)
        self.assertEqual(out["high_water_price"], 105.0)
        out2 = dsl_cli._position_status_summary({"active": False}, "xyz:SILVER")
        self.assertEqual(out2["dex"], "xyz")
        self.assertEqual(out2["status"], "paused")

    def test_count_positions_by_state(self):
        with tempfile.TemporaryDirectory() as d:
            strat = "s1"
            sd = os.path.join(d, strat)
            os.makedirs(sd, exist_ok=True)
            with open(os.path.join(sd, "ETH.json"), "w") as f:
                json.dump({"asset": "ETH", "active": True}, f)
            with open(os.path.join(sd, "BTC.json"), "w") as f:
                json.dump({"asset": "BTC", "active": False}, f)
            with open(os.path.join(sd, "SOL_archived_123.json"), "w") as f:
                f.write("{}")
            active, paused, completed = dsl_cli._count_positions_by_state(d, strat)
            self.assertIn("ETH", active)
            self.assertIn("BTC", paused)
            self.assertIn("SOL", completed)
            self.assertEqual(len(active), 1)
            self.assertEqual(len(paused), 1)
            self.assertEqual(len(completed), 1)

    def test_exit_error(self):
        with open(os.devnull, "w") as devnull:
            with unittest.mock.patch("sys.stdout", devnull), unittest.mock.patch("sys.stderr", devnull):
                with self.assertRaises(SystemExit):
                    dsl_cli._exit_error("test error")

    def test_set_position_active(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ETH.json")
            with open(path, "w") as f:
                json.dump({"asset": "ETH", "active": True}, f)
            err = dsl_cli._set_position_active(path, False, "2024-03-07T12:00:00.000Z")
            self.assertIsNone(err)
            with open(path) as f:
                data = json.load(f)
            self.assertFalse(data["active"])
            self.assertEqual(data.get("pausedAt"), "2024-03-07T12:00:00.000Z")

    def test_archive_position_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "ETH.json")
            dest = os.path.join(d, "ETH_archived_123.json")
            with open(src, "w") as f:
                json.dump({"asset": "ETH"}, f)
            renamed, deleted = dsl_cli._archive_position_file(src, dest)
            self.assertTrue(renamed)
            self.assertFalse(os.path.isfile(src))
            self.assertTrue(os.path.isfile(dest))


def _all_test_classes():
    return [
        TestDslV5PathHelpers,
        TestDslV5UnwrapMcporter,
        TestDslV5NormalizeState,
        TestDslV5TradingLogic,
        TestDslV5ArchivedFilename,
        TestDslV5BuildOutput,
        TestDslV5CleanupAndSave,
        TestDslV5PriceHelpers,
        TestDslCliPathHelpers,
        TestDslCliHelpers,
        TestDslCliValidate,
        TestDslCliConfig,
        TestDslCliPositionState,
        TestDslCliStrategyJson,
        TestDslCliStatusAndCount,
    ]


def run_tests(verbosity=2):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in _all_test_classes():
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    return runner.run(suite)


def list_tests():
    """Print all test names (class.method) so you can see full coverage."""
    loader = unittest.TestLoader()
    names = []
    for cls in _all_test_classes():
        for method in loader.getTestCaseNames(cls):
            names.append(f"{cls.__name__}.{method}")
    for n in sorted(names):
        print(n)
    print(f"\nTotal: {len(names)} tests")
    return len(names)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-l", "--list"):
        list_tests()
        sys.exit(0)
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
