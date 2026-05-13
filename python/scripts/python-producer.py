#!/usr/bin/env python3
# Senpi PYTHON Producer v2.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PYTHON v2.0.0 Producer — The Patience Hunter, helpers-native.

v2.0.0 (2026-05-12): plumbing migration from v1.2. NO thesis change.
v1.2 scoring (4h+1h+1d trend, LONG bias bonus, SM alignment hard-block,
funding, volume, RSI extremes, move-exhaustion penalty), MIN_SCORE 8,
MACRO_TREND_GATE, MAX_LEVERAGE 7, MAX_POSITIONS 2, 12h per-asset
cooldown all preserved verbatim. Multi-day hold thesis preserved.

Six-layer plumbing flip: mcporter → SenpiClient, scanner create_position
→ producer push_signal, openclaw cron → producer_daemon, Python state
files → runtime.guard_rails, MARKET exits → FEE_OPTIMIZED_LIMIT.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import python_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "2.0.0"
SCANNER_NAME = "python_signals"
SIGNAL_TYPE = "PYTHON_PATIENCE_HUNTER"


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v1.2
# ═══════════════════════════════════════════════════════════════

UNIVERSE_SIZE = 50
MIN_OI_USD = 1_000_000
MIN_TRADER_COUNT = 30
MIN_SCORE = 8
ASSET_COOLDOWN_MINUTES = 720    # 12h per-asset

LEVERAGE_TIERS = [
    {"min_score": 12, "leverage": 7},
    {"min_score": 10, "leverage": 5},
    {"min_score": 8,  "leverage": 3},
]
MAX_LEVERAGE = 7
DEFAULT_LEVERAGE = 3

MARGIN_PCT_BASE = 0.25
MARGIN_PCT_STRONG = 0.30
MARGIN_PCT_APEX = 0.40

MACRO_GATE_THRESHOLD_PCT = 10.0
LONG_BIAS_BONUS = 1


def _resolve_wallet():
    env_val = (os.environ.get("PYTHON_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# Universe discovery — preserved verbatim
# ═══════════════════════════════════════════════════════════════

def get_universe():
    data = cfg.mcp_call("market_list_instruments")
    if not data:
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    if not isinstance(instruments, list):
        return []

    filtered = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name or name.startswith("xyz:"):
            continue
        if name.upper() in ("USDC", "USDT", "USDE", "FDUSD", "DAI"):
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        day_ntl_vlm = safe_float(ctx.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        oi = safe_float(ctx.get("openInterest", inst.get("openInterest", 0)))
        mark_px = safe_float(ctx.get("markPx", inst.get("markPx", 0)))
        oi_usd = oi * mark_px

        if oi_usd < MIN_OI_USD:
            continue
        if day_ntl_vlm < 1_000_000:
            continue

        filtered.append({
            "coin": name,
            "volume": day_ntl_vlm,
            "oi_usd": oi_usd,
            "markPx": mark_px,
            "maxLeverage": int(inst.get("maxLeverage", 10) or 10),
        })
    filtered.sort(key=lambda x: -x["volume"])
    return filtered[:UNIVERSE_SIZE]


def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = safe_float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = safe_float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [safe_float(c.get("low", c.get("l", 0))) for c in candles[-lookback:]]
    highs = [safe_float(c.get("high", c.get("h", 0))) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.55:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.55:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def volume_ratio(candles, lookback=10):
    if len(candles) < lookback + 1:
        return 1.0
    vols = [safe_float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 1
    latest = safe_float(candles[-1].get("volume", candles[-1].get("v", candles[-1].get("vlm", 0))))
    return latest / avg if avg > 0 else 1.0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g, l = gains[-period:], losses[-period:]
    avg_g, avg_l = sum(g) / period, sum(l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def get_sm_map():
    data = cfg.mcp_call("leaderboard_get_markets")
    if not data:
        return {}
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    by_coin = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token:
            continue
        direction = m.get("direction", "").lower()
        pct = safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))

        entry = by_coin.setdefault(token, {"long_pct": 0, "short_pct": 0, "traders": 0, "cc_15m": 0})
        if direction == "long":
            entry["long_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m
        elif direction == "short":
            entry["short_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m

    result = {}
    for token, data in by_coin.items():
        total = data["long_pct"] + data["short_pct"]
        if total == 0 or data["traders"] < MIN_TRADER_COUNT:
            continue
        long_ratio = (data["long_pct"] / total) * 100
        if long_ratio > 58:
            result[token] = ("LONG", long_ratio, data["traders"], data["cc_15m"])
        elif long_ratio < 42:
            result[token] = ("SHORT", 100 - long_ratio, data["traders"], data["cc_15m"])
        else:
            result[token] = ("NEUTRAL", 50, data["traders"], data["cc_15m"])
    return result


def build_thesis(coin, max_lev, sm_info):
    """Score a single coin for multi-day hold potential — preserved verbatim from v1.2."""
    data = cfg.mcp_call("market_get_asset_data", asset=coin,
                        candle_intervals=["15m", "1h", "4h", "1d"],
                        include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None
    asset_data = data.get("data", data)

    candles_15m = asset_data.get("candles", {}).get("15m", [])
    candles_1h = asset_data.get("candles", {}).get("1h", [])
    candles_4h = asset_data.get("candles", {}).get("4h", [])
    candles_1d = asset_data.get("candles", {}).get("1d", [])
    asset_ctx = asset_data.get("asset_context", asset_data.get("assetContext", {}))
    funding = safe_float(asset_ctx.get("funding", 0))

    if len(candles_15m) < 8 or len(candles_1h) < 6 or len(candles_4h) < 6:
        return None
    price = safe_float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0)))

    trend_4h, trend_strength_4h = trend_structure(candles_4h)
    if trend_4h == "NEUTRAL":
        return None
    direction = "LONG" if trend_4h == "BULLISH" else "SHORT"

    trend_1h, _ = trend_structure(candles_1h)
    if trend_1h != trend_4h:
        return None

    mom_1h = price_momentum(candles_1h, 2)
    mom_4h = price_momentum(candles_4h, 1)
    mom_15m = price_momentum(candles_15m, 1)
    mom_1d = price_momentum(candles_1d, 1) if len(candles_1d) >= 2 else 0

    if abs(mom_4h) > MACRO_GATE_THRESHOLD_PCT:
        if (direction == "LONG" and mom_4h < 0) or (direction == "SHORT" and mom_4h > 0):
            return None

    if direction == "LONG" and mom_15m < 0.1:
        return None
    if direction == "SHORT" and mom_15m > -0.1:
        return None

    score = 0
    reasons = []

    if trend_strength_4h >= 0.8:
        score += 4
        reasons.append(f"4h_strong_{trend_4h}")
    elif trend_strength_4h >= 0.6:
        score += 3
        reasons.append(f"4h_{trend_4h}")
    else:
        score += 2
        reasons.append(f"4h_weak_{trend_4h}")

    if abs(mom_1h) > 1.0:
        score += 2
        reasons.append(f"1h_strong_{mom_1h:+.2f}%")
    elif abs(mom_1h) > 0.5:
        score += 1
        reasons.append(f"1h_ok_{mom_1h:+.2f}%")

    if len(candles_1d) >= 3:
        if direction == "LONG" and mom_1d > 1.0:
            score += 2; reasons.append(f"1d_bullish_{mom_1d:+.1f}%")
        elif direction == "SHORT" and mom_1d < -1.0:
            score += 2; reasons.append(f"1d_bearish_{mom_1d:+.1f}%")
        elif direction == "LONG" and mom_1d > 0:
            score += 1; reasons.append("1d_up")
        elif direction == "SHORT" and mom_1d < 0:
            score += 1; reasons.append("1d_down")

    if direction == "LONG":
        score += LONG_BIAS_BONUS
        reasons.append("LONG_bias")

    if sm_info:
        sm_dir, sm_pct, sm_count, sm_cc_15m = sm_info
        if sm_dir == direction:
            score += 2
            reasons.append(f"sm_aligned_{sm_pct:.0f}%_{sm_count}t")
            if sm_pct > 70:
                score += 1
                reasons.append("sm_strongly_tilted")
        elif sm_dir != "NEUTRAL" and sm_dir != direction:
            return None  # HARD BLOCK
        if sm_cc_15m > 0.3:
            score += 1
            reasons.append(f"15m_fresh +{sm_cc_15m:.2f}")

    if direction == "LONG" and funding < 0:
        score += 1; reasons.append(f"funding_pays_long_{funding:+.4f}")
    elif direction == "SHORT" and funding > 0:
        score += 1; reasons.append(f"funding_pays_short_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.01):
        score -= 1; reasons.append(f"funding_crowded_{funding:+.4f}")

    vol_1h = volume_ratio(candles_1h)
    if vol_1h >= 1.3:
        score += 1; reasons.append(f"vol_{vol_1h:.1f}x")
    elif vol_1h < 0.6:
        score -= 1; reasons.append("vol_weak")

    closes_1h = [safe_float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > 78:
        return None
    if direction == "SHORT" and rsi < 22:
        return None
    if (direction == "LONG" and 50 < rsi < 68) or (direction == "SHORT" and 32 < rsi < 50):
        score += 1; reasons.append(f"rsi_room_{rsi:.0f}")

    if abs(mom_4h) >= 6.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 2; reasons.append(f"MOVE_EXHAUSTION_{mom_4h:+.1f}%")
    elif abs(mom_4h) >= 4.0:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score -= 1; reasons.append(f"MOVE_TIRING_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "price": price, "trend_4h": trend_4h, "trend_1h": trend_1h,
        "mom_1h": mom_1h, "mom_4h": mom_4h, "mom_1d": mom_1d,
        "funding": funding, "rsi": rsi, "vol_ratio": vol_1h, "max_lev": max_lev,
    }


def get_leverage_for_score(score):
    for tier in LEVERAGE_TIERS:
        if score >= tier["min_score"]:
            return tier["leverage"]
    return DEFAULT_LEVERAGE


def get_margin_pct(score):
    if score >= 12:
        return MARGIN_PCT_APEX
    elif score >= 10:
        return MARGIN_PCT_STRONG
    return MARGIN_PCT_BASE


def get_safe_leverage(wallet, coin, desired):
    try:
        r = cfg.mcp_call("strategy_get_asset_trading_limits",
                          strategy_wallet=wallet, coin=coin)
        if r:
            d = r.get("data", r)
            max_lev = int(d.get("maxLeverage", d.get("max_leverage", MAX_LEVERAGE)))
            return min(desired, max_lev, MAX_LEVERAGE)
    except Exception:
        pass
    return min(desired, MAX_LEVERAGE)


def push_signal(thesis, leverage, margin_usd, held_assets):
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "reasons": thesis["reasons"],
        "trend4h": thesis["trend_4h"],
        "trend1h": thesis["trend_1h"],
        "mom1h": thesis["mom_1h"],
        "mom4h": thesis["mom_4h"],
        "mom1d": thesis["mom_1d"],
        "funding": thesis["funding"],
        "rsi": thesis["rsi"],
        "volRatio": thesis["vol_ratio"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / 16.0, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {thesis['coin']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION {thesis['coin']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_python_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_python_producer_version": VERSION})
        return

    universe = get_universe()
    if not universe:
        cfg.output({"status": "ok", "note": "no universe",
                    "_python_producer_version": VERSION})
        return

    open_coins = {p["coin"].upper() for p in positions}
    sm_map = get_sm_map()

    candidates = []
    for asset in universe:
        coin = asset["coin"]
        coin_upper = coin.upper()
        if coin_upper in open_coins:
            continue
        if cfg.is_asset_cooled_down(coin, ASSET_COOLDOWN_MINUTES):
            continue
        sm_info = sm_map.get(coin_upper)
        thesis = build_thesis(coin, asset["maxLeverage"], sm_info)
        if not thesis:
            continue
        if thesis["score"] < MIN_SCORE:
            continue
        candidates.append(thesis)

    if not candidates:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(universe),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": MIN_SCORE,
            "note": "HUNTING: no patience-hold candidates.",
            "elapsed_sec": round(elapsed, 2),
            "_python_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]

    leverage = get_safe_leverage(STRATEGY_ADDRESS, best["coin"],
                                  get_leverage_for_score(best["score"]))
    margin_pct = get_margin_pct(best["score"])
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(best, leverage, margin_usd, held_assets)
    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(universe),
        "candidates": len(candidates),
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "asset": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "margin_pct": margin_pct,
            "reasons": best["reasons"][:6],
        },
        "top_5_candidates": [
            {"coin": c["coin"], "direction": c["direction"], "score": c["score"]}
            for c in candidates[:5]
        ],
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "_python_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=600,                  # 10min — preserved from v1.x
        name=f"python-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=300,
    )
