#!/usr/bin/env python3
# Senpi DOG Scanner v1.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""DOG v1.0 — The Loyal Consistent Performer.

GOAL: 5% ROE per week. Not homeruns. Not glory. Just steady, reliable profits
that compound over time. The most loyal pup in the fleet.

DESIGN PHILOSOPHY (learned from 22-agent fleet analysis):
1. NEVER CHASE — the #1 fleet failure is entering after exhausted moves.
   Dog has the strictest move-exhaustion gates in the fleet.
2. TAKE PROFITS EARLY — other agents try to ride to 20-50% ROE and get
   stopped at Tier 1. Dog targets 3-5% ROE and exits cleanly.
3. SMALL POSITIONS — 30% margin, max 10x leverage. Smaller bets, more of them.
4. MULTI-ASSET — BTC, ETH, SOL, HYPE. Diversified, not concentrated.
5. QUALITY OVER QUANTITY — MIN_SCORE 10. Fewer trades, better quality.
   Fleet data shows higher-score entries have better win rates.
6. FEE-CONSCIOUS — 10x max leverage keeps notional reasonable. Maker-only.
   At 10x with $300 margin = $3,000 notional = ~$3 fees/trade.
7. FAST EXITS ON LOSERS — tight Phase 1, quick dead weight cut. Don't let
   losers linger. The fleet's losing trades average 79 min; Dog cuts at 60.
8. PATIENCE BETWEEN TRADES — 180-min cooldown. Only 2-3 trades per day max.
   The fleet's overtrading agents (Mantis old, Orca old) lost the most.

MATH: 5% ROE/week = $50 on $1,000.
  3 trades/day × 5 days = 15 trades/week.
  Need: +$50 net / 15 trades = +$3.33 net per trade average.
  At $3 fees/trade: need +$6.33 gross per trade.
  At 10x / $300 margin: need +2.1% ROE per trade average.
  With 60% win rate, avg winner +5% ROE, avg loser -3% ROE:
    0.6 × $15 - 0.4 × $9 = $9 - $3.60 = $5.40 gross/trade
    $5.40 - $3 fees = $2.40 net/trade × 15 = $36/week (3.6% ROE)
  With 65% win rate: $4.05 net/trade × 15 = $60.75/week (6.1% ROE) ✅

THESIS: Multi-asset SM consensus scanner. Scans BTC, ETH, SOL, HYPE every
3 minutes. Enters ONLY when SM consensus is overwhelming (>15%), velocity
is fresh (15m spike), price hasn't already moved too far (exhaustion gates),
and the move is EARLY (4h change < 2%). Takes profit at 3-5% ROE via a
tight Phase 2 Tier 1, with a quick exit on non-performers.

KEY DIFFERENCE FROM FLEET: Dog's Phase 2 is designed for QUICK PROFIT-TAKING.
Tier 1 triggers at 3% ROE and locks at 50% (1.5% ROE floor). This is TIGHTER
than the fleet standard — intentionally. Dog doesn't want to ride to 20% ROE.
Dog wants to bank 2-4% ROE and move on. The high lock % (50% vs 25-30%)
means once Tier 1 triggers, the floor is meaningful — not noise-level.

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
MIN_SCORE = 9                  # High bar, but reachable in moderate markets.

# Max leverage caps per asset (from Hyperliquid)
ASSET_MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "HYPE": 10}

# Mild conviction scaling — 10x base, 12x only on the strongest signals.
# No 15-20x. Fleet data shows that range amplifies fees into net losses.
LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 12},
    {"min_score": 9,  "leverage": 10},
]
DEFAULT_LEVERAGE = 10
MAX_LEVERAGE = 12

# Move exhaustion — Dog has the STRICTEST gates in the fleet.
# If the 4h move is already >2% in entry direction, Dog sits out.
# The fleet standard is 2.5%/-1 and 4%/-2. Dog is tighter: 2%/-2 and 3%/-3.
EXHAUSTION_SEVERE_PCT = 3.0    # -3 points (effectively blocks entry)
EXHAUSTION_MODERATE_PCT = 2.0  # -2 points (makes score 10 very hard to reach)

# Early-move bonus — Dog REWARDS entering early moves
EARLY_MOVE_THRESHOLD = 0.5     # 4h change < 0.5% = early move bonus +1


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

        # ── MOVE EXHAUSTION — Dog's strictest-in-fleet gates ──
        # Dog penalizes MORE aggressively than the fleet standard
        if abs(p4h) >= EXHAUSTION_SEVERE_PCT:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score -= 3; reasons.append(f"DOG_EXHAUSTION_SEVERE {p4h:+.1f}% (>3% already moved)")
        elif abs(p4h) >= EXHAUSTION_MODERATE_PCT:
            if (d == "LONG" and p4h > 0) or (d == "SHORT" and p4h < 0):
                score -= 2; reasons.append(f"DOG_EXHAUSTION {p4h:+.1f}% (>2% already moved)")

        # ── EARLY MOVE BONUS — Dog rewards getting in early ──
        if abs(p4h) < EARLY_MOVE_THRESHOLD:
            if (d == "LONG" and p4h >= 0) or (d == "SHORT" and p4h <= 0):
                score += 1; reasons.append(f"EARLY_MOVE {p4h:+.1f}% (fresh)")

        # ── 1H momentum (0-1) ──
        if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
            score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

        # ── 15m velocity — only fresh spikes matter ──
        if cc_15m > 2.0: score += 3; reasons.append(f"15M_STRONG_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.5: score += 2; reasons.append(f"15M_SPIKE +{cc_15m:.2f}")
        elif cc_15m > 0.1: score += 1; reasons.append(f"15M_BUILDING +{cc_15m:.2f}")
        elif cc_15m < -0.5: score -= 1; reasons.append(f"15M_FADING {cc_15m:.2f}")

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
            note = (f"SNIFFING: best {top['asset']} {top['direction']} "
                    f"score {top['score']}<{MIN_SCORE}. {', '.join(top['reasons'][:3])}")
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note}); return

    # Execute entry — mild conviction scaling
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
            "result": result, "_dog_version": "1.0"})
    else:
        cfg.output({"status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"]},
            "error": result, "_dog_version": "1.0"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
