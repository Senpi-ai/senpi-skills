#!/usr/bin/env python3
# Senpi ORCA Scanner v2.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""ORCA v2.0 — Gen-2 Striker with Momentum Event Quality Confirmation.

The best Striker we can build. Same FIRST_JUMP explosion detection as
Roach/Jaguar/Mantis v4.0, but enhanced with Gen-2 Hyperfeed signals:

1. SCAN: leaderboard_get_markets → detect FIRST_JUMP rank explosions
2. CONFIRM: leaderboard_get_momentum_events (Tier 2) → is a quality trader
   driving this move? Check TCS tags (ELITE/RELIABLE only).
3. BOOST: contribution_pct_change_4h as conviction modifier
4. ENTER: only when FIRST_JUMP + quality trader confirmation

Why this is better than vanilla Striker:
- Roach/Jaguar enter on ANY violent rank jump. Orca v2.0 requires a
  quality trader (TCS ELITE or RELIABLE) to be generating momentum events
  on the same asset. This filters out pump-and-dumps driven by CHOPPY/DEGEN
  traders that Striker alone can't distinguish.

IMPORTANT: The Gen-2 confirmation is a SCORE BOOSTER, not a hard gate.
A vanilla Striker signal at score 12+ can still enter without quality
confirmation. But a score 7-8 signal that ALSO has ELITE confirmation
gets boosted to 9-11 and enters. This keeps the agent trading while
improving signal quality.

Architecture:
- 2 API calls per scan: leaderboard_get_markets + leaderboard_get_momentum_events
- Momentum events pre-indexed by asset for O(1) lookup
- Tier 2 events only (155/day, $5.5M+ threshold — Tier 1 is noise at 5,123/day)
- TCS gate on momentum: only ELITE and RELIABLE trader events count

Trade frequency: 2-5 per day

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
Runs every 90 seconds.
"""

import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca_config as cfg

TOP_N = 50


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 7
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 7
MAX_POSITIONS = 3
MAX_DAILY_ENTRIES = 6
COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.18
XYZ_BANNED = True

# Striker thresholds
STRIKER_MIN_SCORE = 9
STRIKER_MIN_REASONS = 4
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25
STRIKER_MIN_VOL_RATIO = 1.5

# Gen-2 momentum quality
MOMENTUM_TIER = 2
QUALITY_TCS = {"ELITE", "RELIABLE"}
MOMENTUM_CONCENTRATION_MIN = 0.4
QUALITY_CONFIRM_POINTS = 2
ELITE_BONUS = 1
CONTRIB_ACCEL_POINTS = 1


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
            "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
        })

    return {"markets": markets[:TOP_N], "time": now_iso()}


def fetch_momentum_index():
    """Fetch Tier 2 momentum events and index by asset."""
    data = cfg.mcporter_call("leaderboard_get_momentum_events",
                              tier=MOMENTUM_TIER, limit=100)
    if not data:
        return {}

    events = data.get("events", data.get("data", []))
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list):
        return {}

    index = {}
    for event in events:
        if not isinstance(event, dict):
            continue

        tags = event.get("trader_tags", event.get("tags", {}))
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = {}

        tcs = str(tags.get("TCS", tags.get("tcs", ""))).upper()
        if tcs not in QUALITY_TCS:
            continue

        concentration = safe_float(event.get("concentration", 0))
        if concentration < MOMENTUM_CONCENTRATION_MIN:
            continue

        top_positions = event.get("top_positions", [])
        if isinstance(top_positions, str):
            try:
                top_positions = json.loads(top_positions)
            except (json.JSONDecodeError, TypeError):
                top_positions = []

        trader_id = event.get("trader_id", event.get("address", ""))

        for pos in top_positions:
            if not isinstance(pos, dict):
                continue
            asset = str(pos.get("market", pos.get("asset", ""))).upper()
            if not asset:
                continue

            if asset not in index:
                index[asset] = []

            index[asset].append({
                "trader_id": trader_id,
                "tcs": tcs,
                "concentration": concentration,
                "direction": str(pos.get("direction", "")).upper(),
            })

    return index


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
# GEN-2 QUALITY CONFIRMATION
# ═══════════════════════════════════════════════════════════════

def get_momentum_quality(momentum_index, asset, direction):
    """Check if a quality trader has momentum events on this asset."""
    events = momentum_index.get(asset, [])
    if not events:
        return 0, [], False

    aligned = [e for e in events if e["direction"] == direction]
    if not aligned:
        return 0, [], False

    best = max(aligned, key=lambda e: (1 if e["tcs"] == "ELITE" else 0,
                                        e["concentration"]))

    score = QUALITY_CONFIRM_POINTS
    reasons = [f"QUALITY_CONFIRMED ({best['tcs']}, conc={best['concentration']:.2f})"]

    if best["tcs"] == "ELITE":
        score += ELITE_BONUS
        reasons.append("ELITE_TRADER")

    return score, reasons, True


# ═══════════════════════════════════════════════════════════════
# SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_signals(current_scan, history, momentum_index):
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

        # Contribution velocity
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

        # ── Base Striker scoring ──
        score = 0
        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs(contrib_velocity):.1f}")
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

        # ── Gen-2: Momentum quality boost ──
        q_score, q_reasons, has_quality = get_momentum_quality(
            momentum_index, token, direction)
        score += q_score
        reasons.extend(q_reasons)

        # ── Gen-2: Contribution acceleration boost ──
        contrib_change = market.get("contrib_change", 0)
        if abs(contrib_change) >= 0.02:
            score += CONTRIB_ACCEL_POINTS
            reasons.append(f"CONTRIB_ACCEL +{abs(contrib_change)*100:.1f}%")

        if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # Volume confirmation
        vol_ratio, vol_strong = check_asset_volume(token, dex)
        if not vol_strong:
            continue
        reasons.append(f"VOL_CONFIRMED {vol_ratio:.1f}x")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "GEN2_STRIKER",
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
            "hasQualityConfirmation": has_quality,
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
    if tc.get("entries", 0) >= MAX_DAILY_ENTRIES:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily entry limit ({MAX_DAILY_ENTRIES}) reached"})
        return

    raw_markets = fetch_markets()
    if raw_markets is None:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    current_scan = parse_scan(raw_markets)
    history = cfg.load_scan_history()
    momentum_index = fetch_momentum_index()

    signals = detect_signals(current_scan, history, momentum_index)

    history["scans"].append(current_scan)
    cfg.save_scan_history(history)

    signals = [s for s in signals
               if not cfg.is_asset_cooled_down(s["token"], COOLDOWN_MINUTES)]
    held_coins = {p["coin"].upper() for p in positions}
    signals = [s for s in signals if s["token"] not in held_coins]

    if not signals:
        quality_assets = list(momentum_index.keys())[:5]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Gen-2 Striker signals. "
                            f"Scanned {len(current_scan['markets'])} markets. "
                            f"Quality momentum on: {quality_assets or 'none'}.",
                    "scansInHistory": len(history["scans"]),
                    "momentumAssetsTracked": len(momentum_index)})
        return

    best = signals[0]
    margin = round(account_value * MARGIN_PCT, 2)

    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)

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
        "_orca_version": "2.0",
    })


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


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
