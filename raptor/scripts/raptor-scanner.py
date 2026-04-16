#!/usr/bin/env python3
# Senpi RAPTOR Scanner v3.2
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""RAPTOR v3.2 — Hot Streak Follower (whale entry-price discipline).

## v3.2 changes from v3.1 (2026-04-16)

Raptor's self-diagnosis identified the core failure mode: "We are buying
the top of the whale's bags. High-tier traders often sit in underwater or
flat positions for days, averaging down. Following them blindly without
knowing their entry price means we assume all their risk without their
padding."

v3.2 adds an entry-discipline check:
  1. Extract `entryPx` from each whale position (the whale's average entry).
  2. Fetch current market price via `market_get_prices`.
  3. HARD GATE: if current price has run >20% in the whale's favor from
     their entry, SKIP the trade — we'd be buying their top. For a LONG
     whale at $100, if current is >$120, skip. For a SHORT whale at $100,
     if current is <$80, skip.
  4. SCORE BONUS: if current price is WORSE than whale's entry (we'd get
     a better fill than the whale did), add +1 or +2 points to the score.
     This rewards trades where we're piggybacking but at a discount.

This directly addresses why Raptor v3.1 spiraled from +$50 unrealized
to -$60 realized: it kept re-entering the same assets where whales had
already made their money, buying the tops and shorting the bottoms.

## v3.1 changes from v3.0

v3.0 used leaderboard_get_top as the primary filter and then tried to
cross-reference with discovery_get_top_traders for classification. Live
data shows zero overlap between the 4h momentum leaderboard (dominated
by CHOPPY/Degen volatility traders) and the weekly ELITE/RELIABLE pool.
The 10 current top 4h traders are all CHOPPY. v3.0 was structurally
correct but filtered to zero candidates every scan.

v3.1 inverts the architecture: start from the quality pool, find the
winners within it:

  1. discovery_get_top_traders(
       time_frame=WEEKLY,
       sort_by=PROFIT_AND_LOSS_UNREALIZED,
       consistency=[ELITE, RELIABLE],
       open_position_filter=True,
       limit=20
     )
     → 20 quality traders currently holding winning positions

  2. Local filter: unRealizedProfitAndLoss >= min (default $500k weekly)

  3. For top 5-10 quality winners:
     leaderboard_get_trader_positions(trader_id=address)
     → per-market delta PnL breakdown

  4. Pick strongest position, compute concentration locally, check SM alignment

  5. Score and execute via create_position (self-executing)

v3.1 also fixes:
  - _extract_list now handles the triple-nested `data.leaderboard.data`
    response shape from leaderboard_get_top (Raptor agent patched this
    locally in v3.0 but repo still had the bug)
  - Correct field names for discovery_get_top_traders response:
    address, tcsLabel, activityLabel, riskLabel, returnOnInvestment,
    profitAndLoss, unRealizedProfitAndLoss (not consistency, activity,
    risk, roi, pnl)

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

def _dig(raw, *path):
    """Walk an arbitrarily nested dict response to find a list at the end.

    v3.1: handles the triple-nested `data.leaderboard.data` shape from
    leaderboard_get_top and the `data.traders` shape from discovery.
    Tries each path in sequence; first one that yields a list wins.
    """
    if raw is None:
        return []
    cur = raw
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k, cur.get("data", {}).get(k) if isinstance(cur.get("data"), dict) else None)
        if cur is None:
            return []
    if isinstance(cur, list):
        return cur
    return []


def _extract_list(raw, *candidate_paths):
    """Try multiple nested paths and return the first list found.
    Each candidate_paths entry is a tuple of keys to walk."""
    if raw is None:
        return []
    # Try each path
    for path in candidate_paths:
        cur = raw
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                cur = None
                break
        if isinstance(cur, list):
            return cur
    # Fallback: if raw or raw.data is already a list
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return []


def fetch_quality_hot_traders(limit=20, min_delta_usd=500_000):
    """v3.1 primary signal source: quality traders (ELITE/RELIABLE) currently
    holding winning positions this week.

    Inverts the v3.0 flow. v3.0 started from 4h momentum and filtered to
    quality, but quality traders are almost never in the 4h momentum top 10.
    v3.1 starts from the quality pool and filters to currently winning.

    Returns list of dicts with: address, unrealized_pnl, tcs_label, etc.
    """
    raw = cfg.mcporter_call(
        "discovery_get_top_traders",
        time_frame="WEEKLY",
        sort_by="PROFIT_AND_LOSS_UNREALIZED",
        consistency=["ELITE", "RELIABLE"],
        open_position_filter=True,
        limit=limit,
    )
    if not raw:
        return []
    # Real response shape: {data: {traders: [...]}}
    raw_list = _extract_list(
        raw,
        ("data", "traders"),
        ("traders",),
    )
    quality = []
    for t in raw_list:
        if not isinstance(t, dict):
            continue
        addr = str(t.get("address", "")).lower()
        if not addr:
            continue
        # v3.1: correct discovery field names
        unrealized = safe_float(t.get("unRealizedProfitAndLoss", 0))
        realized = safe_float(t.get("realizedProfitAndLoss", 0))
        total_pnl = safe_float(t.get("profitAndLoss", unrealized + realized))
        tcs_label = str(t.get("tcsLabel", "")).upper()
        if tcs_label not in ("ELITE", "RELIABLE"):
            continue
        # Filter to those with meaningful recent unrealized winnings
        if unrealized < min_delta_usd:
            continue
        quality.append({
            "address": addr,
            "unrealized_pnl": unrealized,
            "realized_pnl": realized,
            "total_pnl": total_pnl,
            "tcs_label": tcs_label,
            "tcs_value": safe_float(t.get("tcsValue", 0)),
            "activity_label": str(t.get("activityLabel", "")).upper(),
            "risk_label": str(t.get("riskLabel", "")).upper(),
            "roi": safe_float(t.get("returnOnInvestment", 0)),
            "win_rate": safe_float(t.get("winRate", 0)),
            "avg_leverage": safe_float(t.get("averageLeverageUsed", 0)),
        })
    # Sort by unrealized PnL desc — freshest winners first
    quality.sort(key=lambda x: x["unrealized_pnl"], reverse=True)
    return quality


def fetch_trader_positions(trader_address):
    """Get per-market delta PnL breakdown for a single trader."""
    raw = cfg.mcporter_call("leaderboard_get_trader_positions", trader_id=trader_address)
    if not raw:
        return []
    # Try common nested paths
    positions = _extract_list(
        raw,
        ("data", "positions"),
        ("data", "top_positions"),
        ("positions",),
        ("top_positions",),
    )
    return positions


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

def build_signal(trader, positions, sm_map, hot_cfg, sm_cfg):
    """Given a quality trader (already ELITE/RELIABLE with recent winnings)
    + their positions + SM map, build a signal dict. Returns None if the
    trader doesn't produce a qualifying signal."""
    trader_id = trader["address"]

    # Pick strongest position by |delta_pnl|
    best_pos = None
    best_abs_pnl = 0.0
    total_abs_pnl = 0.0
    position_count = 0
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
        # Try multiple field names — different MCP tools use different keys
        delta_pnl = safe_float(
            pos.get("delta_pnl",
                    pos.get("deltaPnl",
                            pos.get("unrealizedPnl",
                                    pos.get("unrealized_pnl",
                                            pos.get("pnl", 0)))))
        )
        direction = str(
            pos.get("direction",
                    pos.get("side",
                            "LONG" if delta_pnl >= 0 else "SHORT"))
        ).upper()
        if direction not in ("LONG", "SHORT"):
            continue
        # v3.2: capture whale entry price for entry-discipline check
        whale_entry_px = safe_float(
            pos.get("entryPx",
                    pos.get("entry_px",
                            pos.get("entryPrice",
                                    pos.get("entry_price",
                                            pos.get("avgEntryPx",
                                                    pos.get("avg_entry_px", 0))))))
        )
        position_count += 1
        abs_pnl = abs(delta_pnl)
        total_abs_pnl += abs_pnl
        if abs_pnl > best_abs_pnl:
            best_abs_pnl = abs_pnl
            best_pos = {
                "asset": asset,
                "direction": direction,
                "delta_pnl": delta_pnl,
                "whale_entry_px": whale_entry_px,  # v3.2: track for entry discipline
            }

    if not best_pos or best_abs_pnl < hot_cfg.get("minPositionPnl", 100_000):
        return None

    # Concentration = top position PnL as fraction of total
    concentration = (best_abs_pnl / total_abs_pnl) if total_abs_pnl > 0 else 0
    if concentration < hot_cfg.get("minConcentration", 0.35):
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

    # ─────────────────────────────────────────────────────────────
    # v3.2 ENTRY DISCIPLINE: don't buy the top of the whale's bag
    # ─────────────────────────────────────────────────────────────
    # Raptor's 2026-04-16 self-diagnosis: "Elite traders often sit in
    # underwater or flat positions for days, averaging down. Following
    # them blindly without knowing their entry price means we assume all
    # their risk without their padding. We are buying the top of their bags."
    #
    # Fix: compare current market price to whale's entryPx.
    # - If current price is WORSE than whale's entry (asset moved against
    #   their position post-entry), we get a BETTER fill than the whale did
    #   → take the trade.
    # - If current price has run 20%+ FROM whale's entry in their favor,
    #   we'd be buying their top → skip.
    MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY = 20.0
    whale_entry_px = best_pos.get("whale_entry_px", 0)
    if whale_entry_px > 0:
        # Get current price — prefer market_get_prices (fresh) but fall
        # back to any price hint in sm_map if the MCP call fails.
        current_px = 0
        try:
            px_raw = cfg.mcporter_call("market_get_prices",
                                        assets=[best_pos["asset"]])
            if px_raw:
                data = px_raw.get("data", px_raw)
                if isinstance(data, dict):
                    current_px = safe_float(data.get(best_pos["asset"], 0))
        except Exception:
            current_px = 0

        if current_px > 0:
            if best_pos["direction"] == "LONG":
                # Whale is long. We want to enter at entryPx or better
                # (lower). If current > entry * 1.20, we're late.
                run_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
                if run_pct > MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY:
                    return None  # Buying their top
            else:  # SHORT
                # Whale is short. We want to enter at entryPx or better
                # (higher). If current < entry * 0.80, we're late.
                run_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
                if run_pct > MAX_PRICE_RUN_PCT_FROM_WHALE_ENTRY:
                    return None  # Shorting their bottom (they've already made the money)

    # Scoring
    score = 0
    reasons = []

    # TCS consistency (already filtered to ELITE/RELIABLE upstream)
    tcs = trader["tcs_label"]
    if tcs == "ELITE":
        score += 3
        reasons.append(f"ELITE_tcs{trader['tcs_value']:.0f}")
    elif tcs == "RELIABLE":
        score += 2
        reasons.append(f"RELIABLE_tcs{trader['tcs_value']:.0f}")
    else:
        return None

    # Trader's weekly unrealized PnL magnitude
    trader_delta = trader["unrealized_pnl"]
    if trader_delta >= hot_cfg.get("tier3Threshold", 3_000_000):
        score += 3
        reasons.append(f"TIER3_${trader_delta/1e6:.1f}M")
    elif trader_delta >= hot_cfg.get("tier2Threshold", 1_500_000):
        score += 2
        reasons.append(f"TIER2_${trader_delta/1e6:.1f}M")
    else:
        score += 1
        reasons.append(f"TIER1_${trader_delta/1e6:.1f}M")

    # ROI bonus — very high ROI = high-conviction trader on a real streak
    if trader["roi"] >= 50:
        score += 1
        reasons.append(f"ROI_{trader['roi']:.0f}%")

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

    # v3.2: ENTRY_DISCIPLINE bonus — reward trades where we're getting
    # a better fill than the whale (their position is underwater post-entry).
    if whale_entry_px > 0 and current_px > 0:
        if best_pos["direction"] == "LONG":
            edge_pct = ((whale_entry_px - current_px) / whale_entry_px) * 100
        else:
            edge_pct = ((current_px - whale_entry_px) / whale_entry_px) * 100
        if edge_pct >= 5:
            score += 2
            reasons.append(f"BETTER_THAN_WHALE_+{edge_pct:.1f}%")
        elif edge_pct >= 2:
            score += 1
            reasons.append(f"EDGE_VS_WHALE_+{edge_pct:.1f}%")
        elif edge_pct < -10:
            # We're far worse than whale's entry but still under the 20% skip gate
            reasons.append(f"LATE_VS_WHALE_{edge_pct:.1f}%")

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
        "whaleEntryPx": whale_entry_px if whale_entry_px > 0 else None,  # v3.2
        "currentPx": current_px if (whale_entry_px > 0 and current_px > 0) else None,  # v3.2
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════

def get_safe_leverage(wallet, asset, requested_leverage):
    """Query Hyperliquid's max leverage for this asset and clamp.

    Fleet-wide leverage safety fix (batch 4). Raptor's quality pool can
    surface low-cap assets whose Hyperliquid max is below the scanner's
    requested tier. Clamping prevents CREATE_INVALID_LEVERAGE rejections
    and the phantom ENTRY logs they cause.
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


def execute_entry(wallet, signal, account_value, entry_cfg, leverage_cfg):
    """Self-execute the entry via Senpi MCP create_position.
    Matches Wolverine/Phoenix pattern."""
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    high_conv_pct = entry_cfg.get("marginPctHighConv", 0.35)
    margin_pct = high_conv_pct if signal["score"] >= 10 else base_margin_pct
    margin = round(account_value * margin_pct, 2)

    requested_leverage = get_leverage_for_score(
        signal["score"],
        leverage_cfg.get("tiers", []),
        leverage_cfg.get("default", 7),
    )
    # Fleet-wide batch-4 leverage safety: clamp to asset max.
    leverage = get_safe_leverage(wallet, signal["asset"], requested_leverage)

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
    # Fleet-wide batch-4 inner-order success validation.
    if success:
        data = result.get("data", {}) if isinstance(result, dict) else {}
        if isinstance(data, dict):
            orders_result = data.get("orders", data.get("results", []))
            if isinstance(orders_result, list) and orders_result:
                inner = orders_result[0]
                if isinstance(inner, dict) and inner.get("success") is False:
                    err = inner.get("error", "inner order failed")
                    return False, {"error": f"INNER_FAILURE: {err}"}, margin, leverage
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
            "_raptor_version": "3.2",
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

    # ── PHASE 1 (v3.1): Fetch quality winners directly ──
    # Quality-first architecture: pull ELITE/RELIABLE traders with winning
    # positions this week, rather than starting from 4h momentum (which is
    # dominated by CHOPPY degens and almost never contains quality traders).
    quality_traders = fetch_quality_hot_traders(
        limit=hot_cfg.get("qualityPoolSize", 20),
        min_delta_usd=hot_cfg.get("minDeltaPnl", 500_000),
    )

    if not quality_traders:
        cfg.output({
            "status": "ok", "heartbeat": "NO_REPLY",
            "note": f"No ELITE/RELIABLE traders above "
                    f"${hot_cfg.get('minDeltaPnl', 500_000)/1e6:.1f}M weekly unrealized",
        })
        return

    # ── PHASE 2: SM map (1 call) ──
    sm_map = fetch_sm_map()
    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY", "note": "No SM data"})
        return

    # ── PHASE 3: Per-trader position fetch + signal build ──
    seen_events = load_seen_events()
    held_coins = {p["coin"].upper() for p in positions}
    dedupe_hours = dedupe_cfg.get("eventDedupeHours", 4)
    cooldown_minutes = dedupe_cfg.get("perAssetCooldownMinutes", 120)

    candidates = []
    scan_limit = min(len(quality_traders), hot_cfg.get("positionsFetchLimit", 10))
    for t in quality_traders[:scan_limit]:
        positions_data = fetch_trader_positions(t["address"])
        if not positions_data:
            continue

        signal = build_signal(t, positions_data, sm_map, hot_cfg, sm_cfg)
        if not signal:
            continue

        if is_event_seen(seen_events, t["address"], signal["asset"], dedupe_hours):
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
            "_raptor_version": "3.2",
        })
    else:
        error = result.get("error", "unknown") if result else "mcporter_call returned None"
        cfg.output({
            "status": "ok",
            "action": "ENTRY_FAILED",
            "signal": best,
            "error": error,
            "_raptor_version": "3.2",
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
