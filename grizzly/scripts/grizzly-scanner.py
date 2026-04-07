#!/usr/bin/env python3
# Senpi GRIZZLY Scanner v3.2
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""GRIZZLY v3.2 — BTC Alpha Hunter (Conviction Leverage + Extreme Velocity).

v3.2 changes:
- Conviction-scaled leverage: score 8->7x, 9->10x, 10->15x, 11+->20x
- Extreme velocity tiers: 15m >5.0->+4pts, >2.0->+3pts (was capped at +2)
- 1h acceleration: >3.0->+2pts (was capped at +1)
- BTC max leverage on Hyperliquid is 40x, we cap at 20x

v3.1 changes from fleet audit:
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- 4H price alignment: was HARD GATE -> now score contributor
- SM thresholds: was HARD GATE (5%/30t) -> now score contributors
- Margin increased to 50%
- No thesis exit (unchanged from v3.0)
- Checks resting orders before placing new entries (race condition fix)

Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg

ASSET = "BTC"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 2
COOLDOWN_MINUTES = 180
MARGIN_PCT = 0.50
MIN_SCORE = 8

LEVERAGE_TIERS = [
    {"min_score": 11, "leverage": 20},
    {"min_score": 10, "leverage": 15},
    {"min_score": 9,  "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 20  # BTC max on HL is 40x, we cap at 20x

def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE

def has_resting_orders(wallet):
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data: return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list) and len(orders) > 0: return True
    return False


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
    cc_15m = safe_float(btc.get("contribution_pct_change_15m",0))
    cc_1h_contrib = safe_float(btc.get("contribution_pct_change_1h",0))

    if traders < 10: return None

    funding = 0
    try:
        ad = cfg.mcporter_call("market_get_asset_data", asset=ASSET, candle_intervals=["1h"], include_funding=True)
        if ad:
            ac = ad.get("data",ad).get("asset_context", ad.get("data",ad).get("assetContext",{}))
            funding = safe_float(ac.get("funding",0))
    except: pass

    score, reasons = 0, []

    # SM concentration (0-3)
    if pct >= 15: score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10: score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # 4H price alignment (+/-2)
    if abs(p4h) >= 2.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # 1H momentum (0-1)
    if (d=="LONG" and p1h>0.2) or (d=="SHORT" and p1h<-0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # Contribution velocity — expanded extreme tiers
    if cc_15m > 5.0: score += 4; reasons.append(f"15M_EXTREME_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 2.0: score += 3; reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
    elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

    if cc_1h_contrib > 3.0: score += 2; reasons.append(f"1H_STRONG_ACCEL +{cc_1h_contrib:.2f}")
    elif cc_1h_contrib > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h_contrib:.2f}")

    if abs(cc)>=5.0: score += 1; reasons.append(f"4H_MAJOR_SHIFT {cc:+.1f}")

    if cc_15m > 0 and cc_1h_contrib > 0 and cc_15m > cc_1h_contrib:
        score += 1; reasons.append(f"ACCEL_PATTERN 15m({cc_15m:.2f})>1h({cc_1h_contrib:.2f})")

    # Funding alignment (0-1)
    if (d=="SHORT" and funding>0.0002) or (d=="LONG" and funding<-0.0002):
        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # Trader depth (0-1)
    if traders >= 100: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    return {"score":score,"direction":d,"reasons":reasons,"smPct":pct,"smTraders":traders,"priceChg4h":p4h}


def execute_entry(direction, margin, leverage):
    result = cfg.mcporter_call(
        "create_position", coin=ASSET, direction=direction, leverage=leverage,
        margin=margin, orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={"ensureExecutionAsTaker": False, "executionTimeoutSeconds": 30},
    )
    if result and result.get("success"): return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


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

    if has_resting_orders(wallet):
        cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"RESTING ORDER: BTC limit order pending."}); return

    for p in positions:
        if p.get("coin","").upper() == ASSET:
            cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"RIDING: BTC. DSL manages exit.","_v2_no_thesis_exit":True}); return

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

    leverage = get_leverage_for_score(thesis["score"])
    margin = round(av * MARGIN_PCT, 2)

    success, result = execute_entry(thesis["direction"], margin, leverage)
    if success:
        tc["entries"] = tc.get("entries",0) + 1
        save_tc(tc)
        cfg.output({"status":"ok","action":"ENTRY",
            "signal":{"asset":ASSET,"direction":thesis["direction"],"score":thesis["score"],
                "leverage":leverage,"mode":"BTC_HUNTER","reasons":thesis["reasons"]},
            "execution":{"asset":ASSET,"direction":thesis["direction"],"leverage":leverage,
                "margin":margin,"orderType":"FEE_OPTIMIZED_LIMIT","ensureExecutionAsTaker":False},
            "result":result,"_grizzly_version":"3.2"})
    else:
        cfg.output({"status":"ok","action":"ENTRY_FAILED",
            "signal":{"asset":ASSET,"direction":thesis["direction"],"score":thesis["score"],"reasons":thesis["reasons"]},
            "error":result,"_grizzly_version":"3.2"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status":"error","error":str(e)})
