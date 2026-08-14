#!/usr/bin/env python3
"""Fleet-wide dual-DEX (main / xyz) correctness tests.

Three bug classes found live on bobcat + gibbon (2026-07-23). All three are
silent — no exception, no error log, just a strategy that never trades or that
re-opens what it already holds. Each test below fails against the pre-fix code.

Run:
    python3 senpi-strategy-ops/tests/test_dex_awareness.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = ROOT / "strategies"


def _load(path, name):
    """Import a scanner module the way the runtime does — with its own directory
    on sys.path, so the sibling `import scoring` resolves."""
    path = Path(path)
    sys.path.insert(0, str(path.parent))
    try:
        for stale in ("scoring",):           # sibling modules differ per strategy
            sys.modules.pop(stale, None)
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


# ── the REAL upstream shapes (verified against prod MCP 2026-07-23) ───────────
# leaderboard_get_markets: BARE ticker + a separate `dex` field.
LEADERBOARD_ROWS = [
    {"token": "NVDA", "dex": "xyz", "direction": "SHORT", "pct_of_top_traders_gain": 71.0},
    {"token": "NVDA", "dex": "xyz", "direction": "LONG", "pct_of_top_traders_gain": 29.0},
    {"token": "BTC", "dex": "", "direction": "LONG", "pct_of_top_traders_gain": 63.0},
    {"token": "BTC", "dex": "", "direction": "SHORT", "pct_of_top_traders_gain": 37.0},
]
# strategy_get_clearinghouse_state: top level is ALWAYS {"main": ..., "xyz": ...}
CLEARINGHOUSE = {
    "main": {"marginSummary": {"accountValue": "1000.0"},
             "assetPositions": [{"position": {"coin": "BTC", "szi": "0.5", "marginUsed": "300"}}]},
    "xyz": {"marginSummary": {"accountValue": "1000.0"},
            "assetPositions": [{"position": {"coin": "xyz:GOLD", "szi": "-2.0", "marginUsed": "200"}}]},
}


class SmRowMatching(unittest.TestCase):
    """bobcat: `token != coin` never matches an xyz name -> sm_no_data on every
    xyz equity -> a hard smart-money gate blocks the whole universe forever."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load(STRATEGIES / "bobcat/main/scanners/scan.py", "bobcat_scan")

    def test_xyz_coin_matches_bare_leaderboard_token(self):
        row = LEADERBOARD_ROWS[0]
        self.assertTrue(self.mod._sm_row_matches(row, "NVDA", "xyz:NVDA"),
                        "xyz:NVDA must match the bare NVDA row on dex=xyz")

    def test_main_coin_matches_main_row(self):
        self.assertTrue(self.mod._sm_row_matches(LEADERBOARD_ROWS[2], "BTC", "BTC"))

    def test_main_coin_does_not_cross_match_xyz_twin(self):
        # a main-DEX GOLD must never read the xyz:GOLD row's positioning
        self.assertFalse(self.mod._sm_row_matches({"token": "GOLD", "dex": "xyz"}, "GOLD", "GOLD"))

    def test_xyz_coin_does_not_cross_match_main_twin(self):
        self.assertFalse(self.mod._sm_row_matches({"token": "GOLD", "dex": ""}, "GOLD", "xyz:GOLD"))

    def test_different_ticker_never_matches(self):
        self.assertFalse(self.mod._sm_row_matches(LEADERBOARD_ROWS[0], "NVDA", "xyz:TSLA"))

    # The upstream row shape could not be re-confirmed live (leaderboard 401s from
    # this session), so pin BOTH plausible shapes: the helper must be a strict
    # superset of the old `token != coin` compare either way.
    def test_prefixed_token_shape_also_matches(self):
        """If leaderboard ever returns `xyz:NVDA` in `token` with no dex field."""
        self.assertTrue(self.mod._sm_row_matches({"token": "xyz:NVDA"}, "XYZ:NVDA", "xyz:NVDA"))

    def test_prefixed_token_shape_still_blocks_cross_dex(self):
        self.assertFalse(self.mod._sm_row_matches({"token": "xyz:GOLD"}, "XYZ:GOLD", "GOLD"))

    def test_never_regresses_a_plain_main_match(self):
        """Everything the pre-fix compare matched must still match (superset property)."""
        for row, tok, want in (({"token": "BTC"}, "BTC", "BTC"),
                               ({"token": "BTC", "dex": ""}, "BTC", "BTC"),
                               ({"token": "ETH", "dex": "main"}, "ETH", "ETH")):
            with self.subTest(row=row):
                self.assertTrue(self.mod._sm_row_matches(row, tok, want))

    def test_case_insensitive(self):
        self.assertTrue(self.mod._sm_row_matches({"token": "nvda", "dex": "xyz"}, "nvda", "XYZ:NVDA"))

    def test_end_to_end_sm_direction_reads_xyz(self):
        """The whole point: an xyz coin must return a real SM lean, not (None, 0)."""
        class _MCP:
            def call_tool(self, *_a, **_k):
                return {"data": LEADERBOARD_ROWS}

        class _Ctx:
            senpi_mcp = _MCP()

        direction, tilt = self.mod._get_sm_direction(_Ctx(), "xyz:NVDA")
        self.assertEqual(direction, "SHORT")
        self.assertAlmostEqual(tilt, 71.0, places=1)


class DualDexPositions(unittest.TestCase):
    """gibbon: reading assetPositions off the TOP level of the clearinghouse
    returns nothing, so the scanner believes it holds nothing and re-opens
    names it already has (duplicate-open failures / pyramiding)."""

    CASES = ["ant", "chimp", "crane", "gecko", "gibbon", "gorilla",
             "orangutan", "raven", "salmon", "starling", "viper"]

    def test_every_patched_scanner_enumerates_both_sections(self):
        for sid in self.CASES:
            src = (STRATEGIES / sid / "main/scanners/scan.py").read_text()
            with self.subTest(strategy=sid):
                self.assertRegex(src, r'for _sec in \("main", "xyz"\)',
                                 f"{sid} does not enumerate both sub-DEX sections")
                self.assertNotRegex(
                    src, r'for \w+ in d\.get\("assetPositions"',
                    f"{sid} still reads assetPositions off the top level")

    def test_held_returns_both_dex_coins(self):
        mod = _load(STRATEGIES / "gibbon/main/scanners/scan.py", "gibbon_scan")

        class _MCP:
            def call_tool(self, *_a, **_k):
                return CLEARINGHOUSE

        class _Ctx:
            senpi_mcp = _MCP()
            wallet = "0x" + "0" * 40

        self.assertEqual(mod._held(_Ctx()), {"BTC", "GOLD"})


class UniverseDerivation(unittest.TestCase):
    """gibbon: market_list_instruments(dex="") returns BOTH sub-DEXes, so the
    xyz names entered the main pool (misclassified crypto) and again via the
    xyz pool -> a universe of duplicates."""

    FAMILY = ["chimp", "gorilla", "gibbon", "orangutan"]

    # what dex="" actually returns: main rows bare, xyz rows prefixed
    MIXED_ROWS = [
        {"name": "BTC", "vol": 900_000_000, "change_pct": 1.0},
        {"name": "ETH", "vol": 500_000_000, "change_pct": 0.5},
        {"name": "xyz:NVDA", "vol": 40_000_000, "change_pct": 2.0},
        {"name": "xyz:GOLD", "vol": 30_000_000, "change_pct": -1.0},
    ]
    XYZ_ROWS = [
        {"name": "xyz:NVDA", "vol": 40_000_000, "change_pct": 2.0},
        {"name": "xyz:GOLD", "vol": 30_000_000, "change_pct": -1.0},
    ]

    def test_no_duplicates_and_correct_dex_routing(self):
        for sid in self.FAMILY:
            mod = _load(STRATEGIES / sid / "main/scanners/scoring.py", f"{sid}_scoring")
            with self.subTest(strategy=sid):
                pool = mod.derive_universe(self.MIXED_ROWS, self.XYZ_ROWS, {
                    "universeVolFloorUsd": 1_000_000, "xyzVolFloorUsd": 1_000_000,
                    "maxMainNames": 14, "maxXyzNames": 16})
                names = [p["name"] for p in pool]
                self.assertEqual(len(names), len(set(names)),
                                 f"{sid} duplicate names in universe: {names}")
                for p in pool:
                    want = "xyz" if p["name"].lower().startswith("xyz:") else ""
                    self.assertEqual(p["dex"], want,
                                     f"{sid}: {p['name']} tagged dex={p['dex']!r}")


class NoRegression(unittest.TestCase):
    """Lint: no scanner may reintroduce either raw idiom."""

    def test_no_raw_token_compare_fleet_wide(self):
        bad = []
        for p in STRATEGIES.rglob("*/scanners/*.py"):
            if re.search(r'^\s*if token\s*!=\s*\w+(\.upper\(\))?:\s*$',
                         p.read_text(), re.M):
                bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [], f"raw bare-token compare (xyz-blind): {bad}")

    def test_no_top_level_asset_positions_fleet_wide(self):
        bad = []
        for p in STRATEGIES.rglob("*/scanners/*.py"):
            if re.search(r'for \w+ in d\.get\("assetPositions"', p.read_text()):
                bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [], f"top-level assetPositions read (dual-DEX blind): {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
