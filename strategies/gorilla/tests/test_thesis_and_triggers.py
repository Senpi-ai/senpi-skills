"""gorilla fidelity — pulse stance, cohort divergence, thesis, clocks, close triggers.

The pulse + cohort math is ported from the senpi-market-pulse / senpi-smart-money
engines — these tests pin the ported semantics (day classification thresholds,
bias = net/gross, opposite-sides/gap divergence, MIN_MEMBERS/LEAN/GAP constants).

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
check("empty rows -> empty universe", scoring.derive_universe([], U_INPUTS) == [])

# ── pulse stance (market-pulse compute_signals port) ──
GROUPS = {"crypto": ["BTC", "ETH"], "semis": ["NVDA", "AMD"], "indices": ["SP500"],
          "commodities": ["GOLD"], "macro_fx": ["DXY"]}
chg_off = {"BTC": -2.0, "ETH": -3.0, "NVDA": -1.5, "AMD": -2.5, "SP500": -1.0,
           "GOLD": 0.5, "DXY": 0.9}
p = scoring.pulse_stance(chg_off, GROUPS, vix_price=28.0)
check("risk_off day (3+ groups down, 2:1)", p["day"] == "risk_off")
check("dxy stress read", p["checklist"]["dxy"] == "dollar bid — funding stress")
check("vix elevated read", p["checklist"]["vix"] == "fear elevated")
check("gold haven intact", p["checklist"]["gold"] == "haven bid intact")

chg_on = {"BTC": 2.0, "ETH": 3.0, "NVDA": 1.5, "AMD": 2.5, "SP500": 1.0,
          "GOLD": 0.2, "DXY": 0.1}
p_on = scoring.pulse_stance(chg_on, GROUPS, vix_price=15.0)
check("risk_on day", p_on["day"] == "risk_on")
check("vix contained", p_on["checklist"]["vix"] == "fear contained")

chg_mixed = {"BTC": 2.0, "ETH": 3.0, "NVDA": -1.5, "AMD": -2.5, "SP500": 0.1,
             "GOLD": 0.0, "DXY": 0.0}
check("mixed day", scoring.pulse_stance(chg_mixed, GROUPS)["day"] == "mixed")
check("no quotes -> day None", scoring.pulse_stance({}, GROUPS)["day"] is None)

# ── dispersion (pulse.py verbatim: index calm + sector break > 2.5 = rotation) ──
chg_rot = {"BTC": 1.4, "ETH": 1.2, "NVDA": -4.0, "AMD": -4.6, "SP500": 0.4,
           "GOLD": -0.3, "DXY": 0.0}
p_rot = scoring.pulse_stance(chg_rot, GROUPS, vix_price=20.0)
check("rotation day is mixed", p_rot["day"] == "mixed")
check("dispersion read = rotation", p_rot["dispersion"]["read"] == "rotation")
check("worst group named", p_rot["dispersion"]["worst_group"] == "semis")
check("broad read on aligned day", p["dispersion"]["read"] == "broad")
check("no read without sp500",
      scoring.pulse_stance({"BTC": 1.0}, {"crypto": ["BTC"]})["dispersion"]["read"] is None)

# movers surfacing
mv = scoring.top_movers({"AAVE": {"chg": 8.95}, "BTC": {"chg": 1.7}, "SUI": {"chg": -0.9},
                         "NEAR": {"chg": 4.08}, "X": {"chg": None}})
check("movers ranked by |chg|", [m["asset"] for m in mv] == ["AAVE", "NEAR", "BTC"])

# ── cohort bias math (smart-money port: bias = net/gross) ──
traders = [
    {"openPositions": [{"coin": "SOL", "szi": 10, "positionValue": 100_000},
                       {"coin": "ETH", "szi": -5, "positionValue": 50_000}]},
    {"open_positions": [{"coin": "SOL", "szi": 2, "positionValue": 60_000}]},
    {"openPositions": [{"coin": "SOL", "szi": -1, "positionValue": 40_000}]},
]
per = scoring.finalize_bias(scoring.cohort_positions_bias(traders))
check("bias = net/gross", per["SOL"]["bias"] == round((100_000 + 60_000 - 40_000) / 200_000, 3))
check("member counts", per["SOL"]["n_long"] == 2 and per["SOL"]["n_short"] == 1)
check("short-only coin bias -1", per["ETH"]["bias"] == -1.0)
check("notional fallback szi*entryPx",
      scoring._signed_notional({"szi": -2, "entryPx": 100}) == -200)

# ── divergences (opposite sides always flag; gap >= 0.50; min members 5) ──
def perd(bias, members):
    nl = members if bias >= 0 else 0
    return {"bias": bias, "members": members, "n_long": nl,
            "n_short": members - nl, "net": 0.0, "gross": 1.0}

smart = {"SOL": perd(0.8, 10), "ETH": perd(-0.6, 8), "DOGE": perd(0.5, 3),   # too few
         "LINK": perd(0.45, 6)}
crowd = {"SOL": perd(-0.4, 20), "ETH": perd(0.3, 15), "DOGE": perd(-0.9, 30),
         "LINK": perd(0.20, 12)}                                             # gap 0.25 < 0.50, same side
divs = scoring.divergences(smart, crowd, {})
names = {d["asset"] for d in divs}
check("opposite sides flagged", {"SOL", "ETH"} <= names)
check("min-members respected", "DOGE" not in names)
check("small same-side gap not flagged", "LINK" not in names)
check("sorted opposite-first", divs[0]["opposite_sides"])
conv = scoring.smart_conviction(smart, {})
check("conviction needs lean+members",
      {c["asset"] for c in conv} == {"SOL", "ETH", "LINK"})

# ── thesis: divergence-first buckets, RS fallback, honest degrade ──
views = {"SOL": {"chg": 4.0, "sm_dir": "LONG", "sm_pct": 70},
         "ETH": {"chg": -2.0, "sm_dir": "NEUTRAL", "sm_pct": 50},
         "HYPE": {"chg": 6.0, "sm_dir": "NEUTRAL", "sm_pct": 50},
         "DOGE": {"chg": -5.0, "sm_dir": "SHORT", "sm_pct": 60},
         "LINK": {"chg": 1.0, "sm_dir": "NEUTRAL", "sm_pct": 50},
         "AVAX": {"chg": -1.0, "sm_dir": "NEUTRAL", "sm_pct": 50}}
cohort = {"available": True, "smart": smart, "crowd": crowd}
th = scoring.derive_thesis(views, p_on, cohort, "NEUTRAL", INPUTS, 1000.0)
check("stance from pulse day", th["stance"] == "RISK_ON")
check("divergence leads the long bucket", th["leaders"][0] == "SOL")
check("divergence leads the short bucket", th["laggards"][0] == "ETH")
check("rs fallback tops up longs", "HYPE" in th["leaders"])
check("bucket src labeled", "divergence" in th["bucket_src"]["SOL"])
check("cohorts flagged available", th["cohorts_available"])
check("narrative carries pulse", "risk_on" in th["narrative"])
check("derived_at stamped", th["derived_at"] == 1000.0)
check("caps favor long book", th["caps"]["LONG"] == 5 and th["caps"]["SHORT"] == 2)

# rotation day -> NEUTRAL stance runs both books full (dispersion mode)
th_rot = scoring.derive_thesis(views, p_rot, cohort, None, INPUTS, 0.0)
check("rotation day stance NEUTRAL", th_rot["stance"] == "NEUTRAL")
check("dispersion mode caps 4/4", th_rot["caps"] == {"LONG": 4, "SHORT": 4})
check("narrative flags rotation", "DISPERSION: rotation" in th_rot["narrative"])
check("narrative carries movers", "movers:" in th_rot["narrative"])
check("thesis persists dispersion", th_rot["pulse"]["dispersion"]["read"] == "rotation")

# degrade: no cohorts -> RS-ranked + flagged in narrative
th_deg = scoring.derive_thesis(views, {"day": None}, {"available": False}, None, INPUTS, 0.0)
check("degrade stance NEUTRAL", th_deg["stance"] == "NEUTRAL")
check("degrade flagged honestly", "cohorts unavailable" in th_deg["narrative"])
check("degrade longs are RS-ranked", th_deg["leaders"][0] == "HYPE")
check("degrade respects sm hard block", "DOGE" not in th_deg["leaders"])

# ── press scoring (unchanged mechanics) ──
up1, up4 = candles(30, 100, 0.004), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)
pe = scoring.score_entry("SOL", "LONG", up1, up4, {"direction": "LONG", "pct": 70}, INPUTS)
check("press confirms uptrend long", pe is not None and pe["score"] >= 5.5)
pe_bad = scoring.score_entry("SOL", "LONG", dn1, dn4, {"direction": "NEUTRAL", "pct": 50}, INPUTS)
check("press rejects downtrend long", pe_bad["score"] < 5.5)

# ── bands + venue clamp ──
check("band apex", scoring.band_for(8.5, INPUTS) == "apex")
check("band none", scoring.band_for(4.0, INPUTS) is None)
lev, mgn = scoring.sizing_for("apex", INPUTS, venue_max=3)
check("venue clamp", lev == 3 and mgn == 18)

# ── boundary clock ──
check("due after window", scoring.due(1000.0 + 48 * 3600, 1000.0, 48 * 3600))
check("not due inside window", not scoring.due(1000.0 + 47 * 3600, 1000.0, 48 * 3600))
check("zero anchor never due", not scoring.due(999999.0, 0.0, 48 * 3600))

# ── close triggers ──
held = [{"asset": "SOL", "direction": "LONG"}, {"asset": "HYPE", "direction": "LONG"},
        {"asset": "LINK", "direction": "LONG"}]
new_th = dict(th, leaders=["SOL", "AVAX", "BTC"], stance="NEUTRAL",
              smart_bias={"SOL": 0.8, "HYPE": -0.55, "LINK": 0.1},
              cohorts_available=True)
scored = {"SOL": {"score": 7.0}, "HYPE": {"score": 6.0}, "LINK": {"score": 2.0}}

sigs = scoring.close_triggers("LONG", held, new_th, th, scored, INPUTS, True, False)
by = {s["asset"]: s["trigger"] for s in sigs}
check("cohort flip -> divergence_reversed", by.get("HYPE") == "divergence_reversed")
check("bucket-leaver -> thesis_shift", by.get("LINK") == "thesis_shift")
check("kept name not closed", "SOL" not in by)

sigs = scoring.close_triggers("LONG", held, None, th, scored, INPUTS, False, True)
check("weekly recycles only the laggard",
      len(sigs) == 1 and sigs[0]["asset"] == "LINK"
      and sigs[0]["trigger"] == "weekly_rebalance")

sigs = scoring.close_triggers("LONG", held, new_th, th, scored, INPUTS, True, True)
check("no double-close when both due", len(sigs) == len({s['asset'] for s in sigs}))

sigs = scoring.close_triggers("LONG", held, None, th, scored, INPUTS, False, False)
check("nothing closes between boundaries", sigs == [])

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
