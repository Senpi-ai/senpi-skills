"""A simulation taken live goes live from the package it ran, with the settings it ran with: a
variation run at leverage 3 must not go live as the strategy's default 5."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
RAW = (OPS / "SKILL.md").read_text()
SKILL = " ".join(RAW.split())
TAKE_LIVE = SKILL[SKILL.find("**Taking a simulation live**"):SKILL.find("**Running variations** (")]


class TakingASimulationLiveKeepsItsSettings(unittest.TestCase):
    def test_the_settings_are_read_from_the_simulation(self):
        self.assertIn(
            "deploy.py call senpi.packages.get --params '{\"simulationId\":\"simulation-<id>\"}'",
            TAKE_LIVE)
        self.assertIn("its `strategyId` and `settings`", TAKE_LIVE)

    def test_the_confirm_names_the_simulation_settings_not_the_defaults(self):
        self.assertIn("the settings that go live, read from that answer", TAKE_LIVE)
        self.assertIn("never the strategy's defaults", TAKE_LIVE)
        self.assertIn("leverage 3", TAKE_LIVE)

    def test_the_walkthrough_offers_no_levers_since_it_goes_live_as_it_ran(self):
        self.assertIn("with no levers and no name line, since it goes live as it ran", TAKE_LIVE)

    def test_the_deploy_carries_the_simulation_id(self):
        self.assertIn("--from-simulation simulation-<id>", TAKE_LIVE)

    def test_the_package_that_goes_live_is_the_simulations_own(self):
        self.assertIn("goes live from the package that simulation ran", TAKE_LIVE)
        self.assertIn("never choose a strategy to go live in its place", TAKE_LIVE)
        self.assertIn("relay the deploy's line saying it takes the simulation live from its own package",
                      TAKE_LIVE)

    def test_a_package_refusal_stops_without_a_catalog_fallback(self):
        for code in ("E_PACKAGE_NOT_FOUND", "E_SIMULATION_PACKAGE_NOT_FOUND", "SIMULATION_PACKAGE_MISMATCH"):
            self.assertIn(f"`{code}`", TAKE_LIVE)
        self.assertIn("never drop `--from-simulation` or deploy the strategy as it is unasked", TAKE_LIVE)

    def test_the_budget_is_still_asked_in_real_money(self):
        self.assertIn("never reuse the simulation's", TAKE_LIVE)


class VersionIsBumped(unittest.TestCase):
    def test_skill_and_readme_agree_on_a_version_past_the_base(self):
        version = re.search(r'^\s*version: "([0-9.]+)"', RAW, re.M).group(1)
        self.assertGreater(tuple(map(int, version.split("."))), (3, 34, 16))
        readme = (OPS.parent / "README.md").read_text()
        self.assertIn(f"[`senpi-strategy-ops`](senpi-strategy-ops/) | {version} |", readme)


if __name__ == "__main__":
    unittest.main()
