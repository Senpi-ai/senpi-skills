#!/usr/bin/env python3
# Senpi SPIDER Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SPIDER v1.0 — Elite Convergence Scanner.

THESIS: When 2+ independently-operating ELITE/RELIABLE traders with
SNIPER/AGGRESSIVE risk profiles converge on the same asset and direction,
AND 15-minute SM velocity is spiking — enter with max conviction.

The convergence tells you WHAT. The velocity tells you WHEN.

No current agent uses this signal. Existing agents look at SM concentration
(which direction is money flowing), but not WHO is driving it. A 10% SM
concentration driven by 50 CHOPPY traders is noise. A 5% concentration
driven by 3 ELITE/SNIPER traders who are independently +30% ROI this week
is a very different signal.

HOW IT WORKS:
Phase 1 (every 5 min): Build convergence map
  1. Fetch weekly top traders filtered by TCS=ELITE/RELIABLE + Risk=SNIPER/AGGRESSIVE
     with open positions (discovery_get_top_traders)
  2. For each qualifying trader, get their current positions
     (leaderboard_get_trader_positions)
  3. Find assets where 2+ qualifying traders are positioned in the same direction
  4. Cache convergence map to state file

Phase 2 (every 90 sec): Check velocity on convergence assets
  1. Read cached convergence map
  2. leaderboard_get_markets → check 15m velocity on convergence assets
  3. Score: convergence quality + trader quality + velocity alignment
  4. Enter highest-scoring convergence above threshold

TIMING: The 15m velocity spike is the entry trigger. Even if elite traders
entered hours ago, a velocity spike means new money is flowing in NOW.
This solves "too late to the game" — we enter when the move is accelerating,
not when positions were opened.

Dry run (2026-04-07 20:00 UTC):
  BTC LONG — score 10 ✅ Would enter at 10x leverage
  - 0x52e6 (ELITE/100, SNIPER/92) + 0x4bd1 (RELIABLE/67, SNIPER/83) both 40x LONG BTC
  - SM: 16.5%, 15m velocity +5.59 (SPIKE), 1h +1.15 (ACCEL)
  - BTC moved from $68,500 to $69,380 (+1.28%) = +12.8% ROE at 10x in 2.5 hours

CONSTRAINTS:
- Max 1 position at a time
- Max 3 entries per day
- 120-minute cooldown between entries
- MIN_SCORE 8 (requires strong convergence + velocity)
- 50% margin, conviction-scaled leverage (7x/10x)
- Scanner enters, DSL exits. No thesis exit.

Uses: discovery_get_top_traders + leaderboard_get_trader_positions
      + leaderboard_get_markets + strategy_get_open_orders
Runs every 90 seconds (velocity check), convergence map refreshes every 5 min.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spider_config as cfg

MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 3
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.50
MIN_SCORE = 8
MIN_CONVERGENCE = 2    # Minimum ELITE/RELIABLE traders on same asset+direction
XYZ_BANNED = False     # Allow XYZ — elite traders trade CL, GOLD, etc.

# Leverage reduced from 7x/10x to 5x/7x. At 7x, Phase 1's 8% retrace
# gave only 1.14% price tolerance — normal volatility wicked every trade.
# At 5x with widened retrace, tolerance is ~3%, enough to survive noise.
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 7},
    {"min_score": 8,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5

# Quality filters for trader selection
TRADER_TCS_FILTER = ["ELITE", "RELIABLE"]
TRADER_RISK_FILTER = ["SNIPER", "AGGRESSIVE"]
MIN_WEEKLY_ROI = 3.0    # Must be +3% ROI this week to qualify
MAX_TRADERS_TO_SCAN = 30

# Convergence map refresh interval
CONVERGENCE_REFRESH_SECONDS = 300  # 5 minutes


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
# PHASE 1: BUILD CONVERGENCE MAP (every 5 min)
# ═══════════════════════════════════════════════════════════════

def fetch_elite_traders():
    """Get weekly top ELITE/RELIABLE + SNIPER/AGGRESSIVE traders with open positions."""
    data = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame="WEEKLY",
        sort_by="RETURN_ON_INVESTMENT",
        consistency=TRADER_TCS_FILTER,
        risk_labels=TRADER_RISK_FILTER,
        open_position_filter=True,
        limit=MAX_TRADERS_TO_SCAN,
    )
    if not data: return []

    traders_raw = data.get("data", data)
    if isinstance(traders_raw, dict):
        traders_raw = traders_raw.get("traders", [])
    if not isinstance(traders_raw, list): return []

    qualified = []
    for t in traders_raw:
        roi = safe_float(t.get("returnOnInvestment", 0))
        if roi < MIN_WEEKLY_ROI: continue

        qualified.append({
            "address": t.get("address", ""),
            "roi": roi,
            "winRate": safe_float(t.get("winRate", 0)),
            "tcs": t.get("tcsLabel", ""),
            "tcsValue": safe_float(t.get("tcsValue", 0)),
            "risk": t.get("riskLabel", ""),
            "riskScore": safe_float(t.get("riskScore", 0)),
        })

    return qualified


def fetch_trader_positions(trader_address):
    """Get current positions for a single trader."""
    data = cfg.mcporter_call(
        "leaderboard_get_trader_positions",
        trader_id=trader_address,
    )
    if not data: return []

    positions = data.get("data", data)
    if isinstance(positions, dict):
        positions = positions.get("positions", positions)
    if isinstance(positions, dict):
        positions = positions.get("positions", [])
    if not isinstance(positions, list): return []

    result = []
    for p in positions:
        market = str(p.get("market", "")).upper()
        direction = str(p.get("direction", "")).upper()
        if market and direction in ("LONG", "SHORT"):
            result.append({"market": market, "direction": direction})
    return result


def build_convergence_map(traders):
    """Build map of assets where 2+ elite traders converge."""
    asset_positions = {}

    for trader in traders:
        positions = fetch_trader_positions(trader["address"])
        for pos in positions:
            key = (pos["market"], pos["direction"])
            if key not in asset_positions:
                asset_positions[key] = []
            asset_positions[key].append({
                "address": trader["address"],
                "roi": trader["roi"],
                "winRate": trader["winRate"],
                "tcs": trader["tcs"],
                "tcsValue": trader["tcsValue"],
                "risk": trader["risk"],
                "riskScore": trader["riskScore"],
            })

    convergences = {}
    for (asset, direction), trader_list in asset_positions.items():
        if len(trader_list) >= MIN_CONVERGENCE:
            convergences[f"{asset}:{direction}"] = {
                "asset": asset,
                "direction": direction,
                "traders": trader_list,
                "num_traders": len(trader_list),
                "avg_tcs": sum(t["tcsValue"] for t in trader_list) / len(trader_list),
                "avg_risk": sum(t["riskScore"] for t in trader_list) / len(trader_list),
                "avg_roi": sum(t["roi"] for t in trader_list) / len(trader_list),
            }

    return convergences


def load_convergence_map():
    """Load cached convergence map from state file."""
    p = os.path.join(cfg.STATE_DIR, "spider-convergence.json")
    if os.path.exists(p):
        try:
            with open(p) as f: data = json.load(f)
            if time.time() - data.get("timestamp", 0) < CONVERGENCE_REFRESH_SECONDS:
                return data.get("convergences", {}), False  # cached, no refresh needed
        except: pass
    return {}, True  # needs refresh


def save_convergence_map(convergences):
    p = os.path.join(cfg.STATE_DIR, "spider-convergence.json")
    cfg.atomic_write(p, {"timestamp": time.time(), "convergences": convergences})


# ═══════════════════════════════════════════════════════════════
# PHASE 2: SCORE WITH VELOCITY (every 90 sec)
# ═══════════════════════════════════════════════════════════════

def fetch_sm_velocity():
    """Get SM velocity data for all markets."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return {}

    markets = raw.get("data", raw)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return {}

    velocity = {}
    for m in markets:
        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        key = f"xyz:{token}" if dex == "xyz" else token
        velocity[key] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "cc_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "cc_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "cc_4h": safe_float(m.get("contribution_pct_change_4h", 0)),
            "p4h": safe_float(m.get("token_price_change_pct_4h", 0)),
        }
    return velocity


def score_convergences(convergences, velocity):
    """Score each convergence with velocity timing signals."""
    scored = []

    for key, conv in convergences.items():
        asset = conv["asset"]
        direction = conv["direction"]
        n = conv["num_traders"]
        avg_tcs = conv["avg_tcs"]
        avg_risk = conv["avg_risk"]
        avg_roi = conv["avg_roi"]

        score, reasons = 0, []

        # Convergence depth (0-4)
        if n >= 5: score += 4; reasons.append(f"DEEP_CONVERGENCE ({n} elites)")
        elif n >= 3: score += 3; reasons.append(f"STRONG_CONVERGENCE ({n} elites)")
        elif n >= 2: score += 2; reasons.append(f"CONVERGENCE ({n} elites)")

        # Average TCS quality (0-2)
        if avg_tcs >= 80: score += 2; reasons.append(f"ELITE_QUALITY avg_tcs={avg_tcs:.0f}")
        elif avg_tcs >= 50: score += 1; reasons.append(f"RELIABLE_QUALITY avg_tcs={avg_tcs:.0f}")

        # Risk precision (0-1)
        if avg_risk >= 75: score += 1; reasons.append(f"HIGH_PRECISION avg_risk={avg_risk:.0f}")

        # Weekly ROI of converging traders (0-2)
        if avg_roi >= 20: score += 2; reasons.append(f"HOT_TRADERS avg_roi={avg_roi:.1f}%")
        elif avg_roi >= 10: score += 1; reasons.append(f"STRONG_TRADERS avg_roi={avg_roi:.1f}%")

        # SM velocity — THE TIMING SIGNAL (0-4)
        vm = velocity.get(asset, velocity.get(asset.replace("xyz:", ""), {}))
        if vm:
            sm_dir = vm.get("direction", "")
            cc_15m = vm.get("cc_15m", 0)
            cc_1h = vm.get("cc_1h", 0)
            sm_pct = vm.get("pct", 0)

            if sm_dir == direction and sm_pct >= 5:
                score += 1; reasons.append(f"SM_CONFIRMS {sm_pct:.1f}%")

            # 15m velocity freshness gate — striker-class hard gate
            if cc_15m <= 0:
                reasons.append(f"15M_STALE ({cc_15m:.2f})")
                continue  # SM not fresh, skip this convergence
            if cc_15m > 0.5:
                score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
            elif cc_15m > 0.1:
                score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")

            if cc_1h > 1.0:
                score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

            # v1.1: Move-exhaustion penalty — large existing moves reduce conviction
            p4h = vm.get("p4h", 0)
            if abs(p4h) >= 4.0:
                if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
                    score -= 2; reasons.append(f"MOVE_EXHAUSTION {p4h:+.1f}%")
            elif abs(p4h) >= 2.5:
                if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
                    score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}%")

        # US session (0-1)
        hour = datetime.now(timezone.utc).hour
        if 13 <= hour <= 21:
            score += 1; reasons.append("US_SESSION")

        scored.append({
            "asset": asset, "direction": direction, "score": score,
            "reasons": reasons, "traders": conv["traders"],
            "num_traders": n, "avg_tcs": avg_tcs, "avg_roi": avg_roi,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(asset, direction, margin, leverage):
    """Call create_position directly via mcporter."""
    result = cfg.mcporter_call(
        "create_position",
        coin=asset,
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
        except: pass
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
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    av, positions = cfg.get_positions(wallet)
    if av <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    # Riding — DSL manages all exits
    if positions:
        coins = [p.get("coin", "?") for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_v2_no_thesis_exit": True})
        return

    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Cooldown ({remaining}min remaining)"})
        return

    # Phase 1: Get or refresh convergence map
    convergences, needs_refresh = load_convergence_map()
    if needs_refresh:
        traders = fetch_elite_traders()
        if traders:
            convergences = build_convergence_map(traders)
            save_convergence_map(convergences)
        else:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": "SCANNING: no qualified ELITE/RELIABLE traders this week"})
            return

    if not convergences:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "SCANNING: no elite convergence found"})
        return

    # Phase 2: Score with live velocity
    velocity = fetch_sm_velocity()
    scored = score_convergences(convergences, velocity)

    # v1.1: Same-direction re-entry cooldown after a win
    SAME_DIR_COOLDOWN_MINUTES = 60
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)
    if last_win_dir and last_win_ts and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
        scored = [s for s in scored if s["direction"] != last_win_dir]

    if not scored or scored[0]["score"] < MIN_SCORE:
        best = scored[0] if scored else None
        note = "SCANNING: no convergence above threshold"
        if best:
            note = (f"SCANNING: best {best['asset']} {best['direction']} "
                    f"score {best['score']}<{MIN_SCORE}. {best['num_traders']} elites. "
                    f"{', '.join(best['reasons'][:3])}")
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note})
        return

    # Enter best convergence
    best = scored[0]
    leverage = get_leverage_for_score(best["score"])
    margin = round(av * MARGIN_PCT, 2)

    success, result = execute_entry(best["asset"], best["direction"], margin, leverage)

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        tc["last_entry_ts"] = time.time()
        save_tc(tc)
        cfg.output({
            "status": "ok", "action": "ENTRY",
            "signal": {
                "asset": best["asset"],
                "direction": best["direction"],
                "score": best["score"],
                "leverage": leverage,
                "mode": "ELITE_CONVERGENCE",
                "reasons": best["reasons"],
                "convergence": {
                    "num_traders": best["num_traders"],
                    "avg_tcs": round(best["avg_tcs"], 1),
                    "avg_roi": round(best["avg_roi"], 1),
                    "traders": [t["address"][:10] + "..." for t in best["traders"]],
                },
            },
            "execution": {
                "asset": best["asset"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "ensureExecutionAsTaker": False,
            },
            "result": result,
            "_spider_version": "1.0",
        })
    else:
        cfg.output({
            "status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"]},
            "error": result, "_spider_version": "1.0",
        })


if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
