#!/usr/bin/env python3
# Senpi RAPTOR Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""RAPTOR v2.0 — Hot Streak Follower.

When a quality trader crosses $5.5M+ in delta PnL (Tier 2 momentum event),
they're on a hot streak. Raptor looks at what that trader is holding and
follows them into their strongest position.

v1.0 had zero trades — the "momentum event confluence" pipeline was too
complex, requiring cross-referencing per asset. v2.0 inverts the flow:

1. TRIGGER: leaderboard_get_momentum_events (Tier 2) → find hot traders
2. FILTER: Only ELITE/RELIABLE TCS traders with high concentration
3. IDENTIFY: What's their top position? (from event's top_positions)
4. CONFIRM: leaderboard_get_markets → does SM agree on this asset?
5. ENTER: follow the hot trader into their top position

Why this is different from other agents:
- Orca v2.0 uses momentum events as confirmation on Striker signals
- Sentinel v2.0 looks for convergence across many traders
- Raptor v2.0 follows INDIVIDUAL hot traders into their best trade

The edge: a quality trader on a $5.5M+ hot streak has exceptional
short-term alpha. Their top position is where they have the most
conviction. Following them into it is a directional bet on their
continued momentum.

Architecture:
- 2 API calls: leaderboard_get_momentum_events + leaderboard_get_markets
- Event-driven: only fires when a new Tier 2 event appears
- Deduplication: tracks seen events to avoid re-entering on same trader
- Runs every 3 minutes

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raptor_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 5
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.20
MIN_SCORE = 7
XYZ_BANNED = True

# Momentum event filters
MOMENTUM_TIER = 2                   # Tier 2 = $5.5M+ (sweet spot)
QUALITY_TCS = {"ELITE", "RELIABLE"}
MIN_CONCENTRATION = 0.5             # Trader must be concentrated (not spread thin)

# SM confirmation
MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 15

# Deduplication — don't re-enter on same trader within window
SEEN_EVENTS_FILE = "seen-events.json"
EVENT_DEDUP_HOURS = 4               # Ignore same trader for 4 hours


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def now_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return time.time()


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_momentum_events():
    """Fetch recent Tier 2 momentum events."""
    data = cfg.mcporter_call("leaderboard_get_momentum_events",
                              tier=MOMENTUM_TIER, limit=50)
    if not data:
        return []

    events = data.get("events", data.get("data", []))
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list):
        return []

    return events


def fetch_sm_data():
    """Get SM positioning from leaderboard."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}

    markets = []
    if isinstance(raw, dict):
        raw_data = raw.get("data", raw)
        if isinstance(raw_data, dict):
            markets = raw_data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(raw_data, list):
            markets = raw_data
    elif isinstance(raw, list):
        markets = raw

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if not token:
            continue

        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
        }

    return sm_map


# ═══════════════════════════════════════════════════════════════
# EVENT DEDUPLICATION
# ═══════════════════════════════════════════════════════════════

def load_seen_events():
    p = os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen_events(seen):
    # Clean old entries
    cutoff = now_ts() - (EVENT_DEDUP_HOURS * 3600)
    cleaned = {k: v for k, v in seen.items() if v > cutoff}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE), cleaned)


def is_event_seen(seen, trader_id, asset):
    key = f"{trader_id}:{asset}"
    return key in seen


def mark_event_seen(seen, trader_id, asset):
    key = f"{trader_id}:{asset}"
    seen[key] = now_ts()


# ═══════════════════════════════════════════════════════════════
# SIGNAL EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_signals(events, sm_map, seen_events):
    """Extract tradeable signals from momentum events.

    For each quality event:
    1. Check TCS quality
    2. Check concentration
    3. Find the trader's top position (strongest conviction)
    4. Confirm SM agrees on that asset
    5. Score it
    """

    signals = []

    for event in events:
        if not isinstance(event, dict):
            continue

        trader_id = event.get("trader_id", event.get("address", ""))
        if not trader_id:
            continue

        # Check TCS quality
        tags = event.get("trader_tags", event.get("tags", {}))
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = {}

        tcs = str(tags.get("TCS", tags.get("tcs", ""))).upper()
        if tcs not in QUALITY_TCS:
            continue

        # Check concentration
        concentration = safe_float(event.get("concentration", 0))
        if concentration < MIN_CONCENTRATION:
            continue

        # Get top positions
        top_positions = event.get("top_positions", [])
        if isinstance(top_positions, str):
            try:
                top_positions = json.loads(top_positions)
            except (json.JSONDecodeError, TypeError):
                top_positions = []

        if not top_positions:
            continue

        # Find the strongest position (highest absolute delta PnL)
        best_pos = None
        best_pnl = 0
        for pos in top_positions:
            if not isinstance(pos, dict):
                continue
            asset = str(pos.get("market", pos.get("asset", ""))).upper()
            direction = str(pos.get("direction", "")).upper()
            delta_pnl = abs(safe_float(pos.get("delta_pnl",
                            pos.get("deltaPnl", pos.get("pnl", 0)))))

            if not asset or direction not in ("LONG", "SHORT"):
                continue
            if XYZ_BANNED and asset.lower().startswith("xyz"):
                continue

            if delta_pnl > best_pnl:
                best_pnl = delta_pnl
                best_pos = {"asset": asset, "direction": direction,
                            "delta_pnl": delta_pnl}

        if not best_pos:
            continue

        asset = best_pos["asset"]
        direction = best_pos["direction"]

        # Dedup — don't re-enter same trader + asset
        if is_event_seen(seen_events, trader_id, asset):
            continue

        # SM must agree
        sm = sm_map.get(asset)
        if not sm:
            continue
        if sm["direction"] != direction:
            continue
        if sm["pct"] < MIN_SM_PCT or sm["traders"] < MIN_SM_TRADERS:
            continue

        # ── Score ──
        score = 0
        reasons = []

        # TCS quality (0-2)
        if tcs == "ELITE":
            score += 2
            reasons.append(f"ELITE_STREAK (conc={concentration:.2f})")
        else:
            score += 1
            reasons.append(f"RELIABLE_STREAK (conc={concentration:.2f})")

        # Concentration (0-1)
        if concentration >= 0.7:
            score += 1
            reasons.append(f"HIGH_CONVICTION {concentration:.0%}")

        # SM alignment (0-2)
        if sm["pct"] >= 10:
            score += 2
            reasons.append(f"SM_STRONG {sm['pct']:.1f}% ({sm['traders']}t)")
        elif sm["pct"] >= 5:
            score += 1
            reasons.append(f"SM_ALIGNED {sm['pct']:.1f}% ({sm['traders']}t)")

        # Price momentum (0-2)
        p4h = sm["price_chg_4h"]
        p1h = sm["price_chg_1h"]
        if direction == "LONG" and p4h > 0.5:
            score += 1
            reasons.append(f"4H_CONFIRMS +{p4h:.1f}%")
        elif direction == "SHORT" and p4h < -0.5:
            score += 1
            reasons.append(f"4H_CONFIRMS {p4h:.1f}%")

        if direction == "LONG" and p1h > 0.2:
            score += 1
            reasons.append(f"1H_CONFIRMS +{p1h:.2f}%")
        elif direction == "SHORT" and p1h < -0.2:
            score += 1
            reasons.append(f"1H_CONFIRMS {p1h:.2f}%")

        # Tier bonus (0-1)
        tier = event.get("tier", MOMENTUM_TIER)
        if tier >= 3:
            score += 1
            reasons.append("TIER_3_EXTREME")

        signals.append({
            "asset": asset,
            "direction": direction,
            "score": score,
            "mode": "HOT_STREAK",
            "reasons": reasons,
            "traderId": trader_id[:10] + "...",
            "tcs": tcs,
            "concentration": concentration,
            "deltaPnl": best_pos["delta_pnl"],
            "smPct": sm["pct"],
            "smTraders": sm["traders"],
            "priceChg4h": p4h,
            "tier": tier,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER & COOLDOWN
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": now_date(), "entries": 0}


def save_trade_counter(tc):
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def is_on_cooldown(asset):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(asset)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    tc = load_trade_counter()
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
        save_trade_counter(tc)
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Fetch data (2 API calls) ──────────────────────────────
    events = fetch_momentum_events()
    sm_map = fetch_sm_data()

    if not events:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No Tier 2 momentum events in last 4h"})
        return

    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data"})
        return

    # ── Extract signals ───────────────────────────────────────
    seen_events = load_seen_events()
    signals = extract_signals(events, sm_map, seen_events)

    if not signals:
        quality_count = sum(1 for e in events
                           if str((e.get("trader_tags", {}) or {}).get("TCS", "")).upper()
                           in QUALITY_TCS)
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No hot streak signals. "
                            f"{len(events)} Tier 2 events, {quality_count} from quality traders."})
        return

    # ── Filter and enter ──────────────────────────────────────
    held_coins = {p["coin"].upper() for p in positions}

    for signal in signals:
        asset = signal["asset"]

        if signal["score"] < MIN_SCORE:
            continue
        if is_on_cooldown(asset):
            continue
        if asset in held_coins:
            continue

        # ── Entry ─────────────────────────────────────────────
        margin = round(account_value * MARGIN_PCT, 2)

        # Mark as seen to avoid re-entering
        mark_event_seen(seen_events, signal["traderId"], asset)
        save_seen_events(seen_events)

        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

        cfg.output({
            "status": "ok",
            "signal": signal,
            "entry": {
                "asset": asset,
                "direction": signal["direction"],
                "leverage": DEFAULT_LEVERAGE,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
            },
            "constraints": {
                "maxPositions": MAX_POSITIONS,
                "maxLeverage": MAX_LEVERAGE,
                "maxDailyEntries": MAX_DAILY_ENTRIES,
                "cooldownMinutes": COOLDOWN_MINUTES,
                "xyzBanned": XYZ_BANNED,
                "_v2_no_thesis_exit": True,
                "_note": "DSL managed by plugin runtime. Scanner does NOT manage exits.",
            },
            "_raptor_version": "2.0",
        })
        return

    # Report best candidate
    if signals:
        best = signals[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best streak: {best['asset']} {best['direction']} "
                            f"score {best['score']}. {', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Momentum events found but no SM alignment"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
