#!/usr/bin/env python3
"""Hermetic tests for the two facts status.py prints beside health — no MCP, no openclaw.

`paused`: the runtime's OWN entry gate (`components.risk` of a `RuntimeHealthStatus`) holds
entries while health stays `healthy` — the scanner ticks, the DSL runs, nothing opens. A status
surface that prints ✅ and nothing else for that runtime is how "my strategy is dead" gets
answered with a redeploy that market-exits the book and restarts the same gate from zero.

`config_drift`: the recipe the runtime is RUNNING (its rendered descriptor) differs from the
package on disk — an edit that was never applied, printed with the in-place fix.

    python3 -m pytest senpi-strategy-ops/tests/test_status_risk.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _cli    # noqa: E402
import status  # noqa: E402


def _entry(eligibility="OPEN", gates=(), health="healthy"):
    """A `RuntimeHealthStatus` record as `openclaw senpi status --json` prints it (runtime
    src/health/types.ts): the verdict at the top, the risk component under `components`."""
    return {"runtimeName": "spider-main", "health": health, "activePositions": 2,
            "components": {"scanners": {"health": health},
                           "risk": {"component": "risk", "enabled": True,
                                    "eligibility": eligibility, "gates": list(gates)}}}


_CAP = {"gateId": "max_entries_day", "gateName": "Max Entries/Day", "status": "CLOSED",
        "reason": "Max entries: 4/4 entries today", "evaluationOk": True}
_OPEN = {"gateId": "daily_loss_halt", "gateName": "Daily Loss Halt", "status": "OPEN"}
_COOL = {"gateId": "per_asset_cooldown", "gateName": "Per-Asset Cooldown", "status": "COOLDOWN",
         "reason": "HYPE closed 4m ago (cooldown 15m)"}


class TestRiskPause(unittest.TestCase):
    def test_closed_gate_is_reported_with_its_own_reason_and_reset(self):
        p = _cli.risk_pause(_entry("CLOSED", [_OPEN, _CAP]))
        self.assertEqual(p["eligibility"], "CLOSED")
        self.assertEqual([g["gate"] for g in p["gates"]], ["Max Entries/Day"])   # OPEN gates are not listed
        self.assertEqual(p["gates"][0]["reason"], "Max entries: 4/4 entries today")
        self.assertEqual(p["gates"][0]["reset"], "resets at 00:00 UTC")

    def test_cooldown_is_a_pause_too(self):
        p = _cli.risk_pause(_entry("COOLDOWN", [_COOL]))
        self.assertEqual(p["eligibility"], "COOLDOWN")
        self.assertIn("expires on its own", p["gates"][0]["reset"])

    def test_open_or_absent_is_none_not_paused(self):
        self.assertIsNone(_cli.risk_pause(_entry("OPEN", [_OPEN])))
        self.assertIsNone(_cli.risk_pause({"health": "healthy"}))          # older runtime: no risk component
        self.assertIsNone(_cli.risk_pause({"components": {"risk": {"eligibility": "N_A"}}}))
        self.assertIsNone(_cli.risk_pause(None))

    def test_health_is_untouched_by_a_pause(self):
        # The whole point: a paused runtime is HEALTHY. The pause is a second fact, never a downgrade.
        self.assertEqual(_cli.health_verdict(_entry("CLOSED", [_CAP])), "healthy")

    def test_describe_pause_quotes_the_gate(self):
        line = _cli.describe_pause(_cli.risk_pause(_entry("CLOSED", [_CAP])))
        self.assertEqual(line, "CLOSED — Max Entries/Day: Max entries: 4/4 entries today (resets at 00:00 UTC)")
        self.assertEqual(_cli.describe_pause(None), "")


_RECIPE = """\
name: spider-main
group: spider
# a comment the hash must ignore
description: >
  SOL alpha hunter
strategy:
  wallet: "${SPIDER_WALLET}"
  slots: 2
exit:
  dsl_preset: balanced
"""


# Pinned IDENTICALLY in senpi-trading-runtime `src/runtime/__tests__/runtime-descriptor.test.ts`
# (`AUTHORED` / `PARITY_DIGEST`). The runtime publishes `recipeHash` over its stored recipe and
# `config_drift` compares it with `recipe_hash` over the file on disk: widen the drop regex or change
# the join on ONE side and every runtime in the fleet reads "✎ an edit that was never applied". Both
# constants must move together, so both suites assert the same digest.
_PARITY_TEXT = ('name: kodiak-main\ngroup: kodiak\n# a comment the hash must ignore\ndescription: >\n'
                '  SOL alpha hunter\nstrategy:\n  wallet: "${KODIAK_WALLET}"\n  slots: 2   \n\nexit:\n'
                '  dsl_preset: balanced\n')
_PARITY_DIGEST = "0ee8ddd8870febccca803a23bb959b98556dbbd93fc26fbeb80af522600fbbb4"


class TestRecipeHashAndDrift(unittest.TestCase):
    def test_recipe_hash_matches_the_digest_the_runtime_pins(self):
        self.assertEqual(_cli.recipe_hash(_PARITY_TEXT), _PARITY_DIGEST)

    def test_hash_ignores_comments_blank_lines_trailing_space_and_the_wallet_line(self):
        rendered = _RECIPE.replace('"${SPIDER_WALLET}"', "0xabc").replace("# a comment the hash must ignore\n", "")
        rendered = rendered.replace("slots: 2", "slots: 2   ") + "\n\n"
        self.assertEqual(_cli.recipe_hash(_RECIPE), _cli.recipe_hash(rendered))

    def test_hash_changes_with_the_recipe(self):
        self.assertNotEqual(_cli.recipe_hash(_RECIPE), _cli.recipe_hash(_RECIPE.replace("slots: 2", "slots: 3")))

    def _doc(self, text):
        import yaml
        return yaml.safe_load(text)

    def test_named_preset_and_description_drift_are_seen(self):
        desc = {"name": "spider-main", "dslPreset": "balanced", "description": "SOL alpha hunter"}
        edited = _RECIPE.replace("dsl_preset: balanced", "dsl_preset: scalp").replace("SOL alpha hunter", "SOL scalper")
        diffs = _cli.config_drift(desc, edited, self._doc(edited))
        self.assertEqual([d[0] for d in diffs], ["dsl_preset", "description"])
        self.assertEqual(diffs[0][1:], ("balanced", "scalp"))

    def test_agreeing_recipe_has_no_drift_and_no_descriptor_cannot_compare(self):
        desc = {"name": "spider-main", "dslPreset": "balanced", "description": "SOL alpha hunter"}
        self.assertEqual(_cli.config_drift(desc, _RECIPE, self._doc(_RECIPE)), [])
        self.assertIsNone(_cli.config_drift(None, _RECIPE, self._doc(_RECIPE)))

    def test_recipe_hash_from_the_runtime_wins_over_the_weak_fields(self):
        # An inline-ladder edit under an unchanged preset name is invisible to the name/description
        # compare; a runtime that publishes `recipeHash` makes it visible.
        desc = {"name": "spider-main", "dslPreset": "balanced", "description": "SOL alpha hunter",
                "recipeHash": _cli.recipe_hash(_RECIPE)}
        edited = _RECIPE.replace("slots: 2", "slots: 3")
        diffs = _cli.config_drift(desc, edited, self._doc(edited))
        self.assertEqual([d[0] for d in diffs], ["recipe"])
        self.assertEqual(_cli.config_drift(desc, _RECIPE, self._doc(_RECIPE)), [])


def _row(**over):
    row = {"package": "spider", "is_pkg": True, "name": "spider", "name_source": "strategyName",
           "strategyId": "sid-0001", "wallet": "0xtest000000000000000000000000000000test1",
           "status": "ACTIVE", "funded": "$300", "positions": 2, "runtime": "spider-main",
           "health": "healthy", "paused": None, "config_drift": None}
    row.update(over)
    return row


def _render(rows):
    """status.py's default text output for `rows`, with `build` and the MCP client patched out."""
    orig_build, orig_client = status.build, status.MCPClient
    status.build = lambda *a, **k: (list(rows), [], True)
    status.MCPClient = lambda *a, **k: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = status.main(["status.py"])
    finally:
        status.build, status.MCPClient = orig_build, orig_client
    assert rc == 0, rc
    return buf.getvalue()


class TestTextRender(unittest.TestCase):
    def test_paused_row_carries_the_hold_and_the_section_quotes_the_gate(self):
        out = _render([_row(paused=_cli.risk_pause(_entry("CLOSED", [_CAP])))])
        line = next(ln for ln in out.splitlines() if "0xtest0000" in ln)
        self.assertIn("✅ healthy", line)                    # health is not downgraded…
        self.assertIn("⏸ CLOSED", line)                     # …and the hold is on the same line
        self.assertIn("1 paused by a risk gate", out)
        self.assertIn("Max Entries/Day: Max entries: 4/4 entries today (resets at 00:00 UTC)", out)
        self.assertIn("Never close/redeploy to clear it", out)

    def test_drift_section_names_the_field_and_the_in_place_fix(self):
        drift = {"diffs": [{"field": "dsl_preset", "running": "balanced", "on_disk": "scalp"}],
                 "dir": "/data/workspace/strategies/spider", "package_dir": "/data/workspace/strategies/spider"}
        out = _render([_row(config_drift=drift)])
        self.assertIn("never applied", out)
        self.assertIn("dsl_preset running 'balanced', on disk 'scalp'", out)
        self.assertIn("deploy.py update /data/workspace/strategies/spider --id spider-main", out)
        self.assertNotIn("close.py", out.split("never applied", 1)[1].split("\n\n")[0])

    def test_quiet_rows_print_neither_section(self):
        out = _render([_row()])
        self.assertNotIn("⏸", out)
        self.assertNotIn("✎", out)


class TestBuildWiring(unittest.TestCase):
    """`build(deep=True)` reads the fleet-wide status map ONCE and the descriptors ONCE, and each
    row carries `paused` / `config_drift` off them."""

    def test_deep_build_attaches_pause_from_the_fleet_status_map(self):
        wallet = "0xtest000000000000000000000000000000test1"
        payload = {"strategyId": "sid-0001", "status": "ACTIVE", "strategyWalletAddress": wallet,
                   "totalFunded": 300, "strategyName": "spider",
                   "strategyMetadata": {"skillName": "spider"}}
        saved = (_cli.list_strategies_or_none, _cli.list_runtimes, status._openclaw_available,
                 _cli.runtime_health_map, _cli.runtime_descriptors)
        calls = {"health": 0, "desc": 0}

        def health_map(*a, **k):
            calls["health"] += 1
            return {"spider-main": _entry("CLOSED", [_CAP])}

        def descriptors(*a, **k):
            calls["desc"] += 1
            return {}
        _cli.list_strategies_or_none = lambda *a, **k: [payload]
        _cli.list_runtimes = lambda *a, **k: [{"name": "spider-main", "wallet": wallet,
                                              "status": "running", "source": None}]
        status._openclaw_available = lambda: True
        _cli.runtime_health_map, _cli.runtime_descriptors = health_map, descriptors
        try:
            rows, _orphans, _ok = status.build(None, deep=True)
        finally:
            (_cli.list_strategies_or_none, _cli.list_runtimes, status._openclaw_available,
             _cli.runtime_health_map, _cli.runtime_descriptors) = saved
        self.assertEqual((calls["health"], calls["desc"]), (1, 1))
        self.assertEqual(rows[0]["health"], "healthy")
        self.assertEqual(rows[0]["paused"]["eligibility"], "CLOSED")
        self.assertIsNone(rows[0]["config_drift"])      # no descriptor → cannot compare → nothing claimed


if __name__ == "__main__":
    unittest.main()
