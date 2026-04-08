#!/usr/bin/env python3
# Senpi SHARK Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SHARK v2.0 — Liquidation Cascade + SM Conviction Scanner.

Consolidated from v1.0's 8-cron pipeline into a single scanner.

v1.0 had 10 scripts across 8 crons. When any cron died (which happened),
the whole pipeline broke silently. v2.0 runs everything in one script:

1. SCAN: leaderboard_get_markets → where is SM concentrated?
2. VALIDATE: leaderboard_get_top → are top 5 traders aligned?
3. CONFIRM: price momentum → is 4H/1H aligned with SM direction?
4. STRUCTURE: funding + OI context → is the move structurally supported?

When ALL 4 gates pass → enter. Plugin runtime manages exits via runtime.yaml.
Scanner does NOT thesis-exit open positions.

Key changes from v1.0:
- 8 crons → 2 crons (scanner + exits)
- No thesis exit in RIDING mode (lesson from Wolverine/Condor)
- Tighter SM thresholds: 30+ traders, 5%+ contribution, top 5 alignment
- Proper per-asset cooldown (120 min)
- Max 4 entries/day
Runs every 5 minutes.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shark_config as cfg

SCRIPT = "shark-scanner"

# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

# SM thresholds — tighter than v1.0
MIN_SM_PCT = 5.0                # Min SM gain share
MIN_SM_TRADERS = 30             # Min traders for consensus (v1.0 was 30)
MIN_TOP5_ALIGNMENT = 2          # At least 2 of top 5 aligned
MIN_SCORE = 8                   # Minimum conviction score to enter

# Position management
MAX_POSITIONS = 2               # Max concurrent — cascades correlate
MAX_DAILY_ENTRIES = 4           # Max entries per day
COOLDOWN_MINUTES = 120          # Per-asset cooldown
DEFAULT_LEVERAGE = 7            # Conservative — v1.0 was 7-10x
MAX_LEVERAGE = 10
MARGIN_PCT = 0.18               # 18% of account per trade

# Risk
MAX_DAILY_LOSS_PCT = 12         # Daily loss circuit breaker
CONSECUTIVE_LOSS_LIMIT = 3      # 3 losses → 45 min cooldown
COOLDOWN_ON_LOSSES_MIN = 45
XYZ_BANNED = True

# Scoring weights
WEIGHT_SM_CONCENTRATION = 3     # Points for high SM %
WEIGHT_TOP_TRADERS = 3          # Points for top 5 alignment
WEIGHT_PRICE_MOMENTUM = 2       # Points for 4H/1H alignment
WEIGHT_FUNDING = 1              # Points for funding confirmation
WEIGHT_VOLUME = 1               # Points for volume confirmation


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
# SIGNAL SCORING (4 gates)
# ═══════════════════════════════════════════════════════════════

def scan_sm_markets():
    """Gate 1: Find assets where SM is concentrated."""
    markets, err = cfg.fetch_sm_markets(100)
    if err or not markets:
        return []

    by_token = {}
    for m in markets:
        token = (m.get("token") or "").upper()
        dex = m.get("dex", "")
        if dex == "xyz" or not token:
            continue

        pct = float(m.get("pct_of_top_traders_gain", 0))
        direction = (m.get("direction") or "").upper()
        traders = int(m.get("trader_count", 0))
        price_chg_4h = float(m.get("token_price_change_pct_4h", 0))
        price_chg_1h = float(m.get("token_price_change_pct_1h",
                             m.get("price_change_1h", 0)))
        contrib_change = safe_float(m.get("contribution_pct_change_4h", 0))

        key = token
        if key not in by_token or pct > by_token[key].get("pct", 0):
            by_token[key] = {
                "token": token,
                "direction": direction,
                "pct": pct,
                "traders": traders,
                "price_chg_4h": price_chg_4h,
                "price_chg_1h": price_chg_1h,
                "contrib_change": contrib_change,
            }

    candidates = []
    for token, data in by_token.items():
        if data["pct"] >= MIN_SM_PCT and data["traders"] >= MIN_SM_TRADERS:
            candidates.append(data)

    candidates.sort(key=lambda x: x["pct"], reverse=True)
    return candidates


def validate_top_traders(asset, direction):
    """Gate 2: Check if top 5 Hyperfeed traders are aligned."""
    data, err = cfg.mcporter_call("leaderboard_get_top", {"limit": 5})
    if err or not data:
        return False, 0, f"leaderboard_get_top failed"

    leaderboard = data.get("leaderboard", {}).get("data", [])
    if not leaderboard:
        return False, 0, "no leaderboard data"

    aligned = 0
    for trader in leaderboard:
        top_markets = trader.get("top_markets", [])
        clean_asset = asset.replace("xyz:", "").upper()
        if clean_asset in [m.upper() for m in top_markets]:
            trader_id = trader.get("trader_id", "")
            if trader_id:
                pos_data, pos_err = cfg.mcporter_call(
                    "leaderboard_get_trader_positions", {"trader_id": trader_id}
                )
                if not pos_err and pos_data:
                    positions = pos_data.get("positions", {}).get("positions", [])
                    for p in positions:
                        if (p.get("market", "")).upper() == clean_asset:
                            pos_dir = (p.get("direction", "")).upper()
                            if pos_dir == direction.upper():
                                aligned += 1

    validated = aligned >= MIN_TOP5_ALIGNMENT
    return validated, aligned, f"{aligned}/5 top traders aligned"


def check_price_momentum(candidate):
    """Gate 3: Is 4H and 1H price aligned with SM direction?"""
    direction = candidate["direction"]
    p4h = candidate["price_chg_4h"]
    p1h = candidate["price_chg_1h"]

    score = 0
    reasons = []

    if direction == "LONG":
        if p4h > 0.5:
            score += 1
            reasons.append(f"4H +{p4h:.1f}%")
        if p1h > 0.1:
            score += 1
            reasons.append(f"1H +{p1h:.2f}%")
    else:
        if p4h < -0.5:
            score += 1
            reasons.append(f"4H {p4h:.1f}%")
        if p1h < -0.1:
            score += 1
            reasons.append(f"1H {p1h:.2f}%")

    passed = score >= 1  # At least one timeframe aligned
    return passed, score, " | ".join(reasons) if reasons else "no momentum alignment"


def check_funding_structure(asset, direction):
    """Gate 4: Is funding confirming the directional pressure?"""
    data, err = cfg.mcporter_call("market_get_asset_data", {
        "asset": asset, "candle_intervals": [], "include_funding": True
    })
    if err or not data:
        return True, "no funding data — skipping gate"

    ad = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(ad, dict):
        return True, "no funding data — skipping gate"

    ac = ad.get("asset_context", ad.get("assetContext", {}))
    if not isinstance(ac, dict):
        return True, "no asset context"

    funding = safe_float(ac.get("funding", ac.get("fundingRate", 0)))

    if direction == "SHORT" and funding > 0.00005:
        return True, f"funding +{funding:.6f} confirms SHORT pressure"
    elif direction == "LONG" and funding < -0.00005:
        return True, f"funding {funding:.6f} confirms LONG pressure"

    # Funding not confirming but not blocking — pass with note
    return True, f"funding {funding:.6f} neutral"


# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

def score_candidate(candidate, top_aligned, momentum_score, funding_reason):
    """Score a candidate across all dimensions. Returns (score, reasons)."""
    score = 0
    reasons = []

    # SM concentration
    pct = candidate["pct"]
    traders = candidate["traders"]
    if pct >= 10:
        score += 3
        reasons.append(f"HIGH_SM {pct:.1f}% ({traders}t)")
    elif pct >= 7:
        score += 2
        reasons.append(f"SOLID_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1
        reasons.append(f"BASE_SM {pct:.1f}% ({traders}t)")

    # Top trader alignment
    if top_aligned >= 3:
        score += 3
        reasons.append(f"DEEP_ALIGNMENT {top_aligned}/5")
    elif top_aligned >= 2:
        score += 2
        reasons.append(f"ALIGNED {top_aligned}/5")

    # Price momentum
    score += momentum_score
    if momentum_score >= 2:
        reasons.append("4H_1H_ALIGNED")
    elif momentum_score >= 1:
        reasons.append("PARTIAL_MOMENTUM")

    # Contribution acceleration (gen-2 signal)
    contrib = candidate.get("contrib_change", 0)
    if contrib > 0.002:
        score += 1
        reasons.append(f"ACCEL +{contrib*100:.3f}%")

    # Funding
    if "confirms" in funding_reason.lower():
        score += 1
        reasons.append("FUNDING_ALIGNED")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# COOLDOWN & TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def load_trade_counter(state_dir):
    path = os.path.join(state_dir, "trade-counter.json")
    tc = cfg.load_json(path, {})
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0, "dailyLoss": 0,
              "consecutiveLosses": 0, "gate": "OPEN"}
    return tc


def save_trade_counter(state_dir, tc):
    path = os.path.join(state_dir, "trade-counter.json")
    cfg.atomic_write(path, tc)


def is_on_cooldown(state_dir, asset):
    path = os.path.join(state_dir, "cooldowns.json")
    cooldowns = cfg.load_json(path, {})
    entry = cooldowns.get(asset)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


def set_cooldown(state_dir, asset, minutes=120):
    path = os.path.join(state_dir, "cooldowns.json")
    cooldowns = cfg.load_json(path, {})
    cooldowns[asset] = {"until": time.time() + minutes * 60, "set_at": now_iso()}
    cfg.atomic_write(path, cooldowns)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    strategies = cfg.load_all_strategies()
    if not strategies:
        cfg.heartbeat(SCRIPT)
        return

    for strat in strategies:
        sk = strat.get("strategyId")
        wallet = strat.get("wallet")
        if not sk or not wallet:
            continue

        sd = cfg.state_dir(sk)
        budget = strat.get("budget", 1000)

        # ── Check existing positions (NO thesis exit) ─────────
        on_chain, err = cfg.get_active_positions(wallet)
        if err:
            cfg.output({"status": "error", "script": SCRIPT, "error": err})
            return

        if on_chain and len(on_chain) > 0:
            # Position active. Plugin runtime manages exit. Scanner does NOT re-evaluate.
            coins = list(on_chain.keys())
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "script": SCRIPT,
                        "note": f"Position(s) active: {coins}. Plugin runtime manages exit.",
                        "_v2_no_thesis_exit": True})
            return

        # ── Trade counter / circuit breaker ───────────────────
        tc = load_trade_counter(sd)

        if tc.get("gate") == "PAUSED":
            pause_until = tc.get("pauseUntil", 0)
            if now_ts() < pause_until:
                cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "script": SCRIPT,
                            "note": f"Paused: {(pause_until - now_ts())/60:.0f}min"})
                return
            tc["gate"] = "OPEN"

        if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
            cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "script": SCRIPT,
                        "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
            return

        # ── Gate 1: Scan SM markets ───────────────────────────
        candidates = scan_sm_markets()
        if not candidates:
            cfg.heartbeat(SCRIPT)
            return

        # ── Evaluate each candidate through gates 2-4 ────────
        for cand in candidates:
            token = cand["token"]
            direction = cand["direction"]

            # Skip cooldown
            if is_on_cooldown(sd, token):
                continue

            # Skip xyz
            if XYZ_BANNED and token.lower().startswith("xyz"):
                continue

            # Gate 2: Top trader alignment
            validated, top_aligned, val_detail = validate_top_traders(token, direction)
            if not validated:
                continue

            # Gate 3: Price momentum
            momentum_ok, momentum_score, momentum_reason = check_price_momentum(cand)
            if not momentum_ok:
                continue

            # Gate 4: Funding structure
            funding_ok, funding_reason = check_funding_structure(token, direction)
            if not funding_ok:
                continue

            # Score it
            score, reasons = score_candidate(cand, top_aligned, momentum_score, funding_reason)

            if score < MIN_SCORE:
                continue

            # ═══════════════════════════════════════
            # ALL GATES PASSED — ENTER
            # ═══════════════════════════════════════

            # Get account value for margin calculation
            ch_data, _ = cfg.fetch_clearinghouse(wallet)
            account_value = budget
            if ch_data:
                for vk in ("main", "xyz"):
                    v = ch_data.get(vk, {})
                    ms = v.get("marginSummary", {})
                    try:
                        av = float(ms.get("accountValue", 0))
                        if av > 0:
                            account_value = av
                    except (TypeError, ValueError):
                        pass

            margin = round(account_value * MARGIN_PCT, 2)
            leverage = DEFAULT_LEVERAGE

            reason = (f"SHARK v2.0: {direction} {token} — score {score} — "
                      f"{' | '.join(reasons)}")

            cfg.output({
                "status": "ok",
                "signal": {
                    "asset": token,
                    "direction": direction,
                    "score": score,
                    "reasons": reasons,
                },
                "entry": {
                    "asset": token,
                    "direction": direction,
                    "leverage": leverage,
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
                },
            })

            # Update trade counter
            tc["entries"] = tc.get("entries", 0) + 1
            save_trade_counter(sd, tc)
            return

        # No candidate passed all gates
        cfg.heartbeat(SCRIPT)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.output_error(SCRIPT, str(e))
        sys.exit(1)
