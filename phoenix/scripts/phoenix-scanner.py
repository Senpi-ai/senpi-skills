#!/usr/bin/env python3
# Senpi PHOENIX Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""PHOENIX v2.0 — Contribution Velocity Scanner (Hardened).

Same signal as v1.0.1 — contribution_pct_change_4h diverging from price.
The signal works. Phoenix found SOL LONG +$24, ETH LONG +$11, SOL SHORT +$22
on 4/1. The HYPE SHORT at 54x divergence peaked at +50% ROE.

What broke in v1.0.1: the trade counter. When DSL exits moved to the plugin,
the scanner stopped incrementing the counter after entries. Result: 24 entries
in one day instead of 6. -$228 in one day. -40.6% total.

v2.0 fixes:
1. Trade counter is SELF-CONTAINED — the scanner increments it in the output
   flow, not dependent on the exit path
2. SAFETY CHECK: before any entry, query the strategy wallet's clearinghouse
   state and count how many positions were opened today. If that count exceeds
   the daily limit, refuse to enter regardless of what the counter file says.
3. Daily entry cap reduced from 6 to 4 (Phoenix's best days had 3-5 winners)
4. No thesis exit, no DSL state generation

One API call per scan: leaderboard_get_markets.
Runs every 2 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phoenix_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS — same signal thresholds as v1.0.1
# ═══════════════════════════════════════════════════════════════

MAX_LEVERAGE = 10
MIN_LEVERAGE = 5
MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 4               # Reduced from 6 — Phoenix's best days had 3-5 winners
XYZ_BANNED = True

# Contribution velocity thresholds (unchanged from v1.0.1)
MIN_CONTRIB_CHANGE_4H = 5.0
HIGH_CONTRIB_CHANGE_4H = 15.0
EXTREME_CONTRIB_CHANGE_4H = 30.0

# Leaderboard gates (unchanged from v1.0.1)
MIN_RANK = 6
MAX_RANK = 40
MIN_CONTRIBUTION_PCT = 1.0
MIN_TRADER_COUNT = 30
MIN_PRICE_CHG_ALIGNMENT = True

# Entry sizing
MARGIN_TIERS = {12: 0.30, 9: 0.25, 0: 0.20}  # Score → margin %

# Cooldown
COOLDOWN_MINUTES = 90


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


# ═══════════════════════════════════════════════════════════════
# SIGNAL SCORING — identical to v1.0.1 (battle-tested)
# ═══════════════════════════════════════════════════════════════

def fetch_and_score():
    """Single API call. Score every asset by contribution velocity."""
    data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not data:
        return None, []

    markets_data = data.get("data", data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", markets_data)
    if isinstance(markets_data, dict):
        markets_data = markets_data.get("markets", [])
    if not isinstance(markets_data, list):
        return None, []

    signals = []

    for i, m in enumerate(markets_data):
        if not isinstance(m, dict):
            continue

        token = str(m.get("token", "")).upper()
        dex = m.get("dex", "")
        rank = i + 1
        direction = str(m.get("direction", "")).upper()
        contribution = safe_float(m.get("pct_of_top_traders_gain", 0))
        contrib_change = safe_float(m.get("contribution_pct_change_4h", 0))
        price_chg_4h = safe_float(m.get("token_price_change_pct_4h", 0))
        trader_count = int(m.get("trader_count", 0))

        # ─── Hard gates (unchanged from v1.0.1) ───
        if XYZ_BANNED and (dex.lower() == "xyz" or token.lower().startswith("xyz:")):
            continue
        if rank < MIN_RANK or rank > MAX_RANK:
            continue
        if contribution < MIN_CONTRIBUTION_PCT:
            continue
        if trader_count < MIN_TRADER_COUNT:
            continue
        if contrib_change < MIN_CONTRIB_CHANGE_4H:
            continue
        if MIN_PRICE_CHG_ALIGNMENT:
            if direction == "LONG" and price_chg_4h < 0:
                continue
            if direction == "SHORT" and price_chg_4h > 0:
                continue

        # ─── Scoring (unchanged from v1.0.1) ───
        score = 0
        reasons = []

        # Contribution velocity
        if contrib_change >= EXTREME_CONTRIB_CHANGE_4H:
            score += 5
            reasons.append(f"EXTREME_VELOCITY +{contrib_change:.1f}%")
        elif contrib_change >= HIGH_CONTRIB_CHANGE_4H:
            score += 3
            reasons.append(f"HIGH_VELOCITY +{contrib_change:.1f}%")
        else:
            score += 2
            reasons.append(f"CONTRIB_VELOCITY +{contrib_change:.1f}%")

        # Contribution magnitude
        if contribution >= 10:
            score += 2
            reasons.append(f"DOMINANT_SM {contribution:.1f}%")
        elif contribution >= 5:
            score += 1
            reasons.append(f"STRONG_SM {contribution:.1f}%")

        # Rank sweet spot
        if 10 <= rank <= 20:
            score += 2
            reasons.append(f"SWEET_SPOT #{rank}")
        elif 6 <= rank < 10:
            score += 1
            reasons.append(f"NEAR_TOP #{rank}")
        elif 20 < rank <= 30:
            score += 1
            reasons.append(f"DEEP_RISER #{rank}")

        # Trader depth
        if trader_count >= 150:
            score += 2
            reasons.append(f"MASSIVE_SM {trader_count}t")
        elif trader_count >= 80:
            score += 1
            reasons.append(f"DEEP_SM {trader_count}t")

        # Price lag (the alpha window)
        if abs(price_chg_4h) < 1.5:
            score += 2
            reasons.append(f"PRICE_LAG {price_chg_4h:+.1f}% vs +{contrib_change:.1f}%")
        elif abs(price_chg_4h) < 3:
            score += 1
            reasons.append(f"EARLY_MOVE {price_chg_4h:+.1f}%")

        # Velocity divergence
        if abs(price_chg_4h) > 0.1:
            velocity_ratio = contrib_change / abs(price_chg_4h)
            if velocity_ratio >= 10:
                score += 2
                reasons.append(f"EXTREME_DIV {velocity_ratio:.0f}x")
            elif velocity_ratio >= 5:
                score += 1
                reasons.append(f"DIVERGENCE {velocity_ratio:.1f}x")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "score": score,
            "reasons": reasons,
            "rank": rank,
            "contribution": contribution,
            "contrib_change": contrib_change,
            "price_chg_4h": price_chg_4h,
            "trader_count": trader_count,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return len(markets_data), signals


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER — v2.0 HARDENED
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") == now_date():
                return tc
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": now_date(), "entries": 0}


def save_trade_counter(tc):
    """ALWAYS save with today's date."""
    tc["date"] = now_date()
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

    # ── Check existing positions (NO thesis exit) ─────────────
    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    if len(positions) >= MAX_POSITIONS:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{len(positions)} positions active. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    # ── Trade counter (HARDENED) ──────────────────────────────
    tc = load_trade_counter()

    # SAFETY: force reset if date is stale (the v1.0.1 bug)
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
        save_trade_counter(tc)

    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached. "
                            f"Counter: {tc.get('entries', 0)}/{MAX_DAILY_ENTRIES}"})
        return

    # ── Scan (single API call) ────────────────────────────────
    markets_count, signals = fetch_and_score()
    if markets_count is None:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "failed to fetch markets"})
        return

    # Filter: already holding, cooled down, min score
    active_coins = {p["coin"].upper() for p in positions}
    signals = [s for s in signals if s["token"] not in active_coins]
    signals = [s for s in signals if not is_on_cooldown(s["token"])]

    min_score = 7
    signals = [s for s in signals if s["score"] >= min_score]

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"{markets_count} markets, no velocity signals"})
        return

    best = signals[0]

    # ── Margin scaling by conviction ──────────────────────────
    margin_pct = 0.20
    for threshold, pct in sorted(MARGIN_TIERS.items(), reverse=True):
        if best["score"] >= threshold:
            margin_pct = pct
            break
    margin = round(account_value * margin_pct, 2)

    # ── INCREMENT COUNTER BEFORE OUTPUT ───────────────────────
    # This is the v2.0 fix: increment happens HERE, in the scanner,
    # BEFORE the signal is output. Even if the cron agent fails to
    # execute the trade, the counter is incremented. This prevents
    # the runaway entry bug from v1.0.1.
    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)

    cfg.output({
        "status": "ok",
        "signal": {
            "token": best["token"],
            "direction": best["direction"],
            "score": best["score"],
            "reasons": best["reasons"],
            "rank": best["rank"],
            "contribution": best["contribution"],
            "contrib_change": best["contrib_change"],
            "price_chg_4h": best["price_chg_4h"],
            "trader_count": best["trader_count"],
        },
        "entry": {
            "coin": best["token"],
            "direction": best["direction"],
            "leverage": min(MAX_LEVERAGE, 10),
            "margin": margin,
            "orderType": "FEE_OPTIMIZED_LIMIT",
        },
        "constraints": {
            "maxPositions": MAX_POSITIONS,
            "maxLeverage": MAX_LEVERAGE,
            "maxDailyEntries": MAX_DAILY_ENTRIES,
            "cooldownMinutes": COOLDOWN_MINUTES,
            "_v2_no_thesis_exit": True,
            "_note": "DSL managed by plugin runtime. Scanner does NOT manage exits. "
                     f"Trade counter: {tc['entries']}/{MAX_DAILY_ENTRIES} for {now_date()}",
        },
        "allSignals": [{"token": s["token"], "score": s["score"],
                        "direction": s["direction"]} for s in signals[:5]],
        "marketsScanned": markets_count,
        "_phoenix_version": "2.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
