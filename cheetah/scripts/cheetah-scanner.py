#!/usr/bin/env python3
# Senpi CHEETAH Scanner v2.1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""CHEETAH v3.0 — HYPE Contrarian (SM Exhaustion Fader).

v3.0 — DIRECTION FLIP.
Fleet analysis (April 10, 2026) found Cheetah's signal was perfectly
inverted: 33 trades, actual gross -$175, inverted +$175. The momentum
scanner was buying HYPE breakouts that immediately mean-reverted.
Cheetah's own diagnosis: "the thesis itself is actively broken for
HYPE's current price action."

Fix: flip direction. When SM piles into HYPE, fade it.

Changes from v2.1.1:
- CONTRARIAN FLIP: trade opposite to SM consensus direction
- Added MOVE_EXHAUSTION penalty (aligns with Horribilis/Grizzly v4.0)
- Simplified 15m velocity tiers (less spike-chasing)
- Added same-direction cooldown (60 min)
- Fixed resting order filter (now ignores reduceOnly DSL stops)

HYPE single-asset contrarian. CIRCLE → FADE → RIDE reversal.
Uses: leaderboard_get_markets + strategy_get_open_orders (2 API calls)
Runs every 90 seconds.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheetah_config as cfg

ASSET = "HYPE"
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 90
SAME_DIR_COOLDOWN_MINUTES = 60
MARGIN_PCT = 0.50
MIN_SCORE = 8
XYZ_BANNED = True

LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},   # HYPE max leverage on Hyperliquid is 10x
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10  # Hyperliquid HYPE hard cap


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
    """Check for non-reduceOnly resting orders. Ignores DSL stop-losses."""
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if isinstance(orders, list):
        for o in orders:
            if not o.get("reduceOnly", False):
                return True
    return False


def evaluate_hype():
    """Score HYPE with multi-window Hyperfeed velocity. No hard gates."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None

    markets = raw
    if isinstance(markets, dict): markets = markets.get("data", markets)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

    hype = None
    btc_4h = 0
    for m in markets:
        if not isinstance(m, dict): continue
        token = str(m.get("token", "")).upper()
        if token == ASSET:
            hype = m
        elif token == "BTC":
            btc_4h = safe_float(m.get("token_price_change_pct_4h", 0))

    if not hype: return None

    d = str(hype.get("direction", "")).upper()
    if d not in ("LONG", "SHORT"): return None

    pct = safe_float(hype.get("pct_of_top_traders_gain", 0))
    traders = int(hype.get("trader_count", 0))
    p4h = safe_float(hype.get("token_price_change_pct_4h", 0))
    p1h = safe_float(hype.get("token_price_change_pct_1h", hype.get("price_change_1h", 0)))
    cc_4h = safe_float(hype.get("contribution_pct_change_4h", 0))
    cc_15m = safe_float(hype.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(hype.get("contribution_pct_change_1h", 0))

    # Need minimum data
    if traders < 15: return None

    score, reasons = 0, []

    # SM concentration (0-4) — Cheetah uses higher tiers than fleet for HYPE
    if pct >= 30: score += 4; reasons.append(f"SM_DOMINANT {pct:.1f}% ({traders}t)")
    elif pct >= 20: score += 3; reasons.append(f"SM_HEAVY {pct:.1f}% ({traders}t)")
    elif pct >= 12: score += 2; reasons.append(f"SM_STRONG {pct:.1f}% ({traders}t)")
    elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # SM depth (0-2)
    if traders >= 200: score += 2; reasons.append(f"DEEP_SM ({traders}t)")
    elif traders >= 100: score += 1; reasons.append(f"BROAD_SM ({traders}t)")

    # 4H price alignment (±2) — score contributor, not gate
    if abs(p4h) >= 2.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 2; reasons.append(f"4H_STRONG {p4h:+.1f}%")
        else:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
    elif abs(p4h) >= 0.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # 1H momentum (0-1)
    if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # Move-exhaustion penalty
    if abs(p4h) >= 4.0:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 2; reasons.append(f"MOVE_EXHAUSTION {p4h:+.1f}%")
    elif abs(p4h) >= 2.5:
        if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
            score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}%")

    # Contribution velocity — simplified tiers (aligned with fleet contrarian agents)
    if cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
    elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
    elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

    if cc_1h > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

    if abs(cc_4h) >= 5.0: score += 1; reasons.append(f"4H_MAJOR_SHIFT {cc_4h:+.1f}")

    # Acceleration pattern: 15m > 1h > 0
    if cc_15m > 0 and cc_1h > 0 and cc_15m > cc_1h:
        score += 1; reasons.append(f"ACCEL_PATTERN 15m({cc_15m:.2f})>1h({cc_1h:.2f})")

    # BTC as booster (0-1) — not a gate, fetched from same API call
    if d == "LONG" and btc_4h > 0.5:
        score += 1; reasons.append(f"BTC_CONFIRMS +{btc_4h:.1f}%")
    elif d == "SHORT" and btc_4h < -0.5:
        score += 1; reasons.append(f"BTC_CONFIRMS {btc_4h:.1f}%")

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
    default = {"date": now_date(), "entries": 0, "last_entry_ts": 0,
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
                     "note": "RESTING ORDER: HYPE limit order pending. Waiting for fill."})
        return

    # RIDING: position open → NO_REPLY. DSL manages ALL exits.
    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"RIDING: HYPE {p.get('direction','?')}. DSL manages exit.",
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
            "note": f"HYPE on cooldown ({remaining}min remaining)"})
        return

    thesis = evaluate_hype()
    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "CIRCLING: no HYPE thesis"})
        return
    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"CIRCLING: HYPE SM={thesis['direction']} score {thesis['score']}<{MIN_SCORE}. {', '.join(thesis['reasons'][:3])}"})
        return

    # ── CONTRARIAN FLIP ──
    sm_direction = thesis["direction"]
    thesis["direction"] = "SHORT" if sm_direction == "LONG" else "LONG"
    thesis["reasons"].insert(0, f"CONTRARIAN_FLIP (SM is {sm_direction})")

    # Same-direction cooldown (post-flip direction)
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts:
        if (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
            if thesis["direction"] == last_win_dir:
                remaining = int((SAME_DIR_COOLDOWN_MINUTES * 60 - (time.time() - last_win_ts)) / 60)
                cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"SAME_DIR_COOLDOWN: won {last_win_dir} {remaining}min ago"})
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
                "mode": "HYPE_PREDATOR", "reasons": thesis["reasons"]},
            "execution": {"asset": ASSET, "direction": thesis["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
            "result": result,
            "_cheetah_version": "3.0",
        })
    else:
        cfg.output({
            "status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": ASSET, "direction": thesis["direction"],
                "score": thesis["score"], "reasons": thesis["reasons"]},
            "error": result, "_cheetah_version": "3.0",
        })


if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
