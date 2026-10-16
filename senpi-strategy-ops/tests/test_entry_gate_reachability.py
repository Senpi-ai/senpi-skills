#!/usr/bin/env python3
"""Entry-gate reachability: a hard gate must be satisfiable by real upstream data.

Two silent no-trade bugs found live (2026-08-13), where condor ($4,000)
and piranha ($1,500) each sat ACTIVE and fully funded for 15 days, ~7,000 scanner
ticks apiece, and never emitted a single signal. Both scanners were healthy — they
logged `WAITING` every tick and exited clean, which is indistinguishable from a
selective strategy correctly standing aside, so no error-keyed health check fires.

  condor  — gated `pct_of_top_traders_gain >= 70` under the name "SM consensus".
            That field is a share of TOTAL top-trader gain spread across every
            market in the leaderboard response (it sums to ~100 board-wide;
            observed max 25.6 across 271 markets), NOT a per-market "% of traders
            leaning this way". No market can reach 70, so the gate blocked 100%
            of signals. Directional agreement lives in `is_dominant_direction`.

  piranha — read OI velocity from a NESTED `oi_change_pct: {"1h": …}` shape, but
            `market_get_asset_data` returns FLAT keys (`oi_change_pct_1h`), so the
            primary source never resolved; and scan.py refreshed the OI cache
            BEFORE reading the previous value out of it, so the self-computed
            fallback delta was always exactly 0.00%. Gate 1 (OI unwinding
            >= 3%/1h) could therefore never pass by either route.

7 of the 11 tests below fail against the pre-fix code. The remaining 4 are
deliberate controls — a non-dominant market, a quiet tape, the nested oi_velocity
shape, and the fallback given a genuine previous OI — which must keep passing, so
that "make it trade again" cannot be satisfied by simply removing the gates.

Run:
    python3 senpi-strategy-ops/tests/test_entry_gate_reachability.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import importlib.util
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


class _Ctx:
    """Minimal ctx: only `senpi_mcp.call_tool` is exercised by fetch_sm_map."""

    def __init__(self, responses):
        self._responses = responses
        outer = self

        class _MCP:
            def call_tool(self, name, args):        # noqa: ARG002
                return outer._responses[name]

        self.senpi_mcp = _MCP()
        self.state = None


# ── the REAL leaderboard_get_markets shape (verified against prod 2026-08-13) ──
# `pct_of_top_traders_gain` is an ATTRIBUTION SHARE across the whole board — the
# live snapshot summed to 125.2 over 271 markets, max 25.63, median 0.01. Any
# fixture that puts 70+ on a single market is not reproducible in production and
# is what let condor's unreachable gate survive review.
LEADERBOARD_ROWS = [
    # token   dir      gain%  dominant traders   4h      1h     c15m
    ("HYPE",  "short", 25.63, True,    212,     -1.01,  -0.81,  6.69),
    ("ETH",   "short",  8.20, True,    114,      0.26,  -0.02,  7.85),
    ("CRCL",  "long",   5.68, True,     86,      6.71,   2.32, -0.23),
    ("BTC",   "long",   5.52, True,    112,     -0.12,   0.05, -6.84),
    ("LIT",   "short",  5.47, True,     63,     -3.33,  -1.60,  0.94),
    ("SOL",   "long",   3.60, True,     69,      0.55,   0.14,  2.14),
    ("XMR",   "short",  1.21, True,     53,     -1.36,  -0.20, -0.31),
    ("DOGE",  "long",   0.01, True,      8,      2.10,   0.90,  1.50),
]


def _leaderboard():
    return {"data": {"markets": {"markets": [
        {"token": t, "dex": "", "direction": d, "pct_of_top_traders_gain": g,
         "is_dominant_direction": dom, "trader_count": n,
         "token_price_change_pct_4h": p4, "token_price_change_pct_1h": p1,
         "contribution_pct_change_15m": c15, "contribution_pct_change_1h": c15}
        for t, d, g, dom, n, p4, p1, c15 in LEADERBOARD_ROWS
    ]}}}


class CondorGateReachable(unittest.TestCase):
    """condor's entry gate chain must be satisfiable by production-shaped data.

    This class exists because condor once shipped a gate that could never pass:
    `minSmConsensusPct: 70` was applied to `pct_of_top_traders_gain`, a share of TOTAL
    board gain that sums to ~100 ACROSS every market, so no single market could reach 70
    and 100% of signals were blocked.

    On 2026-10-12 the whole chain changed — direction moved off the 4h gain board (which
    was circular: when a coin falls, every wallet short it is mechanically at the top of
    that board) onto the proven cohort, counted by headcount among wallets with >= $1M
    lifetime realized PnL. A new chain is exactly when "is this reachable at all?" needs
    asking again, so the class is rewritten rather than retired — and the lesson is now
    live in a second place: condor-concentrated shipped with a realized-PnL field list
    that omitted the only name the API sends, so its cohort was empty on every tick.
    """

    @classmethod
    def setUpClass(cls):
        cls.scan = _load(STRATEGIES / "condor/main/scanners/scan.py", "condor_scan")
        cls.price_map = cls.scan.fetch_price_changes(
            _Ctx({"leaderboard_get_markets": _leaderboard()}), {})

    def _cohort_for(self, coin, direction, positioned=60, conviction=85.0):
        return {"direction": direction, "total_positioned": positioned,
                "conviction_pct": conviction}

    def test_the_board_supplies_price_data_and_nothing_directional(self):
        """The circular read must be gone, not merely de-weighted."""
        self.assertTrue(self.price_map, "no price data parsed from a production board")
        for coin, row in self.price_map.items():
            for banned in ("direction", "is_dominant", "gain_share_pct", "traders"):
                self.assertNotIn(banned, row,
                                 f"{coin} still carries `{banned}` from the 4h gain board")

    def test_at_least_one_candidate_survives_the_full_gate_chain(self):
        """The whole point: production-shaped prices plus a plausible cohort must produce
        a scorable setup. Zero here means an entry gate is unreachable again."""
        scoring = self.scan.scoring
        hits = []
        for coin, pd in self.price_map.items():
            # direction is price-led, so hand the cohort the side the price implies
            side = "LONG" if pd["p4h"] >= 0 else "SHORT"
            asset_info = {"coin": coin, "oi_usd": 5_000_000, "volume_24h": 1e8,
                          "price": 1.0, "funding": 0.0}
            sig = scoring.evaluate_trend_continuation(
                asset_info, pd, self._cohort_for(coin, side), None, 17)
            if sig:
                hits.append(sig)
        self.assertGreaterEqual(
            len(hits), 1,
            "condor produced ZERO candidates from a production-shaped board with a "
            "cooperating cohort — an entry gate is unreachable, so it can never trade")
        self.assertTrue(any(h["score"] >= scoring.MIN_SCORE for h in hits),
                        f"no candidate cleared MIN_SCORE={scoring.MIN_SCORE}")

    def test_the_cohort_can_still_veto(self):
        """The replacement gate must filter, not just pass everything."""
        scoring = self.scan.scoring
        coin, pd = next(iter(self.price_map.items()))
        wrong_side = "SHORT" if pd["p4h"] >= 0 else "LONG"
        asset_info = {"coin": coin, "oi_usd": 5_000_000, "volume_24h": 1e8,
                      "price": 1.0, "funding": 0.0}
        self.assertIsNone(
            scoring.evaluate_trend_continuation(
                asset_info, pd, self._cohort_for(coin, wrong_side), None, 17),
            "a cohort holding the OTHER side must veto the signal")
        self.assertIsNone(
            scoring.evaluate_trend_continuation(asset_info, pd, None, None, 17),
            "no cohort at all must mean no signal — it is a hard gate")

    def test_a_thin_or_unconvinced_cohort_is_rejected(self):
        scoring = self.scan.scoring
        coin, pd = next(iter(self.price_map.items()))
        side = "LONG" if pd["p4h"] >= 0 else "SHORT"
        asset_info = {"coin": coin, "oi_usd": 5_000_000, "volume_24h": 1e8,
                      "price": 1.0, "funding": 0.0}
        self.assertIsNone(scoring.evaluate_trend_continuation(
            asset_info, pd, self._cohort_for(coin, side, positioned=3), None, 17),
            "a cohort of 3 positioned wallets must not clear the depth floor")
        self.assertIsNone(scoring.evaluate_trend_continuation(
            asset_info, pd, self._cohort_for(coin, side, conviction=51.0), None, 17),
            "a near-even cohort split must not clear the conviction floor")

    def test_the_realized_pnl_field_the_api_actually_sends_is_read(self):
        """The condor-concentrated bug, guarded at the family's shared entry point."""
        self.assertEqual(self.scan._realized_pnl({"realizedProfitAndLoss": 2_000_000}),
                         2_000_000.0,
                         "`realizedProfitAndLoss` is the only name the API sends; a list "
                         "omitting it reads 0.0 for every trader and empties the cohort")


class PiranhaOiGateReachable(unittest.TestCase):
    """piranha's OI-unwind gate must resolve from the shape production sends."""

    @classmethod
    def setUpClass(cls):
        cls.scan = _load(STRATEGIES / "piranha/main/scanners/scan.py", "piranha_scan")
        cls.scoring = cls.scan.scoring

    @staticmethod
    def _payload(oi_1h, move_1h_pct, move_5m_pct, nested=False):
        """market_get_asset_data as prod returns it: oi_velocity with FLAT keys."""
        base = 1000.0
        c1h = [{"c": str(base)}, {"c": str(base)},
               {"c": str(base / (1 + move_1h_pct / 100))}, {"c": str(base)}]
        c5m = [{"c": str(base)}, {"c": str(base)},
               {"c": str(base / (1 + move_5m_pct / 100))}, {"c": str(base)}]
        oiv = ({"oi_change_pct": {"1h": oi_1h}} if nested else
               {"current_oi": 2.5e9, "oi_change_pct_5m": -0.1, "oi_change_pct_15m": -0.5,
                "oi_change_pct_1h": oi_1h, "oi_change_pct_4h": -2.0,
                "oi_trend": "DECLINING", "oi_acceleration": "INCREASING"})
        return {"data": {"candles": {"1h": c1h, "5m": c5m},
                         "asset_context": {"openInterest": "39456.03", "markPx": str(base)},
                         "oi_velocity": oiv,
                         "l2_book": {"levels": [[{"px": "999", "sz": "10"}],
                                                [{"px": "1001", "sz": "10"}]]}}}

    def test_flat_oi_velocity_shape_resolves(self):
        """prod sends oi_change_pct_1h; the nested-only reader silently returned None."""
        for oi in (1.28, -3.5, -7.0):
            val, src = self.scoring.oi_velocity_1h(self._payload(oi, -2.5, -0.4), None)
            self.assertIsNotNone(
                val, "oi_velocity_1h returned None for the FLAT oi_velocity shape that "
                     "market_get_asset_data actually sends — gate 1 can never pass")
            self.assertAlmostEqual(val, oi, places=4)
            self.assertEqual(src, "oi_velocity")

    def test_nested_oi_velocity_shape_still_resolves(self):
        """Back-compat: don't break whichever payloads do use the nested shape."""
        val, src = self.scoring.oi_velocity_1h(self._payload(-7.0, -2.5, -0.4, nested=True), None)
        self.assertAlmostEqual(val, -7.0, places=4)
        self.assertEqual(src, "oi_velocity")

    def test_forced_flow_signature_produces_a_thesis(self):
        thesis = self.scoring.build_thesis(
            "BTC", self._payload(-7.0, -2.5, -0.4), None, ("SHORT", 60.0), {})
        self.assertIsNotNone(
            thesis, "a textbook forced-flow signature (OI -7%/1h, price -2.5%/1h, 5m "
                    "still falling) produced no thesis — the OI gate is unreachable")
        self.assertEqual(thesis["direction"], "SHORT")
        self.assertGreaterEqual(thesis["score"], 5, "below the runtime.yaml minScore floor")

    def test_quiet_market_is_still_rejected(self):
        """The gate must still gate — a calm tape is not forced flow."""
        self.assertIsNone(
            self.scoring.build_thesis("BTC", self._payload(1.28, -0.2, -0.05), None,
                                      ("SHORT", 60.0), {}),
            "a quiet market must not be read as a liquidation cascade")

    def test_self_compute_fallback_uses_the_previous_tick(self):
        """Regression on the cache-ordering bug: prev_oi must differ from current OI.

        scan.py wrote the current OI into oi_cache and then read it straight back
        as `prev_oi`, so this delta was always exactly 0.00% and never cleared the
        -3% gate. Here prev_oi is a genuine earlier value.
        """
        payload = self._payload(-7.0, -2.5, -0.4)
        del payload["data"]["oi_velocity"]           # force the fallback path
        val, src = self.scoring.oi_velocity_1h(payload, 42000.0)
        self.assertEqual(src, "computed")
        self.assertLess(val, -3.0,
                        "fallback delta must be a real change vs the previous tick")

    def test_scan_reads_prev_oi_before_refreshing_the_cache(self):
        """Source-level guard: the CALL SITE must precede the cache write.

        Matching `_prev_oi(oi_cache` naively also hits the `def _prev_oi(...)`
        definition further up the file, which would make this pass spuriously —
        so skip any occurrence that is a definition.
        """
        src = (STRATEGIES / "piranha/main/scanners/scan.py").read_text()
        call_sites = [i for i in range(len(src))
                      if src.startswith("_prev_oi(oi_cache", i)
                      and not src[:i].rstrip().endswith("def")]
        write_at = src.find("oi_cache[cu] = {")
        self.assertTrue(call_sites, "expected a _prev_oi(oi_cache, …) call site")
        self.assertNotEqual(write_at, -1, "expected an oi_cache[cu] = {…} refresh")
        self.assertLess(min(call_sites), write_at,
                        "scan.py refreshes the OI cache BEFORE reading the previous "
                        "value out of it — the computed delta will always be 0.00%")


if __name__ == "__main__":
    unittest.main(verbosity=2)
