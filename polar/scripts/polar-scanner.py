#!/usr/bin/env python3
# Senpi POLAR Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""POLAR v2.0 — ETH Alpha Hunter.

Single-asset ETH lifecycle hunter. The patience benchmark.
Three consecutive wins at +19.8%, +18.4%, +2.2% ROE after removing
thesis exit. The proof that scanner enters, DSL exits.

Two modes: HUNT → RIDE (NO_REPLY) → re-HUNT.
STALKING mode removed (fleet-wide decision: Stalker is dead).

v1.0 had thesis exit in RIDING mode that scratched +0.35% trades
that would have run to +20%. v2.0 removes it entirely.

All v2.0 fleet fixes:
- MCP response parsing: handles data.markets.markets nesting
- Trade counter increments BEFORE output (Phoenix fix)
- Trade counter auto-resets on stale date
- evaluate_eth_position: REMOVED ENTIRELY
- RIDING mode: outputs NO_REPLY only, zero thesis evaluation
- _v2_no_thesis_exit: True on every output
- Config helper points to polar-strategy

DSL exit managed by plugin runtime via runtime.yaml.
Uses: leaderboard_get_markets + market_get_asset_data (2 API calls)
Runs every 3 minutes.
"""

import json, sys, os, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polar_config as cfg

ASSET = "ETH"
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 7
MAX_POSITIONS = 1
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.25
MIN_SCORE = 8
XYZ_BANNED = True

# SM thresholds
MIN_SM_PCT = 8.0
MIN_SM_TRADERS = 40


def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso(): return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# MCP PARSING — handles data.markets.markets nesting
# ═══════════════════════════════════════════════════════════════

def fetch_sm_for_eth():
    """Fetch SM data for ETH. Correctly handles nested MCP response."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw: return None

    markets = raw
    if isinstance(markets, dict): markets = markets.get("data", markets)
    if isinstance(markets, dict): markets = markets.get("markets", markets)
    if isinstance(markets, dict): markets = markets.get("markets", [])
    if not isinstance(markets, list): return None

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


# ═══════════════════════════════════════════════════════════════
# ETH THESIS EVALUATION
# ═══════════════════════════════════════════════════════════════

def evaluate_eth():
    """Build ETH entry thesis. Returns dict or None."""

    eth = fetch_sm_for_eth()
    if not eth: return None

    d = eth["direction"]
    if d not in ("LONG", "SHORT"): return None

    pct = eth["pct"]
    traders = eth["traders"]
    p4h = eth["price_chg_4h"]
    p1h = eth["price_chg_1h"]
    cc = eth["contrib_change"]

    # Hard gates
    if pct < MIN_SM_PCT: return None
    if traders < MIN_SM_TRADERS: return None
    if d == "LONG" and p4h < 0: return None
    if d == "SHORT" and p4h > 0: return None

    # ── Fetch additional data (funding, OI) ───────────────────
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

    # ── Scoring ───────────────────────────────────────────────

    score, reasons = 0, []

    # SM concentration (0-3)
    if pct >= 15:
        score += 3; reasons.append(f"SM_DOMINANT {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2; reasons.append(f"SM_STRONG {pct:.1f}% ({traders}t)")
    else:
        score += 1; reasons.append(f"SM_ALIGNED {pct:.1f}% ({traders}t)")

    # SM depth (0-1)
    if traders >= 100:
        score += 1; reasons.append(f"DEEP_SM ({traders}t)")

    # 4H trend strength (0-2)
    abs_4h = abs(p4h)
    if abs_4h >= 2.0:
        score += 2; reasons.append(f"STRONG_4H {p4h:+.1f}%")
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

    # Funding alignment (0-1)
    if (d == "SHORT" and funding > 0.0002) or (d == "LONG" and funding < -0.0002):
        score += 1; reasons.append(f"FUNDING_PAYS {funding*100:.4f}%")

    # Time of day (0-1)
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1; reasons.append("US_SESSION")

    return {"score": score, "direction": d, "reasons": reasons,
            "smPct": pct, "smTraders": traders, "priceChg4h": p4h}


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
# MAIN — NO evaluate_eth_position, NO thesis exit
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

    # ── RIDING: ETH position open → NO_REPLY (DSL manages exit) ──
    # NO thesis evaluation. NO evaluate_eth_position. NO close_position.
    # The scanner ONLY outputs NO_REPLY when a position is open.
    for p in positions:
        if p.get("coin", "").upper() == ASSET:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"RIDING: ETH {p.get('direction','?')}. DSL manages exit.",
                "_v2_no_thesis_exit": True})
            return

    # ── Trade counter (HARDENED) ──────────────────────────────
    tc = load_tc()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Cooldown ──────────────────────────────────────────────
    if cfg.is_asset_cooled_down(ASSET, COOLDOWN_MINUTES):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"ETH on cooldown ({COOLDOWN_MINUTES}min)"})
        return

    # ── Evaluate thesis (entry ONLY — no position re-evaluation) ──
    thesis = evaluate_eth()

    if not thesis:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": "HUNTING: no ETH thesis"})
        return

    if thesis["score"] < MIN_SCORE:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
            "note": f"HUNTING: ETH {thesis['direction']} score {thesis['score']}<{MIN_SCORE}. "
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
            "mode": "ETH_HUNTER",
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
        "_polar_version": "2.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
