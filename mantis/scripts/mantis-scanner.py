#!/usr/bin/env python3
# Senpi MANTIS Scanner v4.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""MANTIS v4.0 — Striker-Only SM Explosion Scanner.

Stalker is dead. Orca v1.3 proved it: 58 Stalker trades at 43% win rate,
-$0.73 avg P&L. The "slow accumulation" signal catches chop, not trends.

v4.0 is Striker-only: violent FIRST_JUMP from deep in the leaderboard,
confirmed by volume explosion. The same logic that Roach and Jaguar run,
but with Mantis's battle-tested market parser and scan history.

What changed from v3.0:
- Stalker mode: REMOVED (the experiment is over)
- Stalker streak gate: REMOVED (no Stalker = no streak)
- DSL state generation: REMOVED (plugin handles exits)
- Thesis exit: REMOVED (DSL is sole exit mechanism)
- All 'dslState' output: REMOVED
- Position check outputs NO_REPLY (not thesis evaluation)

What stayed:
- Striker detection (FIRST_JUMP, IMMEDIATE_MOVER, CONTRIB_EXPLOSION)
- Volume confirmation gate
- 4H alignment hard gate
- Scan history for rank jump detection
- Per-asset cooldown (120 min)
- Daily entry cap (6/day)
- XYZ ban, leverage 7x, max 3 positions

Uses: leaderboard_get_markets (single API call per scan)
Runs every 90 seconds.
"""

import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mantis_config as cfg

TOP_N = 50
ERRATIC_REVERSAL_THRESHOLD = 5


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 7
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6
COOLDOWN_MINUTES = 120
XYZ_BANNED = True
MARGIN_PCT = 0.18

# Striker thresholds
STRIKER_MIN_SCORE = 9
STRIKER_MIN_REASONS = 4
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25
STRIKER_MIN_VELOCITY_FLOOR = 10
STRIKER_MIN_VOL_RATIO = 1.5


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


def check_4h_alignment(direction, price_chg_4h):
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def time_of_day_modifier():
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        return 1, "US_SESSION"
    return 0, None


def is_erratic_history(rank_history, exclude_last=False):
    ranks = rank_history[:-1] if exclude_last else rank_history
    ranks = [r for r in ranks if r is not None]
    if len(ranks) < 3:
        return False
    reversals = sum(1 for i in range(1, len(ranks) - 1)
                    if (ranks[i] > ranks[i-1] and ranks[i] > ranks[i+1]) or
                       (ranks[i] < ranks[i-1] and ranks[i] < ranks[i+1]))
    return reversals >= ERRATIC_REVERSAL_THRESHOLD


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# FETCH & PARSE
# ═══════════════════════════════════════════════════════════════

def fetch_markets():
    try:
        data = cfg.mcporter_call("leaderboard_get_markets", limit=100)
        data = data.get("data", data)
        raw = data.get("markets", data)
        if isinstance(raw, dict):
            raw = raw.get("markets", [])
        return raw
    except Exception:
        return None


def parse_scan(raw_markets):
    markets = []
    for i, m in enumerate(raw_markets):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = m.get("dex", "")
        if XYZ_BANNED and (dex == "xyz" or token.lower().startswith("xyz:")):
            continue
        if not token:
            continue

        markets.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
        })

    return {"markets": markets[:TOP_N], "time": now_iso()}


def check_asset_volume(token, dex):
    try:
        data = cfg.mcporter_call("market_get_asset_data",
                                  asset=token, candle_intervals=["1h"],
                                  include_funding=False)
        if not data:
            return 0, True
        ad = data.get("data", data)
        if not isinstance(ad, dict):
            return 0, True
        ac = ad.get("asset_context", ad.get("assetContext", {}))
        if not isinstance(ac, dict):
            return 0, True
        vol = safe_float(ac.get("dayNtlVlm", 0))
        prev = safe_float(ac.get("prevDayNtlVlm", 0))
        if prev > 0:
            ratio = vol / prev
            return ratio, ratio >= STRIKER_MIN_VOL_RATIO
        return 0, True
    except Exception:
        return 0, True


# ═══════════════════════════════════════════════════════════════
# STRIKER SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP signals. Identical logic to v3.0 Striker."""

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = market["direction"]
        current_contrib = market["contribution"]

        if current_rank <= 10:
            continue

        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)

        if not prev_market:
            continue

        rank_jump = prev_market["rank"] - current_rank

        is_first_jump = False
        is_immediate = False
        is_contrib_explosion = False
        reasons = []

        if rank_jump >= 10 and prev_market["rank"] >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_market['rank']}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_market["rank"] >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_market['rank']}->#{current_rank}")

        if prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                is_contrib_explosion = True
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        if not is_first_jump and not is_immediate:
            continue

        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i]
                      for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        abs_velocity = abs(contrib_velocity)

        if abs_velocity < STRIKER_MIN_VELOCITY_FLOOR:
            if is_first_jump and contrib_velocity > 0:
                pass
            else:
                continue

        # ── Scoring ──
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs_velocity > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs_velocity:.1f}")

        if prev_market["rank"] >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        if old_market:
            total_climb = old_market["rank"] - current_rank
            if total_climb >= 10:
                score += 1
                reasons.append(f"CLIMBING +{total_climb} over scans")

        tod_mod, tod_reason = time_of_day_modifier()
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        vol_ratio, vol_strong = 0, True
        vol_ratio, vol_strong = check_asset_volume(token, dex)
        if not vol_strong:
            continue
        reasons.append(f"VOL_CONFIRMED {vol_ratio:.1f}x")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "isContribExplosion": is_contrib_explosion,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": market["traders"],
            "priceChg4h": market.get("price_chg_4h", 0),
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


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
        coins = [p["coin"] for p in positions]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": f"RIDING: {coins}. DSL manages exit.",
                     "_v2_no_thesis_exit": True})
        return

    # ── Trade counter ─────────────────────────────────────────
    tc = cfg.load_trade_counter()
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0}
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Fetch and scan ────────────────────────────────────────
    raw_markets = fetch_markets()
    if raw_markets is None:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    current_scan = parse_scan(raw_markets)
    history = cfg.load_scan_history()

    # Detect Striker signals only (Stalker is dead)
    signals = detect_striker_signals(current_scan, history)

    # Save history
    history["scans"].append(current_scan)
    cfg.save_scan_history(history)

    # Apply cooldowns
    cooldown_min = COOLDOWN_MINUTES
    signals = [s for s in signals
               if not cfg.is_asset_cooled_down(s["token"], cooldown_min)]

    # Filter already-held assets
    held_coins = {p["coin"].upper() for p in positions}
    signals = [s for s in signals if s["token"] not in held_coins]

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Striker signals. Scanned {len(current_scan['markets'])} markets.",
                    "scansInHistory": len(history["scans"])})
        return

    # ── Best signal → entry ───────────────────────────────────
    best = signals[0]
    margin = round(account_value * MARGIN_PCT, 2)

    tc["entries"] = tc.get("entries", 0) + 1
    cfg.save_trade_counter(tc)

    cfg.output({
        "status": "ok",
        "signal": best,
        "entry": {
            "asset": best["token"],
            "direction": best["direction"],
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
        "_mantis_version": "4.0",
    })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
