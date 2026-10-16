"""A partial trader_state read is not a headcount. When one batch fails, its wallets vanish from the
count: on this tick that reads as the cohort leaving their positions, and saved as the baseline it reads
as growth on the next full read, which can open a trade nobody in the cohort made. Any failed batch now
means no headcount this tick, so the prior baseline holds (the empty-headcount path keeps it)."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "long", "scanners"))

import scan  # noqa: E402

COHORT = [f"0x{i:040x}" for i in range(60)]          # two batches at stateBatch=50


def _reader(fail_batch):
    def _read(ctx, tool, args, label):
        if label.endswith(f"(b{fail_batch})"):
            return None
        return [{"traderAddress": a, "openPositions": [{"coin": "ETH", "szi": "-1"}]}
                for a in args["trader_addresses"]]
    return _read


def test_a_failed_batch_means_no_headcount_this_tick(monkeypatch):
    monkeypatch.setattr(scan, "_read", _reader(fail_batch=1))
    assert scan._cohort_headcount(types.SimpleNamespace(), COHORT, {"stateBatch": 50}) == {}


def test_a_full_read_counts_every_wallet(monkeypatch):
    monkeypatch.setattr(scan, "_read", _reader(fail_batch=None))
    assert scan._cohort_headcount(types.SimpleNamespace(), COHORT, {"stateBatch": 50}) == {
        "ETH": {"long_n": 0, "short_n": 60, "raw_coin": "ETH"}}
