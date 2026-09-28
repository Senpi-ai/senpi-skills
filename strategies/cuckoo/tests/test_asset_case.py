"""Cuckoo upper-cased the consensus asset, so k-prefixed and HIP-3 names were
emitted in a form Hyperliquid rejects.

`_coin_of()` deliberately preserves the upstream symbol and says so: "the emitted
symbol goes straight into a Senpi tool call and Hyperliquid coin names are
CASE-SENSITIVE (kPEPE / kSHIB / kBONK are rejected as `KPEPE`; HIP-3 prefixes are
lowercase `xyz:`). Callers needing a case-insensitive COMPARISON upper-case at the
comparison site instead."

`tally_consensus()` then upper-cased the value it aggregates, and that value is what
reaches the signal — so every kPEPE vote emitted as KPEPE and every xyz: name as XYZ:.
Observed live on a user's book: KPEPE re-emitted every ~30s for days, rejected each
time with `Unknown coin "KPEPE" on the main Hyperliquid dex`, never filling.

The key stays upper-cased so votes differing only by case still merge.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scoring  # noqa: E402


def test_k_prefixed_coin_keeps_its_case():
    agg = scoring.tally_consensus([{"asset": "kPEPE", "direction": "LONG", "weight": 1.0}])
    assert [r["asset"] for r in agg.values()] == ["kPEPE"]


def test_hip3_prefix_stays_lowercase():
    agg = scoring.tally_consensus([{"asset": "xyz:GOLD", "direction": "LONG", "weight": 1.0}])
    assert [r["asset"] for r in agg.values()] == ["xyz:GOLD"]


def test_votes_differing_only_by_case_still_merge():
    agg = scoring.tally_consensus([
        {"asset": "kPEPE", "direction": "LONG", "weight": 1.0},
        {"asset": "KPEPE", "direction": "LONG", "weight": 2.0},
    ])
    assert len(agg) == 1
    rec = next(iter(agg.values()))
    assert rec["count"] == 2 and rec["weight"] == 3.0


def test_plain_uppercase_coin_is_unchanged():
    agg = scoring.tally_consensus([{"asset": "BTC", "direction": "SHORT", "weight": 1.0}])
    assert [r["asset"] for r in agg.values()] == ["BTC"]
