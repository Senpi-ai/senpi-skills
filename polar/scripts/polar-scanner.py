#!/usr/bin/env python3
# Senpi POLAR Scanner v2.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""POLAR v2.1 — ETH Alpha Hunter (Hardened + Hyperfeed Scoring).

v2.1 changes from fleet audit (2026-04-06):
- Scanner calls create_position internally via mcporter (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: score 8-9 → 7x, score 10+ → 10x
- Margin increased to 50%
- Hard gates (SM%, 4H price, trader count) → score contributors
- Hyperfeed multi-window contribution velocity (15m, 1h, 4h)
- Resting order check prevents stacking maker orders
- No thesis exit (confirmed from v2.0 — scanner enters, DSL exits)

CRITICAL CONTEXT: Polar is the best gross trader in the fleet (+$235.91
gross PnL) destroyed by taker fees ($249.79). The ensureExecutionAsTaker=false
fix is the single highest-impact change. At maker rates, Polar would be
at +23% ROI instead of -1.4%.

ETH single-asset lifecycle hunter. HUNT → RIDE → re-HUNT.
Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polar_config as cfg

ASSET = "ETH"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 8
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7


def safe_float(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso(): return datetime.now(timezone.utc).isoformat()

def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def has_resting_orders(wallet):
    """Check if there are resting entry orders on the book."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list) and len(orders) > 0:
        return True
    return False


def evaluate_eth():
    """Score ETH. All signals are score contributors — no hard gates."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None

    markets = raw
    if isinstance(markets, dict): markets = markets.get("data", markets)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

    eth = None
    for m in markets:
        if not isinstance(m, dict): continue
        if str(m.get("token", "")).upper() == ASSET:
            eth = m; break
    if not eth: return None

    d = str(eth.get("direction", "")).upper()
    if d not in ("LONG", "SHORT"): return None

    pct = safe_float(eth.get("pct_of_top_traders_gain", 0))
    traders = int(eth.get("trader_count", 0))
    p4h = safe_float(eth.get("token_price_change_pct_4h", 0))
    p1h = safe_float(eth.get("token_price_change_pct_1h", eth.get("price_change_1h", 0)))
    cc_4h = safe_float(eth.get("contribution_pct_change_4h", 0))
    cc_15m = safe_float(eth.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(eth.get("contribution_pct_change_1h", 0))

    # Need minimum data to score
    if traders < 15: return None

    # Fetch funding
    funding = 0
    try:
        ad = cfg.mcporter_call("market_get_asset_data", asset=ASSET,
                                candle_intervals=["1h"], include_funding=True)
        if ad:
            ac = ad.get("data", ad).get("asset_context",
                 ad.get("data", ad).get("assetContext", {}))
            if isinstance(ac, dict):
                funding = safe_float(ac.get("funding", 0))
    except: pass

    score, reasons = 0, []

    # SM concentration (0-3)
    if pct >= 15: score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10: score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # Trader depth (0-1)
    if traders >= 100: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    # 4H price alignment (±2) — score contributor, not gate
    if abs(p4h) >= 2.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # 1H momentum (0-1)
    if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # Contribution velocity — multi-window (NEW: 15m + 1h + 4h)
    if cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
    elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

    if cc_1h > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

    if abs(cc_4h) >= 5.0: score += 1; reasons.append(f"4H_MAJOR_SHIFT {cc_4h:+.1f}")

    if cc_15m > 0 and cc_1h > 0 and cc_15m > cc_1h:
        score += 1; reasons.append(f"ACCEL_PATTERN 15m({cc_15m:.2f})>1h({cc_1h:.2f})")

    # Funding alignment (0-1)
    if (d == "SHORT" and funding > 0.0002) or (d == "LONG" and funding < -0.0002):
        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # US session bonus (0-1)
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1; reasons.append("US_SESSION")

    return {"score": score, "direction": d, "reasons": reasons,
            "smPct": pct, "smTraders": traders, "priceChg4h": p4h}


def execute_entry(direction, margin, leverage):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        coin=ASSET,
        direction=direction,
        leverage=leverage,
        margin=margin,
        orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={
            "ensureExecutionAsTaker": False,
            "executionTimeoutSeconds": 30,
        },
    )
    if result and result.get("success"):
        return True, result
    else:
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
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    av, positions = cfg.get_positions(wallet)
    if av <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # Check for resting orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: ETH limit order pending. Waiting for fill."})
        return

    # RIDING: position open → NO_REPLY. DSL manages ALL exits.
    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"RIDING: ETH {p.get('direction','?')}. DSL manages exit.",
                "_v2_no_thesis_exit": True})
            return

    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # Cooldown
    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"ETH on cooldown ({remaining}min remaining)"})
        return

    thesis = evaluate_eth()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "HUNTING: no ETH thesis"})
        return
    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING: ETH {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"})
        return

    leverage = get_leverage_for_score(thesis["score"])
    margin = round(av * MARGIN_PCT, 2)

    success, result = execute_entry(thesis["direction"], margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)
        cfg.output({
            "status": "ok", "action": "ENTRY",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "leverage": leverage,
                "mode": "ETH_HUNTER", "reasons": thesis["reasons"]},
            "execution": {"asset": ASSET, "direction": thesis["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
            "result": result,
            "_polar_version": "2.1",
        })
    else:
        cfg.output({
            "status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "reasons": thesis["reasons"]},
            "error": result, "_polar_version": "2.1",
        })


if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
