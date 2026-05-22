#!/usr/bin/env python3
# Senpi PIRANHA Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""PIRANHA v1.0.0 — Liquidation-Cascade / Forced-Flow Hunter.

When open interest is UNWINDING fast (positions being force-closed /
liquidated) AND price is moving violently in one direction, that's forced
flow — liquidations begetting liquidations. Piranha rides the flow:
  - OI dropping + price spiking UP   = shorts being squeezed   → ride LONG
  - OI dropping + price dropping HARD = longs being liquidated  → ride SHORT

This is detectable at a 5-min cadence as the *continuation* of a
liquidation event (OI unwind over 15m-1h alongside a >2% move + volume
spike), not the microsecond cascade itself. A thin order book on the side
price is moving into confirms there's little resistance left.

Universe: BTC/ETH/SOL/HYPE. DSL: wide "let winners run" ladder (a squeeze
can extend far) + a 24h hard_timeout (forced-flow theses are short-horizon
— if it hasn't resolved in a day, the cascade is over). Producer NEVER
closes — DSL owns exits.

OI velocity: uses market_get_asset_data oi_velocity when present; falls
back to a self-computed delta from the persisted last-OI cache when null.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import piranha_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "piranha_signals"
SIGNAL_TYPE = "PIRANHA_FORCED_FLOW"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5

DEFAULT_OI_DROP_MIN_PCT = 3.0       # OI must be falling at least this % (1h) — positions force-closing
DEFAULT_OI_DROP_STRONG_PCT = 6.0
DEFAULT_PRICE_MOVE_MIN_PCT = 2.0    # 1h price move magnitude that marks "violent"
DEFAULT_VOL_SPIKE_PCT = 50.0


def _resolve_wallet():
    env_val = (os.environ.get("PIRANHA_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


def _f(c, primary, alt=None, default=0.0):
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Microstructure helpers
# ═══════════════════════════════════════════════════════════════

def price_move_pct(candles, n_bars):
    """Signed % move over the last n_bars candles."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f(candles[-(n_bars + 1)], "close", "c")
    new = _f(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def oi_velocity_1h(asset_data, coin):
    """(oi_change_pct_1h, source). Prefers oi_velocity object; falls back to
    a self-computed delta from the persisted last-OI cache. None if neither."""
    data = asset_data.get("data", {})
    ctx = data.get("asset_context", {}) or {}
    cur_oi = _f(ctx, "openInterest")
    oiv = data.get("oi_velocity")
    if isinstance(oiv, dict):
        ch = oiv.get("oi_change_pct")
        if isinstance(ch, dict) and ch.get("1h") is not None:
            try:
                return float(ch["1h"]), "oi_velocity"
            except (TypeError, ValueError):
                pass
    if cur_oi > 0:
        prev = cfg.read_oi_state().get(coin.upper())
        if prev and float(prev.get("oi", 0)) > 0:
            return ((cur_oi - float(prev["oi"])) / float(prev["oi"])) * 100, "computed"
    return None, "unavailable"


def book_thin_side(asset_data):
    """Returns (bid_depth, ask_depth) summed over the visible book levels.
    A thin ask side means little resistance above (favors an up-move);
    thin bid side means little support below (favors a down-move)."""
    ob = asset_data.get("data", {}).get("order_book", {}) or {}
    levels = ob.get("levels") or []
    if len(levels) < 2:
        return 0.0, 0.0
    bids, asks = levels[0], levels[1]
    bid_depth = sum(_f(l, "sz") for l in bids if isinstance(l, dict))
    ask_depth = sum(_f(l, "sz") for l in asks if isinstance(l, dict))
    return bid_depth, ask_depth


def volume_trend(candles, lookback=6):
    if len(candles) < lookback:
        return 0.0
    vols = [_f(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_market_data(asset):
    return cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["5m", "1h"],
        include_funding=False,
        include_order_book=True,
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
        if token != asset:
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
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ═══════════════════════════════════════════════════════════════
# Thesis builder — ride the forced flow
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_5m = data.get("data", {}).get("candles", {}).get("5m", [])
    if len(candles_1h) < 3 or len(candles_5m) < 4:
        oi_now = _f(data.get("data", {}).get("asset_context", {}) or {}, "openInterest")
        if oi_now > 0:
            cfg.record_oi(asset, oi_now)
        return None

    oi_now = _f(data.get("data", {}).get("asset_context", {}) or {}, "openInterest")

    # GATE 1 — OI unwinding fast (positions force-closing)
    oi_pct, oi_src = oi_velocity_1h(data, asset)
    if oi_now > 0:
        cfg.record_oi(asset, oi_now)
    oi_drop_min = float(entry_cfg.get("oiDropMinPct", DEFAULT_OI_DROP_MIN_PCT))
    if oi_pct is None:
        return None  # OI unknown (cache warming) — can't confirm forced flow
    if oi_pct > -oi_drop_min:
        return None  # OI not falling fast enough — no liquidation/unwind signature

    # GATE 2 — violent price move (the flow direction)
    move_1h = price_move_pct(candles_1h, 1)
    move_min = float(entry_cfg.get("priceMoveMinPct", DEFAULT_PRICE_MOVE_MIN_PCT))
    if abs(move_1h) < move_min:
        return None
    direction = "LONG" if move_1h > 0 else "SHORT"   # ride the forced flow

    move_5m = price_move_pct(candles_5m, 1)
    # 5m must still be moving the same way (flow ongoing, not already reversed)
    if (direction == "LONG" and move_5m <= 0) or (direction == "SHORT" and move_5m >= 0):
        return None

    bid_depth, ask_depth = book_thin_side(data)
    vol_pct = volume_trend(candles_5m)
    oi_strong = float(entry_cfg.get("oiDropStrongPct", DEFAULT_OI_DROP_STRONG_PCT))
    vol_spike = float(entry_cfg.get("volSpikePct", DEFAULT_VOL_SPIKE_PCT))

    score = 0
    reasons = []

    # OI unwind magnitude (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"oi_unwind_{oi_pct:+.1f}%_{oi_src}")
    if oi_pct <= -oi_strong:
        score += 1
        reasons.append("oi_unwind_strong")

    # Violent move (gate-confirmed) + acceleration
    score += 2
    reasons.append(f"move_1h_{move_1h:+.2f}%")
    if abs(move_5m) >= move_min / 2:
        score += 1
        reasons.append(f"accel_5m_{move_5m:+.2f}%")

    # Thin book on the side price is running into = little resistance left
    thin_into = (direction == "LONG" and ask_depth > 0 and bid_depth > ask_depth * 1.3) or \
                (direction == "SHORT" and bid_depth > 0 and ask_depth > bid_depth * 1.3)
    if thin_into:
        score += 1
        reasons.append("book_thin_into_move")

    # Volume spike confirms forced activity
    if vol_pct >= vol_spike:
        score += 1
        reasons.append(f"vol_spike_{vol_pct:+.0f}%")

    # SM aligned with the flow (optional confirm)
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    if sm_dir == direction and sm_tilt >= 55:
        score += 1
        reasons.append(f"sm_aligned_{sm_tilt:.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "oi_change_pct": round(oi_pct, 3),
        "oi_source": oi_src,
        "move_1h_pct": round(move_1h, 3),
        "move_5m_pct": round(move_5m, 3),
        "bid_depth": round(bid_depth, 2),
        "ask_depth": round(ask_depth, 2),
        "volume_trend_pct": round(vol_pct, 2),
        "sm_direction": sm_dir if sm_dir else "NONE",
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit
# ═══════════════════════════════════════════════════════════════

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
        "oiChangePct": thesis.get("oi_change_pct") or 0.0,
        "oiSource": thesis.get("oi_source") or "unavailable",
        "move1hPct": thesis.get("move_1h_pct") or 0.0,
        "move5mPct": thesis.get("move_5m_pct") or 0.0,
        "bidDepth": thesis.get("bid_depth") or 0.0,
        "askDepth": thesis.get("ask_depth") or 0.0,
        "volumeTrendPct": thesis.get("volume_trend_pct") or 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "heldAssets": held_assets,
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


# ═══════════════════════════════════════════════════════════════
# MAIN — multi-asset whitelist scan
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    universe = [a.upper() for a in config.get("universe", DEFAULT_UNIVERSE)]

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_piranha_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_piranha_producer_version": VERSION})
        return

    candidates = []
    for asset in universe:
        if asset in held_set:
            continue
        if cfg.was_recently_signaled(asset):
            continue
        thesis = build_thesis(asset, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no forced-flow / liquidation-unwind signature on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_piranha_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
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
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_piranha_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=180,           # 3-min cadence — forced-flow windows are short-lived
        name=f"piranha-producer-{_wallet_lock_id}",
        tick_timeout=150,
    )
