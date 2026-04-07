#!/usr/bin/env python3
# Senpi GRIZZLY HORRIBILIS Scanner v1.2
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""GRIZZLY HORRIBILIS v1.2 — BTC Conviction-Scaled Leverage Hunter.

v1.2: Position scaling (pyramiding into winners).
Unlike other fleet agents with MAX_POSITIONS=1, Horribilis can ADD to a
winning BTC position when conviction remains strong. This is the aggressive
bear — it presses winners.

Pyramiding rules:
1. Only add if existing position is in profit (ROE > +5%)
2. Signal must still be strong (score >= 9 for scale-ups)
3. SM must still be dominant in SAME direction
4. Max 3 total entries per position (initial + 2 scale-ups)
5. Each add uses 50% of remaining capital
6. Never add to a losing position. Never.

v1.1 changes carried forward:
- Scanner calls create_position internally (Wolverine pattern)
- feeOptimizedLimitOptions with ensureExecutionAsTaker: false
- Conviction-scaled leverage: score 8-9 → 7x, score 10+ → 10x
- 4H alignment → score contributor (not hard gate)
- Checks resting orders before placing new entries
- 50% margin on initial entry

Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grizzly_config as cfg

ASSET = "BTC"
MAX_POSITIONS = 1              # Still 1 position — but we ADD to it
MAX_SCALE_UPS = 2              # Max 2 additions to initial position (3 total entries)
MAX_DAILY_ENTRIES = 4          # Total entries including scale-ups (was 2)
COOLDOWN_MINUTES = 90          # Shorter cooldown — scale-ups should be timely (was 180)
INITIAL_MARGIN_PCT = 0.50      # 50% of account on initial entry
SCALEUP_MARGIN_PCT = 0.50      # 50% of REMAINING capital on each scale-up
MIN_SCORE = 8                  # Min score for initial entry
SCALE_MIN_SCORE = 9            # Higher bar for adding to position
SCALE_MIN_ROE_PCT = 5.0        # Position must be at least +5% ROE to add
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7


def safe_float(v, d=0.0):
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


def evaluate_btc():
    """Score BTC. All signals are score contributors — no hard gates."""
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

    if traders < 10: return None

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
    elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    if abs(p4h) >= 2.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    if (d=="LONG" and p1h>0.2) or (d=="SHORT" and p1h<-0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    if abs(cc)>=0.03: score += 2; reasons.append(f"CONTRIB_SURGE +{abs(cc)*100:.1f}%")
    elif abs(cc)>=0.01: score += 1; reasons.append(f"CONTRIB_GROWING +{abs(cc)*100:.2f}%")

    if (d=="SHORT" and funding>0.0002) or (d=="LONG" and funding<-0.0002):
        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    if traders >= 100: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

    return {
        "score": score, "direction": d, "reasons": reasons,
        "smPct": pct, "smTraders": traders, "priceChg4h": p4h,
    }


def execute_entry(direction, margin, leverage):
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
    return {"date": now_date(), "entries": 0, "scale_ups": 0}

def save_tc(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def load_position_state():
    p = os.path.join(cfg.STATE_DIR, "position-state.json")
    if os.path.exists(p):
        try:
            with open(p) as f: return json.load(f)
        except: pass
    return {"direction": None, "scale_count": 0, "entries": []}

def save_position_state(ps):
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "position-state.json"), ps)

def reset_position_state():
    save_position_state({"direction": None, "scale_count": 0, "entries": []})


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    av, positions = cfg.get_positions(wallet)
    if av <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # Check for resting orders — don't stack entries
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: BTC limit order pending. Waiting for fill."})
        return

    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # Find BTC position
    btc_pos = None
    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            btc_pos = p
            break

    ps = load_position_state()

    # ── CASE 1: No position — initial entry ──
    if not btc_pos:
        if ps.get("direction"):
            reset_position_state()
            ps = load_position_state()

        if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "BTC on cooldown"})
            return

        thesis = evaluate_btc()
        if not thesis:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "HUNTING: no BTC thesis"})
            return
        if thesis["score"] < MIN_SCORE:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"HUNTING: BTC {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"})
            return

        leverage = get_leverage_for_score(thesis["score"])
        margin = round(av * INITIAL_MARGIN_PCT, 2)

        success, result = execute_entry(thesis["direction"], margin, leverage)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            save_tc(tc)
            ps = {
                "direction": thesis["direction"],
                "scale_count": 0,
                "entries": [{"time": now_iso(), "score": thesis["score"],
                             "leverage": leverage, "margin": margin, "type": "INITIAL"}],
            }
            save_position_state(ps)
            cfg.output({
                "status": "ok", "action": "ENTRY",
                "signal": {"asset": ASSET, "direction": thesis["direction"],
                    "score": thesis["score"], "leverage": leverage,
                    "mode": "BTC_HUNTER", "reasons": thesis["reasons"]},
                "execution": {"asset": ASSET, "direction": thesis["direction"],
                    "leverage": leverage, "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
                "result": result, "_horribilis_version": "1.2",
            })
        else:
            cfg.output({"status": "ok", "action": "ENTRY_FAILED",
                "signal": {"asset": ASSET, "direction": thesis["direction"],
                    "score": thesis["score"], "reasons": thesis["reasons"]},
                "error": result, "_horribilis_version": "1.2"})
        return

    # ── CASE 2: Position exists — evaluate for scale-up ──
    current_direction = btc_pos["direction"]
    current_upnl = btc_pos.get("upnl", 0)
    current_margin = btc_pos.get("margin", 0)
    current_roe = (current_upnl / current_margin) * 100 if current_margin > 0 else 0

    # Max scale-ups reached
    if ps.get("scale_count", 0) >= MAX_SCALE_UPS:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. Max scale-ups ({MAX_SCALE_UPS}). DSL manages exit.",
            "_v2_no_thesis_exit": True})
        return

    # RULE: Never add to a losing position
    if current_roe < SCALE_MIN_ROE_PCT:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. Need +{SCALE_MIN_ROE_PCT}% to scale. DSL manages exit.",
            "_v2_no_thesis_exit": True})
        return

    # Evaluate signal
    thesis = evaluate_btc()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. No SM signal. DSL manages exit."})
        return

    # Must agree with current direction
    if thesis["direction"] != current_direction:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. SM flipped {thesis['direction']} — not adding."})
        return

    # Higher bar for scale-ups
    if thesis["score"] < SCALE_MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. Score {thesis['score']}<{SCALE_MIN_SCORE} for scale-up."})
        return

    # Cooldown between scale-ups
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. Scale-up on cooldown."})
        return

    # ── SCALE UP ──
    remaining_capital = av - current_margin
    if remaining_capital < 50:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: BTC {current_direction} ROE {current_roe:+.1f}%. Only ${remaining_capital:.0f} remaining — not enough."})
        return

    scale_margin = round(remaining_capital * SCALEUP_MARGIN_PCT, 2)
    leverage = get_leverage_for_score(thesis["score"])

    success, result = execute_entry(thesis["direction"], scale_margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        save_tc(tc)
        ps["scale_count"] = ps.get("scale_count", 0) + 1
        ps["entries"].append({
            "time": now_iso(), "score": thesis["score"],
            "leverage": leverage, "margin": scale_margin,
            "type": "SCALE_UP", "roe_at_add": round(current_roe, 2),
        })
        save_position_state(ps)
        cfg.set_asset_cooldown(ASSET, reason="scale_up")

        cfg.output({
            "status": "ok", "action": "SCALE_UP",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "leverage": leverage,
                "mode": "BTC_PYRAMID", "reasons": thesis["reasons"]},
            "execution": {"asset": ASSET, "direction": thesis["direction"],
                "leverage": leverage, "margin": scale_margin,
                "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
            "scaleInfo": {
                "scaleNumber": ps["scale_count"],
                "maxScaleUps": MAX_SCALE_UPS,
                "roeAtAdd": round(current_roe, 2),
                "existingMargin": round(current_margin, 2),
                "newMargin": scale_margin,
            },
            "result": result, "_horribilis_version": "1.2",
        })
    else:
        cfg.output({"status": "ok", "action": "SCALE_UP_FAILED",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "reasons": thesis["reasons"]},
            "error": result, "_horribilis_version": "1.2"})


if __name__ == "__main__":
    try: run()
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
