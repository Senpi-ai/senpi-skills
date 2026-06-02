#!/usr/bin/env python3
# Senpi LEMON Producer v2.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""LEMON v2.0.0 Producer — Degen Fader, helpers-native.

v2.0.0 (2026-05-12): plumbing migration from v1.3. NO thesis change.
All v1.3 thesis preserved: counter-trade CHOPPY/DEGEN consensus on
crypto majors + XYZ, 15m fading gate, MACRO_TREND_GATE (crypto only,
|BTC 4h| > 3% blocks fades), conviction-scaled leverage capped at 10x,
MARGIN_PCT 30%, MIN_SCORE 9.

Six-layer plumbing flip: same pattern as Dog/Mantis/Bison/Condor/Orca
v3.0+ migrations. mcporter → SenpiClient, scanner create_position →
producer push_signal, openclaw cron → producer_daemon, Python state
files → runtime.guard_rails, MARKET exits → FEE_OPTIMIZED_LIMIT.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lemon_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "2.0.1"
SCANNER_NAME = "lemon_signals"
SIGNAL_TYPE = "LEMON_DEGEN_FADE"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v1.3
# ═══════════════════════════════════════════════════════════════

TRACKED_CRYPTO = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "DOGE", "LINK",
                  "XRP", "ADA", "NEAR", "UNI", "AAVE"]
TRACKED_XYZ = ["BRENTOIL", "CL", "GOLD", "SPX"]
TRACKED_ASSETS = TRACKED_CRYPTO + TRACKED_XYZ

MARGIN_PCT = 0.30
MIN_SCORE = 9
XYZ_BANNED = False
MACRO_GATE_BTC_4H_PCT = 3.0
# 2026-05-14 HYPE +12.8% post-mortem: block fades when the asset itself is
# in a strong directional run (4h ≥ 5%) AND the crowd is correctly riding
# that direction. That's real trend, not exhaustion — fading loses.
PER_ASSET_GATE_4H_PCT = 5.0

LEVERAGE_TIERS = [
    {"min_score": 13, "leverage": 10},
    {"min_score": 11, "leverage": 7},
    {"min_score": 9,  "leverage": 5},
]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 10

MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 20


def _resolve_wallet():
    env_val = (os.environ.get("LEMON_WALLET") or "").strip()
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
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return min(tier["leverage"], MAX_LEVERAGE)
    return DEFAULT_LEVERAGE


def fetch_sm_data():
    raw = cfg.mcp_call("leaderboard_get_markets", limit=100)
    if not raw:
        return {}
    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if XYZ_BANNED and dex == "xyz":
            continue
        if token not in TRACKED_ASSETS:
            continue
        sm_map[token] = {
            "asset": f"xyz:{token}" if dex == "xyz" else token,
            "dex": dex,
            "is_xyz": dex == "xyz",
            "direction": str(m.get("direction", "")).upper(),
            "pct": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "contrib_4h": safe_float(m.get("contribution_pct_change_4h", 0)),
        }
    return sm_map


def evaluate_fade(asset, sm, btc_4h):
    """Score a fade. Fade direction = opposite of SM consensus."""
    sm_direction = sm["direction"]
    if sm_direction not in ("LONG", "SHORT"):
        return None
    fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"

    pct = sm["pct"]
    traders = sm["traders"]
    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    c15m = sm["contrib_15m"]
    c1h = sm["contrib_1h"]
    c4h = sm["contrib_4h"]

    if pct < MIN_SM_PCT or traders < MIN_SM_TRADERS:
        return None
    # 15m must be fading (move exhausting)
    if c15m > 0.1:
        return None

    # MACRO_TREND_GATE (crypto only; XYZ bypasses)
    if not sm.get("is_xyz", False):
        if abs(btc_4h) > MACRO_GATE_BTC_4H_PCT:
            return None  # don't fade during BTC trending regime

    # PER_ASSET_TREND_GATE (applies to crypto + XYZ)
    # 2026-05-14 HYPE +12.8% post-mortem: Lemon shorted HYPE twice during
    # the rally because MACRO_TREND_GATE only checks BTC. When a single
    # asset itself is in a strong directional move ≥ 5% over 4h AND the
    # SM crowd is correctly riding that move, the fade thesis fails —
    # that's real trend, not exhaustion. Block these explicitly.
    if sm_direction == "LONG" and p4h > PER_ASSET_GATE_4H_PCT:
        return None  # asset rallying hard, crowd correctly long, don't fade
    if sm_direction == "SHORT" and p4h < -PER_ASSET_GATE_4H_PCT:
        return None  # asset dumping hard, crowd correctly short, don't fade

    score = 0
    reasons = []

    if pct >= 20:
        score += 4
        reasons.append(f"DEGEN_PILE {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"HEAVY_CROWD {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"CROWDED {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 3:
        score += 1
        reasons.append(f"LEANING {pct:.1f}% ({traders}t) {sm_direction}")

    if c15m < -2.0:
        score += 3
        reasons.append(f"15M_COLLAPSING {c15m:.2f}")
    elif c15m < -0.5:
        score += 2
        reasons.append(f"15M_FADING {c15m:.2f}")
    elif c15m < -0.1:
        score += 1
        reasons.append(f"15M_COOLING {c15m:.2f}")

    if c1h < -0.5:
        score += 1
        reasons.append(f"1H_FADING {c1h:.2f}")

    if sm_direction == "LONG" and p4h > 3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "LONG" and p4h > 1.5:
        score += 1
        reasons.append(f"EXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_SHORT {p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -1.5:
        score += 1
        reasons.append(f"EXTENDED_SHORT {p4h:.1f}%")

    if fade_direction == "LONG" and p1h > 0.1:
        score += 1
        reasons.append(f"1H_REVERSING +{p1h:.2f}%")
    elif fade_direction == "SHORT" and p1h < -0.1:
        score += 1
        reasons.append(f"1H_REVERSING {p1h:.2f}%")

    if c4h < -1.0:
        score += 1
        reasons.append(f"4H_SM_WEAKENING {c4h:.1f}")

    # Funding alignment
    try:
        canonical = sm.get("asset", asset)
        ad = cfg.mcp_call("market_get_asset_data", asset=canonical,
                          candle_intervals=[], include_funding=True,
                          include_order_book=False)
        if ad:
            ac = ad.get("data", ad).get("asset_context",
                 ad.get("data", ad).get("assetContext", {}))
            if isinstance(ac, dict):
                funding = safe_float(ac.get("funding", 0))
                if (fade_direction == "SHORT" and funding > 0.0002):
                    score += 1
                    reasons.append(f"FUNDING_PAYS_FADE +{funding*100:.4f}%")
                elif (fade_direction == "LONG" and funding < -0.0002):
                    score += 1
                    reasons.append(f"FUNDING_PAYS_FADE {funding*100:.4f}%")
    except Exception:
        pass

    hour = datetime.now(timezone.utc).hour
    if 13 <= hour <= 21:
        score += 1
        reasons.append("US_SESSION")

    if score < MIN_SCORE:
        return None

    canonical = sm.get("asset", asset)
    return {
        "asset": canonical,
        "is_xyz": sm.get("is_xyz", False),
        "direction": fade_direction,
        "score": score,
        "reasons": reasons,
        "smDirection": sm_direction,
        "smPct": pct,
        "smTraders": traders,
        "priceChg4h": p4h,
        "contrib15m": c15m,
    }


def push_signal(signal, leverage, margin_usd, held_assets):
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False
    if signal["asset"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": signal["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "isXyz": signal["is_xyz"],
        "smDirection": signal["smDirection"],
        "smPct": signal["smPct"],
        "smTraders": signal["smTraders"],
        "priceChg4h": signal["priceChg4h"],
        "contrib15m": signal["contrib15m"],
        "reasons": signal["reasons"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=signal["asset"],
            direction=signal["direction"],
            score=min(signal["score"] / 16.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {signal['asset']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {signal['asset']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_lemon_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_lemon_producer_version": VERSION})
        return

    sm_map = fetch_sm_data()
    if not sm_map:
        cfg.output({"status": "ok", "note": "no SM data",
                    "_lemon_producer_version": VERSION})
        return

    # BTC 4h for macro gate
    btc_4h = safe_float(sm_map.get("BTC", {}).get("price_chg_4h", 0))

    # Score each tracked asset
    candidates = []
    for token, sm in sm_map.items():
        if cfg.is_asset_cooled_down(token, 120):
            continue
        c = evaluate_fade(token, sm, btc_4h)
        if c is not None:
            # Skip if asset is held (any direction)
            asset_upper = c["asset"].upper()
            if asset_upper in {h.upper() for h in held_assets}:
                continue
            candidates.append(c)

    if not candidates:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(sm_map),
            "candidates": 0,
            "signals_pushed": 0,
            "btc_4h": btc_4h,
            "min_score": MIN_SCORE,
            "note": "No fade signals.",
            "elapsed_sec": round(elapsed, 2),
            "_lemon_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    leverage = get_leverage_for_score(best["score"])
    margin_usd = round(account_value * MARGIN_PCT, 2)
    pushed = push_signal(best, leverage, margin_usd, held_assets)

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(sm_map),
        "candidates": len(candidates),
        "signals_pushed": 1 if pushed else 0,
        "btc_4h": btc_4h,
        "best": {
            "asset": best["asset"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:6],
        },
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_lemon_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,                  # 5min — preserved from v1.x
        name=f"lemon-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
