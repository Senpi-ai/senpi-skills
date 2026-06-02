#!/usr/bin/env python3
# Senpi BALD EAGLE Producer v5.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BALD EAGLE v5.0.0 Producer — XYZ Contrarian Fader, helpers-native.

v5.0.0 (2026-05-12): plumbing migration from v4.1. NO thesis change.
All v4.1 thesis preserved verbatim:
  - 6 XYZ macro assets: CL, BRENTOIL, GOLD, SILVER, SP500, XYZ100
  - Contrarian flip: fade SM consensus direction
  - Conviction-scaled leverage (5x/7x by score; 5-7x range; XYZ caps
    lower than crypto)
  - SM concentration + trader-depth + contribution-velocity scoring
  - Move-exhaustion bonus tuned for XYZ (2%/1% bands vs crypto 3%/5%)
  - 1h momentum, 4h trend, volume trend, funding alignment scoring
  - Spread gate (>0.1% MAX_SPREAD_PCT) — preserved as producer-side
    pre-filter to avoid pushing signals destined to slip through AMM
  - MIN_SCORE 8
  - has_resting_orders 600s stale-cancel auto-purge from v4.1

Six-layer plumbing flip:
  1. MCP calls — cfg.mcporter_call() shim routes through SenpiClient.
  2. Signal emit — scanner-side create_position dropped; producer pushes
     signals via cfg._wrapper_client.push_signal() and runtime owns
     execution + FEE_OPTIMIZED_LIMIT entries.
  3. Daily cap / drawdown — Python state dropped; runtime.guard_rails
     owns max_entries_per_day, drawdown_halt_pct, cooldowns.
  4. Reentrancy — fcntl flock dropped; producer_daemon owns lock.
  5. Tick scheduling — openclaw cron dropped; producer_daemon (5min).
  6. has_resting_orders preserved (XYZ ALO stuck-order risk is real).
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eagle_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "5.0.1"
SCANNER_NAME = "eagle_signals"
SIGNAL_TYPE = "EAGLE_XYZ_CONTRARIAN"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v4.1
# ═══════════════════════════════════════════════════════════════

# Only trade XYZ assets with deep SM signal + real liquidity:
# CL=181 traders, BRENTOIL=166, SP500=35, GOLD=26, SILVER=42, XYZ100=50
TRACKED_XYZ = ["CL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100"]
ALLOWED_ASSETS = set(TRACKED_XYZ)

# Conviction-scaled leverage (XYZ macro caps lower than crypto agents)
LEVERAGE_TIERS = [
    {"min_score": 10, "leverage": 7},
    {"min_score": 8,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 7
MIN_LEVERAGE = 3

MARGIN_PCT = 0.40            # 40% of account equity per trade
MIN_SCORE = 8                # Contrarian threshold (lowered v4.0)
MAX_SPREAD_PCT = 0.001       # 0.1% max book spread — execution-quality
                             # hard gate; AMM spread = guaranteed slippage

# SM thresholds — XYZ has fewer SM traders than crypto
MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 5

# v4.1 resting-order guard
STALE_ORDER_MAX_AGE_SEC = 600


def _resolve_wallet():
    env_val = (os.environ.get("EAGLE_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score):
    """Conviction-scaled leverage. Higher score = higher leverage."""
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


# ═══════════════════════════════════════════════════════════════
# RESTING ORDER GUARD (v4.1 hot-patch preserved verbatim)
# ═══════════════════════════════════════════════════════════════
#
# 2026-05-05 audit found a 4-day-old resting xyz:XYZ100 BUY ALO order
# (oid 406936191678) locking ~$335 collateral. Bald Eagle had NO
# resting-order check — every other v1 agent had it. Without this,
# an entry ALO that never fills hangs indefinitely, blocks
# withdrawable capital, and creates duplicate-entry risk if the
# producer can't see the stale order.
#
# Auto-cancels non-reduceOnly orders older than STALE_ORDER_MAX_AGE_SEC.
# Returns True if any FRESH non-reduceOnly order remains; caller then
# aborts the tick to avoid stacking.

def has_resting_orders(wallet):
    fresh_seen = False
    for dex in ("", "xyz"):
        kwargs = {"strategy_wallet": wallet}
        if dex:
            kwargs["dex"] = dex
        try:
            data = cfg._wrapper_client.mcp_call("strategy_get_open_orders", **kwargs)
        except Exception:
            continue
        if not data:
            continue
        orders = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(orders, dict):
            orders = orders.get("orders", orders.get("openOrders", []))
        if not isinstance(orders, list):
            continue
        now_ms = time.time() * 1000
        max_age_ms = STALE_ORDER_MAX_AGE_SEC * 1000
        for o in orders:
            if not isinstance(o, dict):
                continue
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
                    cancel_kwargs = {
                        "strategyWalletAddress": wallet,
                        "orderId": int(oid),
                    }
                    if dex:
                        cancel_kwargs["dex"] = dex
                    try:
                        cfg._wrapper_client.mcp_call("cancel_order", **cancel_kwargs)
                    except Exception:
                        pass
                continue
            fresh_seen = True
    return fresh_seen


# ═══════════════════════════════════════════════════════════════
# SPREAD GATE
# ═══════════════════════════════════════════════════════════════

def check_spread(asset_token):
    """Live order-book spread for an XYZ asset. Returns (spread_pct, bid, ask)
    or (None, 0, 0). Requires dex='xyz' — XYZ orderbook is silent without it.
    """
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=f"xyz:{asset_token}",
        candle_intervals=[],
        include_funding=False,
        include_order_book=True,
        dex="xyz",
    )
    if not data:
        return None, 0, 0

    ad = data.get("data", data) if isinstance(data, dict) else None
    if not isinstance(ad, dict):
        return None, 0, 0

    ob = ad.get("order_book", ad.get("orderBook", {}))
    if not isinstance(ob, dict):
        return None, 0, 0

    # API returns order_book.levels = [bids_array, asks_array]; each
    # level is a dict {px, sz, n}. Verified vs live xyz:BRENTOIL/GOLD/CL.
    levels = ob.get("levels", [])
    if not isinstance(levels, list) or len(levels) < 2:
        return None, 0, 0
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        return None, 0, 0

    def _lvl_px(lvl):
        if isinstance(lvl, dict):
            return safe_float(lvl.get("px", lvl.get("price", 0)))
        if isinstance(lvl, list) and lvl:
            return safe_float(lvl[0])
        return 0

    best_bid = _lvl_px(bids[0])
    best_ask = _lvl_px(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        return None, 0, 0

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid
    return spread_pct, best_bid, best_ask


# ═══════════════════════════════════════════════════════════════
# CANDLE DATA FOR SCORING
# ═══════════════════════════════════════════════════════════════

def fetch_candle_data(asset_token):
    """1h + 4h candles + funding for technical scoring. Requires dex='xyz'."""
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=f"xyz:{asset_token}",
        candle_intervals=["1h", "4h"],
        include_funding=True,
        include_order_book=False,
        dex="xyz",
    )
    if not data:
        return None
    if isinstance(data, dict) and "success" in data and not data.get("success"):
        return None
    return data.get("data", data) if isinstance(data, dict) else None


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_trend(candles, lookback=6):
    if len(candles) < lookback + 2:
        return 0
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0))))
            for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# SM SCANNING (XYZ-only, focused assets)
# ═══════════════════════════════════════════════════════════════

def scan_xyz_sm():
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw:
        return []

    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return []

    token_best = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if dex != "xyz":
            continue
        if token not in ALLOWED_ASSETS:
            continue

        pct = safe_float(m.get("pct_of_top_traders_gain", 0))
        traders = int(m.get("trader_count", 0))
        direction = str(m.get("direction", "")).upper()
        if direction not in ("LONG", "SHORT"):
            continue

        entry = {
            "token": token,
            "direction": direction,
            "pct": pct,
            "traders": traders,
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_chg_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "contrib_chg_4h": safe_float(m.get("contribution_pct_change_4h", 0)),
        }

        if token not in token_best or pct > token_best[token]["pct"]:
            token_best[token] = entry

    candidates = sorted(token_best.values(), key=lambda x: x["pct"], reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════════
# CONVICTION SCORING (all contributors, no hard gates except spread)
# ═══════════════════════════════════════════════════════════════

def score_candidate(cand, candle_data):
    """Score an XYZ SM candidate. Preserved verbatim from v4.1."""
    score = 0
    reasons = []
    direction = cand["direction"]

    # ── SM concentration (0-4 pts) ──
    pct = cand["pct"]
    if pct >= 20:
        score += 4
        reasons.append(f"DOMINANT_SM {pct:.1f}%")
    elif pct >= 10:
        score += 3
        reasons.append(f"HIGH_SM {pct:.1f}%")
    elif pct >= 5:
        score += 2
        reasons.append(f"SOLID_SM {pct:.1f}%")
    elif pct >= 3:
        score += 1
        reasons.append(f"BASE_SM {pct:.1f}%")

    # ── Trader depth (0-2 pts) ──
    traders = cand["traders"]
    if traders >= 100:
        score += 2
        reasons.append(f"DEEP_SM ({traders}t)")
    elif traders >= 30:
        score += 1
        reasons.append(f"SM_ACTIVE ({traders}t)")

    # ── SM contribution velocity (0-3 pts) ──
    c1h = cand["contrib_chg_1h"]
    c4h = cand["contrib_chg_4h"]
    if c1h > 5:
        score += 2
        reasons.append(f"CONTRIB_SURGE_1H +{c1h:.1f}%")
    elif c1h > 2:
        score += 1
        reasons.append(f"CONTRIB_RISING_1H +{c1h:.1f}%")
    if c4h > 3 and c1h > 0:
        score += 1
        reasons.append(f"CONTRIB_SUSTAINED_4H +{c4h:.1f}%")

    # ── 4H price alignment (±2 pts) ──
    p4h = cand["price_chg_4h"]
    if direction == "LONG" and p4h > 0.5:
        score += 2
        reasons.append(f"4H_ALIGNED +{p4h:.1f}%")
    elif direction == "SHORT" and p4h < -0.5:
        score += 2
        reasons.append(f"4H_ALIGNED {p4h:.1f}%")
    elif direction == "LONG" and p4h > 0:
        score += 1
        reasons.append(f"4H_POSITIVE +{p4h:.2f}%")
    elif direction == "SHORT" and p4h < 0:
        score += 1
        reasons.append(f"4H_POSITIVE {p4h:.2f}%")
    elif direction == "LONG" and p4h < -1:
        score -= 1
        reasons.append(f"4H_OPPOSING {p4h:.1f}%")
    elif direction == "SHORT" and p4h > 1:
        score -= 1
        reasons.append(f"4H_OPPOSING +{p4h:.1f}%")

    # ── MOVE EXHAUSTION BONUS (XYZ-tuned: 1-2% vs crypto 3-5%) ──
    if abs(p4h) >= 2.0:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score += 2
            reasons.append(f"DEEP_EXHAUSTION {p4h:+.1f}%")
    elif abs(p4h) >= 1.0:
        if (direction == "LONG" and p4h > 0) or (direction == "SHORT" and p4h < 0):
            score += 1
            reasons.append(f"EXHAUSTION {p4h:+.1f}%")

    # ── Technical scoring from candles ──
    if candle_data:
        candles_1h = candle_data.get("candles", {}).get("1h", [])
        candles_4h = candle_data.get("candles", {}).get("4h", [])

        # 4H trend structure (0-2 pts)
        if len(candles_4h) >= 6:
            trend_4h, strength = trend_structure(candles_4h)
            if (direction == "LONG" and trend_4h == "BULLISH") or \
               (direction == "SHORT" and trend_4h == "BEARISH"):
                score += 2
                reasons.append(f"4H_TREND_{trend_4h} {strength:.0%}")
            elif trend_4h != "NEUTRAL" and \
                 ((direction == "LONG" and trend_4h == "BEARISH") or
                  (direction == "SHORT" and trend_4h == "BULLISH")):
                score -= 1
                reasons.append("4H_TREND_OPPOSING")

        # 1H momentum (0-1 pts)
        if len(candles_1h) >= 4:
            mom_1h = price_momentum(candles_1h, 2)
            if (direction == "LONG" and mom_1h > 0.3) or \
               (direction == "SHORT" and mom_1h < -0.3):
                score += 1
                reasons.append(f"1H_MOMENTUM {mom_1h:+.2f}%")

        # Volume trend (0-1 pts)
        if len(candles_1h) >= 8:
            vol = volume_trend(candles_1h)
            if vol > 15:
                score += 1
                reasons.append(f"VOL_RISING +{vol:.0f}%")

        # Funding alignment (0-1 pts)
        asset_ctx = candle_data.get("asset_context", candle_data)
        funding = safe_float(asset_ctx.get("funding", 0)) if isinstance(asset_ctx, dict) else 0
        if (direction == "LONG" and funding < -0.005) or \
           (direction == "SHORT" and funding > 0.005):
            score += 1
            reasons.append(f"FUNDING_ALIGNED {funding:+.4f}")

    return score, reasons


# ═══════════════════════════════════════════════════════════════
# SIGNAL EMIT
# ═══════════════════════════════════════════════════════════════

def push_signal(signal_obj, leverage, margin_usd, held_assets):
    """Emit a contrarian-fade signal to the runtime."""
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False

    asset_full = signal_obj["asset"]  # already "xyz:TOKEN"
    if asset_full.upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": signal_obj["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "smDirection": signal_obj["smDirection"],
        "fadeDirection": signal_obj["direction"],
        "smPct": signal_obj["smPct"],
        "smTraders": signal_obj["smTraders"],
        "priceChg4hPct": signal_obj["priceChg4h"],
        "priceChg1hPct": signal_obj["priceChg1h"],
        "contribChg1hPct": signal_obj["contribChg1h"],
        "contribChg4hPct": signal_obj["contribChg4h"],
        "fundingAnnualizedPct": signal_obj.get("fundingAnnualizedPct", 0),
        "spreadPct": signal_obj.get("spreadPct", 0),
        "reasons": signal_obj["reasons"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=asset_full,
            direction=signal_obj["direction"],
            score=min(signal_obj["score"] / 16.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {asset_full}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {asset_full}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_eagle_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_eagle_producer_version": VERSION})
        return

    # v4.1 resting-order guard — preserved
    if has_resting_orders(STRATEGY_ADDRESS):
        cfg.output({"status": "ok",
                    "note": "RESTING ORDER pending — skip tick to avoid stacking",
                    "_eagle_producer_version": VERSION})
        return

    raw_candidates = scan_xyz_sm()
    if not raw_candidates:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(TRACKED_XYZ),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_SCORE,
            "note": f"No SM signals on {', '.join(sorted(ALLOWED_ASSETS))}",
            "elapsed_sec": round(elapsed, 2),
            "_eagle_producer_version": VERSION,
        })
        return

    held_tokens = {p["coin"].upper().replace("XYZ:", "") for p in positions}

    scored = []
    for cand in raw_candidates:
        token = cand["token"]
        if token in held_tokens:
            continue
        if cfg.is_asset_cooled_down(token, 360):
            continue

        candle_data = fetch_candle_data(token)
        score, reasons = score_candidate(cand, candle_data)
        if score < MIN_SCORE:
            continue

        # Spread hard-gate (execution-quality, not thesis)
        spread_pct, bid, ask = check_spread(token)
        if spread_pct is None:
            reasons.append("SPREAD_UNREADABLE")
            continue
        if spread_pct > MAX_SPREAD_PCT:
            reasons.append(f"SPREAD_WIDE {spread_pct*100:.3f}%")
            continue
        reasons.append(f"SPREAD_OK {spread_pct*100:.3f}%")

        # Contrarian flip — fade SM consensus
        sm_direction = cand["direction"]
        fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"
        reasons.insert(0, f"CONTRARIAN_FLIP xyz:{token} (SM is {sm_direction})")

        funding = 0
        if candle_data:
            ac = candle_data.get("asset_context", candle_data)
            if isinstance(ac, dict):
                funding = safe_float(ac.get("funding", 0))

        scored.append({
            "asset": f"xyz:{token}",
            "direction": fade_direction,
            "score": score,
            "reasons": reasons,
            "smDirection": sm_direction,
            "smPct": cand["pct"],
            "smTraders": cand["traders"],
            "priceChg4h": cand["price_chg_4h"],
            "priceChg1h": cand["price_chg_1h"],
            "contribChg1h": cand["contrib_chg_1h"],
            "contribChg4h": cand["contrib_chg_4h"],
            "fundingAnnualizedPct": funding * 100 * 24 * 365,  # rough APR
            "spreadPct": spread_pct * 100,
        })

    if not scored:
        elapsed = time.time() - run_start
        best_raw = raw_candidates[0] if raw_candidates else None
        note = f"{len(raw_candidates)} XYZ candidates"
        if best_raw:
            note += (f", best raw: {best_raw['token']} "
                     f"{best_raw['direction']} {best_raw['pct']:.1f}% SM")
        note += f" — none passed score {MIN_SCORE} + spread gate"
        cfg.output({
            "status": "ok",
            "scanned": len(TRACKED_XYZ),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_SCORE,
            "note": note,
            "elapsed_sec": round(elapsed, 2),
            "_eagle_producer_version": VERSION,
        })
        return

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]

    leverage = get_leverage_for_score(best["score"])
    margin_usd = round(account_value * MARGIN_PCT, 2)
    pushed = push_signal(best, leverage, margin_usd, held_assets)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(TRACKED_XYZ),
        "candidates": len(scored),
        "signals_pushed": 1 if pushed else 0,
        "min_score": MIN_SCORE,
        "best": {
            "asset": best["asset"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "smDirection": best["smDirection"],
            "smPct": best["smPct"],
            "reasons": best["reasons"][:6],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_eagle_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,                  # 5min — preserved from v4.1
        name=f"eagle-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=600,
    )
