#!/usr/bin/env python3
# Senpi DOG Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""DOG v2.0 — The Contrarian Pup (SM Exhaustion Fader).

v2.0 — DIRECTION FLIP.
Fleet analysis (April 10, 2026) found Dog's signal was perfectly inverted:
actual gross -$61, inverted +$61. HYPE caused -$91 of the -$105 net loss.
The SM consensus scanner was systematically entering after exhausted moves.

Dog v2.0 flips the thesis: instead of chasing SM consensus, fade it.
When SM consensus is overwhelmingly strong and the move is already
extended, trade the opposite direction.

Key changes from v1.0:
- CONTRARIAN FLIP: trade opposite to SM consensus direction
- Move-exhaustion inverted: now a BONUS (bigger move = better fade)
- Early-move bonus removed (early moves have nothing to fade)
- Leverage reduced: 7x base, 10x max (contrarian needs room)
- MIN_SCORE lowered to 8 (contrarian signals should fire more often)

Uses: leaderboard_get_markets + market_get_asset_data + strategy_get_open_orders
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dog_config as cfg

# ═══════════════════════════════════════════════════════════════
# CONSTANTS — Tuned for consistency, not magnitude
# ═══════════════════════════════════════════════════════════════

ASSETS = ["BTC", "ETH", "SOL", "HYPE"]
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 3          # 2-3 trades/day max. Quality over quantity.
COOLDOWN_MINUTES = 180         # 3 hours between entries. Patience.
SAME_DIR_COOLDOWN_MINUTES = 90 # 90 min after a win in the same direction
MARGIN_PCT = 0.30              # 30% of account per trade. Small bets.
MIN_SCORE = 8                  # Lowered from 9 — contrarian signals need to fire

# Max leverage caps per asset (from Hyperliquid)
ASSET_MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10}

# Conservative leverage for contrarian — need room for reversals
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 10},
    {"min_score": 8,  "leverage": 7},
]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10

# Move exhaustion — INVERTED for contrarian. Bigger moves = BETTER fades.
EXHAUSTION_BONUS_SEVERE_PCT = 4.0   # +2 points (deep exhaustion = great fade)
EXHAUSTION_BONUS_MODERATE_PCT = 2.5 # +1 point


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
# SCORING — Tight, focused, consistency-oriented
# ═══════════════════════════════════════════════════════════════

def evaluate_assets():
    """Score all four assets and return the best candidate above MIN_SCORE."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None
    markets = raw.get("data", raw)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

    # Build asset map
    asset_data = {}
    for m in markets:
        if not isinstance(m, dict): continue
        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        if dex or token not in ASSETS: continue
        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        # Keep highest conviction direction per asset
        if token not in asset_data or pct > asset_data[token].get("pct", 0):
            asset_data[token] = m

    # Score each asset
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
        cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

        # Hard gate: minimum SM engagement
        if traders < 30: continue

        score, reasons = 0, []

        # ── SM concentration (0-3) ──
        if pct >= 15: score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
        elif pct >= 10: score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
        elif pct >= 5: score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

        # ── Trader depth (0-1) ──
        if traders >= 100: score += 1; reasons.append(f"DEEP_CONSENSUS ({traders}t)")

        # ── 4H price alignment (+/-2) ──
        if abs(p4h) >= 2.0:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
            else:
                score -= 1; reasons.append(f"4H_OPPOSING {p4h:+.1f}%")
        elif abs(p4h) >= 0.5:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

        # ── MOVE EXHAUSTION — INVERTED for contrarian ──
        # Bigger moves = better fade opportunities (opposite of v1.0)
        if abs(p4h) >= EXHAUSTION_BONUS_SEVERE_PCT:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score += 2; reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}% (great fade)")
        elif abs(p4h) >= EXHAUSTION_BONUS_MODERATE_PCT:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score += 1; reasons.append(f"EXHAUSTION {p4h:+.1f}% (fadeable)")

        # ── 1H momentum (0-1) ──
        if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
            score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

        # ── 15m velocity freshness gate ──
        # For contrarian: SM must be actively building the position we're about to fade.
        # If SM is already unwinding (15m <= 0), the fade opportunity is passing — skip.
        if cc_15m <= 0:
            continue  # SM not fresh — stale signal, don't fade
        if cc_15m > 2.0: score += 3; reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")

        # ── 1h acceleration (0-1) ──
        if cc_1h > 1.0: score += 1; reasons.append(f"1H_ACCEL +{cc_1h:.2f}")

        # ── Funding alignment (0-1) — Dog likes funded trades ──
        # Fetch funding for this asset
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

    # Sort by score, return best above threshold
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def execute_entry(asset, direction, margin, leverage):
    """Place maker-only entry."""
    # Clamp leverage to asset max
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
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
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

    # Gate 1: Active positions
    if positions:
        coins = [p.get("coin", "?") for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit. Good boy waits.",
            "_v2_no_thesis_exit": True}); return

    # Gate 2: Resting orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "RESTING ORDER: entry pending. Patient pup."}); return

    # Gate 3: Daily limit
    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached. Good boy rests."}); return

    # Gate 4: General cooldown
    last_entry = tc.get("last_entry_ts", 0)
    if last_entry and (time.time() - last_entry) < COOLDOWN_MINUTES * 60:
        remaining = int((COOLDOWN_MINUTES * 60 - (time.time() - last_entry)) / 60)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Cooldown ({remaining}min remaining). Patience."}); return

    # Gate 5: Evaluate all assets
    candidates = evaluate_assets()
    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "SNIFFING: no SM signals on BTC/ETH/SOL/HYPE"}); return

    # Gate 6: Same-direction cooldown after win
    best = None
    for c in candidates:
        if c["score"] < MIN_SCORE:
            break  # sorted by score desc, so all below are lower
        last_win_dir = tc.get("last_win_direction")
        last_win_ts = tc.get("last_win_ts", 0)
        if last_win_dir and last_win_dir == c["direction"]:
            if last_win_ts and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
                continue  # skip this direction, try next candidate
        best = c
        break

    if not best:
        top = candidates[0] if candidates else None
        note = "SNIFFING: no asset above threshold"
        if top:
            note = (f"SNIFFING: best {top['asset']} SM={top['direction']} "
                    f"score {top['score']}<{MIN_SCORE}. {', '.join(top['reasons'][:3])}")
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note}); return

    # ── CONTRARIAN FLIP ──
    sm_direction = best["direction"]
    best["direction"] = "SHORT" if sm_direction == "LONG" else "LONG"
    best["reasons"].insert(0, f"CONTRARIAN_FLIP {best['asset']} (SM is {sm_direction})")

    # Execute entry — conservative leverage for contrarian
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
            "result": result, "_dog_version": "2.0"})
    else:
        cfg.output({"status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"]},
            "error": result, "_dog_version": "2.0"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
