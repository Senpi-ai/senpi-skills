"""cuckoo scoring regression — position_notional shape handling + symbol case preservation.

Guards the two silent-failure classes behind PR #501 and the fleet casing sweep:

  1. position_notional() must resolve USD notional for BOTH position shapes — the
     leaderboard shape (`notional_size`) and the clearinghouse shape
     (`szi`*`entryPx`) — and must NEVER collapse to the raw token count (that bug
     ranked a $345K meme dust position over a $112M ETH short).

  2. Coin symbols are CASE-SENSITIVE on Hyperliquid (kPEPE/kSHIB/kBONK, xyz:).
     position_asset() and tally_consensus() must keep the EMITTED asset in its
     original case. tally_consensus may upper-case the (asset,direction) dedup
     KEY, but the stored/emitted asset must stay case-preserved.

Runnable two ways:
    pytest strategies/cuckoo/tests/test_scoring_casing.py
    python3 strategies/cuckoo/tests/test_scoring_casing.py     # no pytest needed (CI)
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCORING = ROOT / "main" / "scanners" / "scoring.py"


def _load():
    scanners = str(SCORING.parent)
    sys.path.insert(0, scanners)
    try:
        spec = importlib.util.spec_from_file_location("cuckoo_scoring", SCORING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(scanners)


S = _load()


# ---- position_notional: both shapes resolve to USD, never token count ----

def test_notional_leaderboard_shape_uses_notional_size():
    pos = {"market": "ETH", "direction": "short", "size": -59267.5639,
           "entry_price": 1869.4, "notional_size": 112270546.30}
    assert abs(S.position_notional(pos) - 112270546.30) < 1e-3


def test_notional_clearinghouse_shape_uses_szi_times_entry():
    pos = {"szi": -184160374.0, "entryPx": 0.001634}
    assert abs(S.position_notional(pos) - abs(-184160374.0 * 0.001634)) < 1e-3


def test_notional_prefers_usd_over_token_count():
    # the exact mis-ranking bug: a huge token count / tiny USD must NOT outrank a
    # small token count / huge USD.
    meme = {"market": "PUMP", "size": 184160374.0, "notional_size": 345116.0}
    btc = {"market": "BTC", "size": 100.0, "notional_size": 10000000.0}
    assert S.position_notional(btc) > S.position_notional(meme)


def test_notional_entry_price_fallback():
    pos = {"size": 100.0, "entry_price": 50.0}   # no notional_size present
    assert abs(S.position_notional(pos) - 5000.0) < 1e-6


# ---- casing: the emitted symbol keeps its original case ----

def test_position_asset_preserves_case():
    assert S.position_asset({"coin": "kPEPE"}) == "kPEPE"
    assert S.position_asset({"market": "xyz:GOLD"}) == "xyz:GOLD"


def test_tally_consensus_preserves_asset_case_but_keys_upper():
    agg = S.tally_consensus([
        {"asset": "kPEPE", "direction": "LONG", "weight": 1.0},
        {"asset": "kpepe", "direction": "LONG", "weight": 2.0},  # same coin, other case
    ])
    # case variants collapse to ONE (asset,direction) bucket (dedup key upper-cased)
    assert len(agg) == 1
    rec = next(iter(agg.values()))
    assert rec["count"] == 2 and abs(rec["weight"] - 3.0) < 1e-9
    # ...but the STORED/emitted asset stays case-preserved (first-seen), not "KPEPE"
    assert rec["asset"] == "kPEPE"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
