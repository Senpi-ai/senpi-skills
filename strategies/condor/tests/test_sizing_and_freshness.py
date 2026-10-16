"""Condor put 50-80% of the account into one 10x position, and often opened it on a stale setup.

30-day live read: median drawdown 10.6% across 34 wallets, one wallet -99%. At 10x, 50-80% margin is
5-8x the account in notional, so a single -12% stop costs 6-10% of equity — on a template whose whole
design is one position at a time.

48 of 110 opens fired between 00:00 and 00:59 UTC, 69% of them on a setup first blocked about 2.6h
earlier: the daily entry cap resets at UTC midnight and a signal stayed valid for 1,800s, so the first
tick after the reset took a trade the tape had already moved past. Those 00:xx entries won 36% of the
time (median -7.2%) against 56% (+2.9%) at every other hour.

Sizing is now 20-32% margin (the same score tiers, divided by 2.5) and a signal is good for 300s —
under two 180s ticks — so an entry is the setup the scanner just saw.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"].endswith("_signals"))


def test_one_stop_no_longer_costs_a_tenth_of_the_account():
    tiers = SIGNALS["inputs"]["leverageTiers"]
    assert [t[2] for t in tiers] == [32, 28, 20]          # was 80 / 70 / 50
    assert SIGNALS["inputs"]["marginPct"] == 20            # the fallback matches the lowest tier
    for _score, leverage, margin_pct in tiers:
        # a -12% move on margin, at this size, against the whole account
        assert margin_pct / 100 * 12 <= 4.0, (leverage, margin_pct)


def test_a_signal_is_only_good_for_the_tick_that_made_it():
    assert SIGNALS["default_signal_validity_seconds"] == 300
    assert SIGNALS["interval_seconds"] == 180              # under two ticks


def test_it_still_takes_one_concentrated_trade_a_day():
    assert SIGNALS["inputs"]["maxPositions"] == 1
    assert RUNTIME["risk"]["guard_rails"]["max_entries_per_day"] == 1
    assert all(t[1] == 10 for t in SIGNALS["inputs"]["leverageTiers"])   # leverage unchanged
