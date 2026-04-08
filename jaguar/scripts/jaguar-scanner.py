#!/usr/bin/env python3
# Senpi JAGUAR Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""JAGUAR v2.0 — Striker-Only. No Stalker. No Hunter. No Pyramid.

v1.0 post-mortem: 5 trades, -$293, 4 of 5 losses.
- Stalker: 3 trades, 1 win, -$95 net. Score 6-7 entries were garbage.
- Striker: 2 trades, 0 wins, -$173 net.

v2.0 changes:
- Stalker REMOVED (Roach experiment proved Striker-only wins)
- Hunter REMOVED (0 trades across all testing)
- Pyramiding REMOVED (never triggered, adds complexity)
- Leverage reduced to 7x (v1.0 at 10x, HYPE moved 4.2% in 10 min = -42% ROE)
- Exit management handled by plugin runtime (runtime.yaml)

The Striker logic is identical to Orca/Roach: FIRST_JUMP from #25+,
rank jump 15+, volume 1.5x, score 9+, 4+ reasons. Only the most
violent SM explosions trigger an entry.

Runs every 3 minutes.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jaguar_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 5
MAX_LEVERAGE = 7                    # Reduced from 10x
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4
COOLDOWN_MINUTES = 120
XYZ_BANNED = True

# Striker thresholds (same as Roach/Orca)
STRIKER_MIN_SCORE = 9
STRIKER_MIN_REASONS = 4
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25          # Must come from outside top 25
STRIKER_MIN_VOLUME_RATIO = 1.5


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


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# STRIKER SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, history):
    """Detect violent FIRST_JUMP signals. Identical logic to Roach/Orca Striker."""

    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m.get("token", ""), m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market.get("token", "")
        dex = market.get("dex", "")
        current_rank = market.get("rank", 999)
        direction = market.get("direction", "").upper()
        current_contrib = market.get("contribution", 0)
        traders = market.get("traders", 0)

        # Must not already be top 10
        if current_rank <= 10:
            continue

        # 4H alignment required
        price_chg_4h = market.get("price_chg_4h", 0)
        if not check_4h_alignment(direction, price_chg_4h):
            continue

        # XYZ ban
        if XYZ_BANNED and dex == "xyz":
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        if not prev_market:
            continue

        rank_jump = prev_market.get("rank", 999) - current_rank
        prev_rank = prev_market.get("rank", 999)

        # FIRST_JUMP: must come from outside top 25 with rank jump 10+
        is_first_jump = False
        is_immediate = False
        reasons = []

        if rank_jump >= 10 and prev_rank >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_rank}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_rank >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_rank}->#{current_rank}")

        if not is_first_jump and not is_immediate:
            continue

        # Rank jump gate
        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        # Contribution explosion
        if prev_market.get("contribution", 0) > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        # Contribution velocity
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m.get("contribution", 0))
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        # ── Scoring ──
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs(contrib_velocity):.1f}")

        if prev_rank >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        # 4H strength bonus
        if abs(price_chg_4h) > 3:
            score += 1
            reasons.append(f"STRONG_4H {price_chg_4h:+.1f}%")

        # Trader count bonus
        if traders >= 30:
            score += 1
            reasons.append(f"DEEP_SM ({traders}t)")

        if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # Volume confirmation
        vol_ratio = safe_float(market.get("vol_ratio", market.get("volume_ratio", 0)))
        if vol_ratio < STRIKER_MIN_VOLUME_RATIO:
            # Try to calculate from raw data
            volume = safe_float(market.get("volume", 0))
            avg_volume = safe_float(market.get("avg_volume", market.get("avgVolume", 0)))
            if avg_volume > 0:
                vol_ratio = volume / avg_volume
            if vol_ratio < STRIKER_MIN_VOLUME_RATIO:
                continue
        reasons.append(f"VOL {vol_ratio:.1f}x")

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
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": traders,
            "priceChg4h": price_chg_4h,
        })

    # Sort by score descending
    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals



# ═══════════════════════════════════════════════════════════════
# SCAN HISTORY
# ═══════════════════════════════════════════════════════════════

def load_scan_history():
    p = os.path.join(cfg.STATE_DIR, "scan-history.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > 60:
        history["scans"] = scans[-60:]
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "scan-history.json"), history)


def build_scan_snapshot(markets_data):
    markets = []
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        markets.append({
            "token": str(m.get("token", m.get("asset", ""))).upper(),
            "dex": m.get("dex", ""),
            "rank": int(m.get("rank", m.get("position", 999))),
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return {"markets": markets, "timestamp": now_iso()}


# ═══════════════════════════════════════════════════════════════
# COOLDOWN & TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": now_date(), "entries": 0, "dailyLoss": 0}


def save_trade_counter(tc):
    if tc.get("date") != now_date():
        tc = {"date": now_date(), "entries": 0, "dailyLoss": 0}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, "trade-counter.json"), tc)


def is_on_cooldown(coin):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            cooldowns = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    entry = cooldowns.get(coin)
    if not entry:
        return False
    return time.time() < entry.get("until", 0)


def set_cooldown(coin, minutes=120):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    cooldowns = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                cooldowns = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    cooldowns[coin] = {"until": time.time() + minutes * 60, "set_at": now_iso()}
    cfg.atomic_write(p, cooldowns)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    wallet, strategy_id = cfg.get_wallet_and_strategy()
    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    # ── Check existing positions ──────────────────────────────
    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    our_positions = [p for p in positions if not p.get("coin", "").lower().startswith("xyz")]

    if len(our_positions) >= MAX_POSITIONS:
        cfg.output({
            "status": "ok",
            "heartbeat": "NO_REPLY",
            "note": f"{len(our_positions)} positions active.",
        })
        return

    # ── Trade counter ─────────────────────────────────────────
    tc = load_trade_counter()
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    # ── Fetch SM data ─────────────────────────────────────────
    raw = cfg.mcporter_call("leaderboard_get_markets")
    if not raw:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    markets = []
    if isinstance(raw, dict):
        markets = raw.get("markets", raw.get("data", []))
    elif isinstance(raw, list):
        markets = raw

    # ── Build scan snapshot and save history ───────────────────
    current_scan = build_scan_snapshot(markets)
    history = load_scan_history()
    history["scans"].append(current_scan)
    save_scan_history(history)

    if len(history["scans"]) < 2:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Building scan history (need 2+ scans)"})
        return

    # ── Detect Striker signals ────────────────────────────────
    signals = detect_striker_signals(current_scan, history)

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Striker signals. Scanned {len(current_scan['markets'])} markets."})
        return

    # ── Filter and select best signal ─────────────────────────
    for signal in signals:
        token = signal["token"]

        if is_on_cooldown(token):
            continue

        # Already have this asset
        if any(p["coin"].upper() == token.upper() for p in our_positions):
            continue

        # ── Entry ─────────────────────────────────────────────
        margin = round(account_value * 0.20, 2)  # 20% of account
        leverage = DEFAULT_LEVERAGE

        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)

        cfg.output({
            "status": "ok",
            "signal": {
                "asset": token,
                "direction": signal["direction"],
                "score": signal["score"],
                "mode": "STRIKER",
                "reasons": signal["reasons"],
                "rankJump": signal["rankJump"],
                "isFirstJump": signal["isFirstJump"],
                "volRatio": signal["volRatio"],
                "traders": signal["traders"],
            },
            "entry": {
                "asset": token,
                "direction": signal["direction"],
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
            },
        })
        return

    cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                "note": f"{len(signals)} Striker signals found but all filtered (cooldown/duplicate)"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
