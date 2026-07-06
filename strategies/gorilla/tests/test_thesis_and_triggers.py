"""gorilla fidelity — thesis derivation, press scoring, boundary clocks, close triggers.

Run: python3 strategies/gorilla/tests/test_thesis_and_triggers.py
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


def candles(n, start, drift):
    out, px = [], start
    for i in range(n):
        o = px
        px *= (1 + drift)
        out.append({"t": i, "o": str(o), "c": str(px), "h": str(max(o, px)),
                    "l": str(min(o, px)), "v": "100", "n": 5})
    return out


INPUTS = {"bucketSize": 3, "minScore": 5.5, "goodScore": 6.5, "apexScore": 8.0,
          "exitScore": 3.5, "leverageTiers": {"apex": 6, "good": 5, "base": 4},
          "marginPctTiers": {"apex": 18, "good": 14, "base": 10}}

# ── universe derivation — reads the market, never a preset list ──
rows = [
    {"name": "BTC", "vol": 900e6}, {"name": "ETH", "vol": 500e6},
    {"name": "SOL", "vol": 300e6}, {"name": "HYPE", "vol": 120e6},
    {"name": "DOGE", "vol": 60e6}, {"name": "PUMPY", "vol": 4e6},      # under floor
    {"name": "xyz:NVDA", "vol": 80e6},                                  # other dex
    {"name": "btc", "vol": 900e6},                                      # dup (case)
    {"name": "SCAMX", "vol": 90e6},                                     # excluded
]
U_INPUTS = {"universeVolFloorUsd": 25_000_000, "universeMaxNames": 4,
            "excludeAssets": ["SCAMX"]}
u = scoring.derive_universe(rows, U_INPUTS)
check("universe sorted by volume", u == ["BTC", "ETH", "SOL", "HYPE"])
check("under-floor name dropped", "PUMPY" not in u)
check("xyz dex skipped", all(":" not in n for n in u))
check("exclude respected", "SCAMX" not in u)
u2 = scoring.derive_universe(rows, {"universeVolFloorUsd": 25_000_000, "universeMaxNames": 10})
check("cap is the only limit", "DOGE" in u2 and "SCAMX" in u2 and len(u2) == 6)
check("dedupe by case", u2.count("BTC") + u2.count("btc") == 1)
check("empty rows -> empty universe", scoring.derive_universe([], U_INPUTS) == [])

# ── thesis derivation ──
def views(spread):
    """spread: {name: (rs, sm_dir, sm_pct)}"""
    return {n: {"rs": v[0], "sm_dir": v[1], "sm_pct": v[2]} for n, v in spread.items()}

up1, up4 = candles(30, 100, 0.004), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)

v_bull = views({"SOL": (12, "LONG", 70), "HYPE": (9, "NEUTRAL", 50), "SUI": (7, "LONG", 62),
                "LINK": (4, "NEUTRAL", 50), "BTC": (3, "LONG", 60), "ETH": (2, "NEUTRAL", 50),
                "LTC": (-2, "NEUTRAL", 50), "DOGE": (-5, "SHORT", 61)})
th = scoring.derive_thesis(v_bull, up1, up4, "SHORT_CROWDED", INPUTS, 1000.0)
check("stance RISK_ON", th["stance"] == "RISK_ON")
check("leaders top-3 by rs", th["leaders"] == ["SOL", "HYPE", "SUI"])
check("laggards weakest first", th["laggards"][0] == "DOGE")
check("caps favor long book", th["caps"]["LONG"] == 5 and th["caps"]["SHORT"] == 2)
check("narrative carries buckets", "SOL" in th["narrative"] and "RISK_ON" in th["narrative"])
check("derived_at stamped", th["derived_at"] == 1000.0)

# SM >= 58% against a bucket direction disqualifies the name
v_block = dict(v_bull)
v_block["SOL"] = {"rs": 12, "sm_dir": "SHORT", "sm_pct": 65}
th_b = scoring.derive_thesis(v_block, up1, up4, None, INPUTS, 0.0)
check("SM-against leader excluded", "SOL" not in th_b["leaders"])
v_lag = dict(v_bull)
v_lag["DOGE"] = {"rs": -5, "sm_dir": "LONG", "sm_pct": 70}
th_l = scoring.derive_thesis(v_lag, up1, up4, None, INPUTS, 0.0)
check("SM-defended laggard excluded", "DOGE" not in th_l["laggards"])

v_bear = views({n: (-abs(v[0]), v[1], v[2]) for n, v in
                {"SOL": (12, "NEUTRAL", 50), "HYPE": (9, "NEUTRAL", 50), "SUI": (7, "NEUTRAL", 50),
                 "LINK": (4, "NEUTRAL", 50), "BTC": (3, "NEUTRAL", 50), "ETH": (2, "NEUTRAL", 50),
                 "LTC": (2, "NEUTRAL", 50), "DOGE": (5, "NEUTRAL", 50)}.items()})
th2 = scoring.derive_thesis(v_bear, dn1, dn4, "LONG_CROWDED", INPUTS, 0.0)
check("stance RISK_OFF", th2["stance"] == "RISK_OFF")
check("caps favor short book", th2["caps"]["SHORT"] == 5 and th2["caps"]["LONG"] == 2)
check("no leaders when nothing rs>0", th2["leaders"] == [])

check("bucket_for LONG", scoring.bucket_for("LONG", th) == th["leaders"])
check("bucket_for SHORT", scoring.bucket_for("SHORT", th) == th["laggards"])

# ── press scoring ──
pe = scoring.score_entry("SOL", "LONG", up1, up4, {"direction": "LONG", "pct": 70}, INPUTS)
check("press confirms uptrend long", pe is not None and pe["score"] >= 5.5)
pe_bad = scoring.score_entry("SOL", "LONG", dn1, dn4, {"direction": "NEUTRAL", "pct": 50}, INPUTS)
check("press rejects downtrend long", pe_bad["score"] < 5.5)
pe_short = scoring.score_entry("DOGE", "SHORT", dn1, dn4, {"direction": "SHORT", "pct": 65}, INPUTS)
check("press confirms downtrend short", pe_short["score"] >= 5.5)

# ── boundary clock ──
check("due after window", scoring.due(1000.0 + 48 * 3600, 1000.0, 48 * 3600))
check("not due inside window", not scoring.due(1000.0 + 47 * 3600, 1000.0, 48 * 3600))
check("zero anchor never due", not scoring.due(999999.0, 0.0, 48 * 3600))

# ── close triggers ──
held = [{"asset": "SOL", "direction": "LONG"}, {"asset": "HYPE", "direction": "LONG"},
        {"asset": "LINK", "direction": "LONG"}]
new_th = dict(th, leaders=["SOL", "SUI", "BTC"], stance="NEUTRAL")
scored = {"SOL": {"score": 7.0}, "HYPE": {"score": 6.0}, "LINK": {"score": 2.0}}

sigs = scoring.close_triggers("LONG", held, new_th, th, scored, INPUTS, True, False)
check("thesis_shift closes bucket-leavers",
      sorted(s["asset"] for s in sigs) == ["HYPE", "LINK"]
      and all(s["trigger"] == "thesis_shift" for s in sigs))
check("thesis_shift keeps SOL", all(s["asset"] != "SOL" for s in sigs))

sigs = scoring.close_triggers("LONG", held, None, th, scored, INPUTS, False, True)
check("weekly recycles only the laggard",
      len(sigs) == 1 and sigs[0]["asset"] == "LINK"
      and sigs[0]["trigger"] == "weekly_rebalance")

sigs = scoring.close_triggers("LONG", held, new_th, th, scored, INPUTS, True, True)
check("no double-close when both due",
      sorted(s["asset"] for s in sigs) == ["HYPE", "LINK"])

sigs = scoring.close_triggers("LONG", held, None, th, scored, INPUTS, False, False)
check("nothing closes between boundaries", sigs == [])

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
