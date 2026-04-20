#!/usr/bin/env python3
# Senpi PANGOLIN Scanner v1.4
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PANGOLIN v1.4 — Extreme Funding Rate Fader (persistence + regime upgrade).

## v1.4 changes (2026-04-20) — SIGNAL QUALITY UPGRADE

Integrates three new Senpi MCP market-data tools. Pangolin's original thesis
was right — fade extreme funding — but the execution suffered from two blind
spots that the new tools solve:

1. NO PERSISTENCE CHECK: v1.3 fired on instantaneous funding rate, so a
   one-hour funding spike could trigger a trade before the crowd was
   actually crowded. Now uses market_get_funding_history.persistence_hours
   to require funding has been extreme for at least 3 consecutive hours.

2. NO MACRO CROWDING SIGNAL: v1.3 judged crowding per-asset only. Now uses
   market_get_funding_regime to cross-check market-wide positioning.
   If we want to fade longs (go SHORT) but the market regime isn't
   LONG_CROWDED, we're probably looking at an isolated anomaly, not
   a real crowding setup.

### New scoring components

    persistence_hours >= 12  → +3 (mature crowding, highest-conviction fade)
    persistence_hours >= 6   → +2 (stable crowding)
    persistence_hours >= 3   → +1 (minimum to act)
    persistence_hours < 3    → SKIP (fresh spike, likely noise)

    trend == INCREASING      → +1 (crowding still building, strong fade)
    trend == DECREASING      → -1 (crowding already unwinding, late entry)

    regime matches fade      → +2 (macro consensus with our trade)
    regime opposes fade      → SKIP (we'd be fighting the crowd, not fading)

MIN_SCORE raised 7 → 9 because v1.4 adds up to +6 new points.

### Null handling (per skill-dev notes)

persistence_hours uses up to 48h of ClickHouse history. On freshly-polled
assets it starts low (~1h). funding_history values can be null on first
deployment. v1.4 handles null as "not enough data, skip" rather than as
zero — conservative default.

---

## v1.3 changes (2026-04-16) — CRITICAL UNBLOCK

Root cause: market_list_instruments returns funding/OI/price nested inside
the `context` sub-object. Pre-v1.3 read them at top level and got 0 — every
asset failed filters. v1.3 reads from context correctly + lowered MIN_OI_USD
from $3M to $1M. Unblocked Pangolin to actually trade.

## Thesis

Extreme funding = crowd paying heavily to hold position. Mean-reverts within
24-48h as cost of carry forces liquidation. Pangolin enters opposite direction
— collects funding every 8h while waiting for crowd to capitulate.

Edge comes from:
1. Collecting funding every 8 hours while waiting for mean reversion
2. Price reversal when the overcrowded side unwinds

Different from:
- Barracuda (follows trend + collects funding as bonus)
- Lemon (fades degen traders based on track record)
- Vulture (fades SM consensus exhaustion on price action)

Runs every 5 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pangolin_config as cfg

# ── CONFIGURATION ──
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 3


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:       return 12
    elif pnl_pct >= 0:     return 8
    elif pnl_pct >= -5:    return 5
    elif pnl_pct >= -15:   return 3
    elif pnl_pct >= -25:   return 1
    else:                  return 0


COOLDOWN_MINUTES = 240
MARGIN_PCT = 0.25
MIN_SCORE = 9                    # v1.4: raised from 7 (new signals add points)
MIN_FUNDING_RATE = 0.00015
XYZ_BANNED = True
MIN_OI_USD = 1_000_000

# v1.4: persistence requirements (new funding_history tool)
MIN_PERSISTENCE_HOURS = 3        # Hard gate: skip funding spikes < 3h old

# Very conservative leverage — crowded unwinds are violent
LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 5},  # raised from score 10 (new point ceiling)
    {"min_score": 9,  "leverage": 3},
]
DEFAULT_LEVERAGE = 3


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling stale >10min."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600
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
            continue
        has_fresh = True
    return has_fresh


# ═══════════════════════════════════════════════════════════════
# v1.4: New MCP data tools integration
# ═══════════════════════════════════════════════════════════════

def get_funding_regime():
    """Fetch market-wide funding regime (LONG_CROWDED / SHORT_CROWDED / NEUTRAL).
    One call per scan. Returns regime string or None on failure."""
    try:
        r = cfg.mcporter_call("market_get_funding_regime")
        if not r:
            return None
        data = r.get("data", r)
        return data.get("regime")
    except Exception:
        return None


def get_funding_history(asset):
    """Fetch per-asset funding history with persistence + trend.
    Returns dict with persistence_hours, trend, or None on failure / no data."""
    try:
        r = cfg.mcporter_call("market_get_funding_history", asset=asset)
        if not r:
            return None
        data = r.get("data", r)
        return {
            "persistence_hours": data.get("persistence_hours"),
            "funding_direction": data.get("funding_direction"),
            "trend": data.get("trend"),
            "annualized_pct": data.get("annualized_pct"),
        }
    except Exception:
        return None


def regime_confirms_fade(fade_direction, regime):
    """A fade is confirmed when regime shows crowding in the OPPOSITE direction
    of our trade. We go SHORT to fade LONG_CROWDED. We go LONG to fade SHORT_CROWDED.

    Returns True/False/None (None = regime unavailable, neutral)."""
    if regime is None or regime == "NEUTRAL":
        return None
    if fade_direction == "SHORT" and regime == "LONG_CROWDED":
        return True
    if fade_direction == "LONG" and regime == "SHORT_CROWDED":
        return True
    return False


# ═══════════════════════════════════════════════════════════════

def scan_funding_extremes():
    """Find assets with extreme funding rates, gate by v1.4 persistence +
    regime checks, then score. Returns sorted list of candidates."""
    # Pull universe + SM data (one call each)
    raw = cfg.mcporter_call("market_list_instruments")
    if not raw:
        return [], None

    instruments = raw.get("data", raw)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", instruments.get("universe", []))
    if not isinstance(instruments, list):
        return [], None

    sm_raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    sm_map = {}
    if sm_raw:
        sm_markets = sm_raw.get("data", sm_raw)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", sm_markets)
        if isinstance(sm_markets, dict):
            sm_markets = sm_markets.get("markets", [])
        if isinstance(sm_markets, list):
            for m in sm_markets:
                if isinstance(m, dict):
                    token = str(m.get("token", "")).upper()
                    dex = str(m.get("dex", "")).lower()
                    if dex != "xyz":
                        sm_map[token] = m

    # v1.4: Fetch market-wide regime once
    regime = get_funding_regime()

    candidates = []

    for inst in instruments:
        if not isinstance(inst, dict):
            continue

        name = str(inst.get("name", inst.get("coin", ""))).upper()
        dex = str(inst.get("dex", "")).lower()

        if XYZ_BANNED and dex == "xyz":
            continue

        # v1.3 context-nested read
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}

        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", ctx.get("midPx",
                              inst.get("markPx", inst.get("midPx", 0)))))
        oi_usd = oi * mark_px if mark_px > 0 else 0
        if oi_usd < MIN_OI_USD:
            continue

        funding = safe_float(ctx.get("funding", inst.get("funding", 0)))

        if abs(funding) < MIN_FUNDING_RATE:
            continue

        # Determine directions
        crowd_direction = "LONG" if funding > 0 else "SHORT"
        fade_direction = "SHORT" if funding > 0 else "LONG"

        # ═══════════════════════════════════════════════════════
        # v1.4 HARD GATE 1: regime must confirm fade (or be neutral/unavailable)
        # ═══════════════════════════════════════════════════════
        regime_confirms = regime_confirms_fade(fade_direction, regime)
        if regime_confirms is False:
            # Regime says crowd is on OUR side — we'd be fighting, not fading. Skip.
            continue

        # ═══════════════════════════════════════════════════════
        # v1.4 HARD GATE 2: persistence must be >= MIN_PERSISTENCE_HOURS
        # ═══════════════════════════════════════════════════════
        fh = get_funding_history(name)
        if fh is None:
            # No history tool response — treat as insufficient data, skip
            continue

        persistence_hours = fh.get("persistence_hours")
        if persistence_hours is None:
            # Null persistence = not enough ClickHouse history yet, skip conservatively
            continue
        try:
            persistence_hours = float(persistence_hours)
        except (TypeError, ValueError):
            continue

        if persistence_hours < MIN_PERSISTENCE_HOURS:
            # Funding is extreme NOW but has been that way <3h — likely a spike, skip
            continue

        # ═══════════════════════════════════════════════════════
        # Scoring
        # ═══════════════════════════════════════════════════════

        score = 0
        reasons = []

        # ── Funding extremity (1-4 pts) ──
        abs_funding = abs(funding)
        annualized = abs_funding * 3 * 365 * 100
        if abs_funding >= 0.001:
            score += 4
            reasons.append(f"EXTREME_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0006:
            score += 3
            reasons.append(f"HIGH_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")
        elif abs_funding >= 0.0003:
            score += 2
            reasons.append(f"ELEVATED_FUNDING {funding*100:.4f}% ({annualized:.0f}% ann)")

        # ── v1.4: Persistence bonus (1-3 pts) ──
        if persistence_hours >= 12:
            score += 3
            reasons.append(f"MATURE_CROWDING {persistence_hours:.0f}h")
        elif persistence_hours >= 6:
            score += 2
            reasons.append(f"STABLE_CROWDING {persistence_hours:.0f}h")
        else:
            score += 1
            reasons.append(f"FRESH_CROWDING {persistence_hours:.0f}h")

        # ── v1.4: Trend direction (-1 to +1 pts) ──
        trend = fh.get("trend", "").upper() if fh.get("trend") else ""
        if trend == "INCREASING":
            score += 1
            reasons.append("CROWDING_INCREASING")
        elif trend == "DECREASING":
            score -= 1
            reasons.append("CROWDING_DECREASING")

        # ── v1.4: Regime confirmation bonus (+2 pts) ──
        if regime_confirms is True:
            score += 2
            reasons.append(f"REGIME_CONFIRMS_{regime}")
        elif regime_confirms is None and regime is not None:
            reasons.append(f"REGIME_{regime}")  # neutral, no score adjustment

        # ── SM confirmation (0-3 pts) ──
        sm = sm_map.get(name)
        if sm:
            sm_dir = str(sm.get("direction", "")).upper()
            sm_pct = safe_float(sm.get("pct_of_top_traders_gain", 0))
            sm_traders = int(sm.get("trader_count", 0))

            if sm_dir == fade_direction:
                if sm_pct >= 10:
                    score += 3
                    reasons.append(f"SM_FADING_CROWD {sm_pct:.1f}% ({sm_traders}t)")
                elif sm_pct >= 5:
                    score += 2
                    reasons.append(f"SM_ALIGNED_FADE {sm_pct:.1f}% ({sm_traders}t)")
                else:
                    score += 1
                    reasons.append(f"SM_CONFIRMS {sm_pct:.1f}% ({sm_traders}t)")
            elif sm_dir == crowd_direction:
                if sm_pct >= 10:
                    score -= 2
                    reasons.append(f"SM_WITH_CROWD {sm_pct:.1f}% (dangerous)")
                else:
                    score -= 1
                    reasons.append(f"SM_SLIGHT_CROWD {sm_pct:.1f}%")

            cc_15m = safe_float(sm.get("contribution_pct_change_15m", 0))
            if sm_dir == crowd_direction and cc_15m < -0.5:
                score += 1
                reasons.append(f"SM_MOMENTUM_FADING {cc_15m:.2f}")

        # ── OI turnover (sticky positions) ──
        volume_24h = safe_float(inst.get("dayNtlVlm", inst.get("volume24h", 0)))
        if oi > 0 and volume_24h > 0:
            oi_turnover = volume_24h / oi if oi > 0 else 0
            if oi_turnover < 0.5:
                score += 1
                reasons.append(f"STICKY_OI (turnover {oi_turnover:.2f}x)")

        # ── Price reversing already? ──
        p4h = safe_float(sm.get("token_price_change_pct_4h", 0)) if sm else 0
        if crowd_direction == "LONG" and p4h < -0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")
        elif crowd_direction == "SHORT" and p4h > 0.5:
            score += 1
            reasons.append(f"PRICE_REVERSING {p4h:+.1f}%")

        reasons.insert(0, f"FADE_FUNDING {name} (crowd is {crowd_direction})")

        candidates.append({
            "token": name,
            "funding": funding,
            "crowd_direction": crowd_direction,
            "fade_direction": fade_direction,
            "score": score,
            "reasons": reasons,
            "annualized_pct": annualized,
            "persistence_hours": persistence_hours,
            "trend": trend,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates, regime


def execute_entry(wallet, token, direction, margin, leverage):
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[{
            "coin": token,
            "direction": direction,
            "leverage": leverage,
            "marginAmount": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "feeOptimizedLimitOptions": {
                "ensureExecutionAsTaker": False,
                "executionTimeoutSeconds": 45,
            },
        }],
    )
    if not result:
        return False, {"error": "mcporter_call returned None"}
    if not result.get("success"):
        return False, {"error": result.get("error", "outer_envelope_failed"), "raw": result}

    # v1.4: inner-order validation (fleet-standard pattern)
    data = result.get("data", result)
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if orders and isinstance(orders, list):
        first = orders[0] if isinstance(orders[0], dict) else {}
        if first and not first.get("success", True):
            return False, {"error": first.get("error", "inner_order_failed"), "raw": result}

    return True, result


def run():
    wallet, sid = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"CURLED UP: {coins}. DSL manages exit. Collecting funding.",
                     "_v2_no_thesis_exit": True})
        return

    tc = cfg.load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # Scan with v1.4 gates
    candidates, regime = scan_funding_extremes()
    if not candidates:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"SNIFFING: no candidates passed gates. regime={regime}"})
        return

    held_coins = {p["coin"].upper() for p in positions}

    for cand in candidates:
        token = cand["token"]

        if token in held_coins:
            continue

        if cand["score"] < MIN_SCORE:
            continue

        if cfg.is_asset_cooled_down(token, COOLDOWN_MINUTES):
            continue

        leverage = get_leverage_for_score(cand["score"])
        margin = round(account_value * MARGIN_PCT, 2)

        success, result = execute_entry(wallet, token, cand["fade_direction"], margin, leverage)

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            cfg.save_trade_counter(tc)
            cfg.set_asset_cooldown(token, COOLDOWN_MINUTES, reason="entry")

            cfg.output({
                "status": "ok",
                "action": "FUNDING_FADE",
                "signal": {
                    "asset": token,
                    "crowd_direction": cand["crowd_direction"],
                    "fade_direction": cand["fade_direction"],
                    "funding_rate": cand["funding"],
                    "annualized_pct": round(cand["annualized_pct"], 1),
                    "persistence_hours": cand["persistence_hours"],
                    "trend": cand["trend"],
                    "regime": regime,
                    "score": cand["score"],
                    "leverage": leverage,
                    "mode": "FUNDING_FADE",
                    "reasons": cand["reasons"],
                },
                "execution": {
                    "asset": token,
                    "direction": cand["fade_direction"],
                    "leverage": leverage,
                    "margin": margin,
                    "orderType": "FEE_OPTIMIZED_LIMIT",
                    "ensureExecutionAsTaker": False,
                },
                "result": result,
                "_pangolin_version": "1.4",
            })
            return
        else:
            cfg.output({
                "status": "ok",
                "action": "FUNDING_FADE_FAILED",
                "signal": {"asset": token, "score": cand["score"],
                           "reasons": cand["reasons"]},
                "error": result,
                "_pangolin_version": "1.4",
            })
            return

    # No candidates passed
    best = candidates[0] if candidates else None
    if best:
        note = (f"SNIFFING: best {best['token']} funding {best['funding']*100:.4f}% "
                f"persistence {best['persistence_hours']:.0f}h score {best['score']}<{MIN_SCORE} "
                f"regime={regime}")
    else:
        note = f"SNIFFING: no extreme funding passed gates. regime={regime}"
    cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": note})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
