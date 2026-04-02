#!/usr/bin/env python3
# Senpi OWL Scanner v6.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""OWL v6.0 — Pure Contrarian Crowding Scanner.

The only agent in the fleet that enters AGAINST the crowd.

v5.2 had 7 trades and -0.9% ROE. The thesis was sound but the pipeline
was too complex — funding floor blocked everything, too many data sources
requiring too many API calls, old DSL cron architecture.

v6.0 simplifies radically:

1. CROWDING: leaderboard_get_markets shows where SM is heavily tilted.
   If SM is 15%+ concentrated with 80+ traders in one direction,
   that's the crowd.

2. FUNDING CONFIRMATION: market_get_asset_data checks funding rate.
   If funding aligns with the crowd (crowd long + positive funding,
   or crowd short + negative funding), the crowd is paying to hold.
   That's the setup for an unwind.

3. PRICE EXHAUSTION: if the 4H price change is small (<1%) despite
   heavy SM concentration and extreme funding, the crowd's trade has
   stopped working. They're trapped.

4. ENTER AGAINST: go opposite to the crowd direction.

That's it. Three signals, two API calls. No persistence timers,
no OI concentration, no RSI divergence, no volume declining checks.
The simplicity is the point — v5.2 proved that complexity prevented
the scanner from ever firing.

RE-CROWDING EXIT: The one exception to "no thesis exit." If the crowd
rebuilds stronger than when we entered (SM concentration increases AND
funding gets more extreme), the unwind thesis is dead. Exit immediately.
This is the ONLY case where the scanner can close a position.

Plugin runtime handles DSL trailing. Wide lifecycle hunter DSL —
contrarian entries retrace hard before working.

2 API calls per scan. Runs every 5 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 1                   # One contrarian bet at a time
MAX_DAILY_ENTRIES = 2
COOLDOWN_MINUTES = 360              # 6 hours — if contrarian thesis fails, wait
MARGIN_PCT = 0.20
MIN_SCORE = 8
XYZ_BANNED = True

# Crowding thresholds
MIN_SM_PCT = 12.0                   # SM must be heavily concentrated
MIN_SM_TRADERS = 60                 # Broad crowd, not a few whales
MIN_FUNDING_RATE = 0.00015          # Funding must be meaningful (annualized ~13%+)
MAX_PRICE_MOVE = 2.0                # Price must NOT have moved much (crowd is trapped)

# Re-crowding exit thresholds
RECROWDING_SM_INCREASE = 5.0        # SM% must increase by 5+ points from entry
RECROWDING_FUNDING_INCREASE = 1.5   # Funding must be 1.5x worse than at entry

# Assets to scan (liquid only)
SCAN_ASSETS = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "SUI", "AVAX",
               "LINK", "AAVE", "ARB", "OP", "MATIC", "WIF", "JUP",
               "PEPE", "NEAR", "FIL", "APT", "SEI", "TIA",
               "INJ", "RNDR", "FET", "BONK", "WLD"]


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso(): return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_sm_data():
    """Get SM positioning for all assets."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return {}

    markets = []
    if isinstance(raw, dict):
        raw_data = raw.get("data", raw)
        if isinstance(raw_data, dict):
            markets = raw_data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict): continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz": continue
        if not token or token not in SCAN_ASSETS: continue

        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
        }

    return sm_map


def fetch_funding(asset):
    """Get funding rate for a specific asset."""
    try:
        data = cfg.mcporter_call("market_get_asset_data",
                                  asset=asset, candle_intervals=[],
                                  include_funding=True)
        if not data: return 0

        ad = data.get("data", data)
        if not isinstance(ad, dict): return 0
        ac = ad.get("asset_context", ad.get("assetContext", {}))
        if not isinstance(ac, dict): return 0

        return safe_float(ac.get("funding", ac.get("fundingRate", 0)))
    except:
        return 0


# ═══════════════════════════════════════════════════════════════
# CROWDING DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_crowding(sm_map):
    """Find assets where the crowd is heavily positioned and potentially trapped.
    Returns list of candidates sorted by crowding score."""

    candidates = []

    for asset, sm in sm_map.items():
        crowd_direction = sm["direction"]
        if crowd_direction not in ("LONG", "SHORT"):
            continue

        sm_pct = sm["pct"]
        sm_traders = sm["traders"]
        price_chg_4h = sm["price_chg_4h"]

        # ── Hard gates ────────────────────────────────────────

        # SM must be heavily concentrated (the crowd exists)
        if sm_pct < MIN_SM_PCT:
            continue

        # Must be a broad crowd, not a few whales
        if sm_traders < MIN_SM_TRADERS:
            continue

        # Price must NOT have moved much in the crowd's direction
        # (if price moved 5% in their direction, the crowd is winning — not trapped)
        if crowd_direction == "LONG" and price_chg_4h > MAX_PRICE_MOVE:
            continue
        if crowd_direction == "SHORT" and price_chg_4h < -MAX_PRICE_MOVE:
            continue

        # ── Fetch funding (second API call, only for candidates) ──
        funding = fetch_funding(asset)

        # Funding must align with crowd direction (crowd is paying to hold)
        funding_aligned = False
        if crowd_direction == "LONG" and funding > MIN_FUNDING_RATE:
            funding_aligned = True
        elif crowd_direction == "SHORT" and funding < -MIN_FUNDING_RATE:
            funding_aligned = True

        if not funding_aligned:
            continue

        # ── Scoring ───────────────────────────────────────────

        score = 0
        reasons = []

        # SM concentration (0-4)
        if sm_pct >= 20:
            score += 4
            reasons.append(f"EXTREME_CROWD {sm_pct:.1f}% SM ({sm_traders}t)")
        elif sm_pct >= 15:
            score += 3
            reasons.append(f"HEAVY_CROWD {sm_pct:.1f}% SM ({sm_traders}t)")
        elif sm_pct >= 12:
            score += 2
            reasons.append(f"CROWDED {sm_pct:.1f}% SM ({sm_traders}t)")

        # Funding extremity (0-3)
        abs_funding = abs(funding)
        if abs_funding >= 0.0005:
            score += 3
            reasons.append(f"EXTREME_FUNDING {funding*100:.4f}%/hr")
        elif abs_funding >= 0.0003:
            score += 2
            reasons.append(f"HIGH_FUNDING {funding*100:.4f}%/hr")
        elif abs_funding >= MIN_FUNDING_RATE:
            score += 1
            reasons.append(f"FUNDING_PAYS {funding*100:.4f}%/hr")

        # Price exhaustion — crowd's trade stopped working (0-3)
        if crowd_direction == "LONG":
            if price_chg_4h < 0:
                score += 3
                reasons.append(f"CROWD_LOSING: LONG but price {price_chg_4h:+.1f}%")
            elif price_chg_4h < 0.5:
                score += 2
                reasons.append(f"PRICE_STALLING: LONG but only +{price_chg_4h:.1f}%")
            elif price_chg_4h < MAX_PRICE_MOVE:
                score += 1
                reasons.append(f"CROWD_WEAK: LONG, price +{price_chg_4h:.1f}% (underwhelming)")
        elif crowd_direction == "SHORT":
            if price_chg_4h > 0:
                score += 3
                reasons.append(f"CROWD_LOSING: SHORT but price +{price_chg_4h:.1f}%")
            elif price_chg_4h > -0.5:
                score += 2
                reasons.append(f"PRICE_STALLING: SHORT but only {price_chg_4h:.1f}%")
            elif price_chg_4h > -MAX_PRICE_MOVE:
                score += 1
                reasons.append(f"CROWD_WEAK: SHORT, price {price_chg_4h:.1f}% (underwhelming)")

        # Trader depth — more traders = bigger unwind (0-1)
        if sm_traders >= 100:
            score += 1
            reasons.append(f"DEEP_CROWD ({sm_traders}t)")

        # Contribution stalling — SM not gaining anymore (0-1)
        contrib = sm.get("contrib_change", 0)
        if abs(contrib) < 0.005:
            score += 1
            reasons.append("CONTRIB_STALLING (SM gains flat)")

        # Entry direction is OPPOSITE to crowd
        entry_direction = "SHORT" if crowd_direction == "LONG" else "LONG"

        candidates.append({
            "asset": asset,
            "crowdDirection": crowd_direction,
            "entryDirection": entry_direction,
            "score": score,
            "reasons": reasons,
            "smPct": sm_pct,
            "smTraders": sm_traders,
            "funding": funding,
            "priceChg4h": price_chg_4h,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════════
# RE-CROWDING EXIT
# ═══════════════════════════════════════════════════════════════

def check_recrowding(position, sm_map):
    """Check if the crowd has rebuilt stronger than when we entered.
    Returns True if we should exit (thesis dead)."""

    asset = position.get("coin", "").upper()
    sm = sm_map.get(asset)
    if not sm:
        return False

    # Load entry conditions from state
    state = load_entry_state(asset)
    if not state:
        return False

    entry_sm_pct = state.get("entrySMPct", 0)
    entry_funding = abs(state.get("entryFunding", 0))
    crowd_direction = state.get("crowdDirection", "")

    # Current SM in the CROWD's direction (not our direction)
    if sm["direction"] != crowd_direction:
        # SM flipped to our side — crowd is unwinding, thesis is working
        return False

    current_sm_pct = sm["pct"]
    current_funding = abs(fetch_funding(asset))

    # Re-crowding: SM concentration increased significantly AND funding got worse
    sm_increased = current_sm_pct >= entry_sm_pct + RECROWDING_SM_INCREASE
    funding_worse = entry_funding > 0 and current_funding >= entry_funding * RECROWDING_FUNDING_INCREASE

    if sm_increased and funding_worse:
        return True

    return False


# ═══════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_entry_state(asset):
    p = os.path.join(cfg.STATE_DIR, f"owl-entry-{asset}.json")
    if os.path.exists(p):
        try:
            with open(p) as f: return json.load(f)
        except: pass
    return None


def save_entry_state(asset, state):
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, f"owl-entry-{asset}.json"), state)


def clear_entry_state(asset):
    p = os.path.join(cfg.STATE_DIR, f"owl-entry-{asset}.json")
    if os.path.exists(p):
        try: os.remove(p)
        except: pass


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

    # ── Fetch SM data ─────────────────────────────────────────
    sm_map = fetch_sm_data()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no SM data"})
        return

    # ── RE-CROWDING CHECK on open positions ───────────────────
    # This is the ONE exception: OWL can close positions on thesis invalidation
    for pos in positions:
        asset = pos.get("coin", "").upper()
        if check_recrowding(pos, sm_map):
            # Thesis dead — crowd came back stronger. Close position.
            cfg.output({
                "status": "ok",
                "action": "close_position",
                "asset": asset,
                "reason": "RE-CROWDING: crowd rebuilt stronger. Thesis dead.",
                "_owl_recrowding_exit": True,
            })
            clear_entry_state(asset)
            return

    # ── If holding, output NO_REPLY ───────────────────────────
    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"HOLDING: {coins}. DSL manages trailing. Watching for re-crowding.",
                     "_v2_no_thesis_exit": True})
        return

    # ── Trade counter ─────────────────────────────────────────
    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Detect crowding ───────────────────────────────────────
    candidates = detect_crowding(sm_map)

    if not candidates:
        # Report top SM concentrations for debugging
        top_sm = sorted(sm_map.items(), key=lambda x: x[1]["pct"], reverse=True)[:3]
        top_summary = [(a, f"{d['pct']:.1f}% {d['direction']}") for a, d in top_sm]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No crowding detected. Top SM: {top_summary}"})
        return

    # ── Filter and enter ──────────────────────────────────────
    held_coins = {p["coin"].upper() for p in positions}

    for cand in candidates:
        asset = cand["asset"]
        if cand["score"] < MIN_SCORE:
            continue
        if asset in held_coins:
            continue
        if cfg.is_asset_cooled_down(asset, COOLDOWN_MINUTES):
            continue

        # ── Entry ─────────────────────────────────────────────
        margin = round(av * MARGIN_PCT, 2)

        # Save entry conditions for re-crowding check later
        save_entry_state(asset, {
            "entrySMPct": cand["smPct"],
            "entryFunding": cand["funding"],
            "crowdDirection": cand["crowdDirection"],
            "entryTime": now_iso(),
            "entryScore": cand["score"],
        })

        tc["entries"] = tc.get("entries", 0) + 1
        save_tc(tc)

        cfg.output({
            "status": "ok",
            "signal": {
                "asset": asset,
                "direction": cand["entryDirection"],
                "score": cand["score"],
                "mode": "CONTRARIAN",
                "reasons": cand["reasons"],
                "crowdDirection": cand["crowdDirection"],
                "smPct": cand["smPct"],
                "smTraders": cand["smTraders"],
                "funding": cand["funding"],
                "priceChg4h": cand["priceChg4h"],
            },
            "entry": {
                "asset": asset,
                "direction": cand["entryDirection"],
                "leverage": DEFAULT_LEVERAGE,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "constraints": {
                "maxPositions": MAX_POSITIONS,
                "maxLeverage": MAX_LEVERAGE,
                "maxDailyEntries": MAX_DAILY_ENTRIES,
                "cooldownMinutes": COOLDOWN_MINUTES,
                "_v2_no_thesis_exit": True,
                "_note": "DSL manages trailing. OWL only exits on RE-CROWDING (thesis dead).",
            },
            "_owl_version": "6.0",
        })
        return

    # Report best candidate that didn't pass
    if candidates:
        best = candidates[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best crowding: {best['asset']} crowd {best['crowdDirection']} "
                            f"score {best['score']}/{MIN_SCORE}. {', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Crowding found but no exhaustion signals"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
