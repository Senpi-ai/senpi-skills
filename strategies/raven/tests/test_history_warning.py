"""Raven warned "verify the payload shape" at every wallet that had not traded yet.

`track_record` returns n=0 both when no closed trades came back and when rows came back
that it could not parse. Raven printed the same payload-shape WARNING for both, so every
fresh deploy logged it on its first scans — and a warning that fires on healthy wallets is
one nobody reads, which is how an actual parse failure would have slipped past.

Zero rows is now reported as what it is; the shape warning is reserved for rows that
arrived and did not parse. Run: python3 -m pytest strategies/raven/tests -q
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402


class _Ctx:
    wallet = "0xraven"


def _recalibrate_with(payload):
    """Drive _recalibrate with what the history read returned; capture stderr."""
    original = scan._read
    scan._read = lambda ctx, tool, args, label: payload
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = scan._recalibrate(_Ctx(), {"historyTrades": 40}, {}, 0)
    finally:
        scan._read = original
    return result, err.getvalue()


def test_a_wallet_with_no_closed_trades_yet_is_not_a_payload_defect():
    (_, _, stats, _), err = _recalibrate_with([])
    assert stats["n"] == 0
    assert "WARNING" not in err
    assert "no closed trades yet" in err


def test_rows_that_arrive_and_do_not_parse_still_warn_about_the_shape():
    (_, _, stats, _), err = _recalibrate_with([{"coin": "BTC"}, {"coin": "ETH"}])
    assert stats["n"] == 0
    assert "WARNING" in err
    assert "2 history rows returned but 0 parsed" in err
    assert "discovery_get_trader_history" in err


def test_an_unreadable_read_holds_calibration_and_claims_nothing():
    """None is "could not read", which is neither of the above — it must not be
    reported as an empty history, and it must not tune on the silence."""
    (_, _, stats, note), err = _recalibrate_with(None)
    assert stats["n"] == -1
    assert "holding calibration" in note
    assert "no closed trades yet" not in err and "WARNING" not in err


def test_real_rows_still_calibrate():
    (_, _, stats, _), err = _recalibrate_with([{"realizedPnl": 10}, {"realizedPnl": -4}])
    assert stats["n"] == 2 and stats["win_rate"] == 0.5
    assert err == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} RAVEN HISTORY TESTS PASS")
