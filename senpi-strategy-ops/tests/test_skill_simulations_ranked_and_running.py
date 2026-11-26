"""A user's "best" simulations are ranked by return unless they name another measure, and a simulation
is called running, never live: live means real money. Agent chat once answered a batch with "Both
simulations are live" over a "🟢 Live" column, and the box's own reads answer `live` for a running
simulation."""

import re
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
RAW = (OPS / "SKILL.md").read_text()
SKILL = " ".join(RAW.split())
RULE = SKILL[SKILL.find("**Simulations: running, ranked by return.**"):]
RULE = RULE[:RULE.find(" `deploy.py` polls")]


class BestMeansReturn(unittest.TestCase):
    def test_the_rule_exists(self):
        self.assertTrue(RULE.startswith("**Simulations: running, ranked by return.**"), RULE[:80])

    def test_best_ranks_by_return_by_default(self):
        self.assertIn('"Show my best simulations" ranks them by return', RULE)

    def test_a_named_measure_ranks_by_that_measure(self):
        self.assertIn("unless the user names another measure", RULE)
        for measure in ("Sharpe ratio", "drawdown", "win rate", "trade count"):
            self.assertIn(measure, RULE)
        self.assertIn("rank by that one", RULE)

    def test_the_answer_names_the_measure_it_ranked_by(self):
        self.assertIn("say which measure ranks them", RULE)

    def test_a_status_of_several_ranks_best_return_first(self):
        self.assertIn('"How are my simulations doing?" and any status of several rank the same way, best first.', RULE)

    def test_return_is_quoted_from_senpi_and_a_null_goes_last(self):
        self.assertIn("Return is each one's `simulation_metrics.return_pct` from "
                      "`strategy_get_pnl_and_account_value_history` (`strategy_wallet` = its wallet), "
                      "quoted as written, never computed; a null one goes last with its `not_available` reason.", RULE)
        self.assertNotIn("simulation_list", RULE)

class SimulationsAreRunningNotLive(unittest.TestCase):
    def test_a_simulation_is_running_and_live_means_real_money(self):
        self.assertIn('call a running simulation "running", never "live": "live" means real money', RULE)

    def test_box_reads_that_answer_live_are_reported_as_running(self):
        self.assertIn("the batch's `phase`", RULE)
        self.assertIn("`deploy.py`'s `overall`", RULE)
        self.assertIn('say "running"', RULE)

    def test_the_batch_report_and_status_table_are_covered(self):
        self.assertIn("the message after a batch starts", RULE)
        self.assertIn("a status column", RULE)


class VersionBumped(unittest.TestCase):
    def test_skill_and_readme_versions_agree_and_moved(self):
        version = re.search(r'^  version: "([0-9.]+)"', RAW, re.M).group(1)
        readme = (OPS.parent / "README.md").read_text()
        self.assertIn(f"| [`senpi-strategy-ops`](senpi-strategy-ops/) | {version} |", readme)
        self.assertNotEqual(version, "3.34.16")


if __name__ == "__main__":
    unittest.main()
