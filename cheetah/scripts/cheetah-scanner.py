#!/usr/bin/env python3
# Senpi CHEETAH Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""CHEETAH v2.0 — HYPE Predator.

Hunts HYPE exclusively using SM commitment as the primary signal.
Unlike Wolverine (full timeframe alignment), Cheetah fires when SM
commitment exceeds threshold — overwhelming smart money consensus.

Top performer in the fleet at +7.6% / 38 trades.

Key signals:
- SM_DOMINANT: SM concentration on HYPE (threshold: high %)
- 4H trend alignment (hard gate)
- 1H momentum (score booster)
- CONTRIB_SURGE: contribution_pct_change_4h (SM velocity)
- BTC as booster, not gate

All v2.0 fleet fixes:
- MCP response parsing: handles data.markets.markets nesting
- Trade counter increments BEFORE output (Phoenix fix)
- Trade counter auto-resets on stale date
- _v2_no_thesis_exit: True on every output
- RIDING mode outputs NO_REPLY only
- Config helper points to cheetah-strategy

DSL exit managed by plugin runtime via runtime.yaml.
Uses: leaderboard_get_markets (single API call per scan)
Runs every 90 seconds.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheetah_config as cfg

ASSET = "HYPE"
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 7
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 90
MARGIN_PCT = 0.25
MIN_SCORE = 8
XYZ_BANNED = True

# SM thresholds for HYPE
MIN_SM_PCT = 8.0
MIN_SM_TRADERS = 30


def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso(): return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# MCP PARSING — handles data.markets.markets nesting
# ═══════════════════════════════════════════════════════════════

def fetch_sm_for_hype():
    """Fetch SM data for HYPE. Correctly handles nested MCP response."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None

    # Handle triple nesting: data.markets.markets
    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None

    for m in markets:
        if not isinstance(m, dict): continue
        token = str(m.get("token", "")).upper()
        if token == ASSET:
            return {
                "direction": str(m.get("direction", "")).upper(),
                "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
                "traders": int(m.get("trader_count", 0)),
                "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
                "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                           m.get("price_change_1h", 0))),
                "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
            }
    return None


def fetch_btc_trend():
    """Check BTC 4H trend as a conviction booster."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return 0

    markets = raw
    if isinstance(markets, dict): markets = markets.get("data", markets)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return 0

    for m in markets:
        if not isinstance(m, dict): continue
        if str(m.get("token", "")).upper() == "BTC":
            return safe_float(m.get("token_price_change_pct_4h", 0))
    return 0


# ═══════════════════════════════════════════════════════════════
# HYPE THESIS EVALUATION — 14-point scoring
# ═══════════════════════════════════════════════════════════════

def evaluate_hype():
    """Evaluate HYPE entry thesis. Returns dict or None."""

    hype = fetch_sm_for_hype()
    if not hype: return None

    d = hype["direction"]
    if d not in ("LONG", "SHORT"): return None

    pct = hype["pct"]
    traders = hype["traders"]
    p4h = hype["price_chg_4h"]
    p1h = hype["price_chg_1h"]
    cc = hype["contrib_change"]

    # Hard gates
    if pct < MIN_SM_PCT: return None
    if traders < MIN_SM_TRADERS: return None

    # 4H must align with SM direction
    if d == "LONG" and p4h < 0: return None
    if d == "SHORT" and p4h > 0: return None

    # ── Scoring (14-point system) ─────────────────────────────

    score, reasons = 0, []

    # SM concentration (0-4)
    if pct >= 30:
        score += 4; reasons.append(f"SM_DOMINANT {pct:.1f}% ({traders}t)")
    elif pct >= 20:
        score += 3; reasons.append(f"SM_HEAVY {pct:.1f}% ({traders}t)")
    elif pct >= 12:
        score += 2; reasons.append(f"SM_STRONG {pct:.1f}% ({traders}t)")
    else:
        score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # SM depth — trader count (0-2)
    if traders >= 200:
        score += 2; reasons.append(f"DEEP_SM ({traders}t)")
    elif traders >= 100:
        score += 1; reasons.append(f"BROAD_SM ({traders}t)")

    # 4H trend strength (0-2)
    abs_4h = abs(p4h)
    if abs_4h >= 2.0:
        score += 2; reasons.append(f"4H_STRONG {p4h:+.1f}%")
    elif abs_4h >= 0.5:
        score += 1; reasons.append(f"4H_CONFIRMS {p4h:+.1f}%")

    # 1H momentum (0-1)
    if (d == "LONG" and p1h > 0.2) or (d == "SHORT" and p1h < -0.2):
        score += 1; reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

    # Contribution velocity (0-2)
    if abs(cc) >= 0.03:
        score += 2; reasons.append(f"CONTRIB_SURGE +{abs(cc)*100:.1f}%")
    elif abs(cc) >= 0.01:
        score += 1; reasons.append(f"CONTRIB_GROWING +{abs(cc)*100:.2f}%")

    # BTC as booster (0-1) — not a gate
    btc_4h = fetch_btc_trend()
    if d == "LONG" and btc_4h > 0.5:
        score += 1; reasons.append(f"BTC_CONFIRMS +{btc_4h:.1f}%")
    elif d == "SHORT" and btc_4h < -0.5:
        score += 1; reasons.append(f"BTC_CONFIRMS {btc_4h:.1f}%")

    # Time of day (0-1)
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1; reasons.append("US_SESSION")

    return {"score": score, "direction": d, "reasons": reasons,
            "smPct": pct, "smTraders": traders, "priceChg4h": p4h,
            "contribChange": cc}


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER — HARDENED (Phoenix fix)
# ═══════════════════════════════════════════════════════════════

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

    # ── RIDING: HYPE position open → NO_REPLY ─────────────────
    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"RIDING: HYPE {p.get('direction','?')}. DSL manages exit.",
                "_v2_no_thesis_exit": True})
            return

    # ── Trade counter (HARDENED) ──────────────────────────────
    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached. Counter: {tc['entries']}/{MAX_DAILY_ENTRIES}"})
        return

    # ── Cooldown ──────────────────────────────────────────────
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HYPE on cooldown ({COOLDOWN_MINUTES}min)"})
        return

    # ── Evaluate thesis ───────────────────────────────────────
    thesis = evaluate_hype()

    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "HUNTING: no HYPE thesis"})
        return

    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING: HYPE {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. "
                    f"{', '.join(thesis['reasons'][:3])}"})
        return

    # ── Entry ─────────────────────────────────────────────────
    margin = round(av * MARGIN_PCT, 2)

    # INCREMENT COUNTER BEFORE OUTPUT (Phoenix fix)
    tc["entries"] = tc.get("entries", 0) + 1
    save_tc(tc)

    cfg.output({
        "status": "ok",
        "signal": {
            "asset": ASSET,
            "direction": thesis["direction"],
            "score": thesis["score"],
            "mode": "HYPE_PREDATOR",
            "reasons": thesis["reasons"],
            "smPct": thesis["smPct"],
            "smTraders": thesis["smTraders"],
            "priceChg4h": thesis["priceChg4h"],
        },
        "entry": {
            "asset": ASSET,
            "direction": thesis["direction"],
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
            "_note": f"DSL managed by plugin runtime. Counter: {tc['entries']}/{MAX_DAILY_ENTRIES}",
        },
        "_cheetah_version": "2.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
