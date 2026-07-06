"""cuttlefish fidelity — pulse gating, cohort divergence, composite, close triggers.

The pulse + cohort math is ported from the senpi-market-pulse / senpi-smart-money
engines — these tests pin the ported semantics (day classification thresholds,
bias = net/gross, opposite-sides/gap divergence, hard blocks, board fallback).

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
    "weights": {"smartLean": 2.5, "divergence": 3.0, "nearTerm": 1.0, "trend4h": 1.5,
                "align1h": 1.0, "mom24h": 1.0, "volRatio": 0.5, "regimeBonus": 0.5},
    "minScore": 5.5, "goodScore": 6.5, "apexScore": 8.0, "exitScore": 3.5,
    "leverageTiers": {"apex": 6, "good": 5, "base": 4},
    "marginPctTiers": {"apex": 22, "good": 17, "base": 12},
    "pulseFlipConfirmTicks": 2, "cohorts": {},
}

# ── universe derivation ──
rows = [{"name": "BTC", "vol": 900e6}, {"name": "SOL", "vol": 300e6},
        {"name": "PUMPY", "vol": 4e6}, {"name": "xyz:NVDA", "vol": 80e6}]
u = scoring.derive_universe(rows, {"universeVolFloorUsd": 25_000_000, "universeMaxNames": 16})
check("universe derived (floor + dex filter)", u == ["BTC", "SOL"])

# ── pulse day + gating (market-pulse port) ──
GROUPS = {"crypto": ["BTC", "ETH"], "semis": ["NVDA", "AMD"], "indices": ["SP500"],
          "commodities": ["GOLD"], "macro_fx": ["DXY"]}
chg_off = {"BTC": -2.0, "ETH": -3.0, "NVDA": -1.5, "AMD": -2.5, "SP500": -1.0,
           "GOLD": 0.5, "DXY": 0.9}
p_off = scoring.pulse_stance(chg_off, GROUPS, vix_price=28.0)
check("risk_off day", p_off["day"] == "risk_off")
check("checklist dxy stress", p_off["checklist"]["dxy"] == "dollar bid — funding stress")
chg_on = {"BTC": 2.0, "ETH": 3.0, "NVDA": 1.5, "AMD": 2.5, "SP500": 1.0,
          "GOLD": 0.2, "DXY": 0.1}
check("risk_on day", scoring.pulse_stance(chg_on, GROUPS)["day"] == "risk_on")
check("long blocked on risk_off", not scoring.pulse_allows("LONG", "risk_off"))
check("short blocked on risk_on", not scoring.pulse_allows("SHORT", "risk_on"))
check("both allowed on mixed", scoring.pulse_allows("LONG", "mixed")
      and scoring.pulse_allows("SHORT", "mixed"))
check("both allowed on no read", scoring.pulse_allows("LONG", None))

# ── dispersion (pulse.py verbatim: index calm + sector break > 2.5 = rotation) ──
chg_rot = {"BTC": 1.4, "ETH": 1.2, "NVDA": -4.0, "AMD": -4.6, "SP500": 0.4,
           "GOLD": -0.3, "DXY": 0.0}
p_rot = scoring.pulse_stance(chg_rot, GROUPS, vix_price=20.0)
check("rotation day is mixed", p_rot["day"] == "mixed")
check("dispersion read = rotation", p_rot["dispersion"]["read"] == "rotation")
check("worst group named", p_rot["dispersion"]["worst_group"] == "semis")
check("both books allowed on rotation day", scoring.pulse_allows("LONG", p_rot["day"])
      and scoring.pulse_allows("SHORT", p_rot["day"]))
check("broad read on aligned day", p_off["dispersion"]["read"] == "broad")
mv = scoring.top_movers({"AAVE": 8.95, "BTC": 1.7, "SUI": -0.9, "NEAR": 4.08},
                        ["AAVE", "BTC", "SUI", "NEAR", "MISSING"])
check("movers ranked by |chg|", [m["asset"] for m in mv] == ["AAVE", "NEAR", "BTC"])

# ── cohort math (smart-money port) ──
traders = [
    {"openPositions": [{"coin": "SOL", "szi": 10, "positionValue": 100_000},
                       {"coin": "ETH", "szi": -5, "positionValue": 50_000}]},
    {"open_positions": [{"coin": "SOL", "szi": 2, "positionValue": 60_000}]},
    {"openPositions": [{"coin": "SOL", "szi": -1, "positionValue": 40_000}]},
]
per = scoring.finalize_bias(scoring.cohort_positions_bias(traders))
check("bias = net/gross", per["SOL"]["bias"] == 0.6)
check("short-only coin bias -1", per["ETH"]["bias"] == -1.0)


def perd(bias, members):
    nl = members if bias >= 0 else 0
    return {"bias": bias, "members": members, "n_long": nl,
            "n_short": members - nl, "net": 0.0, "gross": 1.0}


smart = {"SOL": perd(0.8, 10), "ETH": perd(-0.6, 8), "DOGE": perd(0.5, 3),
         "LINK": perd(0.45, 6)}
crowd = {"SOL": perd(-0.4, 20), "ETH": perd(0.3, 15), "DOGE": perd(-0.9, 30),
         "LINK": perd(0.20, 12)}
COHORT = {"available": True, "smart": smart, "crowd": crowd}

divs = scoring.divergences(smart, crowd, {})
check("opposite sides flagged", {d["asset"] for d in divs} == {"SOL", "ETH"})

cv = scoring.cohort_view_for("SOL", "LONG", COHORT, {})
check("view: divergent long", cv["divergent"] and cv["gap"] == 1.2 and not cv["against"])
cv_eth_long = scoring.cohort_view_for("ETH", "LONG", COHORT, {})
# ETH bias -0.6 -> lean_against 0.6: past BOTH entry (0.40) and close (0.55) bars
check("view: proven cohort against long (entry block)", cv_eth_long["against"])
check("view: proven cohort DECISIVELY reversed (close)", cv_eth_long["reversed"])
cv_eth_short = scoring.cohort_view_for("ETH", "SHORT", COHORT, {})
check("view: divergent short", cv_eth_short["divergent"] and not cv_eth_short["against"])
cv_link = scoring.cohort_view_for("LINK", "LONG", COHORT, {})
check("view: conviction w/o divergence", not cv_link["divergent"]
      and cv_link["smart_bias"] == 0.45)
cv_unavail = scoring.cohort_view_for("SOL", "LONG", {"available": False}, {})
check("view: unavailable", not cv_unavail["available"] and cv_unavail["smart_bias"] is None)

# HYSTERESIS BAND: a name against by 0.45 (>= entry 0.40, < close 0.55) is
# entry-blocked but NOT closeable — the gap that stops the two scanners fighting.
mid = {"available": True, "smart": {"MID": perd(-0.45, 8)}, "crowd": {}}
cv_mid = scoring.cohort_view_for("MID", "LONG", mid, {})
check("hysteresis band: against but not reversed", cv_mid["against"] and not cv_mid["reversed"])

# ── composite: divergence-led entry, hard blocks, board fallback ──
up1, up4 = candles(30, 100, 0.004, vol=120), candles(10, 95, 0.01)
dn1, dn4 = candles(30, 100, -0.004), candles(10, 105, -0.01)
nt_long = {"direction": "LONG", "pct": 70}

th = scoring.score_asset("SOL", "LONG", up1, up4, {}, cv, nt_long, "SHORT_CROWDED", INPUTS)
check("divergence thesis scores", th["score"] >= 5.5)
check("divergence component full", th["components"]["divergence"] == 3.0)
check("smartLean credited", th["components"]["smartLean"] == 2.0)   # 2.5 * 0.8
check("nearTerm its real role", 0 < th["components"]["nearTerm"] <= 1.0)
check("squeeze bonus", th["components"]["regimeBonus"] == 0.5)

th_blk = scoring.score_asset("ETH", "LONG", up1, up4, {}, cv_eth_long, nt_long, None, INPUTS)
check("proven-cohort hard block", th_blk["blocked"] == "smart_cohort_against")

th_fb = scoring.score_asset("SOL", "LONG", up1, up4, {}, cv_unavail,
                            {"direction": "SHORT", "pct": 65}, None, INPUTS)
check("board fallback block when cohorts unavailable", th_fb["blocked"] == "sm_board_against")

th_deg = scoring.score_asset("SOL", "LONG", up1, up4, {}, cv_unavail, nt_long, None, INPUTS)
check("degraded composite scores w/o cohort credit",
      th_deg["blocked"] is None and th_deg["components"]["smartLean"] == 0.0
      and th_deg["components"]["divergence"] == 0.0)
check("degraded flagged on thesis dict", th_deg["cohorts_available"] is False)

# conviction-without-crowd gets half divergence credit
th_conv = scoring.score_asset("LINK", "LONG", up1, up4, {}, cv_link, nt_long, None, INPUTS)
check("conviction half-credit", th_conv["components"]["divergence"] == 1.5)

# ── bands + clamp ──
check("band apex", scoring.band_for(8.5, INPUTS) == "apex")
lev, mgn = scoring.sizing_for("apex", INPUTS, venue_max=3)
check("venue clamp", lev == 3 and mgn == 22)

# ── close triggers ──
held = [{"asset": "SOL", "direction": "LONG"}, {"asset": "ETH", "direction": "LONG"},
        {"asset": "LINK", "direction": "LONG"}]
views = {"SOL": {"cohort": cv, "score": 7.0, "nt_dir": "LONG", "nt_pct": 70},
         "ETH": {"cohort": cv_eth_long, "score": 6.0, "nt_dir": "NEUTRAL", "nt_pct": 50},
         "LINK": {"cohort": cv_link, "score": 2.0, "nt_dir": "NEUTRAL", "nt_pct": 50}}

sigs = scoring.close_triggers("LONG", "risk_off", 1, held, views, INPUTS, False)
check("flip unconfirmed -> per-name triggers only",
      all(s["trigger"] != "pulse_flip" for s in sigs))
sigs = scoring.close_triggers("LONG", "risk_off", 2, held, views, INPUTS, False)
check("flip confirmed -> close book",
      len(sigs) == 3 and all(s["trigger"] == "pulse_flip" for s in sigs))

sigs = scoring.close_triggers("LONG", "risk_on", 0, held, views, INPUTS, False)
by = {s["asset"]: s["trigger"] for s in sigs}
check("proven-cohort reversal closes ETH", by.get("ETH") == "divergence_reversed")
check("laggard kept before refresh", "LINK" not in by)
sigs = scoring.close_triggers("LONG", "risk_on", 0, held, views, INPUTS, True)
by = {s["asset"]: s["trigger"] for s in sigs}
check("refresh recycles LINK", by.get("LINK") == "basket_refresh")
check("SOL never closed", "SOL" not in by)

# board fallback reversal when cohorts unavailable
views_fb = {"SOL": {"cohort": cv_unavail, "score": 7.0, "nt_dir": "SHORT", "nt_pct": 66}}
sigs = scoring.close_triggers("LONG", "mixed", 0,
                              [{"asset": "SOL", "direction": "LONG"}], views_fb, INPUTS, False)
check("board-fallback reversal flagged",
      len(sigs) == 1 and sigs[0]["trigger"] == "divergence_reversed"
      and "cohorts unavailable" in sigs[0]["reason"])

# ── COHERENCE: a name in the cohort hysteresis band (against-but-not-reversed)
#    that still scores is NEVER closed — the entries/rebalance scanners can't fight ──
views_mid = {"MID": {"cohort": cv_mid, "score": 7.0, "nt_dir": "LONG", "nt_pct": 60}}
sigs = scoring.close_triggers("LONG", "mixed", 0,
                              [{"asset": "MID", "direction": "LONG"}], views_mid, INPUTS, True)
check("HYSTERESIS: against-band name that scores is NOT closed", sigs == [])

ok, _ = scoring.enforce_hysteresis(dict(INPUTS, cohorts={"leanThreshold": 0.40,
                                                         "reversalThreshold": 0.55}))
check("enforce_hysteresis: valid config", ok)
check("enforce_hysteresis: flags exitScore >= minScore",
      not scoring.enforce_hysteresis({"minScore": 4.0, "exitScore": 5.0})[0])
check("enforce_hysteresis: flags reversal <= lean",
      not scoring.enforce_hysteresis(dict(INPUTS,
          cohorts={"leanThreshold": 0.55, "reversalThreshold": 0.40}))[0])

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
