#!/usr/bin/env python3
# Senpi GRIZZLY Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""GRIZZLY v3.0 — BTC Alpha Hunter (Tightened).

Single-asset BTC lifecycle hunter. HUNT → RIDE (NO_REPLY) → re-HUNT.
Stalking mode removed (Stalker proved dead across fleet).

v2.1.1 issues fixed:
- Thesis exit REMOVED (was chopping winners)
- DSL retrace: 8% (was 3% — too tight for BTC volatility)
- Hard timeout: 360min/6h (was 180min — BTC trends are slow)
- Leverage: 7x (was 10x)
- Trade counter increments BEFORE output (Phoenix fix)
- No DSL state generation (plugin handles it)
- STALKING mode removed

Uses: leaderboard_get_markets + market_get_asset_data (2 API calls)
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg

ASSET = "BTC"
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 2
COOLDOWN_MINUTES = 180
MARGIN_PCT = 0.25
MIN_SCORE = 8

def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def evaluate_btc():
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None
    markets = raw.get("data", raw)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])

    btc = None
    for m in markets:
        if not isinstance(m, dict): continue
        if str(m.get("token","")).upper() == ASSET: btc = m; break
    if not btc: return None

    d = str(btc.get("direction","")).upper()
    if d not in ("LONG","SHORT"): return None
    pct = safe_float(btc.get("pct_of_top_traders_gain",0))
    traders = int(btc.get("trader_count",0))
    p4h = safe_float(btc.get("token_price_change_pct_4h",0))
    p1h = safe_float(btc.get("token_price_change_pct_1h", btc.get("price_change_1h",0)))
    cc = safe_float(btc.get("contribution_pct_change_4h",0))

    if pct < 5.0 or traders < 30: return None
    if d == "LONG" and p4h < 0: return None
    if d == "SHORT" and p4h > 0: return None

    funding = 0
    try:
        ad = cfg.mcporter_call("market_get_asset_data", asset=ASSET, candle_intervals=["1h"], include_funding=True)
        if ad:
            ac = ad.get("data",ad).get("asset_context", ad.get("data",ad).get("assetContext",{}))
            funding = safe_float(ac.get("funding",0))
    except: pass

    score, reasons = 0, []
    if pct >= 15: score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10: score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    else: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    if abs(p4h) >= 2.0: score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
    elif abs(p4h) >= 0.5: score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    if (d=="LONG" and p1h>0.2) or (d=="SHORT" and p1h<-0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    if abs(cc)>=0.03: score += 2; reasons.append(f"CONTRIB_SURGE +{abs(cc)*100:.1f}%")
    elif abs(cc)>=0.01: score += 1; reasons.append(f"CONTRIB_GROWING +{abs(cc)*100:.2f}%")

    if (d=="SHORT" and funding>0.0002) or (d=="LONG" and funding<-0.0002):
        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    if traders >= 100: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    return {"score":score,"direction":d,"reasons":reasons,"smPct":pct,"smTraders":traders,"priceChg4h":p4h}

def load_tc():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f: tc = json.load(f)
            if tc.get("date") == now_date(): return tc
        except: pass
    return {"date": now_date(), "entries": 0}

def save_tc(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)

def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet: cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"no wallet"}); return

    av, positions = cfg.get_positions(wallet)
    if av <= 0: cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"cannot read account"}); return

    for p in positions:
        if p.get("coin","").upper() == ASSET:
            cfg.output({"status":"ok","heartbeat":"NO_REPLY",
                "note":f"RIDING: BTC. DSL manages exit.","_v2_no_thesis_exit":True}); return

    tc = load_tc()
    if tc.get("entries",0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":f"Daily limit ({MAX_DAILY_ENTRIES}) reached"}); return

    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"BTC on cooldown"}); return

    thesis = evaluate_btc()
    if not thesis:
        cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"HUNTING: no BTC thesis"}); return
    if thesis["score"] < MIN_SCORE:
        cfg.output({"status":"ok","heartbeat":"NO_REPLY",
            "note":f"HUNTING: BTC {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"}); return

    margin = round(av * MARGIN_PCT, 2)
    tc["entries"] = tc.get("entries",0) + 1
    save_tc(tc)

    cfg.output({
        "status":"ok",
        "signal":{"asset":ASSET,"direction":thesis["direction"],"score":thesis["score"],
            "mode":"BTC_HUNTER","reasons":thesis["reasons"],"smPct":thesis["smPct"],"smTraders":thesis["smTraders"]},
        "entry":{"asset":ASSET,"direction":thesis["direction"],"leverage":DEFAULT_LEVERAGE,
            "margin":margin,"orderType":"FEE_OPTIMIZED_LIMIT"},
        "constraints":{"maxPositions":MAX_POSITIONS,"maxLeverage":DEFAULT_LEVERAGE,
            "maxDailyEntries":MAX_DAILY_ENTRIES,"cooldownMinutes":COOLDOWN_MINUTES,
            "_v2_no_thesis_exit":True,"_note":"DSL managed by plugin runtime."},
        "_grizzly_version":"3.0",
    })

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}"); import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status":"error","error":str(e)})
