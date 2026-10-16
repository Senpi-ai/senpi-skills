"""Hyperfeed Movers: the two tiers, and the rule that a stale baseline is never scored quietly.

The section exists because users ask "what's pumping right now?" and the strategies cannot answer
on demand — Penguin's detector is a diff against a scan 90 seconds old, held in runtime state. The
danger is not that a skill can't reproduce that; it is that it CAN reproduce something that looks
identical from a baseline hours old, and present it in the same words. These tests pin the
separation:

  Tier A never depends on history at all. It ranks on `contribution_pct_change_15m`, a delta the
  FEED computes, and applies only Penguin's stateless gates (rank > 10, 4h/direction agreement,
  cc_15m > 0, the trader floor).

  Tier B runs the verbatim scorer, and only inside a freshness window whose band is printed.
  STALE means NOT SCORED — not "scored with a caveat".

Run:
  python3 -m pytest senpi-signals/tests/test_hyperfeed.py -q
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import hyperfeed  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "leaderboard_markets.json")
T0 = 1790000000.0          # a fixed clock; hour_utc is derived from it, so US_SESSION is deterministic


def _raw():
    with open(FIXTURE) as fh:
        return json.load(fh)


def _call(raw=None):
    payload = raw if raw is not None else _raw()
    return lambda name, args: payload


class Normalize(unittest.TestCase):
    def test_rank_is_prefilter_index_and_thin_sides_are_dropped(self):
        m = hyperfeed.normalize(_raw())
        by = {r["token"]: r for r in m}
        self.assertNotIn("THIN", by, "a 4-trader side must not enter the rank order")
        # THIN sat at feed index 15 (rank 15). Dropping it must NOT renumber the rows after it:
        # every jump threshold is expressed in the feed's own rank.
        self.assertEqual(by["DOGE"]["rank"], 16)
        self.assertEqual(by["WLD"]["rank"], 11)

    def test_xyz_ban_is_opt_in(self):
        self.assertIn("GOLD", {r["token"] for r in hyperfeed.normalize(_raw())})
        self.assertNotIn("GOLD", {r["token"] for r in hyperfeed.normalize(_raw(), xyz_banned=True)})

    def test_envelope_shapes_all_unwrap(self):
        rows = _raw()["data"]["markets"]["markets"]
        for shape in (_raw(), {"data": {"markets": rows}}, {"data": rows}, rows):
            self.assertTrue(hyperfeed.normalize(shape), f"failed to unwrap {type(shape)}")


class TierA(unittest.TestCase):
    def test_gates_are_penguins_stateless_ones(self):
        rows = hyperfeed.tier_a(hyperfeed.normalize(_raw()), top=20)
        toks = [r["token"] for r in rows]
        self.assertNotIn("BTC", toks, "rank <= 10 has no jump room; penguin skips it")
        self.assertNotIn("HYPE", toks, "rank 4 is inside the top-10 gate")
        self.assertNotIn("OP", toks, "4h move disagrees with the LONG side")
        self.assertNotIn("SUI", toks, "cc_15m <= 0 is penguin's freshness gate")
        self.assertIn("WLD", toks)
        self.assertIn("FIL", toks, "SHORT with the 4h move down IS aligned")

    def test_ranked_by_the_feeds_own_15m_delta(self):
        rows = hyperfeed.tier_a(hyperfeed.normalize(_raw()), top=3)
        # GOLD is an xyz row at cc 6.0 and outranks DOGE at 5.5 — xyz is in the universe unless the
        # caller bans it, so the order is by the delta alone, never by venue.
        self.assertEqual([r["token"] for r in rows], ["WLD", "ARB", "GOLD"])
        self.assertEqual(rows[0]["cc_15m"], 14.2)
        self.assertEqual([r["token"] for r in
                          hyperfeed.tier_a(hyperfeed.normalize(_raw(), xyz_banned=True), top=3)],
                         ["WLD", "ARB", "DOGE"])

    def test_tier_a_needs_no_history_whatsoever(self):
        """The whole point: a first-ever read still answers "what's hot right now"."""
        with tempfile.TemporaryDirectory() as d:
            rep = hyperfeed.read(_call(), state_dir=d, now=T0, top=4)
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["movers"], "first read produced no movers")
        self.assertEqual(rep["baseline"]["band"], "NONE")
        self.assertEqual(rep["rotations"], [])


class FreshnessBands(unittest.TestCase):
    def test_classify_boundaries(self):
        self.assertEqual(hyperfeed.classify(None), "NONE")
        # BOTH ends are refused. A 3s baseline was called "comparable to the scanner's own
        # 90-second cadence" on the first live run; nothing rank-jumps in 3 seconds.
        self.assertEqual(hyperfeed.classify(0), "TOOFRESH")
        self.assertEqual(hyperfeed.classify(hyperfeed.MIN_BASELINE_S - 0.1), "TOOFRESH")
        self.assertEqual(hyperfeed.classify(hyperfeed.MIN_BASELINE_S), "LIVE")
        self.assertEqual(hyperfeed.classify(hyperfeed.LIVE_MAX_S), "LIVE")
        self.assertEqual(hyperfeed.classify(hyperfeed.LIVE_MAX_S + 0.1), "WIDE")
        self.assertEqual(hyperfeed.classify(hyperfeed.WIDE_MAX_S), "WIDE")
        self.assertEqual(hyperfeed.classify(hyperfeed.WIDE_MAX_S + 0.1), "STALE")

    def test_a_stale_baseline_is_not_scored_at_all(self):
        """The failure this whole design exists to prevent: a rank jump measured over hours,
        reported in the same words as one measured over 90 seconds."""
        with tempfile.TemporaryDirectory() as d:
            hyperfeed.read(_call(), state_dir=d, now=T0, top=6)
            stale_at = T0 + hyperfeed.WIDE_MAX_S + 60
            rep = hyperfeed.read(_call(), state_dir=d, now=stale_at, top=6)
        self.assertEqual(rep["baseline"]["band"], "STALE")
        self.assertEqual(rep["rotations"], [], "a stale baseline must produce NO jump math")
        block = hyperfeed.render(rep)
        self.assertIn("too long to call anything *sudden*", block)
        self.assertIn("Ask again in a couple of minutes", block)
        self.assertTrue(rep["movers"], "tier A must still answer on a stale ring")

    def test_wide_baseline_is_scored_but_labelled_as_wider(self):
        with tempfile.TemporaryDirectory() as d:
            hyperfeed.read(_call(), state_dir=d, now=T0, top=6)
            rep = hyperfeed.read(_call(), state_dir=d, now=T0 + hyperfeed.LIVE_MAX_S + 60, top=6)
        self.assertEqual(rep["baseline"]["band"], "WIDE")
        block = hyperfeed.render(rep)
        self.assertIn("a WIDE window", block)
        self.assertIn("not in the last minute or two", block)

    def test_live_baseline_is_called_comparable(self):
        with tempfile.TemporaryDirectory() as d:
            hyperfeed.read(_call(), state_dir=d, now=T0, top=6)
            rep = hyperfeed.read(_call(), state_dir=d, now=T0 + 90, top=6)
        self.assertEqual(rep["baseline"]["band"], "LIVE")
        self.assertIn("the same kind the live strategies watch", hyperfeed.render(rep))


class TierB(unittest.TestCase):
    def test_a_real_rank_jump_scores_with_penguins_own_reasons(self):
        """Second read where WLD has climbed 11 -> ... no: build the jump explicitly.

        Baseline puts WLD deep (rank 62) with a small contribution; the current read has it at 11
        with a 4.5 contribution. That is a +51 jump off a >=25 prior rank with a >=3x contribution
        explosion — IMMEDIATE_MOVER + FIRST_JUMP + CONTRIB_EXPLOSION, which is what penguin fires on.
        """
        with tempfile.TemporaryDirectory() as d:
            ring = [{"ts": T0, "markets": [{"token": "WLD", "dex": "", "rank": 62,
                                            "contribution": 0.2}]}]
            hyperfeed.save_ring(d, ring)
            rep = hyperfeed.read(_call(), state_dir=d, now=T0 + 90, top=6)
        self.assertEqual(rep["baseline"]["band"], "LIVE")
        wld = [r for r in rep["rotations"] if r["token"] == "WLD"]
        self.assertTrue(wld, f"WLD should have scored; got {[r['token'] for r in rep['rotations']]}")
        r = wld[0]
        self.assertGreaterEqual(r["score"], hyperfeed.scoring.STRIKER_MIN_SCORE)
        self.assertGreaterEqual(len(r["reasons"]), hyperfeed.scoring.STRIKER_MIN_REASONS)
        joined = " ".join(r["reasons"])
        self.assertIn("IMMEDIATE_MOVER", joined)
        self.assertIn("FIRST_JUMP", joined)
        self.assertIn("CONTRIB_EXPLOSION", joined)
        self.assertEqual(r["meta"]["rankJump"], 51)

    def test_no_rotation_is_reported_as_a_real_read(self):
        """Same board twice = no jumps. That is the common case and must not read as a failure."""
        with tempfile.TemporaryDirectory() as d:
            hyperfeed.read(_call(), state_dir=d, now=T0, top=6)
            rep = hyperfeed.read(_call(), state_dir=d, now=T0 + 90, top=6)
        self.assertEqual(rep["rotations"], [])
        self.assertIn("That is the normal answer most of the time", hyperfeed.render(rep))


class ExitCode(unittest.TestCase):
    """A handled feed outage exits 0, like sweep.py's "Not measured this run" degrade.

    Found live: run side by side on a box that could not reach the leaderboard, sweep.py exited 0
    and this exited 1 — so the agent driving it reported a crashed script rather than presenting the
    block, which already said "unavailable" and named the reason. Nonzero here buys nothing a caller
    cannot get from rep["ok"] in --json, and costs the section its voice.
    """

    def test_main_exits_zero_on_a_handled_outage(self):
        import contextlib
        import io

        class Boom(Exception):
            pass

        def raising(name, args, timeout=None):
            raise Boom("tool failed: UNAVAILABLE: Leaderboard API error")

        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = hyperfeed.main(["--state-dir", d, "--no-persist"],
                                    _call_tool=raising)
        self.assertEqual(rc, 0, "a handled outage must not look like a crash")
        self.assertIn("unavailable", buf.getvalue())


class Degradation(unittest.TestCase):
    def test_a_failed_feed_read_says_so_and_invents_nothing(self):
        err = {"success": False, "error": {"code": "UNAVAILABLE", "message": "Leaderboard API error"}}
        with tempfile.TemporaryDirectory() as d:
            rep = hyperfeed.read(_call(err), state_dir=d, now=T0)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["movers"], [])
        block = hyperfeed.render(rep)
        self.assertIn("unavailable", block)
        self.assertIn("rather than filling the gap from memory", block)

    def test_a_raising_transport_degrades_instead_of_tracebacking(self):
        """The real client RAISES; it does not return a `success: false` envelope.

        mcp_client._unwrap converts a failed tool into MCPError, so the envelope test above passed
        while the CLI tracebacked at a down leaderboard API — the section promises "unavailable and
        invents nothing" and instead printed a stack. Both failure shapes must degrade identically.
        """
        class Boom(Exception):
            pass

        def raising(name, args):
            raise Boom("tool failed: UNAVAILABLE: Leaderboard API error")

        with tempfile.TemporaryDirectory() as d:
            rep = hyperfeed.read(raising, state_dir=d, now=T0)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["movers"], [])
        self.assertEqual(rep["rotations"], [])
        self.assertIn("Boom", rep["degraded"])
        self.assertIn("UNAVAILABLE", rep["degraded"])
        block = hyperfeed.render(rep)
        self.assertIn("unavailable", block)
        self.assertIn("rather than filling the gap from memory", block)

    def test_a_raising_transport_does_not_poison_the_ring(self):
        """A failed read must not append a snapshot — an empty baseline would silently become the
        thing the next successful read diffs against."""
        with tempfile.TemporaryDirectory() as d:
            hyperfeed.read(_call(), state_dir=d, now=T0)
            before = hyperfeed.load_ring(d, T0)

            def raising(name, args):
                raise RuntimeError("down")

            hyperfeed.read(raising, state_dir=d, now=T0 + 60)
            self.assertEqual(len(hyperfeed.load_ring(d, T0 + 60)), len(before))

    def test_a_corrupt_ring_degrades_to_tier_a(self):
        with tempfile.TemporaryDirectory() as d:
            with open(hyperfeed.ring_path(d), "w") as fh:
                fh.write("{not json")
            rep = hyperfeed.read(_call(), state_dir=d, now=T0, top=4)
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["movers"])
        self.assertEqual(rep["baseline"]["band"], "NONE")

    def test_ring_is_ttl_pruned_and_capped(self):
        with tempfile.TemporaryDirectory() as d:
            old = [{"ts": T0 - hyperfeed.RING_TTL_S - 10, "markets": []}]
            hyperfeed.save_ring(d, old + [{"ts": T0 - 60, "markets": []}])
            self.assertEqual(len(hyperfeed.load_ring(d, T0)), 1, "TTL did not drop the old snapshot")
            hyperfeed.save_ring(d, [{"ts": T0 - i, "markets": []}
                                    for i in range(hyperfeed.RING_MAX + 10, 0, -1)])
            self.assertLessEqual(len(hyperfeed.load_ring(d, T0)), hyperfeed.RING_MAX)


class CliWiring(unittest.TestCase):
    """The entry point nothing else covers.

    Every test above injects `call_tool` directly, which is right for the engine and useless for the
    CLI: the first live run of this script died with `AttributeError: 'MCPClient' object has no
    attribute 'call_tool'` while the whole suite was green. The engine was fine; the one line that
    connects it to a real MCP session was not. So the adapter's contract is pinned against the real
    class, by name.
    """

    def test_mcpclient_exposes_mcp_call_and_not_call_tool(self):
        import mcp_client
        self.assertTrue(hasattr(mcp_client.MCPClient, "mcp_call"),
                        "MCPClient lost mcp_call — the adapter in hyperfeed.py routes through it")
        self.assertFalse(hasattr(mcp_client.MCPClient, "call_tool"),
                         "MCPClient grew a call_tool; simplify _adapter rather than keeping both")

    def test_adapter_routes_name_and_args_through_mcp_call(self):
        seen = {}

        class FakeClient:
            def mcp_call(self, tool, timeout=12, **arguments):
                seen.update(tool=tool, timeout=timeout, arguments=arguments)
                return {"success": True, "data": {"markets": {"markets": []}}}

        hyperfeed._adapter(FakeClient())("leaderboard_get_markets", {"limit": 100})
        self.assertEqual(seen["tool"], "leaderboard_get_markets")
        self.assertEqual(seen["arguments"], {"limit": 100},
                         "args must reach the server as the tool-arguments payload, unwrapped")
        self.assertEqual(seen["timeout"], hyperfeed.READ_TIMEOUT_S)

    def test_the_adapter_is_what_main_uses(self):
        """Guards the specific regression: main() must not hand-roll its own client call again."""
        src = open(os.path.join(HERE, "..", "scripts", "hyperfeed.py")).read()
        main_src = src[src.index("def main(argv=None, _call_tool=None):"):]
        self.assertIn("_adapter(MCPClient())", main_src)
        self.assertNotIn("client.call_tool", main_src)


class InTheSweep(unittest.TestCase):
    """The movers section rides in every sweep, and says the right thing about what it cannot do."""

    def _rows(self):
        return _raw()["data"]["markets"]["markets"]

    def test_block_is_built_from_the_sweeps_own_board_rows(self):
        import sweep
        blk = sweep.hyperfeed_block(self._rows(), now="2026-09-27T23:10:00+00:00")
        self.assertTrue(blk and blk["report"]["movers"])
        self.assertIn("Hyperfeed Movers", blk["markdown"])

    def test_the_sweep_never_promises_a_baseline_it_cannot_keep(self):
        """A sweep keeps no history, so "ask again in ~2 minutes" would be a lie there — a second
        sweep prints the identical line. It must point at the command that owns a ring instead."""
        import sweep
        md = sweep.hyperfeed_block(self._rows(), now="2026-09-27T23:10:00+00:00")["markdown"]
        rot = md.split("*Anything just breaking out?*")[1]
        self.assertIn("needs two readings minutes apart", rot)
        self.assertIn("Ask again in a couple of minutes", rot)

    def test_no_rows_and_no_module_both_degrade_to_nothing(self):
        import sweep
        self.assertIsNone(sweep.hyperfeed_block([], now=None))
        saved = sweep.hyperfeed
        try:
            sweep.hyperfeed = None          # the vendored scanners ship without it
            self.assertIsNone(sweep.hyperfeed_block(self._rows(), now=None))
        finally:
            sweep.hyperfeed = saved


if __name__ == "__main__":
    unittest.main()
