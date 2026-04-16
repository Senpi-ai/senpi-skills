#!/usr/bin/env python3
# Senpi PHOENIX Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""PHOENIX v3.0 — Contribution Velocity Scanner (Lemon DSL profile + reset).

Same contribution-velocity signal as v1.0.1/v2.0. Phoenix found SOL LONG
+$24, ETH LONG +$11, SOL SHORT +$22 on 4/1. The HYPE SHORT at 54x
divergence peaked at +50% ROE. The signal works.

## v3.0 changes — DSL profile adoption + capital reset

Phoenix was locked by the circuit breaker at -36.3% drawdown (daily cap=0
after the pnl-aware trigger fell past -25%). Scanner diagnostics show
54% of losing trades were killed by `weak_peak_cut` — valid signals were
being cut before they could run. The Lemon DSL profile removes
weak_peak_cut entirely and widens Phase 2 tiers.

Runtime.yaml changes:
- Removed `weak_peak_cut` block entirely (54% of losers killed by it)
- `hard_timeout.interval_in_minutes`: 45 → 480 (winners need time to run)
- `dead_weight_cut.interval_in_minutes`: → 20
- `phase1.max_loss_pct`: 25.0 → 15.0
- `phase1.retrace_threshold`: → 8
- `phase1.consecutive_breaches_required`: → 3
- `phase2.tiers`: replaced with Lemon's wider ladder
  (5/20, 10/40, 15/60, 20/75, 30/85, 50/92)

Scanner capital reset:
- `STARTING_BUDGET` 1000.0 → 637.93 (current equity from latest
  leaderboard data). Rebases the pnl-aware daily cap so Phoenix can
  actually enter again after the previous drawdown.

v2.0 retained fixes (trade counter SELF-CONTAINED, stale-date reset,
no thesis exit, no DSL state generation).

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


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 637.93  # v3.0: rebased to current equity after drawdown

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

        # 15m velocity freshness gate — SM must be actively building, not stale
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
        if cc_15m <= 0:
            continue  # SM velocity is flat or fading — signal is stale, don't enter

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


def set_cooldown(asset, minutes=None):
    """Set per-asset cooldown after entry. Prevents re-entering the same
    asset immediately after DSL cuts it."""
    if minutes is None:
        minutes = COOLDOWN_MINUTES
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    cooldowns = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                cooldowns = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    cooldowns[asset] = {"until": time.time() + minutes * 60, "set_at": now_iso()}
    cfg.atomic_write(p, cooldowns)


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

    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
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

    # ── INCREMENT COUNTER + SET COOLDOWN BEFORE OUTPUT ────────
    # v2.0 fix: increment happens HERE, BEFORE signal output.
    # v2.1 fix: set per-asset cooldown to prevent re-entering the
    # same asset immediately after DSL cuts it. Without this,
    # Phoenix hammers the same losing trade every 2 minutes.
    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)
    set_cooldown(best["token"])

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
        "_phoenix_version": "3.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
