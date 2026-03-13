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

    def test_normalize_state_backfills_high_water_roe(self):
        """State with highWaterPrice but no highWaterRoe gets highWaterRoe backfilled."""
        state = {
            "entryPrice": 100.0,
            "leverage": 10,
            "direction": "LONG",
            "highWaterPrice": 110.0,
        }
        dsl_v5.normalize_state_phase_config(state)
        self.assertIn("highWaterRoe", state)
        self.assertEqual(state["highWaterRoe"], 100.0)  # (110-100)/100 * 10 * 100


class TestDslV5TradingLogic(unittest.TestCase):
    def test_update_high_water(self):
        state = {"highWaterPrice": 100.0}
        hw = dsl_v5.update_high_water(state, 105.0, True)
        self.assertEqual(hw, 105.0)
        self.assertEqual(state["highWaterPrice"], 105.0)
        self.assertIn("highWaterRoe", state)
        hw2 = dsl_v5.update_high_water(state, 103.0, True)
        self.assertEqual(hw2, 105.0)
        state_short = {"highWaterPrice": 100.0}
        hw3 = dsl_v5.update_high_water(state_short, 95.0, False)
        self.assertEqual(hw3, 95.0)

    def test_update_high_water_sets_high_water_roe(self):
        state = {
            "highWaterPrice": 100.0,
            "entryPrice": 100.0,
            "leverage": 10,
            "direction": "LONG",
        }
        dsl_v5.update_high_water(state, 110.0, True)
        self.assertEqual(state["highWaterRoe"], 100.0)  # (110-100)/100 * 10 * 100 = 100% ROE

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

    def test_apply_tier_upgrades_no_lock_mode_fixed_roe(self):
        """State with no lockMode behaves as fixed_roe (lockPct = fraction of range)."""
        state = {
            "tiers": [{"triggerPct": 10, "lockPct": 20}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 110.0)
        self.assertEqual(tier_idx, 0)
        # LONG: entry + (hw - entry) * lockPct/100 = 100 + 10*0.2 = 102
        self.assertAlmostEqual(tier_floor, 102.0, places=2)

    def test_apply_tier_upgrades_pct_of_high_water(self):
        """lockMode pct_of_high_water: floor = entry * (1 + (highWaterRoe * lockHwPct/100)/100/leverage)."""
        state = {
            "tiers": [
                {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
                {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
            ],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 25.0, True, 110.0)
        self.assertEqual(tier_idx, 1)
        # Tier 1: lockHwPct=85, highWaterRoe=100 -> tier_floor_roe = 85; price = 100*(1+85/100/10) = 108.5
        self.assertAlmostEqual(tier_floor, 108.5, places=2)
        self.assertAlmostEqual(state["tierFloorPrice"], 108.5, places=2)

    def test_apply_tier_upgrades_pct_of_high_water_recalc_every_tick(self):
        """In pct_of_high_water, floor is recalculated every tick from current highWaterRoe."""
        state = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 85}],
            "currentTierIndex": 0,
            "tierFloorPrice": 105.0,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 50.0,
            "phase2": {"enabled": True},
            "lockMode": "pct_of_high_water",
        }
        # highWaterRoe advances to 100 -> floor should be 100*(1+85/100/10)=108.5 (ratchet keeps it)
        state["highWaterRoe"] = 100.0
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 110.0)
        self.assertEqual(tier_idx, 0)
        self.assertAlmostEqual(tier_floor, 108.5, places=2)

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

    def test_compute_effective_floor_per_tier_breaches(self):
        """Per-tier consecutiveBreachesRequired (and breachesRequired fallback) override phase2 default."""
        state = {
            "phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 1},
            "tiers": [
                {"triggerPct": 10, "lockPct": 5, "consecutiveBreachesRequired": 3},
                {"triggerPct": 20, "lockPct": 14, "breachesRequired": 2},
            ],
            "leverage": 10,
        }
        _, _, needed_t0, _ = dsl_v5.compute_effective_floor(state, 2, 0, 102.0, 105.0, True)
        self.assertEqual(needed_t0, 3)
        _, _, needed_t1, _ = dsl_v5.compute_effective_floor(state, 2, 1, 103.0, 105.0, True)
        self.assertEqual(needed_t1, 2)

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

    def test_validate_dsl_config_high_water_mode(self):
        """lockMode pct_of_high_water: lockHwPct required, 0-100, increasing; consecutiveBreachesRequired 1-5."""
        self.assertEqual(
            dsl_cli.validate_dsl_config({
                "lockMode": "pct_of_high_water",
                "tiers": [
                    {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
                    {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
                ],
            }),
            [],
        )
        errs = dsl_cli.validate_dsl_config({
            "lockMode": "pct_of_high_water",
            "tiers": [{"triggerPct": 10}]  # missing lockHwPct
        })
        self.assertTrue(any("lockHwPct" in e for e in errs))
        errs2 = dsl_cli.validate_dsl_config({
            "lockMode": "pct_of_high_water",
            "tiers": [
                {"triggerPct": 10, "lockHwPct": 50},
                {"triggerPct": 20, "lockHwPct": 40},  # not increasing
            ],
        })
        self.assertTrue(any("strictly greater" in e for e in errs2))
        errs3 = dsl_cli.validate_dsl_config({
            "lockMode": "pct_of_high_water",
            "tiers": [{"triggerPct": 10, "lockHwPct": 50, "consecutiveBreachesRequired": 10}],
        })
        self.assertTrue(any("1–5" in e for e in errs3))

    def test_validate_dsl_config_fixed_roe_requires_lock_pct(self):
        """Without lockMode or with fixed_roe, tiers require lockPct."""
        self.assertEqual(dsl_cli.validate_dsl_config({"tiers": [{"triggerPct": 10, "lockPct": 5}]}), [])
        errs = dsl_cli.validate_dsl_config({"tiers": [{"triggerPct": 10}]})
        self.assertTrue(any("lockPct" in e for e in errs))


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

    def test_resolve_config_preserves_legacy_tiers_when_lock_mode_absent(self):
        """When lockMode is absent, existing tiers are preserved and lockMode is inferred (no overwrite with DEFAULT_TIERS_HIGH_WATER)."""
        legacy_tiers = [
            {"triggerPct": 10, "lockPct": 5},
            {"triggerPct": 20, "lockPct": 14},
        ]
        base = {"phase1": {"enabled": True}, "tiers": legacy_tiers}
        out = dsl_cli.resolve_config(base, None)
        self.assertEqual(out.get("lockMode"), "fixed_roe", "lockMode should be inferred from lockPct tiers")
        self.assertEqual(len(out["tiers"]), 2)
        self.assertEqual(out["tiers"][0]["lockPct"], 5)
        self.assertEqual(out["tiers"][1]["triggerPct"], 20)
        # High-water tiers (lockHwPct) infer pct_of_high_water
        hw_tiers = [{"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3}]
        base_hw = {"phase1": {"enabled": True}, "tiers": hw_tiers}
        out_hw = dsl_cli.resolve_config(base_hw, None)
        self.assertEqual(out_hw.get("lockMode"), "pct_of_high_water")
        self.assertEqual(len(out_hw["tiers"]), 1)
        self.assertEqual(out_hw["tiers"][0]["lockHwPct"], 40)

    def test_resolve_config_fixed_roe_gets_lock_pct_tiers(self):
        """When lockMode is explicitly fixed_roe and no tiers are provided, default tiers use lockPct (not lockHwPct)."""
        base = {"phase1": {"enabled": True}, "lockMode": "fixed_roe"}
        out = dsl_cli.resolve_config(base, None)
        self.assertEqual(out.get("lockMode"), "fixed_roe")
        self.assertGreater(len(out["tiers"]), 0)
        self.assertIn("lockPct", out["tiers"][0], "fixed_roe default tiers must have lockPct for engine")
        self.assertNotIn("lockHwPct", out["tiers"][0])

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
        # fixed_roe with no tiers must get lockPct default tiers (not lockHwPct)
        config_fixed = {"phase1": {"enabled": True}, "phase2": {"enabled": True}, "lockMode": "fixed_roe"}
        _, _, _, tiers_fixed = dsl_cli.config_to_phase1_phase2_tiers(config_fixed, 100.0, 10.0, "LONG")
        self.assertGreater(len(tiers_fixed), 0)
        self.assertIn("lockPct", tiers_fixed[0])
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
        updated2 = dsl_cli.patch_config_into_state(state, {"lockMode": "pct_of_high_water", "phase2TriggerRoe": 7})
        self.assertIn("lockMode", updated2)
        self.assertIn("phase2TriggerRoe", updated2)
        self.assertEqual(state["lockMode"], "pct_of_high_water")
        self.assertEqual(state["phase2TriggerRoe"], 7)

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
        self.assertEqual(state["highWaterRoe"], 0)
        self.assertEqual(state["lockMode"], "fixed_roe")
        self.assertEqual(state["currentTierIndex"], -1)
        self.assertTrue(state["active"])
        self.assertIn("phase1", state)
        self.assertIn("tiers", state)
        state_hw = dsl_cli.build_position_state(
            "ETH", "main", "0xwallet", "strat-1",
            100.0, 1.0, 10.0, "LONG",
            {**config, "lockMode": "pct_of_high_water", "phase2TriggerRoe": 7}, "2024-03-07T12:00:00.000Z",
        )
        self.assertEqual(state_hw["lockMode"], "pct_of_high_water")
        self.assertEqual(state_hw["phase2TriggerRoe"], 7)


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


# ---------------------------------------------------------------------------
# DSL High Water Implementation Spec — Testing Checklist (dsl-high-water-implementation-spec.md)
# ---------------------------------------------------------------------------

class TestDslHighWaterChecklist(unittest.TestCase):
    """Tests matching the spec testing checklist. Run to verify High Water implementation."""

    def test_checklist_1_state_no_lock_mode_behaves_fixed_roe(self):
        """State file with no lockMode → behaves exactly as current (fixed_roe)."""
        state = {
            "tiers": [{"triggerPct": 10, "lockPct": 20}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            # no lockMode key
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 110.0)
        self.assertEqual(tier_idx, 0)
        # fixed_roe: LONG floor = entry + (hw - entry) * lockPct/100 = 100 + 10*0.2 = 102
        self.assertAlmostEqual(tier_floor, 102.0, places=2)

    def test_checklist_2_state_lock_mode_fixed_roe_same_as_current(self):
        """State file with lockMode: 'fixed_roe' → same as current."""
        state = {
            "tiers": [{"triggerPct": 10, "lockPct": 20}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "fixed_roe",
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 110.0)
        self.assertEqual(tier_idx, 0)
        self.assertAlmostEqual(tier_floor, 102.0, places=2)

    def test_checklist_3_pct_of_high_water_floor_is_percentage_of_hw_roe(self):
        """State file with lockMode: pct_of_high_water + lockHwPct tiers → floor is percentage of hwROE."""
        state = {
            "tiers": [
                {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
                {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
            ],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 25.0, True, 110.0)
        self.assertEqual(tier_idx, 1)
        # Tier 1: lockHwPct=85, highWaterRoe=100 → tier_floor_roe = 85; price = 100*(1+85/100/10) = 108.5
        self.assertAlmostEqual(tier_floor, 108.5, places=2)

    def test_checklist_4_high_water_20_to_50_roe_tier5_85_floor_17_to_42_5(self):
        """High water advances from 20% to 50% ROE at Tier 5 (85%) → floor moves from 17% to 42.5% ROE."""
        # 5 tiers: use index 4 as "Tier 5" with lockHwPct=85
        state = {
            "tiers": [
                {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
                {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
                {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
                {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
            ],
            "currentTierIndex": 3,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "phase2": {"enabled": True, "retraceThreshold": 0.015},
            "lockMode": "pct_of_high_water",
        }
        # At hwROE=20%: floor_roe = 20 * 85/100 = 17%
        state["highWaterRoe"] = 20.0
        _, tier_floor_20, _, _ = dsl_v5.apply_tier_upgrades(state, 25.0, True, 102.0)
        eff_20, _, _, _ = dsl_v5.compute_effective_floor(state, 2, 3, tier_floor_20, 102.0, True)
        # Floor price at 17% ROE: 100 * (1 + 17/100/10) = 101.7
        expected_floor_20 = 100.0 * (1 + 17.0 / 100 / 10)
        self.assertAlmostEqual(tier_floor_20, round(expected_floor_20, 4), places=2)
        # At hwROE=50%: floor_roe = 50 * 85/100 = 42.5%
        state["highWaterRoe"] = 50.0
        _, tier_floor_50, _, _ = dsl_v5.apply_tier_upgrades(state, 55.0, True, 105.0)
        expected_floor_50 = 100.0 * (1 + 42.5 / 100 / 10)
        self.assertAlmostEqual(tier_floor_50, round(expected_floor_50, 4), places=2)
        self.assertGreater(tier_floor_50, tier_floor_20)

    def test_checklist_5_high_water_advances_sl_would_sync(self):
        """High water advances → SL would be synced at new floor (effective_floor changes)."""
        state = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 85}],
            "currentTierIndex": 0,
            "tierFloorPrice": 105.0,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 20.0,
            "phase2": {"enabled": True, "retraceThreshold": 0.015},
            "lockMode": "pct_of_high_water",
        }
        _, tier_floor_old, _, _ = dsl_v5.apply_tier_upgrades(state, 25.0, True, 110.0)
        eff_old, _, _, _ = dsl_v5.compute_effective_floor(state, 2, 0, tier_floor_old, 110.0, True)
        last_synced = round(eff_old, 4)
        # Advance high water: 20% -> 50% ROE → floor moves
        state["highWaterRoe"] = 50.0
        state["highWaterPrice"] = 115.0
        _, tier_floor_new, _, _ = dsl_v5.apply_tier_upgrades(state, 55.0, True, 115.0)
        eff_new, _, _, _ = dsl_v5.compute_effective_floor(state, 2, 0, tier_floor_new, 115.0, True)
        eff_new_rounded = round(eff_new, 4)
        need_sync = last_synced is None or abs(last_synced - eff_new_rounded) > 1e-9
        self.assertTrue(need_sync, "SL should sync when floor changes after high water advance")

    def test_checklist_6_high_water_flat_sl_not_resynced(self):
        """High water flat (no new peak) → SL not re-synced (saves API calls)."""
        state = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 85}],
            "currentTierIndex": 0,
            "tierFloorPrice": 108.5,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True, "retraceThreshold": 0.015},
            "lockMode": "pct_of_high_water",
        }
        _, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 110.0)
        eff, _, _, _ = dsl_v5.compute_effective_floor(state, 2, 0, tier_floor, 110.0, True)
        last_synced = round(eff, 4)
        # Same high water (no advance) — run again, floor should be unchanged
        _, tier_floor2, _, _ = dsl_v5.apply_tier_upgrades(state, 12.0, True, 110.0)
        eff2, _, _, _ = dsl_v5.compute_effective_floor(state, 2, 0, tier_floor2, 110.0, True)
        eff2_rounded = round(eff2, 4)
        need_sync = last_synced is None or abs(last_synced - eff2_rounded) > 1e-9
        self.assertFalse(need_sync, "SL should not re-sync when floor unchanged (high water flat)")

    def test_checklist_7_per_tier_breach_count_tier1_3_tier5_1(self):
        """Per-tier breach count: Tier 1 at 3 breaches, Tier 5 at 1 breach."""
        state = {
            "phase2": {"retraceThreshold": 0.015, "consecutiveBreachesRequired": 1},
            "tiers": [
                {"triggerPct": 7, "lockHwPct": 40, "consecutiveBreachesRequired": 3},
                {"triggerPct": 12, "lockHwPct": 55, "consecutiveBreachesRequired": 2},
                {"triggerPct": 15, "lockHwPct": 75, "consecutiveBreachesRequired": 2},
                {"triggerPct": 20, "lockHwPct": 85, "consecutiveBreachesRequired": 1},
            ],
            "leverage": 10,
        }
        _, _, breaches_tier1, _ = dsl_v5.compute_effective_floor(state, 2, 0, 102.0, 105.0, True)
        _, _, breaches_tier5, _ = dsl_v5.compute_effective_floor(state, 2, 3, 108.0, 110.0, True)
        self.assertEqual(breaches_tier1, 3)
        self.assertEqual(breaches_tier5, 1)

    def test_checklist_8_tier_only_lock_pct_uses_lock_pct_regardless_of_lock_mode(self):
        """Tier with only lockPct (no lockHwPct) → uses lockPct regardless of lockMode."""
        state = {
            "tiers": [{"triggerPct": 10, "lockPct": 25}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 112.0,
            "highWaterRoe": 120.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        tier_idx, tier_floor, _, _ = dsl_v5.apply_tier_upgrades(state, 15.0, True, 112.0)
        self.assertEqual(tier_idx, 0)
        # Should use lockPct (fraction of range): 100 + (112-100)*0.25 = 103
        self.assertAlmostEqual(tier_floor, 103.0, places=2)
        # If it used lockHwPct it would be 120*25/100=30% ROE → different price

    def test_checklist_9_long_floor_calculation_correct(self):
        """LONG direction: floor calculation correct (fixed_roe and pct_of_high_water)."""
        # fixed_roe LONG: entry + (hw - entry) * lockPct/100
        state_fixed = {
            "tiers": [{"triggerPct": 10, "lockPct": 30}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 110.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
        }
        _, floor_fixed, _, _ = dsl_v5.apply_tier_upgrades(state_fixed, 15.0, True, 110.0)
        self.assertAlmostEqual(floor_fixed, 100.0 + 10.0 * 0.30, places=2)
        # pct_of_high_water LONG: entry * (1 + (hwRoe * lockHwPct/100)/100/leverage)
        state_hw = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 50}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        _, floor_hw, _, _ = dsl_v5.apply_tier_upgrades(state_hw, 15.0, True, 110.0)
        expected_hw = 100.0 * (1 + 50.0 / 100 / 10)
        self.assertAlmostEqual(floor_hw, expected_hw, places=2)

    def test_checklist_10_short_floor_calculation_correct(self):
        """SHORT direction: floor calculation correct (fixed_roe and pct_of_high_water)."""
        # fixed_roe SHORT: entry - (entry - hw) * lockPct/100
        state_fixed = {
            "tiers": [{"triggerPct": 10, "lockPct": 30}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 90.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
        }
        _, floor_fixed, _, _ = dsl_v5.apply_tier_upgrades(state_fixed, 15.0, False, 90.0)
        self.assertAlmostEqual(floor_fixed, 100.0 - 10.0 * 0.30, places=2)
        # pct_of_high_water SHORT: entry * (1 - (hwRoe * lockHwPct/100)/100/leverage)
        state_hw = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 50}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 90.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        _, floor_hw, _, _ = dsl_v5.apply_tier_upgrades(state_hw, 15.0, False, 90.0)
        expected_hw = 100.0 * (1 - 50.0 / 100 / 10)
        self.assertAlmostEqual(floor_hw, expected_hw, places=2)

    def test_checklist_11_mixed_state_files_each_uses_own_lock_mode(self):
        """Mixed state files in same strategy dir (some fixed, some HW) → each uses its own lockMode."""
        state_fixed = {
            "tiers": [{"triggerPct": 10, "lockPct": 20}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "fixed_roe",
        }
        state_hw = {
            "tiers": [{"triggerPct": 10, "lockHwPct": 85}],
            "currentTierIndex": -1,
            "tierFloorPrice": None,
            "phase": 2,
            "currentBreachCount": 0,
            "entryPrice": 100.0,
            "leverage": 10,
            "highWaterPrice": 110.0,
            "highWaterRoe": 100.0,
            "phase2": {"enabled": True},
            "phase2TriggerTier": 0,
            "lockMode": "pct_of_high_water",
        }
        _, floor_fixed, _, _ = dsl_v5.apply_tier_upgrades(state_fixed, 15.0, True, 110.0)
        _, floor_hw, _, _ = dsl_v5.apply_tier_upgrades(state_hw, 15.0, True, 110.0)
        # fixed_roe: 100 + 10*0.2 = 102
        self.assertAlmostEqual(floor_fixed, 102.0, places=2)
        # pct_of_high_water: 100*(1+85/100/10) = 108.5
        self.assertAlmostEqual(floor_hw, 108.5, places=2)
        self.assertNotEqual(floor_fixed, floor_hw)


def _all_test_classes():
    return [
        TestDslV5PathHelpers,
        TestDslV5UnwrapMcporter,
        TestDslV5NormalizeState,
        TestDslV5TradingLogic,
        TestDslHighWaterChecklist,
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
