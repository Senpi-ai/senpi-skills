"""A partial pool read is not a reading, and for this scanner that is worse than it sounds.

Jackal fires on positions that are NEW since the last tick. A wallet missing from the map does not
look unchanged — it looks like it closed everything. Saved as the baseline, every position it really
holds then looks newly opened on the next full read, and `maxEntryAgeSeconds` throws all of them away
for being old. The scanner goes quiet for good and reports "no fresh pool-member entries", which is a
read that failed wearing the words of a market with nothing in it.

Observed live on 2026-09-18: 126 consecutive ticks of that line while
`discovery_get_trader_state` was degraded. Same guard as `_cohort_headcount` in whalehunter /
phalanx / athena / starling / pilotfish (#658), which this scanner was missed by because it spells
the function differently.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

POOL = [{"address": f"0x{i:040x}"} for i in range(60)]        # two batches of 50


def _reader(fail_batch):
    seen = {"n": 0}

    def _read(ctx, name, args):
        if name != "discovery_get_trader_state":
            return None
        i = seen["n"]
        seen["n"] += 1
        if i == fail_batch:
            return None                                        # the degraded batch
        return {"data": {"traders": [{"address": a, "openPositions": [{"coin": "ETH", "szi": "-1"}]}
                                     for a in args["trader_addresses"]]}}
    return _read


def test_a_failed_batch_means_no_pool_reading_this_tick(monkeypatch):
    monkeypatch.setattr(scan, "_read", _reader(fail_batch=1))
    assert scan._fetch_pool_positions(types.SimpleNamespace(), POOL) is None


def test_a_full_read_returns_every_wallet(monkeypatch):
    monkeypatch.setattr(scan, "_read", _reader(fail_batch=None))
    got = scan._fetch_pool_positions(types.SimpleNamespace(), POOL)
    assert got is not None and len(got) == 60
    assert all(v == [{"coin": "ETH", "szi": "-1"}] for v in got.values())


def test_the_partial_map_is_never_returned_as_a_reading(monkeypatch):
    """The failure that matters is not the missing wallets — it is the 50 that DID read being
    mistaken for the whole pool, because that map becomes the next tick's baseline."""
    monkeypatch.setattr(scan, "_read", _reader(fail_batch=0))
    got = scan._fetch_pool_positions(types.SimpleNamespace(), POOL)
    assert got is not None or got is None          # explicit: we accept only None here
    assert got is None, "a 50-of-60 map was returned as if it were the pool"
