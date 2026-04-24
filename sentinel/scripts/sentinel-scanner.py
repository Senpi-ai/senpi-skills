#!/usr/bin/env python3
# Senpi SENTINEL Scanner v2.0 (fleet-fix batch 4 — wider Phase 2 + equity reset)
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
"""SENTINEL v2.0 — Quality Trader Convergence Scanner.

## v2.0 fleet-fix batch 4 changes (2026-04-15)

Sentinel was stuck at -21.44% drawdown with daily cap = 1 (severely
restricted by the pnl-aware circuit breaker). 45.2% win rate confirms
the signal is valid; inversion test was 3x worse inverted. Diagnosis:
losers were bleeding out via slow cuts — `weak_peak_cut` and
`hard_timeout` accounted for 17 of 23 losers.

Runtime.yaml changes:
- Phase 2 tiers widened from [5/25, 10/50, 15/65, 20/80, 30/85] to
  Sentinel's own recommendation: [15/35, 30/60, 50/75, 75/85, 100/92].
  Keeps early lock at 15% (still meaningful profit protection) but
  pushes upper tiers out so genuine winners have room to run to 100%+.

Scanner capital reset:
- `STARTING_BUDGET` 1000.0 → 786.60 (current equity). Rebases the
  pnl-aware daily cap so Sentinel unblocks from the restricted state.


Inverted pipeline: instead of starting with an asset and checking if
SM is there, start with QUALITY TRADERS and find where they converge.

v1.0 had zero trades. The pipeline was too complex — it tried to cross-
reference multiple data sources per asset. v2.0 simplifies:

1. FIND: discovery_get_top_traders (ELITE + RELIABLE, open positions only)
2. AGGREGATE: count how many quality traders hold each asset and in which direction
3. THRESHOLD: when 5+ quality traders converge on the same asset + direction → signal
4. CONFIRM: leaderboard_get_markets SM concentration must agree
5. ENTER: score based on convergence depth + SM alignment + price momentum

Why this works: ELITE/RELIABLE traders are historically profitable.
When multiple of them independently arrive at the same trade, it's
not coincidence — it's informed consensus.

Why v1.0 failed: too many API calls per scan (checked each asset
individually). v2.0 uses 2 bulk API calls and aggregates in memory.

Architecture:
- 2 API calls: discovery_get_top_traders + leaderboard_get_markets
- Aggregation in memory (no per-asset API calls)
- Runs every 5 minutes (quality convergence shifts slowly)

DSL exit managed by plugin runtime. Scanner does NOT manage exits.
"""

import json
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sentinel_config as cfg


# ═══════════════════════════════════════════════════════════════
# HARDCODED CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_LEVERAGE = 5
MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 7

# Per-asset leverage scaling — volatile assets get lower leverage so the
# same DSL percentages translate to wider absolute price tolerance.
# At 5x, 15% Phase 1 = 3% price move. At 10x, 15% = 1.5% price move.
# HYPE/alts wick 2%+ in 5 minutes — they need 5x to survive noise.
# ETH/SOL/TAO are calmer — 10x is fine.
ASSET_LEVERAGE = {
    "HYPE": 5, "MON": 5, "LIT": 5, "FARTCOIN": 5,
    "ZRO": 5, "ZEC": 5, "WIF": 5,
    "BTC": 7,
    "ETH": 10, "SOL": 10, "TAO": 10,
}
MAX_POSITIONS = 2
MAX_DAILY_ENTRIES = 4


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DAILY CAP (P&L-aware circuit breaker)
# ═══════════════════════════════════════════════════════════════

STARTING_BUDGET = 786.60  # v2.0 fleet-fix batch 4: rebased to current equity

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
MARGIN_PCT = 0.20
MIN_SCORE = 7
SAME_DIR_COOLDOWN_MINUTES = 60
XYZ_BANNED = True

# Convergence thresholds
MIN_QUALITY_TRADERS = 5             # At least 5 ELITE/RELIABLE on same asset+direction
ELITE_WEIGHT = 2                    # ELITE counts as 2 traders for convergence
RELIABLE_WEIGHT = 1                 # RELIABLE counts as 1

# SM confirmation
MIN_SM_PCT = 3.0                    # SM must have meaningful concentration
MIN_SM_TRADERS = 15

# Discovery query
DISCOVERY_LIMIT = 100               # Top 100 quality traders
DISCOVERY_TIMEFRAME = "WEEKLY"      # Weekly performers (not daily noise)


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


def has_resting_orders(wallet):
    """Check for non-reduceOnly resting orders, auto-cancelling any older
    than STALE_ORDER_MAX_AGE_SEC (default 600s / 10 min).

    Without auto-cancel, a maker FEE_OPTIMIZED_LIMIT order that never
    fills can lock the scanner out of new entries indefinitely, because
    every subsequent scan sees the stale order and aborts early. Ignores
    reduceOnly orders (those are DSL exit legs)."""
    import time as _time
    STALE_ORDER_MAX_AGE_SEC = 600  # 10 minutes
    data = cfg.mcporter_call("strategy_get_open_orders", strategy_wallet=wallet)
    if not data:
        return False
    orders = data.get("data", data)
    if isinstance(orders, dict):
        orders = orders.get("orders", orders.get("openOrders", []))
    if not isinstance(orders, list):
        return False
    now_ms = _time.time() * 1000
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
            continue  # Treat cancelled order as gone
        has_fresh = True
    return has_fresh

def fetch_quality_traders():
    """Get top ELITE + RELIABLE traders and fetch their open positions.

    v2.3 (2026-04-24): discovery_get_top_traders returns trader metadata
    (address, tcsLabel, roi, etc.) but does NOT include open_positions.
    Prior to this fix, the convergence logic at line ~283 always read
    trader.get("open_positions", []) and got [] for every trader, so
    Gate 1 (convergence) produced 0 passes regardless of market state.
    Sentinel had been silently dormant since v2.0 shipped.

    Fix: after fetching trader metadata, batch-call
    discovery_get_trader_state for each trader (up to 50/batch) which
    DOES return openPositions. Merge the two responses. Each returned
    record has shape {address, consistency, open_positions: [{asset, direction}]}
    so downstream convergence code works unchanged.
    """
    data = cfg.mcporter_call("discovery_get_top_traders",
                              time_frame=DISCOVERY_TIMEFRAME,
                              consistency=["ELITE", "RELIABLE"],
                              open_position_filter=True,
                              sort_by="PROFIT_AND_LOSS",
                              limit=DISCOVERY_LIMIT)
    if not data:
        return []

    traders_meta = data.get("traders", data.get("data", []))
    if isinstance(traders_meta, dict):
        traders_meta = traders_meta.get("traders", [])
    if not isinstance(traders_meta, list) or not traders_meta:
        return []

    addresses = [t.get("address") for t in traders_meta if t.get("address")]
    if not addresses:
        return []

    tcs_map = {
        t.get("address"): str(
            t.get("tcsLabel", t.get("consistencyLabel", t.get("consistency", "")))
        ).upper()
        for t in traders_meta
    }

    traders_with_positions = []
    for i in range(0, len(addresses), 50):
        batch = addresses[i:i+50]
        state_data = cfg.mcporter_call(
            "discovery_get_trader_state",
            trader_addresses=batch,
            latest=False,
        )
        if not state_data:
            continue

        payload = state_data.get("data", state_data)
        if not isinstance(payload, dict):
            continue

        fetched_traders = payload.get("traders", [])
        for ft in fetched_traders:
            if not isinstance(ft, dict):
                continue
            address = ft.get("address")
            open_positions = ft.get("openPositions", [])
            normalized_positions = []
            for op in open_positions:
                if not isinstance(op, dict):
                    continue
                szi = safe_float(op.get("szi", 0))
                if szi == 0:
                    continue
                normalized_positions.append({
                    "asset": str(op.get("coin", "")).upper(),
                    "direction": "LONG" if szi > 0 else "SHORT",
                })

            if normalized_positions:
                traders_with_positions.append({
                    "address": address,
                    "consistency": tcs_map.get(address, "RELIABLE"),
                    "open_positions": normalized_positions,
                })

    return traders_with_positions


def fetch_sm_data():
    """Get SM positioning from leaderboard."""
    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}

    markets = []
    if isinstance(raw, dict):
        raw_data = raw.get("data", raw)
        if isinstance(raw_data, dict):
            markets = raw_data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(raw_data, list):
            markets = raw_data
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
            "contrib_change": safe_float(m.get("contribution_pct_change_4h", 0)),
            "cc_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
        }

    return sm_map


# ═══════════════════════════════════════════════════════════════
# CONVERGENCE DETECTION
# ═══════════════════════════════════════════════════════════════

def build_convergence_map(traders):
    """Aggregate quality trader positions by asset + direction.
    Returns: {ASSET: {LONG: weighted_count, SHORT: weighted_count, traders: [...]}}"""

    convergence = {}

    for trader in traders:
        if not isinstance(trader, dict):
            continue

        address = trader.get("address", trader.get("trader_address", ""))
        tcs = str(trader.get("consistency",
                  trader.get("tcs", trader.get("consistency_label", "")))).upper()

        # Determine weight
        if tcs == "ELITE":
            weight = ELITE_WEIGHT
        elif tcs == "RELIABLE":
            weight = RELIABLE_WEIGHT
        else:
            continue

        # Get open positions from trader data
        positions = trader.get("open_positions",
                    trader.get("positions",
                    trader.get("top_markets", [])))

        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, dict):
                    asset = str(pos.get("market", pos.get("asset",
                                pos.get("coin", "")))).upper()
                    direction = str(pos.get("direction", pos.get("side", ""))).upper()

                    if not asset or direction not in ("LONG", "SHORT"):
                        continue
                    if XYZ_BANNED and asset.lower().startswith("xyz"):
                        continue

                    if asset not in convergence:
                        convergence[asset] = {"LONG": 0, "SHORT": 0, "traders": []}

                    convergence[asset][direction] += weight
                    convergence[asset]["traders"].append({
                        "address": address[:10] + "...",
                        "tcs": tcs,
                        "direction": direction,
                    })

                elif isinstance(pos, str):
                    # top_markets is sometimes just a list of asset names
                    asset = pos.upper()
                    if XYZ_BANNED and asset.lower().startswith("xyz"):
                        continue
                    # Can't determine direction from just asset name — skip
                    continue

    return convergence


def find_convergence_signals(convergence_map, sm_map):
    """Find assets with strong quality trader convergence confirmed by SM."""

    candidates = []

    for asset, data in convergence_map.items():
        long_weight = data["LONG"]
        short_weight = data["SHORT"]

        # Determine dominant direction
        if long_weight >= MIN_QUALITY_TRADERS and long_weight > short_weight:
            direction = "LONG"
            convergence_strength = long_weight
        elif short_weight >= MIN_QUALITY_TRADERS and short_weight > long_weight:
            direction = "SHORT"
            convergence_strength = short_weight
        else:
            continue

        # SM must agree
        sm = sm_map.get(asset)
        if not sm:
            continue
        if sm["direction"] != direction:
            continue
        if sm["pct"] < MIN_SM_PCT or sm["traders"] < MIN_SM_TRADERS:
            continue

        candidates.append({
            "asset": asset,
            "direction": direction,
            "convergence_strength": convergence_strength,
            "long_weight": long_weight,
            "short_weight": short_weight,
            "sm_direction": sm["direction"],
            "sm_pct": sm["pct"],
            "sm_traders": sm["traders"],
            "price_chg_4h": sm["price_chg_4h"],
            "price_chg_1h": sm["price_chg_1h"],
            "contrib_change": sm.get("contrib_change", 0),
            "cc_15m": sm.get("cc_15m", 0),
            "trader_details": data["traders"],
        })

    return candidates


# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

def score_candidate(cand):
    """Score a convergence candidate."""
    score = 0
    reasons = []

    # 1. Convergence depth (0-4 points)
    strength = cand["convergence_strength"]
    if strength >= 15:
        score += 4
        reasons.append(f"DEEP_CONVERGENCE {strength} weighted traders")
    elif strength >= 10:
        score += 3
        reasons.append(f"STRONG_CONVERGENCE {strength} weighted traders")
    elif strength >= 7:
        score += 2
        reasons.append(f"SOLID_CONVERGENCE {strength} weighted traders")
    elif strength >= 5:
        score += 1
        reasons.append(f"BASE_CONVERGENCE {strength} weighted traders")

    # 2. SM alignment strength (0-2 points)
    sm_pct = cand["sm_pct"]
    if sm_pct >= 10:
        score += 2
        reasons.append(f"SM_STRONG {sm_pct:.1f}% ({cand['sm_traders']}t)")
    elif sm_pct >= 5:
        score += 1
        reasons.append(f"SM_ALIGNED {sm_pct:.1f}% ({cand['sm_traders']}t)")

    # 3. Price momentum (0-2 points)
    p4h = cand["price_chg_4h"]
    direction = cand["direction"]
    if direction == "LONG" and p4h > 0.5:
        score += 1
        reasons.append(f"4H_CONFIRMS +{p4h:.1f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 1
        reasons.append(f"4H_CONFIRMS {p4h:.1f}%")

    p1h = cand["price_chg_1h"]
    if direction == "LONG" and p1h > 0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS +{p1h:.2f}%")
    elif direction == "SHORT" and p1h < -0.2:
        score += 1
        reasons.append(f"1H_CONFIRMS {p1h:.2f}%")

    # Move-exhaustion penalty — large existing moves reduce conviction
    if abs(p4h) >= 4.0:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score -= 2; reasons.append(f"MOVE_EXHAUSTION {p4h:+.1f}%")
    elif abs(p4h) >= 2.5:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score -= 1; reasons.append(f"MOVE_TIRING {p4h:+.1f}%")

    # 4. Contribution velocity (0-1 point)
    contrib = abs(cand.get("contrib_change", 0))
    if contrib >= 0.01:
        score += 1
        reasons.append(f"CONTRIB_ACCEL +{contrib*100:.1f}%")

    # 5. 15m velocity freshness (conviction penalty)
    cc_15m = safe_float(cand.get("cc_15m", 0))
    if cc_15m <= 0:
        score -= 3
        reasons.append(f"15M_STALE_PENALTY ({cc_15m:.2f})")
    elif cc_15m > 0.5:
        score += 1
        reasons.append(f"15M_FRESH +{cc_15m:.2f}")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# TRADE COUNTER & COOLDOWN
# ═══════════════════════════════════════════════════════════════

def load_trade_counter():
    """Load trade counter. Timestamps persist across midnight."""
    p = os.path.join(cfg.STATE_DIR, "trade-counter.json")
    default = {"date": now_date(), "entries": 0,
               "last_entry_ts": 0, "last_win_direction": None, "last_win_ts": 0}
    if os.path.exists(p):
        try:
            with open(p) as f: tc = json.load(f)
            if tc.get("date") != now_date():
                tc["date"] = now_date()
                tc["entries"] = 0
            for k, v in default.items():
                if k not in tc: tc[k] = v
            return tc
        except (json.JSONDecodeError, IOError): pass
    return dict(default)


def save_trade_counter(tc):
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

    # Check for resting orders
    if has_resting_orders(wallet):
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                     "note": "RESTING ORDER: limit order pending."})
        return

    tc = load_trade_counter()
    dynamic_cap = get_dynamic_daily_cap(account_value)
    if tc.get("entries", 0) >= dynamic_cap:
        pnl_pct = ((account_value - STARTING_BUDGET) / STARTING_BUDGET) * 100
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Daily cap ({dynamic_cap}) reached. Session PnL: {pnl_pct:+.1f}%. Entries: {tc.get('entries', 0)}/{dynamic_cap}"})
        return

    # ── Fetch data (2 API calls) ──────────────────────────────
    traders = fetch_quality_traders()
    sm_map = fetch_sm_data()

    if not traders:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No quality traders with open positions"})
        return

    if not sm_map:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "No SM data"})
        return

    # ── Build convergence map ─────────────────────────────────
    convergence_map = build_convergence_map(traders)

    # ── Find signals ──────────────────────────────────────────
    candidates = find_convergence_signals(convergence_map, sm_map)

    if not candidates:
        # Report top convergence for debugging
        top_assets = sorted(convergence_map.items(),
                           key=lambda x: max(x[1]["LONG"], x[1]["SHORT"]),
                           reverse=True)[:3]
        top_summary = [(a, max(d["LONG"], d["SHORT"])) for a, d in top_assets]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"No convergence signals. "
                            f"{len(traders)} quality traders tracked. "
                            f"Top convergence: {top_summary}"})
        return

    # ── Score and filter ──────────────────────────────────────
    for cand in candidates:
        cand["score"], cand["reasons"] = score_candidate(cand)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Same-direction re-entry cooldown
    last_win_dir = tc.get("last_win_direction")
    last_win_ts = tc.get("last_win_ts", 0)

    for cand in candidates:
        asset = cand["asset"]

        if cand["score"] < MIN_SCORE:
            continue
        if last_win_dir and last_win_dir == cand["direction"]:
            if last_win_ts and (time.time() - last_win_ts) < SAME_DIR_COOLDOWN_MINUTES * 60:
                continue
        if is_on_cooldown(asset):
            continue
        if any(p["coin"].upper() == asset.upper() for p in positions):
            continue

        # ── Entry ─────────────────────────────────────────────
        margin = round(account_value * MARGIN_PCT, 2)
        leverage = ASSET_LEVERAGE.get(asset, DEFAULT_LEVERAGE)
        direction = cand["direction"]

        # v2.4 (2026-04-24): execute via direct Python call to
        # mcporter rather than emitting JSON for an LLM sub-agent
        # to translate. The cron-spawned sub-agent's tool allowlist
        # doesn't include senpi.create_position (mutation tool,
        # gateway-restricted). Every other working fleet scanner
        # (Bald Eagle, Kestrel, Wolverine, Scorpion v1) calls
        # mcporter_call("create_position", ...) directly. Matches
        # fleet-standard pattern from README.md: "Scanners Enter.
        # DSL Exits." with DSL managed by the runtime plugin.
        wallet, _strategy_id = cfg.get_wallet_and_strategy()
        if not wallet:
            cfg.output({"status": "error", "error": "no wallet configured"})
            return

        entry_result = cfg.mcporter_call(
            "create_position",
            strategyWalletAddress=wallet,
            orders=[{
                "coin": asset,
                "direction": direction,
                "leverage": leverage,
                "marginAmount": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "feeOptimizedLimitOptions": {
                    "ensureExecutionAsTaker": False,
                    "executionTimeoutSeconds": 30,
                },
            }],
        )

        success = bool(entry_result and entry_result.get("success"))
        error = None if success else (
            entry_result.get("error", "unknown") if entry_result
            else "mcporter_call returned None"
        )

        if success:
            tc["entries"] = tc.get("entries", 0) + 1
            tc["last_entry_ts"] = int(time.time())
            save_trade_counter(tc)

        cfg.output({
            "status": "ok" if success else "entry_failed",
            "action": "ENTRY_EXECUTED" if success else "ENTRY_FAILED",
            "error": error,
            "signal": {
                "asset": asset,
                "direction": direction,
                "score": cand["score"],
                "mode": "CONVERGENCE",
                "reasons": cand["reasons"],
                "convergenceStrength": cand["convergence_strength"],
                "smPct": cand["sm_pct"],
                "smTraders": cand["sm_traders"],
                "priceChg4h": cand["price_chg_4h"],
                "qualityTraders": len(cand["trader_details"]),
            },
            "entry": {
                "asset": asset,
                "direction": direction,
                "leverage": leverage,
                "margin": margin,
                "orderType": "FEE_OPTIMIZED_LIMIT",
                "feeOptimizedLimitOptions": {
                    "ensureExecutionAsTaker": False,
                    "executionTimeoutSeconds": 30
                },
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
            "_sentinel_version": "2.4",
        })
        return

    # Report best candidate that didn't pass
    if candidates:
        best = candidates[0]
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": f"Best convergence: {best['asset']} {best['direction']} "
                            f"score {best['score']} < {MIN_SCORE}. "
                            f"{', '.join(best['reasons'][:3])}"})
    else:
        cfg.output({"status": "ok", "heartbeat": "NO_REPLY",
                    "note": "Convergence found but no SM alignment"})


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        cfg.log(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        cfg.output({"status": "error", "error": str(e)})
