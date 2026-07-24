#!/usr/bin/env python3
"""Dual-DEX guard — positive-evidence checks, shared by senpi-strategy-author and senpi-strategy-ops.

Hyperliquid is two sub-DEXes behind one cross-margined wallet, and the two APIs disagree about how a
name is spelled. Both mistakes are silent, so the guard exists — but a guard that enumerates WRONG
spellings is defeated by any rewording of the same bug. These checks ask the opposite question: did the
file do the thing that makes it correct?

Run: python3 senpi-strategy-ops/tests/test_dex_guard.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "senpi-strategy-ops" / "scripts"))

import _pkg  # noqa: E402

G = _pkg.dex_blind_offenders
XYZ = 'assets: ["xyz:NVDA"]'          # a package WITH xyz exposure
MAIN_ONLY = "assets: [BTC, ETH]"      # no xyz exposure at all

_LB = 'raw = ctx.call_tool("leaderboard_get_markets")\nfor m in raw:\n    %s\n        continue\n'


class LeaderboardRule(unittest.TestCase):
    """Positive evidence: a file that consumes leaderboard rows must either consult the row's `dex`
    or normalise the `xyz:` prefix. Any spelling of the bug fails that test; any spelling of the fix
    passes it."""

    def test_every_phrasing_of_the_bug_is_caught(self):
        for v in ('if token != coin:', 'if token.upper() != coin:', 'if row["token"] != coin:',
                  'if tok != coin:', 'if token != coin and ok:', 'if tk != c:'):
            with self.subTest(spelling=v):
                self.assertTrue(G(_LB % v, XYZ), f"missed: {v}")

    def test_same_line_body_is_caught(self):
        """`if …: continue` on one line — a spelling the old regex could not express."""
        src = ('raw = ctx.call_tool("leaderboard_get_markets")\n'
               'for m in raw:\n'
               '    if token != coin: continue\n')
        self.assertTrue(G(src, XYZ))

    def test_every_phrasing_of_the_fix_passes(self):
        for v in ('if m.get("dex") != want: continue',
                  'if token != coin.split(":")[-1]: continue',
                  'if token != coin.removeprefix("xyz:"): continue',
                  'if coin.startswith("xyz:") != (m["dex"] == "xyz"): continue'):
            with self.subTest(spelling=v):
                self.assertEqual(G(_LB % v, XYZ), [], f"false positive: {v}")

    def test_a_docstring_mention_is_not_a_use(self):
        """Scoring modules describe the row shape they are handed; that is not a call."""
        self.assertEqual(G('"""a normalized leaderboard_get_markets row"""\nx = 1\n', XYZ), [])

    def test_main_only_package_is_not_nagged(self):
        self.assertEqual(G(_LB % "if token != coin:", MAIN_ONLY), [])

    def test_package_that_bans_xyz_is_not_nagged(self):
        """Several packages mention xyz only to ban it — the word is not exposure."""
        self.assertEqual(G(_LB % "if token != coin:", "xyzBanned: true"), [])
        self.assertEqual(G(_LB % "if token != coin:", "assets: []  # XYZ banned"), [])

    def test_no_package_context_never_suppresses(self):
        self.assertTrue(G(_LB % "if token != coin:", None))


class AssetPositionsRule(unittest.TestCase):
    """Scoped to the READ, not the file: `assetPositions` off the raw clearinghouse result is the bug,
    and a file that handles the sections correctly in one function can still grow the bug in another."""

    CORRECT = ('def ok(ctx):\n'
               '    ch = ctx.call_tool("strategy_get_clearinghouse_state")\n'
               '    d = ch.get("data", ch)\n'
               '    for section in ("main", "xyz"):\n'
               '        d.get(section, {}).get("assetPositions")\n')
    BUGGY = ('def _held(ctx):\n'
             '    st = ctx.call_tool("strategy_get_clearinghouse_state")\n'
             '    return [x for x in st.get("assetPositions", [])]\n')

    def test_top_level_read_is_caught(self):
        self.assertTrue(G(self.BUGGY))

    def test_correct_read_is_clean(self):
        self.assertEqual(G(self.CORRECT), [])

    def test_a_correct_file_that_grows_a_buggy_read_is_still_caught(self):
        """The likeliest real regression, and what a file-wide text suppressor misses entirely."""
        found = G(self.CORRECT + "\n\n" + self.BUGGY)
        self.assertTrue(found)
        self.assertIn("assetPositions", found[0])

    def test_reversed_section_order_is_correct(self):
        self.assertEqual(G(self.CORRECT.replace('("main", "xyz")', '("xyz", "main")')), [])

    def test_generic_section_walk_is_correct(self):
        src = ('def f(ctx):\n'
               '    ch = ctx.call_tool("strategy_get_clearinghouse_state")\n'
               '    for s in ch.values():\n'
               '        s.get("assetPositions")\n')
        self.assertEqual(G(src), [])

    def test_legacy_flat_fallback_beside_a_correct_read_is_allowed(self):
        """A deliberate fallback in the SAME function as a both-sections read is not the bug."""
        src = ('def f(ctx):\n'
               '    ch = ctx.call_tool("strategy_get_clearinghouse_state")\n'
               '    d = ch.get("data", ch)\n'
               '    rows = []\n'
               '    for sec in ("main", "xyz"):\n'
               '        rows.extend(d.get(sec, {}).get("assetPositions", []) or [])\n'
               '    if not rows:\n'
               '        rows = d.get("assetPositions", []) or []\n')
        self.assertEqual(G(src), [])

    def test_unparseable_source_fails_open(self):
        self.assertEqual(G("def broken(:\n"), [])


class SharedContract(unittest.TestCase):
    def test_author_and_ops_run_the_identical_guard(self):
        """author-green == deploy-green: the same function over the same file set on both sides."""
        sys.path.insert(0, str(ROOT / "senpi-strategy-author" / "scripts"))
        import validate_strategy  # noqa: E402
        src_a = validate_strategy.dex_blind_offenders.__doc__
        src_o = _pkg.dex_blind_offenders.__doc__
        self.assertEqual(src_a, src_o)
        for case in (self.__class__.__name__,):     # behavioural parity on the real cases
            for text, ctx in ((_LB % "if token != coin:", XYZ), (AssetPositionsRule.BUGGY, None),
                              (AssetPositionsRule.CORRECT, None), (_LB % 'if m["dex"] != d: pass', XYZ)):
                self.assertEqual(validate_strategy.dex_blind_offenders(text, ctx),
                                 _pkg.dex_blind_offenders(text, ctx))

    def test_tests_dir_is_excluded_from_the_scan(self):
        """A deliberate bug fixture under tests/ is not a defect."""
        files = _pkg.dex_scan_files(ROOT / "strategies" / "bison")
        self.assertTrue(files)
        self.assertFalse([f for f in files if "tests" in f.parts])


class FleetSweep(unittest.TestCase):
    def test_zero_findings_across_the_fleet(self):
        """Reproducible on THIS branch — the claim must hold against the tree it ships with."""
        bad = []
        for pkg in sorted(p.parent for p in (ROOT / "strategies").glob("*/strategy.yaml")):
            ctx = _pkg.dex_pkg_context(pkg)
            for f in _pkg.dex_scan_files(pkg):
                for m in G(f.read_text(errors="ignore"), ctx):
                    bad.append(f"{f.relative_to(ROOT)}: {m[:60]}")
        self.assertEqual(bad, [], f"{len(bad)} findings")


if __name__ == "__main__":
    unittest.main(verbosity=2)
