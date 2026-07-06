"""Rotation-Rider engine fidelity (pure scoring.py) — shared by Gibbon & Orangutan.

Reuses the allocator engine for universe/pulse/entry/sizing; the posture is the
sector-rotation variant (long strong-sector leaders, short weak-sector laggards,
sized by dispersion).

Run: python3 strategies/gibbon/tests/test_engine.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "main", "scanners"))

import scoring  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


def candles(n, start, drift):
    out, px = [], start
    for i in range(n):
        o = px
        px *= (1 + drift)
        out.append({"o": str(o), "c": str(px), "h": str(max(o, px)), "l": str(min(o, px)),
                    "v": "100", "n": 5})
    return out


INPUTS = {"minScore": 5.5, "goodScore": 6.5, "apexScore": 8.0,
          "leverageTiers": {"apex": 5, "good": 4, "base": 3},
          "marginPctTiers": {"apex": 14, "good": 10, "base": 7},
          "groupThreshold": 0.5, "broadSizeScale": 0.5, "mixedSizeScale": 0.6}

# shared engine still present + working
check("derive_universe present", callable(scoring.derive_universe))
check("score_candidate present", callable(scoring.score_candidate))
up1, up4 = candles(30, 100, 0.004), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)
check("long confirmed on uptrend", (scoring.score_candidate("NVDA", "LONG", up1, up4, {"available": False}, {}, INPUTS) or {}).get("score", 0) >= 5.5)
check("long rejected on downtrend", scoring.score_candidate("NVDA", "LONG", dn1, dn4, {"available": False}, {}, INPUTS) is None)

# ── rotation posture: sectors ranked; ride winners, fade losers ──
# semis logic strong (+3), semis memory weak (-4), software mild (+1), crypto flat (~0)
POOL = [
    {"name": "xyz:AMD", "dex": "xyz", "chg": 4.9, "class": "risk"},   # semis (logic) — winner
    {"name": "xyz:AVGO", "dex": "xyz", "chg": 2.0, "class": "risk"},  # semis
    {"name": "xyz:MU", "dex": "xyz", "chg": -3.7, "class": "risk"},   # semis (memory) — but same group...
    {"name": "xyz:META", "dex": "xyz", "chg": 1.6, "class": "risk"},  # software
    {"name": "xyz:GOLD", "dex": "xyz", "chg": -0.3, "class": "defensive"},  # commodities — weak
    {"name": "xyz:SILVER", "dex": "xyz", "chg": -1.9, "class": "defensive"},
    {"name": "BTC", "dex": "", "chg": 1.7, "class": "risk"},
    {"name": "SOL", "dex": "", "chg": 1.2, "class": "risk"},
]
# group averages: semis strong, commodities weak, crypto mild-strong, software mild
gavg = {"semis": 3.5, "megacap_software": 1.6, "crypto": 1.4, "commodities": -1.1}
pulse_rot = {"group_avgs": gavg, "dispersion": "rotation"}
pulse_broad = {"group_avgs": gavg, "dispersion": "broad"}

p = scoring.build_posture(POOL, pulse_rot, "LONG_CROWDED", {"available": False}, {}, INPUTS, 100.0)
check("rotation stance", p["stance"] == "ROTATION" and p["size_scale"] == 1.0)
check("longs come from STRONG sectors (semis/software/crypto)",
      any(n in p["longs"] for n in ("xyz:AMD", "xyz:AVGO", "xyz:META", "BTC")))
check("longs exclude weak-sector names (commodities)",
      "xyz:GOLD" not in p["longs"] and "xyz:SILVER" not in p["longs"])
check("shorts come from WEAK sectors (commodities down)",
      "xyz:SILVER" in p["shorts"] or "xyz:GOLD" in p["shorts"])
check("winning-sector leader ranks first long", p["longs"][0] == "xyz:AMD")
check("posture narrative names strongest/weakest", "strongest" in p["narrative"] and "semis" in p["narrative"])

pb = scoring.build_posture(POOL, pulse_broad, "LONG_CROWDED", {"available": False}, {}, INPUTS, 0.0)
check("broad day -> reduced size (dispersion edge absent)", pb["stance"] == "BROAD" and pb["size_scale"] == 0.5)

pn = scoring.build_posture(POOL, {"group_avgs": gavg, "dispersion": None}, "NEUTRAL", {"available": False}, {}, INPUTS, 0.0)
check("no dispersion read -> mixed size", pn["stance"] == "MIXED" and pn["size_scale"] == 0.6)

# crypto pool strength computed even if 'crypto' not in gavg
gavg2 = {"semis": 3.5, "commodities": -1.1}
POOL_UP = [{"name": "BTC", "dex": "", "chg": 3.0, "class": "risk"},
           {"name": "SOL", "dex": "", "chg": 2.0, "class": "risk"},
           {"name": "xyz:MU", "dex": "xyz", "chg": -4.0, "class": "risk"}]
p2 = scoring.build_posture(POOL_UP, {"group_avgs": gavg2, "dispersion": "rotation"},
                           "NEUTRAL", {"available": False}, {}, INPUTS, 0.0)
check("crypto strength derived from pool when absent from gavg", "BTC" in p2["longs"])

# sizing scaled by dispersion size
lev, mgn = scoring.sizing_for("apex", 0.5, INPUTS, venue_max=4)
check("broad-size margin scaled", lev == 4 and mgn == round(14 * 0.5, 2))

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
