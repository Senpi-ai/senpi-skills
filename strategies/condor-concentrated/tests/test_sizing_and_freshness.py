"""Sizing and signal freshness for condor-concentrated — and the history behind both.

This file replaces the copy inherited from condor, whose sizing assertion this package
deliberately breaks. Keeping the inherited assertions would have been wrong; deleting them
silently would have been worse. So the reasons are recorded here.

WHAT HAPPENED TO CONDOR (#668, condor 1.3.0, 2026-09-17). condor originally put 50-80% of
the account into one 10x position. A 30-day live read found a median drawdown of 10.6%
across 34 wallets, with one wallet at -99%. The fix divided its score tiers by 2.5 to
20/28/32% and pinned the result with `margin_pct/100 * 12 <= 4.0` — one stop must not cost
more than ~4% of the account.

WHY THIS PACKAGE IS AT 80% ANYWAY. Two reasons, and the second is the one that matters:

  1. Jason asked for it explicitly (2026-10-12) having been shown the arithmetic: at 80%
     and 10x the 15% ROE stop costs ~12% of the wallet, so the 25% halt absorbs two.
  2. The catalog ALREADY lists `wild-condor` at 90% margin / 10x / 15% ROE — 13.5% per
     stop — and it was last tuned 2026-10-01, AFTER #668. So the ~4% invariant is condor's
     own, not a house rule, and this package is strictly less aggressive than a sibling
     that is already deployable.

So the sizing here is a reviewed choice sitting between condor (4.8%/stop) and wild-condor
(13.5%/stop), not an unwitting regression. The test below pins the arithmetic so that if
either the margin or the stop moves, the risk copy on the card has to move with it.

WHAT CARRIES OVER UNCHANGED. The other half of #668 was freshness: 48 of 110 opens fired
in the 00:00-00:59 UTC hour, 69% of them on a setup first blocked ~2.6h earlier, because
the daily entry cap reset at midnight while a signal stayed valid for 1,800s. Those entries
won 36% of the time (median -7.2%) against 56% (+2.9%) at every other hour. The fix —
validity 300s under a 180s tick, so an entry is the setup the scanner just saw — is kept
here verbatim and asserted below.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"].endswith("_signals"))
GUARDS = RUNTIME["risk"]["guard_rails"]
STOP_ROE = RUNTIME["exit"]["dsl_preset"]["phase1"]["max_loss_pct"]


def test_the_sizing_is_flat_and_the_cost_of_a_stop_is_pinned():
    tiers = SIGNALS["inputs"]["leverageTiers"]
    assert [t[2] for t in tiers] == [80], "sizing is meant to be FLAT 80% above the floor"
    assert SIGNALS["inputs"]["marginPct"] == 80, "the fallback must match the single tier"
    cost = 80 / 100 * STOP_ROE
    assert abs(cost - 12.0) < 0.01, (
        f"one stop now costs {cost:.1f}% of the account, not 12% — the card, the manifest "
        "header and this file all quote 12%, so they must be updated together")


def test_it_is_less_aggressive_than_the_wild_sibling_already_in_the_catalog():
    """The precedent that makes 80% a reviewed choice rather than a regression. If
    wild-condor is ever cut below this, that argument is gone and this should be revisited."""
    wild = yaml.safe_load(open(os.path.join(HERE, "..", "..", "wild-condor", "main",
                                            "runtime.yaml"), encoding="utf-8"))
    wild_signals = next(s for s in wild["scanners"] if s["name"].endswith("_signals"))
    wild_margin = max(t[2] for t in wild_signals["inputs"]["leverageTiers"])
    assert wild_margin >= 80, (
        f"wild-condor is now at {wild_margin}% margin, at or below this package's 80%. The "
        "justification for 80% here was that a MORE aggressive condor variant was already "
        "listed — re-check this package's sizing against #668's ~4%-per-stop invariant.")


def test_the_halt_still_absorbs_more_than_one_stop():
    absorbed = GUARDS["drawdown_halt_pct"] / (80 / 100 * STOP_ROE)
    assert absorbed >= 2.0, (
        f"the drawdown halt absorbs {absorbed:.1f} stops — under two, the first loss of the "
        "day ends the day on a one-trade-per-day strategy")
    assert GUARDS["drawdown_reset_on_day_rollover"] is False


def test_a_signal_is_only_good_for_the_tick_that_made_it():
    """The other half of #668, kept verbatim."""
    assert SIGNALS["default_signal_validity_seconds"] == 300
    assert SIGNALS["interval_seconds"] == 180              # under two ticks


def test_it_still_takes_one_concentrated_trade_a_day():
    assert SIGNALS["inputs"]["maxPositions"] == 1
    assert GUARDS["max_entries_per_day"] == 1
    assert all(t[1] == 10 for t in SIGNALS["inputs"]["leverageTiers"])   # leverage unchanged
