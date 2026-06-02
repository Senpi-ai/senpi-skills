#!/usr/bin/env python3
# Senpi LEMUR Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""LEMUR v1.0.0 — Pre-IPO Perpetuals (IPOP) basket on Hyperliquid XYZ.

Auto-discovers IPOPs from the live xyz: instrument list using the
structural funding signature documented by trade.xyz:

  is_ipop(inst) iff
    name.startswith("xyz:") AND
    not is_delisted AND
    abs(funding_rate) <= 1e-7 (1% multiplier vs 0.5 standard = ~100x smaller)
    AND max_leverage <= 5 (pre-listing leverage cap)
    AND daily_notional_volume >= 100000 (liquidity floor)

Today this returns [xyz:SPCX] (SpaceX). When trade.xyz lists more IPOPs
(ANTHROPIC, OPENAI, STRIPE, DATABRICKS), Lemur auto-expands.

When an IPOP CONVERTS to a normal equity perp after the company IPOs,
its funding rate jumps from ~6.25e-8 to ~6.25e-6 (100x) — Lemur
auto-drops it from the universe at that point. Standard equity perps
are Bobcat's territory.

Discovery Bounds throttle price velocity on IPOPs (trade.xyz design)
so the DSL profile is moderate, not catastrophic — Phase 1 max_loss
10%, Phase 2 wide ladder for multi-day directional discovery.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lemur_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "lemur_signals"
SIGNAL_TYPE = "LEMUR_IPOP"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70
DEFAULT_IPOP_FUNDING_MAX = 1e-7
DEFAULT_IPOP_LEV_CAP = 5
DEFAULT_IPOP_MIN_VOL = 100000


def _resolve_wallet():
    env_val = (os.environ.get("LEMUR_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# IPOP universe discovery
# ═══════════════════════════════════════════════════════════════

def fetch_ipop_universe(config):
    """Filter xyz: instruments to those matching the IPOP signature.

    Returns list of dicts: [{name, max_leverage, funding, vol_usd}].
    """
    raw = cfg.mcp_call("market_list_instruments", dex="xyz")
    if not raw or not raw.get("success", True):
        return []
    data = raw.get("data", raw)
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []

    max_funding = float(config.get("ipopFundingMaxAbs", DEFAULT_IPOP_FUNDING_MAX))
    max_lev = int(config.get("ipopMaxLeverageCap", DEFAULT_IPOP_LEV_CAP))
    min_vol = float(config.get("ipopMinDailyVolUsd", DEFAULT_IPOP_MIN_VOL))

    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        if int(inst.get("max_leverage", 999)) > max_lev:
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(ctx.get("funding", 0)))
        if funding_abs > max_funding:
            continue
        vol_usd = float(ctx.get("dayNtlVlm", 0))
        if vol_usd < min_vol:
            continue
        universe.append({
            "name": name,
            "max_leverage": int(inst.get("max_leverage", 5)),
            "funding": funding_abs,
            "vol_usd": vol_usd,
        })
    return universe


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0.0
    lows = [_f(c, "low", "l") for c in candles[-lookback:]]
    highs = [_f(c, "high", "h") for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h", "4h"],
        include_funding=False,
        include_order_book=False,
    )


def fetch_sm_direction(asset):
    raw = cfg.mcp_call("leaderboard_get_markets")
    if not raw or not raw.get("success", True):
        return None, 0.0
    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if token != asset.upper():
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


def build_thesis(asset_name, entry_cfg):
    data = fetch_market_data(asset_name)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    if len(candles_4h) < 6 or len(candles_1h) < 6:
        return None

    t4, s4 = trend_structure(candles_4h)
    t1, _ = trend_structure(candles_1h)
    if t4 == "NEUTRAL":
        return None

    direction = "LONG" if t4 == "BULLISH" else "SHORT"

    sm_dir, sm_tilt = fetch_sm_direction(asset_name)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    # Note: IPOP SM data may be sparse pre-listing — fallback to 4h-trend-only if SM not available
    if sm_dir is None:
        sm_dir = direction  # fallback: assume aligned (SM data not available)
        sm_tilt = sm_min     # minimum tilt for scoring purposes
    elif sm_dir == "NEUTRAL" or sm_dir != direction:
        return None
    elif sm_tilt < sm_min:
        return None

    score = 0
    reasons = []
    score += 3
    reasons.append(f"4h_{t4.lower()}_{s4:.0%}")
    if (direction == "LONG" and t1 == "BULLISH") or (direction == "SHORT" and t1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirms_{t1.lower()}")
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%" if sm_tilt > DEFAULT_SM_TILT_MIN else "sm_data_sparse_assumed_aligned")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    return {
        "coin": asset_name,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "trend_4h": t4,
        "trend_4h_strength": s4,
        "trend_1h": t1,
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
    }


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    coin = thesis["coin"]
    if coin.upper() in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "trend4h": thesis["trend_4h"],
        "trend4hStrength": thesis["trend_4h_strength"],
        "trend1h": thesis["trend_1h"],
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis["sm_tilt_pct"],
        "heldAssets": held_assets,
        "ipopFlag": True,  # marker so downstream knows this is a pre-IPO product
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 9.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {coin}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {coin}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_lemur_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_lemur_producer_version": VERSION})
        return

    universe = fetch_ipop_universe(config)
    if not universe:
        cfg.output({
            "status": "ok",
            "note": "no IPOPs in universe (no instruments match funding+leverage heuristic)",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_lemur_producer_version": VERSION,
        })
        return

    candidates = []
    for inst in universe:
        coin = inst["name"]
        if coin.upper() in held_set:
            continue
        if cfg.was_recently_signaled(coin):
            continue
        thesis = build_thesis(coin, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            thesis["max_leverage_cap"] = inst["max_leverage"]
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no IPOP setup with 4h trend + SM agreement",
            "ipop_universe": [u["name"] for u in universe],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_lemur_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
    config_leverage = int(config.get("leverage", DEFAULT_LEVERAGE))
    # Auto-cap to the IPOP's own max_leverage (typically 5 for SPCX)
    leverage = min(config_leverage, best.get("max_leverage_cap", MAX_LEVERAGE), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(best["coin"])

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "ipop_universe": [u["name"] for u in universe],
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_lemur_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=900,  # 15min — IPOPs move slower per Discovery Bounds
        name=f"lemur-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
