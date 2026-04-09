#!/usr/bin/env python3
# Senpi DOG Scanner v1.1
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""DOG v1.1 — The Loyal Consistent Performer (Hardened).

v1.1 fixes from 24-hour forensics (2026-04-09):
Dog v1.0 lost -$105 on 23 trades. -$73.86 gross. The signal was WRONG.
Root causes:
  1. MIN_SCORE 9 too easy — DOMINANT_SM + DEEP_CONSENSUS + 4H + 1H + 15M +
     US_SESSION = 8 points. One 15M_SPIKE reaches 9. Now MIN_SCORE 10.
  2. Phase 1 max_loss_pct 15% at 10x = 1.5% price stop. BTC wicks 1% in
     30 minutes regularly. Trades stopped before thesis plays out. Now 25%.
  3. 4H alignment REWARDS a 1.9% move (+1) but exhaustion only PUNISHES
     at 2.0% — cliff function. Replaced with graduated curve.
  4. dead_weight_cut 45 min too aggressive — winners need 60-90 min.
  5. hard_timeout 120 min too short — even quick-profit needs 2-4 hours.
  6. weak_peak_cut min_value 1.0 meaningless at 10x (0.1% price move).

Multi-asset SM consensus scanner. BTC, ETH, SOL, HYPE.
Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dog_config as cfg

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

ASSETS = ["BTC", "ETH", "SOL", "HYPE"]
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 3
COOLDOWN_MINUTES = 180
SAME_DIR_COOLDOWN_MINUTES = 90
MARGIN_PCT = 0.30
MIN_SCORE = 10                 # v1.1: was 9, too easy to reach

ASSET_MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10}

LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 12},
    {"min_score": 10, "leverage": 10},
]
DEFAULT_LEVERAGE = 10
MAX_LEVERAGE = 12


def safe_float(v, d=0.0):
    if v is None: return d
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso(): return datetime.now(timezone.utc).isoformat()


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders."""
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


# ═══════════════════════════════════════════════════════════════
# SCORING — v1.1: graduated exhaustion, no cliff functions
# ═══════════════════════════════════════════════════════════════

def evaluate_assets():
    """Score all four assets. Return candidates sorted by score."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None
    markets = raw.get("data", raw)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

    asset_data = {}
    for m in markets:
        if not isinstance(m, dict): continue
        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        if dex or token not in ASSETS: continue
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        if token not in asset_data or pct > asset_data[token].get("pct_of_top_traders_gain", 0):
            asset_data[token] = m

    candidates = []
    for token in ASSETS:
        m = asset_data.get(token)
        if not m: continue

        d = str(m.get("direction", "")).upper()
        if d not in ("LONG", "SHORT"): continue

        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        p4h = safe_float(m.get("token_price_change_pct_4h", 0))
        p1h = safe_float(m.get("token_price_change_pct_1h", m.get("price_change_1h", 0)))
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
        cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))

        if traders < 30: continue

        score, reasons = 0, []

        # ── SM concentration (0-3) ──
        if pct >= 15: score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
        elif pct >= 10: score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
        elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

        # ── Trader depth (0-1) ──
        if traders >= 150: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

        # ── 4H MOVE ASSESSMENT — v1.1 graduated curve, replaces separate
        #    alignment + exhaustion blocks that created cliff at 2.0% ──
        # This is a SINGLE block. No double-counting.
        move_aligned = (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0)
        move_opposing = (d == "LONG" and p4h < 0) or (d == "SHORT" and p4h > 0)
        abs_p4h = abs(p4h)

        if move_opposing and abs_p4h >= 0.5:
            score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
        elif move_aligned:
            if abs_p4h < 0.3:
                score += 1; reasons.append(f"VERY_EARLY_MOVE {p4h:+.2f}% (pristine)")
            elif abs_p4h < 1.0:
                score += 1; reasons.append(f"EARLY_MOVE {p4h:+.1f}% (good timing)")
            elif abs_p4h < 1.5:
                # Neutral — move is underway but not exhausted. No bonus, no penalty.
                reasons.append(f"4H_UNDERWAY {p4h:+.1f}% (neutral)")
            elif abs_p4h < 2.5:
                score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}%")
            elif abs_p4h < 4.0:
                score -= 2; reasons.append(f"MOVE_EXHAUSTED {p4h:+.1f}%")
            else:
                score -= 3; reasons.append(f"MOVE_SPENT {p4h:+.1f}% (way too late)")

        # ── 1H momentum (0-1) ──
        if (d == "LONG" and p1h > 0.3) or (d == "SHORT" and p1h < -0.3):
            score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

        # ── 15m velocity — Dog only rewards strong spikes ──
        if cc_15m > 2.0: score += 3; reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.2: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
        elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

        # ── 1h acceleration (0-1) ──
        if cc_1h > 1.5: score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

        # ── Acceleration pattern — fresh signal confirmation ──
        if cc_15m > 0 and cc_1h > 0 and cc_15m > cc_1h:
            score += 1; reasons.append(f"ACCEL_PATTERN 15m>1h")

        # ── Funding alignment (0-1) ──
        try:
            ad = cfg.mcporter_call("market_get_asset_data", asset=token,
                                    candle_intervals=[], include_funding=True,
                                    include_order_book=False)
            if ad:
                ac = ad.get("data", ad).get("asset_context",
                     ad.get("data", ad).get("assetContext", {}))
                if isinstance(ac, dict):
                    funding = safe_float(ac.get("funding", 0))
                    if (d == "SHORT" and funding > 0.0002) or (d == "LONG" and funding < -0.0002):
                        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")
        except: pass

        # ── US session bonus (0-1) ──
        hour = datetime.now(timezone.utc).hour
        if 13 <= hour <= 21:
            score += 1; reasons.append("US_SESSION")

        candidates.append({
            "asset": token, "direction": d, "score": score,
            "reasons": reasons, "smPct": pct, "smTraders": traders,
            "priceChg4h": p4h,
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def execute_entry(asset, direction, margin, leverage):
    asset_max = ASSET_MAX_LEVERAGE.get(asset, 10)
    leverage = min(leverage, asset_max, MAX_LEVERAGE)
    result = cfg.mcporter_call(
        "create_position", coin=asset, direction=direction, leverage=leverage,
        margin=margin, orderType="FEE_OPTIMIZED_LIMIT",
        feeOptimizedLimitOptions={"ensureExecutionAsTaker": False, "executionTimeoutSeconds": 30},
    )
    if result and result.get("success"): return True, result
    error = result.get("error", "unknown") if result else "mcporter_call returned None"
    return False, {"error": error}


def load_tc():
    """Load trade counter. Timestamps persist across midnight."""
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0,
               "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f: tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc: tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError): pass
    return dict(default)

def save_tc(tc):
    tc["date"] = now_date()
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"}); return

    av, positions = cfg.get_positions(wallet)
    if av <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"}); return

    if positions:
        coins = [p.get("coin", "?") for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_v2_no_thesis_exit": True}); return

    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "RESTING ORDER: entry pending."}); return

    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached."}); return

    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Cooldown ({remaining}min remaining)."}); return

    candidates = evaluate_assets()
    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "SNIFFING: no SM signals on BTC/ETH/SOL/HYPE"}); return

    best = None
    for c in candidates:
        if c["score"] < MIN_SCORE:
            break
        last_win_dir = tc.get("last_win_direction")
        last_win_ts = tc.get("last_win_ts", 0)
        if last_win_dir and last_win_dir == c["direction"]:
            if last_win_ts and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
                continue
        best = c
        break

    if not best:
        top = candidates[0] if candidates else None
        note = "SNIFFING: no asset above threshold"
        if top:
            note = (f"SNIFFING: best {top['asset']} {top['direction']} "
                    f"score {top['score']}<{MIN_SCORE}. {', '.join(top['reasons'][:3])}")
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note}); return

    leverage = DEFAULT_LEVERAGE
    for tier in LEVERAGE_TIERS:
        if best["score"] >= tier["min_score"]:
            leverage = tier["leverage"]
            break
    margin = round(av * MARGIN_PCT, 2)

    success, result = execute_entry(best["asset"], best["direction"], margin, leverage)
    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)
        cfg.output({"status": "ok", "action": "ENTRY",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "leverage": leverage,
                "mode": "LOYAL_CONSISTENT", "reasons": best["reasons"]},
            "execution": {"asset": best["asset"], "direction": best["direction"],
                "leverage": leverage, "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT", "ensureExecutionAsTaker": False},
            "result": result, "_dog_version": "1.1"})
    else:
        cfg.output({"status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"]},
            "error": result, "_dog_version": "1.1"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
