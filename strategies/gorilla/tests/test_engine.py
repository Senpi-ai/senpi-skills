"""Regime-Allocator engine fidelity (pure scoring.py) — shared by Chimp & Gorilla.

Run: python3 strategies/chimp/tests/test_engine.py
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
          "riskOffSizeScale": 0.7, "mixedSizeScale": 0.6}

# ── universe: BOTH dexes, vol floors, class tagging ──
main_rows = [{"name": "BTC", "vol": 900e6, "change_pct": 1.7},
             {"name": "SOL", "vol": 300e6, "change_pct": 1.2},
             {"name": "PUMP", "vol": 2e6, "change_pct": 5.0}]       # under main floor
xyz_rows = [{"name": "xyz:NVDA", "vol": 80e6, "change_pct": 2.0},
            {"name": "xyz:GOLD", "vol": 40e6, "change_pct": -0.3},
            {"name": "xyz:TINY", "vol": 1e6, "change_pct": 9.0}]    # under xyz floor
pool = scoring.derive_universe(main_rows, xyz_rows, dict(INPUTS,
       universeVolFloorUsd=25_000_000, xyzVolFloorUsd=3_000_000, maxMainNames=14, maxXyzNames=16))
names = {i["name"] for i in pool}
check("universe spans both dexes", {"BTC", "SOL", "xyz:NVDA", "xyz:GOLD"} <= names)
check("under-floor names dropped (both dexes)", "PUMP" not in names and "xyz:TINY" not in names)
check("xyz prefix preserved", any(i["name"] == "xyz:NVDA" and i["dex"] == "xyz" for i in pool))
check("class: GOLD defensive", scoring.classify("xyz:GOLD", INPUTS) == "defensive")
check("class: BTC risk", scoring.classify("BTC", INPUTS) == "risk")

# ── pulse: day + dispersion + checklist ──
GROUPS = {"crypto": ["BTC", "ETH"], "semis": ["NVDA", "AMD"], "indices": ["SP500"],
          "commodities": ["GOLD"], "macro_fx": ["DXY"]}
on = {"BTC": 2, "ETH": 3, "NVDA": 1.5, "AMD": 2.5, "SP500": 1.0, "GOLD": 0.2, "DXY": 0.1}
off = {"BTC": -2, "ETH": -3, "NVDA": -1.5, "AMD": -2.5, "SP500": -1.0, "GOLD": 0.5, "DXY": 0.9}
rot = {"BTC": 1.4, "ETH": 1.2, "NVDA": -4.0, "AMD": -4.6, "SP500": 0.4, "GOLD": -0.3, "DXY": 0.0}
check("risk_on day", scoring.pulse_stance(on, GROUPS, 15)["day"] == "risk_on")
check("risk_off day", scoring.pulse_stance(off, GROUPS, 28)["day"] == "risk_off")
p_off = scoring.pulse_stance(off, GROUPS, 28)
check("dxy funding-stress read", p_off["checklist"]["dxy"] == "dollar bid — funding stress")
p_rot = scoring.pulse_stance(rot, GROUPS, 20)
check("rotation dispersion (mixed day)", p_rot["day"] == "mixed" and p_rot["dispersion"] == "rotation")

# ── posture across regimes ──
POOL = [{"name": "BTC", "dex": "", "chg": 3.0, "class": "risk"},
        {"name": "SOL", "dex": "", "chg": 2.0, "class": "risk"},
        {"name": "xyz:NVDA", "dex": "xyz", "chg": 2.5, "class": "risk"},
        {"name": "xyz:GOLD", "dex": "xyz", "chg": 0.8, "class": "defensive"},
        {"name": "xyz:BRENTOIL", "dex": "xyz", "chg": 0.5, "class": "defensive"},
        {"name": "DOGE", "dex": "", "chg": -3.0, "class": "risk"},
        {"name": "xyz:MU", "dex": "xyz", "chg": -4.0, "class": "risk"}]
board = {"BTC": {"direction": "LONG", "pct": 70}}
noco = {"available": False}

p_on = scoring.build_posture(POOL, scoring.pulse_stance(on, GROUPS, 15), "SHORT_CROWDED", noco, board, INPUTS, 100.0)
check("risk_on: long-only, full size", p_on["stance"] == "RISK_ON" and p_on["shorts"] == [] and p_on["size_scale"] == 1.0)
check("risk_on longs are the strong risk names", "BTC" in p_on["longs"] and "DOGE" not in p_on["longs"])

p_of = scoring.build_posture(POOL, scoring.pulse_stance(off, GROUPS, 28), "LONG_CROWDED", noco, board, INPUTS, 0.0)
check("risk_off: defensives long, risk short, reduced size", p_of["stance"] == "RISK_OFF"
      and "xyz:GOLD" in p_of["longs"] and "xyz:MU" in p_of["shorts"] and p_of["size_scale"] == 0.7)
check("risk_off never longs a risk asset", all(scoring.classify(n, INPUTS) == "defensive" for n in p_of["longs"]))

p_rt = scoring.build_posture(POOL, p_rot, "LONG_CROWDED", noco, board, INPUTS, 0.0)
check("rotation: long strong / short weak, small size", p_rt["stance"] == "ROTATION"
      and p_rt["longs"][0] in ("BTC", "xyz:NVDA", "SOL") and p_rt["size_scale"] == 0.6)
check("posture carries a narrative", "RISK_OFF" in p_of["narrative"])
check("cohorts-unavailable flagged", "board lean" in p_of["narrative"])

# ── entry: tape must confirm the posture direction ──
up1, up4 = candles(30, 100, 0.004), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)
check("long confirmed on uptrend", (scoring.score_candidate("BTC", "LONG", up1, up4, noco, board, INPUTS) or {}).get("score", 0) >= 5.5)
check("long REJECTED on downtrend (hard 4h confirm)", scoring.score_candidate("BTC", "LONG", dn1, dn4, noco, board, INPUTS) is None)
check("short confirmed on downtrend", (scoring.score_candidate("DOGE", "SHORT", dn1, dn4, noco, board, INPUTS) or {}).get("score", 0) >= 5.5)
check("short REJECTED on uptrend", scoring.score_candidate("DOGE", "SHORT", up1, up4, noco, board, INPUTS) is None)

# ── bands + size-scaled sizing + venue clamp ──
check("band apex", scoring.band_for(8.5, INPUTS) == "apex")
lev, mgn = scoring.sizing_for("apex", 1.0, INPUTS, venue_max=3)
check("venue clamp", lev == 3 and mgn == 14)
_, mgn_small = scoring.sizing_for("apex", 0.6, INPUTS)
check("size_scale shrinks margin", mgn_small == round(14 * 0.6, 2))

# ── recalibration clock ──
check("first tick is due (anchor 0)", scoring.due(1000.0, 0.0, 86400))
check("not due inside window", not scoring.due(1000.0 + 80000, 1000.0, 86400))
check("due after the window", scoring.due(1000.0 + 90000, 1000.0, 86400))

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
