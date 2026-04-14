#!/usr/bin/env python3
# Senpi CHEETAH Scanner v5.0-APEX
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""CHEETAH v5.0 APEX — Multi-signal confluence sniper.

Purpose-built to win the Senpi Arena via asymmetric ROE% optimization.

## Thesis

The Arena rewards ROE% over 7 days with a $25k weekly volume floor. Two
winning shapes exist: scalpers (100+ trades, tiny edge each) and snipers
(15-20 trades, huge edge each). Scalping is fee-drag death on Hyperliquid
(the fleet is 37 red / 2 green precisely because of this). Sniping works.

APEX takes the sniper path: refuse to trade unless ALL major signals align
simultaneously (score >= 14 out of 15). 5 target trades per week hits the
$25k volume floor at 80% margin + 10x leverage. Every trade is maximum
conviction, maximum size, aggressive profit ratcheting.

## Pipeline

Every 3 minutes:
  1. fetch_sm_markets() - leaderboard_get_markets (top 100, non-XYZ)
  2. update_scan_history() - track rank changes across scans
  3. fetch_quality_trader_positions() - cached 15 min, top 8 ELITE/RELIABLE
  4. For each asset that passes hard gates:
     - score_confluence() - add up all signals
     - Keep if score >= 14
  5. Pick highest score, execute via create_position (Wolverine pattern)
  6. Log entry to entry-log.jsonl (survives session clears)

## Scoring (max = 15, threshold = 14)

  +4  SM_STRONG: pct_of_top_traders_gain >= 10% AND trader_count >= 25
  +2  VELOCITY: 15m contribution >= 1.0 OR 1h contribution >= 3.0
  +2  ACCELERATING: 15m > 1h > 0 (SM actively building)
  +2  DUAL_PRICE: 4h move >= 2% AND 1h agrees same direction
  +1  VOLUME: current volume >= 2x 6h average
  +3  QUALITY_TRADER: >= 1 ELITE/RELIABLE trader positioned same direction
  +1  RANK_CLIMB: climbed >= 5 positions in last 2 scans

Hard gates (any failure = reject before scoring):
  - XYZ banned
  - Not already held by APEX
  - Not in cooldown
  - SM direction non-empty
  - Either 15m OR 1h velocity must pass minimum (can't be both stale)

## Runtime

Runs as detached bash loop (no LLM wake). Self-executing via mcporter.
Persistent entry log. All fleet-standard guardrails.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cheetah_config as cfg

MAX_POSITIONS = 1
XYZ_BANNED = True
ENTRY_LOG_FILE = "entry-log.jsonl"
SCAN_HISTORY_FILE = "scan-history.json"
QUALITY_CACHE_FILE = "quality-cache.json"
STALE_ORDER_MAX_AGE_SEC = 600  # 10 minutes


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (rebased to $648, Cheetah's equity)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 648.0  # APEX rebase — not $1000

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap. 5 target matches $25k Arena volume floor."""
    if starting_budget <= 0:
        return 5
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:     return 8   # Hot hand
    elif pnl_pct >= 0:   return 5   # Target rate — Arena volume floor
    elif pnl_pct >= -5:  return 3   # Careful
    elif pnl_pct >= -15: return 2   # Defensive
    elif pnl_pct >= -25: return 1   # Preserve
    else:                return 0   # HARD STOP


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score, tiers, default_leverage):
    for tier in sorted(tiers, key=lambda t: t.get("minScore", 0), reverse=True):
        if score >= tier.get("minScore", 0):
            return tier.get("leverage", default_leverage)
    return default_leverage


# ═══════════════════════════════════════════════════════════════
# FLEET STANDARD: has_resting_orders with auto-cancel
# ═══════════════════════════════════════════════════════════════

def has_resting_orders(wallet):
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = time.time() * 1000
    max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
    has_fresh = False
    for o in orders:
        if o.get("reduceOnly", False):
            continue
        ts_raw = o.get("timestamp", 0) or 0
        try:
            ts = float(ts_raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0 and (now_ms - ts) > max_age_ms:
            oid = o.get("oid") or o.get("orderId") or o.get("id")
            if oid:
                try:
                    cfg.mcporter_call(
                        "cancel_order",
                        strategyWalletAddress=wallet,
                        orderId=int(oid),
                    )
                except Exception:
                    pass
            continue
        has_fresh = True
    return has_fresh


# ═══════════════════════════════════════════════════════════════
# PERSISTENT ENTRY LOG (Wolverine v2.3 pattern)
# ═══════════════════════════════════════════════════════════════

def append_entry_log(event_type, asset, direction, **kwargs):
    """Append a JSONL line to entry-log.jsonl. Survives session clears."""
    record = {
        "ts": time.time(),
        "iso": cfg.now_iso(),
        "event": event_type,
        "asset": asset,
        "direction": direction,
    }
    record.update(kwargs)
    try:
        p = os.path.join(cfg.STATE_DIR, ENTRY_LOG_FILE)
        with open(p, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except (IOError, OSError):
        pass


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_sm_markets():
    """Get current SM leaderboard. Returns list of normalized market dicts."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return []
    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw

    normalized = []
    for i, m in enumerate(markets):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if not token:
            continue
        normalized.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                             m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume_6h": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return normalized


def load_scan_history():
    p = os.path.join(cfg.STATE_DIR, SCAN_HISTORY_FILE)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"scans": []}


def save_scan_history(history):
    scans = history.get("scans", [])
    if len(scans) > 20:
        history["scans"] = scans[-20:]
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, SCAN_HISTORY_FILE), history)


def build_scan_snapshot(markets):
    """Snapshot just the fields we need for rank tracking."""
    return {
        "ts": time.time(),
        "markets": [
            {"token": m["token"], "dex": m["dex"], "rank": m["rank"], "direction": m["direction"]}
            for m in markets
        ],
    }


def get_rank_climb(history, token, dex):
    """How many positions did this token climb in the last 2 scans?
    Returns a non-negative int. 0 if no history or no climb."""
    scans = history.get("scans", [])
    if len(scans) < 2:
        return 0
    prev = scans[-2]
    current = scans[-1]
    prev_rank = None
    current_rank = None
    for m in prev.get("markets", []):
        if m.get("token") == token and m.get("dex", "") == dex:
            prev_rank = m.get("rank", 999)
            break
    for m in current.get("markets", []):
        if m.get("token") == token and m.get("dex", "") == dex:
            current_rank = m.get("rank", 999)
            break
    if prev_rank is None or current_rank is None:
        return 0
    return max(0, prev_rank - current_rank)  # positive = climbed


def fetch_quality_trader_positions(quality_cfg):
    """Fetch top ELITE/RELIABLE traders and their current positions.
    Cached for quality_cfg.cacheMinutes to reduce API cost.

    Returns: dict mapping (asset, direction) -> list of trader addresses
    """
    cache_path = os.path.join(cfg.STATE_DIR, QUALITY_CACHE_FILE)
    cache_minutes = quality_cfg.get("cacheMinutes", 15)

    # Try cache first
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
            if time.time() - cache.get("ts", 0) < cache_minutes * 60:
                return cache.get("positions_map", {})
        except (json.JSONDecodeError, IOError):
            pass

    # Fetch quality traders
    raw = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame=quality_cfg.get("timeFrame", "WEEKLY"),
        sort_by="PROFIT_AND_LOSS_UNREALIZED",
        consistency=quality_cfg.get("consistency", ["ELITE", "RELIABLE"]),
        open_position_filter=True,
        limit=quality_cfg.get("poolSize", 8),
    )
    if not raw:
        return {}

    traders = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            traders = data.get("traders", [])
        elif isinstance(data, list):
            traders = data

    addresses = []
    for t in traders:
        if isinstance(t, dict):
            addr = str(t.get("address", "")).lower()
            if addr:
                addresses.append(addr)

    # Fetch each trader's positions
    positions_map = {}  # "(asset, direction)" -> [addr, ...]
    for addr in addresses:
        data = cfg.mcporter_call("leaderboard_get_trader_positions", trader_id=addr)
        if not data:
            continue
        positions = []
        if isinstance(data, dict):
            d = data.get("data", data)
            if isinstance(d, dict):
                positions = d.get("positions", d.get("top_positions", []))
            elif isinstance(d, list):
                positions = d
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            asset = str(
                pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
            ).upper()
            if not asset:
                continue
            # Infer direction from szi/pnl if not explicit
            direction = str(pos.get("direction", pos.get("side", ""))).upper()
            if direction not in ("LONG", "SHORT"):
                szi = safe_float(pos.get("szi", 0))
                if szi != 0:
                    direction = "LONG" if szi > 0 else "SHORT"
                else:
                    direction = "LONG"  # fallback
            key = f"{asset}:{direction}"
            positions_map.setdefault(key, []).append(addr)

    # Cache result
    try:
        cfg.atomic_write(cache_path, {"ts": time.time(), "positions_map": positions_map})
    except Exception:
        pass

    return positions_map


# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

def score_confluence(market, quality_positions, history, config):
    """Score a single market for APEX confluence. Returns (score, reasons).

    Hard gates reject with score=0 and an empty reasons list.
    """
    sm_cfg = config.get("sm", {})
    vel_cfg = config.get("velocity", {})
    price_cfg = config.get("priceConfirmation", {})
    vol_cfg = config.get("volume", {})
    rank_cfg = config.get("rankClimb", {})

    direction = market["direction"]
    if not direction:
        return 0, []

    reasons = []
    score = 0

    # Hard gate: SM consensus must meet minimum
    sm_pct = market["pct"]
    sm_traders = market["traders"]
    min_pct = sm_cfg.get("minSmPct", 10.0)
    min_traders = sm_cfg.get("minSmTraders", 25)
    if sm_pct < min_pct or sm_traders < min_traders:
        return 0, []
    score += 4
    reasons.append(f"SM_STRONG {sm_pct:.1f}%/{sm_traders}t")

    # Hard gate: at least one velocity axis must pass
    c15m = market["contrib_15m"]
    c1h = market["contrib_1h"]
    min_15m = vel_cfg.get("min15m", 1.0)
    min_1h = vel_cfg.get("min1h", 3.0)
    if c15m < min_15m and c1h < min_1h:
        return 0, []
    if c15m >= min_15m or c1h >= min_1h:
        score += 2
        reasons.append(f"VELOCITY 15m={c15m:.2f}/1h={c1h:.2f}")

    # Accelerating: 15m > 1h > 0 (SM inflow building, not decaying)
    if c15m > 0 and c1h > 0 and c15m > c1h:
        score += 2
        reasons.append(f"ACCEL 15m({c15m:.2f})>1h({c1h:.2f})")

    # Dual price confirmation (4h + 1h both aligned)
    p4h = market["price_chg_4h"]
    p1h = market["price_chg_1h"]
    min_4h = price_cfg.get("min4hMovePct", 2.0)
    if direction == "LONG":
        if p4h >= min_4h and p1h > 0:
            score += 2
            reasons.append(f"DUAL_PRICE +{p4h:.1f}/+{p1h:.2f}%")
    else:  # SHORT
        if p4h <= -min_4h and p1h < 0:
            score += 2
            reasons.append(f"DUAL_PRICE {p4h:.1f}/{p1h:.2f}%")

    # Volume spike
    vol = market["volume"]
    avg_vol = market["avg_volume_6h"]
    min_ratio = vol_cfg.get("minVolumeRatio", 2.0)
    if avg_vol > 0 and vol >= avg_vol * min_ratio:
        score += 1
        reasons.append(f"VOL {vol/avg_vol:.1f}x")

    # Quality trader alignment
    key = f"{market['token']}:{direction}"
    aligned_traders = quality_positions.get(key, [])
    if aligned_traders:
        score += 3
        reasons.append(f"QUALITY_ALIGN {len(aligned_traders)}_traders")

    # Rank climb bonus
    climb = get_rank_climb(history, market["token"], market["dex"])
    min_climb = rank_cfg.get("minClimbPerTwoScans", 5)
    top_n = rank_cfg.get("topNFilter", 15)
    if climb >= min_climb and market["rank"] <= top_n:
        score += 1
        reasons.append(f"RANK_CLIMB +{climb}→#{market['rank']}")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# COOLDOWN / TRADE COUNTER
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    today = cfg.now_date()
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
    tc["date"] = cfg.now_date()
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


def set_cooldown(asset, minutes):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    cooldowns = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                cooldowns = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    cooldowns[asset] = {
        "until": time.time() + minutes * 60,
        "set_at": cfg.now_iso(),
    }
    cfg.atomic_write(p, cooldowns)


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, signal, account_value, entry_cfg, leverage_cfg):
    """Self-execute via Senpi MCP create_position. Wolverine pattern."""
    margin_pct = entry_cfg.get("marginPct", 0.80)
    margin = round(account_value * margin_pct, 2)

    leverage = get_leverage_for_score(
        signal["score"],
        leverage_cfg.get("tiers", []),
        leverage_cfg.get("default", 8),
    )

    order = {
        "coin": signal["asset"],
        "direction": signal["direction"],
        "leverage": leverage,
        "marginAmount": margin,
        "orderType": entry_cfg.get("orderType", "FEE_OPTIMIZED_LIMIT"),
        "feeOptimizedLimitOptions": {
            "ensureExecutionAsTaker": entry_cfg.get("ensureExecutionAsTaker", True),
            "executionTimeoutSeconds": entry_cfg.get("executionTimeoutSeconds", 30),
        },
    }

    reason = (
        f"CHEETAH APEX v5.0 confluence fire: score={signal['score']}, "
        f"reasons={','.join(signal['reasons'][:5])}"
    )
    result = cfg.mcporter_call(
        "create_position",
        strategyWalletAddress=wallet,
        orders=[order],
        reason=reason,
    )
    success = bool(result and result.get("success"))
    return success, result, margin, leverage


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run():
    config = cfg.load_config()
    wallet, _ = cfg.get_wallet_and_strategy()

    if not wallet:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "no wallet"})
        return

    account_value, positions = cfg.get_positions(wallet)
    if account_value <= 0:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "cannot read account"})
        return

    # Max positions
    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_cheetah_version": "5.0-APEX",
        })
        return

    # Daily cap (P&L-aware, rebased to $648)
    tc = load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%",
            "_cheetah_version": "5.0-APEX",
        })
        return

    # Resting orders (auto-cancel stale ones)
    if has_resting_orders(wallet):
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY", "note": "resting order pending",
            "_cheetah_version": "5.0-APEX",
        })
        return

    # ── Fetch SM markets ──
    markets = fetch_sm_markets()
    if not markets:
        cfg.output({"status": "error", "error": "failed to fetch markets"})
        return

    # ── Update scan history ──
    history = load_scan_history()
    history["scans"].append(build_scan_snapshot(markets))
    save_scan_history(history)

    # ── Fetch quality trader positions (cached) ──
    quality_cfg = config.get("qualityTraders", {})
    quality_positions = fetch_quality_trader_positions(quality_cfg)

    # ── Score all markets ──
    held_coins = {p["coin"].upper() for p in positions}
    sm_cfg = config.get("sm", {})
    entry_cfg = config.get("entry", {})
    leverage_cfg = config.get("leverage", {})
    cooldown_cfg = config.get("cooldown", {})
    cooldown_min = cooldown_cfg.get("perAssetMinutes", 240)
    min_score = entry_cfg.get("minScore", 14)

    candidates = []
    all_scored = []

    for market in markets:
        # Already held
        if market["token"] in held_coins:
            continue
        # On cooldown
        if is_on_cooldown(market["token"]):
            continue

        score, reasons = score_confluence(market, quality_positions, history, config)
        if score == 0:
            continue

        all_scored.append({"token": market["token"], "direction": market["direction"], "score": score})

        if score >= min_score:
            candidates.append({
                "asset": market["token"],
                "direction": market["direction"],
                "score": score,
                "reasons": reasons,
                "rank": market["rank"],
                "smPct": market["pct"],
                "smTraders": market["traders"],
                "contrib15m": market["contrib_15m"],
                "contrib1h": market["contrib_1h"],
                "price4h": market["price_chg_4h"],
                "price1h": market["price_chg_1h"],
            })

    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"0 candidates at score >= {min_score} ({len(all_scored)} scored)",
            "topScored": top3,
            "_cheetah_version": "5.0-APEX",
        })
        return

    # Highest score wins
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # Execute
    success, result, margin, leverage = execute_entry(
        wallet, best, account_value, entry_cfg, leverage_cfg
    )

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)
        set_cooldown(best["asset"], cooldown_min)

        append_entry_log(
            "ENTRY",
            asset=best["asset"],
            direction=best["direction"],
            score=best["score"],
            reasons=best["reasons"],
            leverage=leverage,
            margin=margin,
            sm_pct=best["smPct"],
            sm_traders=best["smTraders"],
            contrib_15m=best["contrib15m"],
            contrib_1h=best["contrib1h"],
            price_4h=best["price4h"],
            price_1h=best["price1h"],
            dynamic_cap=dynamic_cap,
            account_value=account_value,
        )

        cfg.output({
            "status": "ok",
            "action": "ENTRY",
            "signal": best,
            "execution": {
                "asset": best["asset"],
                "direction": best["direction"],
                "leverage": leverage,
                "margin": margin,
                "orderType": entry_cfg.get("orderType", "FEE_OPTIMIZED_LIMIT"),
            },
            "result": result,
            "_cheetah_version": "5.0-APEX",
        })
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        append_entry_log(
            "ENTRY_FAILED",
            asset=best["asset"],
            direction=best["direction"],
            score=best["score"],
            error=error,
        )
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": error,
            "_cheetah_version": "5.0-APEX",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
