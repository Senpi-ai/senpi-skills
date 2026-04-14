#!/usr/bin/env python3
# Senpi RAPTOR Scanner v3.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""RAPTOR v3.0 — Hot Streak Follower (complete rewrite).

v3.0 COMPLETE REWRITE — fixes the zero-trade bug.

## Why v2.1 never traded

v2.1 used leaderboard_get_momentum_events as the primary signal source and
filtered on event.concentration, event.top_positions, and event.trader_tags.
All three of those fields are NULL on blocked momentum events — and 100%
of tier-2 events in recent windows (820/820 over 44h) are blocked with
trader_cooldown_active or system_cooldown_active.

Per the senpi guide:
> Blocked events are equally valid momentum signals. The blocking only
> affects notification delivery, not signal quality. Events with
> top_positions: null will not match any asset filter.

Because Raptor's filters dereferenced these null fields, every single
event was silently dropped before the SM alignment check. Raptor was
mathematically incapable of producing a signal in any window.

## v3.0 architecture

Instead of momentum_events (mostly blocked with null data), v3.0 uses
leaderboard_get_top as the primary filter — it returns currently active
hot traders with populated delta PnL data.

Pipeline:
  1. leaderboard_get_top(limit=30) → top 30 by 4h delta PnL
  2. Local filter: delta_pnl >= minDeltaPnl (default $2M = tier 1 threshold)
  3. discovery_get_top_traders(addresses=[...], consistency=[ELITE,RELIABLE])
     → filter to quality traders and get their classification labels
  4. For each quality hot trader: leaderboard_get_trader_positions(address)
     → per-market delta PnL breakdown (actually populated, unlike blocked
       momentum events)
  5. Pick each trader's strongest position by |delta_pnl|, compute
     concentration locally from the positions list
  6. leaderboard_get_markets → SM alignment check on each candidate
  7. Score and execute best candidate via create_position (self-executing)

## Fleet-standard guardrails

- STARTING_BUDGET + get_dynamic_daily_cap (P&L-aware circuit breaker)
- has_resting_orders() with auto-cancel for stale maker orders >10 min old
- Per-asset cooldown (2h default)
- Per-trader event dedupe (4h window)
- Self-executing via create_position (Wolverine pattern)

Runs every 3 minutes.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raptor_config as cfg

MAX_POSITIONS = 2
STARTING_BUDGET = 1000.0
STALE_ORDER_MAX_AGE_SEC = 600  # 10 min
XYZ_BANNED = True


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker, fleet standard)
# ═══════════════════════════════════════════════════════════════

def get_dynamic_daily_cap(account_value, starting_budget=STARTING_BUDGET):
    """P&L-aware daily entry cap. Matches fleet PR #176."""
    if starting_budget <= 0:
        return 4
    pnl_pct = ((account_value - starting_budget) / starting_budget) * 100
    if pnl_pct >= 5:     return 12  # Hot hand — up >5%
    elif pnl_pct >= 0:   return 8   # Small win / breakeven
    elif pnl_pct >= -5:  return 5   # Careful
    elif pnl_pct >= -15: return 3   # Defensive
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
# FLEET-STANDARD: auto-cancel stale resting orders
# ═══════════════════════════════════════════════════════════════

def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC. Matches fleet PR #177 pattern."""
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
# DATA FETCHING (v3.0: leaderboard_get_top, not momentum_events)
# ═══════════════════════════════════════════════════════════════

def _extract_list(raw, *keys):
    """Unwrap nested {data: {...: [...]}} API responses to get the inner list."""
    if raw is None:
        return []
    cur = raw
    if isinstance(cur, dict) and "data" in cur:
        cur = cur["data"]
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
    if isinstance(cur, list):
        return cur
    return []


def fetch_top_traders(limit=30):
    """Primary signal source: top traders by 4h delta PnL (rolling window).
    This replaces momentum_events because the latter returns null fields on
    blocked events (which is 100% of recent events)."""
    raw = cfg.mcporter_call("leaderboard_get_top", limit=limit)
    if not raw:
        return []
    traders = _extract_list(raw, "traders", "top", "leaderboard")
    if not traders and isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, list):
            traders = data
    return traders if isinstance(traders, list) else []


def fetch_quality_classifications(addresses):
    """Fetch ELITE/RELIABLE classifications for a batch of addresses in one
    call via discovery_get_top_traders. Returns a dict: address -> labels."""
    if not addresses:
        return {}
    raw = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame="WEEKLY",
        sort_by="PROFIT_AND_LOSS",
        consistency=["ELITE", "RELIABLE"],
        addresses=list(addresses),
        limit=len(addresses),
    )
    if not raw:
        return {}
    traders = _extract_list(raw, "traders")
    if not traders and isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, list):
            traders = data
    classifications = {}
    for t in traders:
        if not isinstance(t, dict):
            continue
        addr = str(t.get("address", t.get("trader_address", ""))).lower()
        if not addr:
            continue
        classifications[addr] = {
            "consistency": str(t.get("consistency", t.get("consistency_label", ""))).upper(),
            "activity": str(t.get("activity", t.get("activity_label", ""))).upper(),
            "risk": str(t.get("risk", t.get("risk_label", ""))).upper(),
            "roi": safe_float(t.get("roi", t.get("return_on_investment", 0))),
            "pnl": safe_float(t.get("pnl", t.get("profit_and_loss", 0))),
            "win_rate": safe_float(t.get("win_rate", 0)),
        }
    return classifications


def fetch_trader_positions(trader_address):
    """Get per-market delta PnL breakdown for a single trader. Populated data
    (unlike blocked momentum event top_positions)."""
    raw = cfg.mcporter_call("leaderboard_get_trader_positions", trader_id=trader_address)
    if not raw:
        return []
    positions = _extract_list(raw, "positions", "top_positions")
    if not positions and isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            positions = data.get("positions", data.get("top_positions", []))
    return positions if isinstance(positions, list) else []


def fetch_sm_map():
    """Fetch SM leaderboard for alignment checks. Same call most scanners use."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
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
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


# ═══════════════════════════════════════════════════════════════
# DEDUPE + COOLDOWN
# ═══════════════════════════════════════════════════════════════

SEEN_EVENTS_FILE = "seen-events.json"


def load_seen_events():
    p = os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen_events(seen, dedupe_hours=4):
    cutoff = time.time() - (dedupe_hours * 3600)
    cleaned = {k: v for k, v in seen.items() if v > cutoff}
    cfg.atomic_write(os.path.join(cfg.STATE_DIR, SEEN_EVENTS_FILE), cleaned)


def is_event_seen(seen, trader_id, asset, dedupe_hours=4):
    key = f"{trader_id[:10].lower()}:{asset}"
    ts = seen.get(key, 0)
    if ts <= 0:
        return False
    return (time.time() - ts) < (dedupe_hours * 3600)


def mark_event_seen(seen, trader_id, asset):
    key = f"{trader_id[:10].lower()}:{asset}"
    seen[key] = time.time()


def is_on_cooldown(asset, cooldown_minutes=120):
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


def set_cooldown(asset, cooldown_minutes=120):
    p = os.path.join(cfg.STATE_DIR, "cooldowns.json")
    cooldowns = {}
    if os.path.exists(p):
        try:
            with open(p) as f:
                cooldowns = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    cooldowns[asset] = {
        "until": time.time() + cooldown_minutes * 60,
        "set_at": cfg.now_iso(),
    }
    cfg.atomic_write(p, cooldowns)


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER
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


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════

def build_signal(trader, classification, positions, sm_map, hot_cfg, sm_cfg):
    """Given a quality hot trader + their positions + SM map, build a signal
    dict (or return None if the trader doesn't produce a qualifying signal)."""
    trader_id = str(trader.get("trader_id", trader.get("address", ""))).lower()
    if not trader_id:
        return None

    # Pick strongest position by |delta_pnl|
    best_pos = None
    best_abs_pnl = 0.0
    total_abs_pnl = 0.0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        asset = str(
            pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
        ).upper()
        if not asset:
            continue
        if XYZ_BANNED and asset.lower().startswith("xyz:"):
            continue
        delta_pnl = safe_float(
            pos.get("delta_pnl", pos.get("deltaPnl", pos.get("unrealized_pnl", pos.get("pnl", 0))))
        )
        direction = str(
            pos.get("direction", "LONG" if delta_pnl >= 0 else "SHORT")
        ).upper()
        if direction not in ("LONG", "SHORT"):
            continue
        abs_pnl = abs(delta_pnl)
        total_abs_pnl += abs_pnl
        if abs_pnl > best_abs_pnl:
            best_abs_pnl = abs_pnl
            best_pos = {
                "asset": asset,
                "direction": direction,
                "delta_pnl": delta_pnl,
            }

    if not best_pos or best_abs_pnl < hot_cfg.get("minPositionPnl", 500_000):
        return None

    # Concentration = top position PnL as fraction of total
    concentration = (best_abs_pnl / total_abs_pnl) if total_abs_pnl > 0 else 0
    if concentration < hot_cfg.get("minConcentration", 0.40):
        return None

    # SM alignment check
    sm = sm_map.get(best_pos["asset"])
    if not sm:
        return None
    if sm_cfg.get("requireDirectionMatch", True) and sm["direction"] != best_pos["direction"]:
        return None
    if sm["pct"] < sm_cfg.get("minSmPct", 2.0):
        return None
    if sm["traders"] < sm_cfg.get("minSmTraders", 10):
        return None

    # Scoring
    score = 0
    reasons = []

    tcs = classification.get("consistency", "")
    if tcs == "ELITE":
        score += 3
        reasons.append("ELITE")
    elif tcs == "RELIABLE":
        score += 2
        reasons.append("RELIABLE")
    else:
        return None  # only ELITE/RELIABLE allowed

    # Delta PnL magnitude tiers
    trader_delta = safe_float(
        trader.get("delta_pnl", trader.get("unrealized_pnl", trader.get("pnl", 0)))
    )
    if trader_delta >= hot_cfg.get("tier3Threshold", 10_000_000):
        score += 3
        reasons.append(f"TIER3_${trader_delta/1e6:.1f}M")
    elif trader_delta >= hot_cfg.get("tier2Threshold", 5_500_000):
        score += 2
        reasons.append(f"TIER2_${trader_delta/1e6:.1f}M")
    else:
        score += 1
        reasons.append(f"TIER1_${trader_delta/1e6:.1f}M")

    # Concentration conviction
    if concentration >= 0.70:
        score += 2
        reasons.append(f"HIGH_CONV_{concentration:.0%}")
    elif concentration >= 0.55:
        score += 1
        reasons.append(f"CONC_{concentration:.0%}")

    # SM strength
    if sm["pct"] >= 8:
        score += 2
        reasons.append(f"SM_STRONG_{sm['pct']:.1f}%")
    elif sm["pct"] >= 4:
        score += 1
        reasons.append(f"SM_ALIGNED_{sm['pct']:.1f}%")

    # Multi-timeframe price confirmation
    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    if best_pos["direction"] == "LONG":
        if p4h > 0.5 and p1h > 0.2:
            score += 2
            reasons.append(f"4H+1H_CONFIRMS_+{p4h:.1f}/+{p1h:.1f}%")
        elif p4h > 0.5:
            score += 1
            reasons.append(f"4H_CONFIRMS_+{p4h:.1f}%")
        elif p4h < -2:
            score -= 1
            reasons.append(f"4H_OPPOSING_{p4h:.1f}%")
    else:
        if p4h < -0.5 and p1h < -0.2:
            score += 2
            reasons.append(f"4H+1H_CONFIRMS_{p4h:.1f}/{p1h:.1f}%")
        elif p4h < -0.5:
            score += 1
            reasons.append(f"4H_CONFIRMS_{p4h:.1f}%")
        elif p4h > 2:
            score -= 1
            reasons.append(f"4H_OPPOSING_+{p4h:.1f}%")

    # 15m velocity freshness — fleet-standard penalty
    c15m = sm.get("contrib_15m", 0)
    if c15m > 0.5:
        score += 1
        reasons.append(f"15M_SPIKE_+{c15m:.2f}")
    elif c15m <= 0:
        score -= 1
        reasons.append(f"15M_STALE_{c15m:.2f}")

    return {
        "asset": best_pos["asset"],
        "direction": best_pos["direction"],
        "score": score,
        "reasons": reasons,
        "traderId": trader_id[:10] + "...",
        "fullTraderId": trader_id,
        "tcs": tcs,
        "traderDeltaPnl": trader_delta,
        "positionDeltaPnl": best_pos["delta_pnl"],
        "concentration": concentration,
        "smPct": sm["pct"],
        "smTraders": sm["traders"],
        "priceChg4h": p4h,
        "priceChg1h": p1h,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_entry(wallet, signal, account_value, entry_cfg, leverage_cfg):
    """Self-execute the entry via Senpi MCP create_position.
    Matches Wolverine/Phoenix pattern."""
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    high_conv_pct = entry_cfg.get("marginPctHighConv", 0.35)
    margin_pct = high_conv_pct if signal["score"] >= 10 else base_margin_pct
    margin = round(account_value * margin_pct, 2)

    leverage = get_leverage_for_score(
        signal["score"],
        leverage_cfg.get("tiers", []),
        leverage_cfg.get("default", 7),
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
        f"RAPTOR v3.0 hot streak: {signal['tcs']} trader "
        f"delta=${signal['traderDeltaPnl']/1e6:.1f}M, "
        f"conc={signal['concentration']:.0%}, score={signal['score']}"
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

    if len(positions) >= MAX_POSITIONS:
        coins = [p["coin"] for p in positions]
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"RIDING: {coins}. DSL manages exit.",
            "_raptor_version": "3.0",
        })
        return

    # Daily cap (P&L-aware)
    tc = load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%.",
        })
        return

    # Don't stack new entries while a maker order is still resting
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "resting order pending"})
        return

    hot_cfg = config.get("hotStreak", {})
    sm_cfg = config.get("smAlignment", {})
    dedupe_cfg = config.get("dedupe", {})
    entry_cfg = config.get("entry", {})
    leverage_cfg = config.get("leverage", {})

    # ── PHASE 1: Fetch top hot traders by 4h delta PnL ──
    top_traders = fetch_top_traders(limit=hot_cfg.get("topTraderLimit", 30))
    if not top_traders:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "leaderboard_get_top returned empty"})
        return

    # Filter to traders above the minimum delta PnL threshold
    min_delta = hot_cfg.get("minDeltaPnl", 2_000_000)
    hot_traders = []
    for t in top_traders:
        if not isinstance(t, dict):
            continue
        delta = safe_float(
            t.get("delta_pnl", t.get("unrealized_pnl", t.get("pnl", 0)))
        )
        if delta >= min_delta:
            hot_traders.append(t)

    if not hot_traders:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"No traders above ${min_delta/1e6:.1f}M delta PnL ({len(top_traders)} scanned)",
        })
        return

    # ── PHASE 2: Classify via discovery_get_top_traders (batch, 1 call) ──
    addresses = [
        str(t.get("trader_id", t.get("address", ""))).lower()
        for t in hot_traders
    ]
    addresses = [a for a in addresses if a]
    classifications = fetch_quality_classifications(addresses)

    quality_traders = []
    for t in hot_traders:
        addr = str(t.get("trader_id", t.get("address", ""))).lower()
        cls = classifications.get(addr)
        if not cls:
            continue  # not in ELITE/RELIABLE set
        if cls["consistency"] not in ("ELITE", "RELIABLE"):
            continue
        t["_classification"] = cls
        t["_address"] = addr
        quality_traders.append(t)

    if not quality_traders:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"{len(hot_traders)} hot traders, 0 ELITE/RELIABLE",
        })
        return

    # Sort by delta PnL desc so we prioritize the hottest streaks first
    quality_traders.sort(
        key=lambda t: safe_float(
            t.get("delta_pnl", t.get("unrealized_pnl", t.get("pnl", 0)))
        ),
        reverse=True,
    )

    # ── PHASE 3: SM map (1 call) ──
    sm_map = fetch_sm_map()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "No SM data"})
        return

    # ── PHASE 4: Per-trader position fetch + signal build ──
    seen_events = load_seen_events()
    held_coins = {p["coin"].upper() for p in positions}
    dedupe_hours = dedupe_cfg.get("eventDedupeHours", 4)
    cooldown_minutes = dedupe_cfg.get("perAssetCooldownMinutes", 120)

    candidates = []
    scan_limit = min(len(quality_traders), 10)  # cap positions fetch to top 10
    for t in quality_traders[:scan_limit]:
        addr = t["_address"]
        positions_data = fetch_trader_positions(addr)
        if not positions_data:
            continue

        signal = build_signal(
            t, t["_classification"], positions_data, sm_map, hot_cfg, sm_cfg
        )
        if not signal:
            continue

        if is_event_seen(seen_events, addr, signal["asset"], dedupe_hours):
            continue
        if is_on_cooldown(signal["asset"], cooldown_minutes):
            continue
        if signal["asset"] in held_coins:
            continue

        candidates.append(signal)

    if not candidates:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"{len(quality_traders)} quality traders, 0 candidates passed filters",
        })
        return

    candidates.sort(key=lambda s: s["score"], reverse=True)
    best = candidates[0]

    if best["score"] < entry_cfg.get("minScore", 6):
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"Best score {best['score']} < {entry_cfg.get('minScore', 6)}. {', '.join(best['reasons'][:3])}",
            "allCandidates": [
                {"asset": c["asset"], "dir": c["direction"], "score": c["score"]}
                for c in candidates[:5]
            ],
        })
        return

    # ── PHASE 5: Execute ──
    mark_event_seen(seen_events, best["fullTraderId"], best["asset"])
    save_seen_events(seen_events, dedupe_hours)
    set_cooldown(best["asset"], cooldown_minutes)

    success, result, margin, leverage = execute_entry(
        wallet, best, account_value, entry_cfg, leverage_cfg
    )

    if success:
        tc["entries"] = tc.get("entries", 0) + 1
        save_trade_counter(tc)
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
            "_raptor_version": "3.0",
        })
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": error,
            "_raptor_version": "3.0",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
