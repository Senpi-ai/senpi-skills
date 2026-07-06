"""cuttlefish fidelity — divergence scoring, tide gating, close triggers (pure scoring.py).

Run: python3 strategies/cuttlefish/tests/test_scoring_and_triggers.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "long", "scanners"))

import scoring  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


def candles(n, start, drift, vol=100.0):
    out, px = [], start
    for i in range(n):
        o = px
        px *= (1 + drift)
        out.append({"t": i, "o": str(o), "c": str(px), "h": str(max(o, px)),
                    "l": str(min(o, px)), "v": str(vol), "n": 5})
    return out


INPUTS = {
    "weights": {"smLean": 2.5, "divergence": 3.0, "trend4h": 1.5, "align1h": 1.0,
                "mom24h": 1.0, "volRatio": 0.5, "regimeBonus": 0.5},
    "minScore": 5.5, "goodScore": 6.5, "apexScore": 8.0, "exitScore": 3.5,
    "leverageTiers": {"apex": 6, "good": 5, "base": 4},
    "marginPctTiers": {"apex": 22, "good": 17, "base": 12},
    "tideFlipConfirmTicks": 2,
}

# ── tide ──
up1, up4 = candles(30, 100, 0.004), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)
tide, _ = scoring.tide_from_btc(up1, up4)
check("tide bull", tide == "BULL")
tide, _ = scoring.tide_from_btc(dn1, dn4)
check("tide bear", tide == "BEAR")
check("long allowed in BULL", scoring.tide_allows("LONG", "BULL"))
check("long allowed in MIXED", scoring.tide_allows("LONG", "MIXED"))
check("long blocked in BEAR", not scoring.tide_allows("LONG", "BEAR"))
check("short blocked in BULL", not scoring.tide_allows("SHORT", "BULL"))

# ── divergence core: SM LONG + crowd contra (funding<=0) + price contra (24h down) ──
sm_long = {"direction": "LONG", "pct": 75}
weak24 = candles(30, 100, -0.002)          # drifting down — SM accumulating into weakness
th = scoring.score_asset("SOL", "LONG", weak24, candles(10, 100, 0.002),
                         {"funding": -0.00002}, sm_long, "SHORT_CROWDED", INPUTS)
check("divergence thesis scores", th is not None and th["score"] >= 5.5)
check("divergence component full", th["components"]["divergence"] == 3.0)
check("squeeze bonus applied", th["components"]["regimeBonus"] == 0.5)

# SM hard block: smart money >=58% AGAINST the book zeroes the asset
th_block = scoring.score_asset("SOL", "LONG", up1, up4, {"funding": 0.0},
                               {"direction": "SHORT", "pct": 70}, None, INPUTS)
check("sm hard block", th_block["blocked"] == "sm_hard_block" and th_block["score"] == 0.0)

# trend-following case: SM long + everything up still clears (trend legs carry it)
th_up = scoring.score_asset("ETH", "LONG", up1, up4, {"funding": 0.00001},
                            sm_long, None, INPUTS)
check("aligned-trend case scores", th_up is not None and th_up["score"] > 0)
check("no divergence credit when crowd+price agree",
      th_up["components"]["divergence"] == 0.0)

# ── bands + venue clamp ──
check("band apex", scoring.band_for(8.5, INPUTS) == "apex")
check("band base", scoring.band_for(5.6, INPUTS) == "base")
check("band none", scoring.band_for(4.0, INPUTS) is None)
lev, mgn = scoring.sizing_for("apex", INPUTS, venue_max=3)
check("venue clamp", lev == 3 and mgn == 22)
lev, _ = scoring.sizing_for("apex", INPUTS, venue_max=None)
check("no venue -> desired", lev == 6)

# ── close triggers ──
held = [{"asset": "SOL", "direction": "LONG"}, {"asset": "ETH", "direction": "LONG"}]
scored = {"SOL": {"score": 6.0, "sm_dir": "LONG", "sm_pct": 70},
          "ETH": {"score": 2.0, "sm_dir": "NEUTRAL", "sm_pct": 50}}

# tide flip needs the confirm streak
sigs = scoring.close_triggers("LONG", "BEAR", 1, held, scored, INPUTS, False)
check("flip unconfirmed -> no closes", sigs == [])
sigs = scoring.close_triggers("LONG", "BEAR", 2, held, scored, INPUTS, False)
check("flip confirmed -> close book",
      len(sigs) == 2 and all(s["trigger"] == "tide_flip" for s in sigs))

# divergence reversal closes the name even mid-refresh-window
scored_rev = dict(scored, SOL={"score": 6.0, "sm_dir": "SHORT", "sm_pct": 65})
sigs = scoring.close_triggers("LONG", "BULL", 0, held, scored_rev, INPUTS, False)
check("sm reversal closes SOL",
      len(sigs) == 1 and sigs[0]["asset"] == "SOL" and sigs[0]["trigger"] == "divergence_reversed")

# basket refresh recycles the stale-thesis laggard only when due
sigs = scoring.close_triggers("LONG", "BULL", 0, held, scored, INPUTS, False)
check("laggard kept before refresh", sigs == [])
sigs = scoring.close_triggers("LONG", "BULL", 0, held, scored, INPUTS, True)
check("refresh recycles ETH",
      len(sigs) == 1 and sigs[0]["asset"] == "ETH" and sigs[0]["trigger"] == "basket_refresh")

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
