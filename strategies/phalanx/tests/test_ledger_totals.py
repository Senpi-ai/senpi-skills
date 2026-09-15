"""The hit-rate ledger keeps lifetime totals and logs every evaluation, so the daily report can
read whether a template keeps its edge after 20 signals, not only the rolling window."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scoring  # noqa: E402


def test_an_evaluated_signal_counts_toward_the_totals_and_is_logged(capsys):
    acc = scoring.new_accuracy_state()
    scoring.add_pending_signal(acc, "crypto", "BTC", "LONG", 100.0, 0.0)
    scoring.update_accuracy(acc, "crypto", scoring.EVAL_DELAY_S + 1.0, lambda asset: 101.0)
    assert acc["crypto"]["evaluated"] == 1 and acc["crypto"]["hits"] == 1
    assert "[sm-ledger] EVAL crypto BTC LONG hit" in capsys.readouterr().err


def test_a_state_persisted_before_the_totals_still_evaluates():
    acc = scoring.new_accuracy_state()
    for c in acc.values():
        c.pop("evaluated"); c.pop("hits")            # a record written by an older engine
    scoring.add_pending_signal(acc, "xyz", "xyz:NVDA", "SHORT", 100.0, 0.0)
    scoring.update_accuracy(acc, "xyz", scoring.EVAL_DELAY_S + 1.0, lambda asset: 102.0)
    assert acc["xyz"]["evaluated"] == 1 and acc["xyz"]["hits"] == 0
