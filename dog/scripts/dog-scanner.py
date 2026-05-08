#!/usr/bin/env python3
# Senpi DOG Scanner v2.5
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""DOG v2.4 — Contrarian Pup (parser fix + DSL loosen for fades).

## v2.4 changes (2026-04-22) — WATCH-tier recovery support

Two surgical changes after Arena W4 showed +12.9% recovery while
Predators still at -11.2%:

1. funding_history parser fix — v2.3 added persistence + trend bonus
   scoring but it never fired because the parser read data.<field>
   directly; MCP returns data.data=[{asset, ...}]. Now parses the list
   correctly and normalizes funding_trend enum
   (INTENSIFYING→INCREASING, DECAYING→DECREASING). Same bug family as
   Pangolin v1.5.

2. DSL dead_weight_cut 30 → 60 min in runtime.yaml. Fades are mean
   reversions — need time to develop. 30 min cut winners that were
   consolidating before reversal.

Signal quality gates (MIN_SCORE=8, exhaustion gates) unchanged —
Arena W4 says the signal is working. Don't over-tune a recovering agent.

---

## v2.2 — EXHAUSTION GATE WIDENED.

After v2.1 shipped with a 2.5% exhaustion gate, 24h live data on
2026-04-15 showed Dog was taking small losses because BTC and HYPE
trends were blowing through the 2.5% level without reversing.
Example losses in that window:
  HYPE SHORT -$28.61, SOL LONG -$47.72, BTC SHORT -$32.58
The 2.5% gate was insufficient to identify genuine exhaustion in
trending markets. v2.2 raises the gate to 4.5% — a meaningful
overextension that requires the market to have genuinely moved too
far before Dog will fade it.

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


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Default starting budget — override per-agent if different

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap based on drawdown from starting budget.

    Winners get more trades (ride the hot hand).
    Losers get fewer trades (preserve capital).
    Catastrophic drawdown triggers HARD STOP (circuit breaker).
    """
    if starting_budget <= 0:
        return 4  # Safe fallback
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12   # Hot hand — up >5%
    elif pnl_pct >= 0:     return 8    # Small win / breakeven
    elif pnl_pct >= -5:    return 5    # Careful
    elif pnl_pct >= -15:   return 3    # Defensive
    elif pnl_pct >= -25:   return 1    # Preserve — only highest conviction
    else:                  return 0    # HARD STOP — circuit breaker

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
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC (default 600s / 10 min).

    Without auto-cancel, a maker FEE_OPTIMIZED_LIMIT order that never
    fills can lock the scanner out of new entries indefinitely, because
    every subsequent scan sees the stale order and aborts early. Ignores
    reduceOnly orders (those are DSL exit legs)."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600  # 10 minutes
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue  # Treat cancelled order as gone
        has_fresh = True
    return has_fresh

def _get_funding_regime():
    """v2.3: market-wide funding regime via new MCP tool."""
    try:
        r = cfg.mcporter_call("market_get_funding_regime")
        if r:
            return r.get("data", r).get("regime")
    except Exception:
        pass
    return None


def _get_funding_history(asset):
    """v2.4 parser fix: MCP returns data.data=[{asset, ...}], not data.<field>.
    v2.3 silently got None for persistence_hours and trend so the bonus
    scoring branch never fired. Now iterate the list + match by asset, and
    normalize funding_trend enum (INTENSIFYING→INCREASING, DECAYING→DECREASING)."""
    try:
        r = cfg.mcporter_call("market_get_funding_history", asset=asset)
        if not r:
            return None
        outer = r.get("data", r)
        rows = outer.get("data") if isinstance(outer, dict) else None
        if not rows:
            return None
        row = next((x for x in rows if x.get("asset") == asset), None)
        if row is None:
            return None
        raw_trend = (row.get("funding_trend") or row.get("trend") or "").upper()
        if raw_trend == "INTENSIFYING":
            trend = "INCREASING"
        elif raw_trend == "DECAYING":
            trend = "DECREASING"
        else:
            trend = raw_trend
        return {
            "persistence_hours": row.get("persistence_hours"),
            "trend": trend,
        }
    except Exception:
        return None


def _regime_confirms_fade(fade_direction, regime):
    """v2.3: fade is confirmed when regime shows crowding in OPPOSITE direction
    of trade. Dog goes SHORT to fade LONG_CROWDED. Returns True/False/None."""
    if regime is None or regime == "NEUTRAL":
        return None  # neutral — neither confirms nor denies
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


def evaluate_assets():
    """Score all four assets and return the best candidate above MIN_SCORE."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None
    markets = raw.get("data", raw)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

    # v2.3: fetch market-wide regime once per scan
    regime = _get_funding_regime()

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

        # CONTRARIAN EXHAUSTION GATE
        # v2.1 (2.5%): too loose — fired on trend-continuation candles,
        #   -$53 over 10 trades.
        # v2.2 (4.5%): too strict — fired ZERO times in ~3 weeks across
        #   BTC/ETH/SOL/HYPE. 4.5% 4h moves on majors are rare events
        #   (BTC's biggest 4h move in past 44h was +2.27%; majors rarely
        #   cross 4% in a 4h window).
        # v2.5 (3.0%): empirical sweet spot. Captures legitimate
        #   overextension events (~2-4 per week per asset) while still
        #   rejecting daily 1-2% trend-continuation noise. Combined
        #   with DEEP_EXHAUSTION_BONUS still triggering at higher levels
        #   (preserved below), bigger overextensions get +2 score on top.
        MIN_EXHAUSTION_PCT = 3.0
        if abs(p4h) < MIN_EXHAUSTION_PCT:
            continue  # Not exhausted yet — don't fight a fresh trend
        if (d == "LONG" and p4h < 0) or (d == "SHORT" and p4h > 0):
            continue  # SM direction opposes price — not an exhaustion pattern

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

        # ── v2.3: Regime HARD GATE ──
        # Dog fades SM consensus. Regime confirms crowding in the direction
        # we're fading. If regime contradicts (says crowd is with our direction),
        # we're fighting — not fading — and the signal is suspect. Skip.
        regime_confirms = _regime_confirms_fade(d, regime)
        if regime_confirms is False:
            continue  # regime contradicts fade direction — skip
        if regime_confirms is True:
            score += 2; reasons.append(f"REGIME_CONFIRMS_{regime}")
        elif regime is not None:
            reasons.append(f"REGIME_{regime}")  # neutral, no score change

        # ── v2.3: Persistence + trend via funding_history ──
        # If SM crowding has been persistent for hours, the fade is mature and
        # high-conviction. If crowding is still INCREASING, Dog may be early.
        fh = _get_funding_history(token)
        if fh:
            ph = fh.get("persistence_hours")
            trend = (fh.get("trend") or "").upper()
            try:
                ph_val = float(ph) if ph is not None else None
            except (TypeError, ValueError):
                ph_val = None
            if ph_val is not None:
                if ph_val >= 12:
                    score += 2; reasons.append(f"MATURE_CROWDING_{ph_val:.0f}h")
                elif ph_val >= 6:
                    score += 1; reasons.append(f"STABLE_CROWDING_{ph_val:.0f}h")
            if trend == "INCREASING":
                score -= 1; reasons.append("CROWDING_STILL_BUILDING (early fade)")
            elif trend == "DECREASING":
                score += 1; reasons.append("CROWDING_UNWINDING (fade confirmed)")

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


def execute_entry(wallet, asset, direction, margin, leverage):
    """Place maker-only entry."""
    # Clamp leverage to asset max
    asset_max = ASSET_MAX_LEVERAGE.get(asset, 10)
    leverage = min(leverage, asset_max, MAX_LEVERAGE)

    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": asset,
            "direction": direction,
            "leverage": leverage,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {"ensureExecutionAsTaker": False, "executionTimeoutSeconds": 30},
        }],
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
    dynamic_cap = get_dynamic_daily_cap(av)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((av - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

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

    success, result = execute_entry(wallet, best["asset"], best["direction"], margin, leverage)
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
            "result": result, "_dog_version": "2.5"})
    else:
        cfg.output({"status": "ok", "action": "ENTRY_FAILED",
            "signal": {"asset": best["asset"], "direction": best["direction"],
                "score": best["score"], "reasons": best["reasons"]},
            "error": result, "_dog_version": "2.5"})

if __name__ == "__main__":
    try: run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback; traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
