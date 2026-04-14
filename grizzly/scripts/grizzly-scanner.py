#!/usr/bin/env python3
# Senpi GRIZZLY Scanner v3.2
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""GRIZZLY v4.0 — BTC Contrarian (SM Exhaustion Fader).

v4.0 — DIRECTION FLIP + A/B alignment with Horribilis.
Fleet analysis (April 10, 2026) found that the SM consensus signal is
perfectly inverted on BTC: the multi-timeframe confirmation requirement
means the scanner enters after the move is exhausted. Inversion test on
11 trades showed 81.8% WR if direction were flipped.

Changes from v3.2:
- CONTRARIAN FLIP: trade opposite to SM consensus direction
- Leverage tiers aligned with Horribilis: 7x/10x (was 7x/10x/15x/20x)
- Added MOVE_EXHAUSTION penalty (was missing, Horribilis had it)
- 15m velocity tiers aligned with Horribilis (simpler, less spike-chasing)
- 1h acceleration aligned with Horribilis
- Added same-direction cooldown (60 min)
- Fixed resting order filter (now ignores reduceOnly DSL stops)
- A/B variable vs Horribilis: Grizzly = single entry, Horribilis = pyramids

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

SAME_DIR_COOLDOWN_MINUTES = 60

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7

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
    """Check for non-reduceOnly resting orders. Ignores DSL stop-losses."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data: return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list):
        for o in orders:
            if not o.get("reduceOnly", False):
                return True
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

    # CONTRARIAN EXHAUSTION GATE (v4.1)
    # Fleet analysis April 13: Grizzly v4.0 lost $107 in 15h shorting BTC while
    # BTC pumped from $72,294 to $74,758 (+3.4%). The contrarian flip was fading
    # an ACCELERATING breakout, not exhausted consensus.
    #
    # Before fading SM, require that the 4H price has already moved significantly
    # in their direction. If BTC is only up 0.7% and SM is piling in, they might
    # be early and RIGHT. Only fade when the move is actually exhausted.
    MIN_EXHAUSTION_PCT = 2.5  # BTC 4H must have moved >2.5% in SM direction
    if abs(p4h) < MIN_EXHAUSTION_PCT:
        return None  # Not exhausted yet — don't fight a fresh trend
    # Must be moving in SM direction (otherwise SM is already wrong)
    if (d == "LONG" and p4h < 0) or (d == "SHORT" and p4h > 0):
        return None  # SM direction opposes price — not an exhaustion pattern

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

    # Move-exhaustion penalty — large existing moves reduce conviction
    if abs(p4h) >= 4.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 2; reasons.append(f"MOVE_EXHAUSTION {p4h:+.1f}%")
    elif abs(p4h) >= 2.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}%")

    # 15m velocity freshness gate — SM must be actively building
    # For contrarian: we want SM aggressively piling in (high 15m) so we can fade the peak
    # If SM is already unwinding (15m <= 0), the fade opportunity is passing
    if cc_15m <= 0:
        return None  # SM not fresh — stale signal

    # Contribution velocity scoring
    if cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")

    if cc_1h_contrib > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h_contrib:.2f}")

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
    default = {"date": now_date(), "entries": 0,
               "last_win_direction": None, "last_win_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f: tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc: tc[k] = v
            return tc
        except: pass
    return dict(default)

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

    # Same-direction re-entry cooldown after a win
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)

    thesis = evaluate_btc()
    if not thesis:
        cfg.output({"status":"ok","heartbeat":"NO_REPLY","note":"HUNTING: no BTC thesis"}); return
    if thesis["score"] < MIN_SCORE:
        cfg.output({"status":"ok","heartbeat":"NO_REPLY",
            "note":f"HUNTING: BTC SM={thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"}); return

    # ── CONTRARIAN FLIP ──
    # SM says LONG → we go SHORT. SM says SHORT → we go LONG.
    # The inversion test on 11 trades showed 81.8% WR if flipped.
    sm_direction = thesis["direction"]
    thesis["direction"] = "SHORT" if sm_direction == "LONG" else "LONG"
    thesis["reasons"].insert(0, f"CONTRARIAN_FLIP (SM is {sm_direction})")

    # Same-direction cooldown (post-flip direction)
    if last_win_dir and last_win_ts:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            if thesis["direction"] == last_win_dir:
                remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
                cfg.output({"status":"ok","heartbeat":"NO_REPLY",
                    "note":f"SAME_DIR_COOLDOWN: won {last_win_dir} {remaining}min ago"}); return

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
            "result":result,"_grizzly_version":"4.1"})
    else:
        cfg.output({"status":"ok","action":"ENTRY_FAILED",
            "signal":{"asset":ASSET,"direction":thesis["direction"],"score":thesis["score"],"reasons":thesis["reasons"]},
            "error":result,"_grizzly_version":"4.1"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status":"error","error":str(e)})
