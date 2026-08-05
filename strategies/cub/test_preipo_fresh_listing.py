"""CUB preipo — fresh-listing fast-path regression tests (pure, no I/O).

Proves (1) the normal 4h-confirmed path is UNCHANGED by the fast-path refactor, and (2) the
fresh-listing 1h starter path fires only when it should, at reduced size, and stays OFF by default.
Run: python3 strategies/cub/test_preipo_fresh_listing.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "preipo", "scanners"))
import scoring  # noqa: E402


def C(o, h, l, c):
    return {"o": o, "h": h, "l": l, "c": c, "v": 1000}


def bull(n, base=100):
    # strictly higher lows + higher highs -> trend_structure BULLISH
    return [C(base + i, base + 2 + i, base + i, base + 1 + i) for i in range(n)]


def bear(n, base=100):
    # strictly lower highs + lower lows -> trend_structure BEARISH
    return [C(base - i, base + 2 - i, base - 2 - i, base - 1 - i) for i in range(n)]


FRESH = {"freshListing": {"enabled": True, "minCandles1h": 6, "starterSizeFactor": 0.5, "maxRunPct": 25}}
OFF = {}  # no freshListing -> fast path disabled


def _assert(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise SystemExit(f"FAILED: {name}")


# 1) NORMAL 4h path unchanged: mature bullish name (8x1h + 6x4h), up since launch.
#    4h_bull(+3) + 1h_bull secondary(+1) + abs_up(+1) = 5 ; full size ; basis 4h.
t = scoring.score_thematic("xyz:NVDA", bull(8), bull(6), 0.0, 5.0, "LONG", OFF)
_assert("normal-4h score==5", t and t["score"] == 5)
_assert("normal-4h size_factor==1.0", t["size_factor"] == 1.0)
_assert("normal-4h basis==4h", t["basis"] == "4h")
# same inputs WITH freshListing enabled must be byte-identical (name has 4h history -> never fresh)
t2 = scoring.score_thematic("xyz:NVDA", bull(8), bull(6), 0.0, 5.0, "LONG", FRESH)
_assert("freshListing does not change a name that has 4h history", t2 == t)

# 2) FRESH path fires: just-listed name, 6x1h bullish, only 1x4h, up +5% since launch.
#    1h_bull(+3) + abs_up(+1) = 4 ; half size ; basis 1h ; clears minScore 4.
f = scoring.score_thematic("xyz:UNITREE", bull(6), bull(1), 0.0, 5.0, "LONG", FRESH)
_assert("fresh fires with score==4", f and f["score"] == 4)
_assert("fresh size_factor==0.5", f["size_factor"] == 0.5)
_assert("fresh basis==1h", f["basis"] == "1h")

# 3) OFF by default: same fresh name with no freshListing input -> None (waits for 4h history).
_assert("fresh OFF by default -> None",
        scoring.score_thematic("xyz:UNITREE", bull(6), bull(1), 0.0, 5.0, "LONG", OFF) is None)

# 4) Chase guard: fresh + already up big (+40% > maxRunPct 25) -> -2 -> score 2 (below minScore).
c = scoring.score_thematic("xyz:UNITREE", bull(6), bull(1), 0.0, 40.0, "LONG", FRESH)
_assert("chase guard drops a vertical fresh name below minScore", c and c["score"] == 2)

# 5) Fresh DUMP rejected: 1h bearish -> hard gate -> None (never buy a fresh downtrend).
_assert("fresh 1h-bearish -> None",
        scoring.score_thematic("xyz:UNITREE", bear(6), bull(1), 0.0, 5.0, "LONG", FRESH) is None)

# 6) LONG-only: a fresh SHORT never takes the fast path (fresh_on requires LONG) -> None.
_assert("fresh path is LONG-only (short returns None)",
        scoring.score_thematic("xyz:MU", bear(6), bear(1), 0.0, -5.0, "SHORT", FRESH) is None)
# and a mature SHORT is unaffected by freshListing.
s1 = scoring.score_thematic("xyz:MU", bear(8), bear(6), 0.0, -5.0, "SHORT", FRESH)
s2 = scoring.score_thematic("xyz:MU", bear(8), bear(6), 0.0, -5.0, "SHORT", OFF)
_assert("mature short unaffected by freshListing", s1 == s2 and s1 and s1["basis"] == "4h")

# 7) Too-fresh: fewer than minCandles1h 1h candles -> None even with fast path on.
_assert("below minCandles1h -> None",
        scoring.score_thematic("xyz:UNITREE", bull(4), bull(1), 0.0, 5.0, "LONG", FRESH) is None)

print("\nall fresh-listing tests passed")
