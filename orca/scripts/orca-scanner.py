#!/usr/bin/env python3
# Senpi ORCA Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""ORCA v3.0 — Gen-1 Vanilla Striker revert.

## v3.0 change — revert to Gen-1 vanilla Striker

v2.0 added Gen-2 quality confirmation (Tier 2 momentum events + TCS
trader quality tags + contribution_pct_change_4h booster). Live data and
Orca's own self-diagnosis: the second API call adds latency, and by the
time the quality confirmation score lands, the move has already run — we
buy local tops after the move. Orca's recommendation was explicit: revert
to Gen-1 vanilla Striker (pure FIRST_JUMP + base scoring + volume
confirmation). v3.0 executes that:

- Removed leaderboard_get_momentum_events API call
- Removed QUALITY_TCS gate (ELITE/RELIABLE)
- Removed MOMENTUM_CONCENTRATION_MIN check
- Removed QUALITY_CONFIRM_POINTS / ELITE_BONUS score booster
- Removed CONTRIB_ACCEL_POINTS booster
- Removed contrib_change field from parse_scan
- Back to a single API call: leaderboard_get_markets

Also applies the fleet-wide batch-4 leverage safety fix: the emitted
entry.leverage is clamped via strategy_get_asset_trading_limits so
downstream executors never hit CREATE_INVALID_LEVERAGE.

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


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 1000.0  # Default starting budget — override per-agent if different

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

COOLDOWN_MINUTES = 120
MARGIN_PCT = 0.18
XYZ_BANNED = True

# Striker thresholds
STRIKER_MIN_SCORE = 9
STRIKER_MIN_REASONS = 4
STRIKER_MIN_RANK_JUMP = 15
STRIKER_MIN_PREV_RANK = 25
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
            "cc_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
        })

    return {"markets": markets[:TOP_N], "time": now_iso()}


def get_safe_leverage(wallet, asset, requested_leverage):
    """Query Hyperliquid's max leverage for this asset and clamp.

    Fleet-wide leverage safety fix (batch 4). Orca emits a signal with a
    suggested leverage but does not itself call create_position — clamp
    the suggested leverage here so the downstream executor never requests
    more than the asset's Hyperliquid max.
    """
    try:
        limits = cfg.mcporter_call(
            "strategy_get_asset_trading_limits",
            strategy_wallet=wallet,
            coin=asset,
        )
        if limits:
            data = limits.get("data", limits)
            if isinstance(data, dict):
                lev = data.get("leverage", {})
                if isinstance(lev, dict):
                    max_lev = int(float(lev.get("value", 20)))
                    return min(requested_leverage, max_lev)
                elif isinstance(lev, (int, float)):
                    return min(requested_leverage, int(lev))
    except Exception:
        pass
    return requested_leverage


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
# SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_signals(current_scan, history):
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

        if score < STRIKER_MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # 15m velocity freshness gate — SM must be actively building, not stale
        cc_15m = safe_float(market.get("cc_15m", 0))
        if cc_15m <= 0:
            continue  # SM velocity is flat or fading — signal is stale, don't enter

        # Volume confirmation
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
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    raw_markets = fetch_markets()
    if raw_markets is None:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    current_scan = parse_scan(raw_markets)
    history = cfg.load_scan_history()

    signals = detect_signals(current_scan, history)

    history["scans"].append(current_scan)
    cfg.save_scan_history(history)

    signals = [s for s in signals
               if not cfg.is_asset_cooled_down(s["token"], COOLDOWN_MINUTES)]
    held_coins = {p["coin"].upper() for p in positions}
    signals = [s for s in signals if s["token"] not in held_coins]

    if not signals:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No Striker signals. "
                            f"Scanned {len(current_scan['markets'])} markets.",
                    "scansInHistory": len(history["scans"])})
        return

    best = signals[0]
    margin = round(account_value * MARGIN_PCT, 2)
    # Fleet-wide batch-4 leverage safety: clamp emitted leverage to asset max.
    safe_leverage = get_safe_leverage(wallet, best["token"], DEFAULT_LEVERAGE)

    tc["entries"] = tc.get("entries", 0) + 1
    save_trade_counter(tc)

    cfg.output({
        "status": "ok",
        "signal": best,
        "entry": {
            "asset": best["token"],
            "direction": best["direction"],
            "leverage": safe_leverage,
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
        "_orca_version": "3.0",
    })


def load_trade_counter():
    # Fleet-wide stale date fix. Without the date check, an agent that
    # doesn't trade for a day stays locked forever because load returns
    # stale data and the rollover only ran on save.
    today = now_date()
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                tc = json.load(f)
            if tc.get("date") == today:
                return tc
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": today, "entries": 0}


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
