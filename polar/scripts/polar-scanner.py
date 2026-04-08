#!/usr/bin/env python3
# Senpi POLAR Scanner v2.3
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""POLAR v2.3 — ETH Alpha Hunter (Entry Timing Fixes).

v2.3 changes from overnight analysis (2026-04-08):
- FIX: UTC midnight cooldown bug — last_entry_ts and last_win_ts now persist
  across date rollover. Only the daily entries counter resets at midnight.
- NEW: Move-exhaustion scoring — penalizes entering after large 4h moves.
  4h change >= 4% in entry direction: -2 points (MOVE_EXHAUSTION)
  4h change >= 2.5% in entry direction: -1 point (MOVE_TIRING)
  Creates tension with 4H_CONFIRMS: a 3% move gets net +1, a 5% move gets net 0.
- NEW: Same-direction re-entry cooldown — after a winning exit, blocks
  re-entering the same direction on ETH for 60 minutes. Prevents the pattern:
  catch breakout -> exit -> scanner sees hot 4h candle -> re-enters at top.

v2.2 changes:
- Conviction-scaled leverage: score 8->7x, 9->10x, 10->15x, 11+->20x
- Extreme velocity tiers: 15m >5.0->+4pts, >2.0->+3pts (was capped at +2)
- 1h acceleration: >3.0->+2pts (was capped at +1)
- ETH max leverage on Hyperliquid is 25x, we cap at 20x

ETH single-asset lifecycle hunter. HUNT -> RIDE -> re-HUNT.
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
SAME_DIR_COOLDOWN_MINUTES = 60
MARGIN_PCT = 0.50
MIN_SCORE = 8
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 11, "leverage": 20},
    {"min_score": 10, "leverage": 15},
    {"min_score": 9,  "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 20  # ETH max on HL is 25x, we cap at 20x


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
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data: return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list) and len(orders) > 0: return True
    return False


def evaluate_eth():
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
        if str(m.get("token", "")).upper() == ASSET: eth = m; break
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

    if traders < 15: return None

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

    # 4H price alignment (+/-2)
    if abs(p4h) >= 2.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
        else:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # v2.3: Move-exhaustion penalty — large existing moves reduce conviction.
    # Tension with 4H alignment: 3% move = net +1. 4%+ move = net 0 or -1.
    if abs(p4h) >= 4.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 2; reasons.append(f"MOVE_EXHAUSTION {p4h:+.1f}% (4h already extended)")
    elif abs(p4h) >= 2.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}% (4h extended)")

    # 1H momentum (0-1)
    if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # Contribution velocity — expanded extreme tiers
    if cc_15m > 5.0: score += 4; reasons.append(f"15M_EXTREME_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 2.0: score += 3; reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
    elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

    if cc_1h > 3.0: score += 2; reasons.append(f"1H_STRONG_ACCEL +{cc_1h:.2f}")
    elif cc_1h > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

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
    result = cfg.mcporter_call(
        "create_position", coin=ASSET, direction=direction, leverage=leverage,
        margin=margin, orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={"ensureExecutionAsTaker": False, "executionTimeoutSeconds": 30},
    )
    if result and result.get("success"): return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def load_tc():
    """Load trade counter. v2.3 fix: timestamps persist across midnight.
    Only the daily entries counter resets on date change."""
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0,
               "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != now_date():
                # New day: reset daily entries counter ONLY.
                # Preserve last_entry_ts, last_win_direction, last_win_ts
                # so cooldowns survive midnight.
                tc["date"] = now_date()
                tc["entries"] = 0
            # Backfill any missing keys from older versions
            for k, v in default.items():
                if k not in tc:
                    tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError):
            pass
    return dict(default)

def save_tc(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"}); return

    av, positions = cfg.get_positions(wallet)
    if av <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"}); return

    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: ETH limit order pending."}); return

    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"RIDING: ETH {p.get('direction','?')}. DSL manages exit.",
                "_v2_no_thesis_exit": True}); return

    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"}); return

    # General cooldown (survives midnight)
    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"ETH on cooldown ({remaining}min remaining)"}); return

    # Score ETH
    thesis = evaluate_eth()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "HUNTING: no ETH thesis"}); return

    # v2.3: Same-direction re-entry cooldown after a win
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_dir == thesis["direction"]:
        if last_win_ts and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"SAME_DIR_COOLDOWN: won {last_win_dir} {remaining}min ago, waiting"}); return

    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING: ETH {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"}); return

    leverage = get_leverage_for_score(thesis["score"])
    margin = round(av * MARGIN_PCT, 2)

    success, result = execute_entry(thesis["direction"], margin, leverage)
    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)
        cfg.output({"status": "ok", "action": "ENTRY",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "leverage": leverage,
                "mode": "ETH_HUNTER", "reasons": thesis["reasons"]},
            "execution": {"asset": ASSET, "direction": thesis["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
            "result": result, "_polar_version": "2.3"})
    else:
        cfg.output({"status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "reasons": thesis["reasons"]},
            "error": result, "_polar_version": "2.3"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
