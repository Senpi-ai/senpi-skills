#!/usr/bin/env python3
# Senpi BADGER Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""BADGER v1.0.0 — OI-Divergence Breakout Anticipator.

Open interest is the fuel of a breakout. A price push beyond its recent
range that is confirmed by RISING open interest means new money is
committing — the breakout has conviction and follow-through. A breakout
on flat/declining OI is usually a fakeout (no new money, just stops).

LONG when: price breaks the prior 24h high AND OI is rising AND Smart
Money is net long. SHORT when: price breaks the prior 24h low AND OI is
rising AND Smart Money is net short. Universe: BTC/ETH/SOL/HYPE.

The OI-rising gate is what distinguishes Badger from a pure price-breakout
agent (Hawk): Badger only takes the breakout the order flow confirms.

DSL: wide "let winners run" Phase 2 ladder (T0 +10% / lock 0 → T5 +100% /
lock 85). A confirmed breakout can run far; Phase 1 max_loss 20% cuts a
failed one. Time-cuts disabled. Producer NEVER closes — DSL owns exits.

OI velocity: uses market_get_asset_data's oi_velocity object when present;
when it is null, Badger falls back to a self-computed delta from a
persisted last-OI cache (warms up after one tick per asset).
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badger_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.1"
SCANNER_NAME = "badger_signals"
SIGNAL_TYPE = "BADGER_OI_BREAKOUT"

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 5
DEFAULT_MIN_SCORE = 5

DEFAULT_BREAKOUT_LOOKBACK = 24       # 1h bars (prior 24h) for the range edge
DEFAULT_OI_RISING_MIN_PCT = 2.0     # OI must rise at least this % to confirm
DEFAULT_OI_STRONG_PCT = 5.0         # strongly-building bonus
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70


def _resolve_wallet():
    env_val = (os.environ.get("BADGER_WALLET") or "").strip()
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
# Technical helpers
# ═══════════════════════════════════════════════════════════════

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


def breakout_signal(candles_1h, lookback):
    """Returns (direction, magnitude_pct) where direction is 'UP' (close
    above prior `lookback`-bar high), 'DOWN' (below prior low), or None."""
    if len(candles_1h) < lookback + 1:
        return None, 0.0
    closes = [_f(c, "close", "c") for c in candles_1h[-(lookback + 1):]]
    latest = closes[-1]
    prior = closes[:-1]
    if not prior or latest <= 0:
        return None, 0.0
    hi, lo = max(prior), min(prior)
    if hi > 0 and latest > hi:
        return "UP", ((latest - hi) / hi) * 100
    if lo > 0 and latest < lo:
        return "DOWN", ((lo - latest) / lo) * 100
    return None, 0.0


def oi_velocity_1h(asset_data, coin):
    """Return (oi_change_pct_1h, source). Prefers the runtime oi_velocity
    object; falls back to a self-computed delta from the persisted last-OI
    cache. Returns (None, 'unavailable') when neither is usable yet."""
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
            pct = ((cur_oi - float(prev["oi"])) / float(prev["oi"])) * 100
            return pct, "computed"
    return None, "unavailable"


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
    if long_ratio >= 50:
        return "LONG", long_ratio
    return "SHORT", 100 - long_ratio


# ═══════════════════════════════════════════════════════════════
# Thesis builder
# ═══════════════════════════════════════════════════════════════

def build_thesis(asset, entry_cfg):
    data = fetch_market_data(asset)
    if not data or not data.get("success", True):
        return None
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    lookback = int(entry_cfg.get("breakoutLookbackHours", DEFAULT_BREAKOUT_LOOKBACK))
    if len(candles_1h) < lookback + 1 or len(candles_4h) < 6:
        # still refresh the OI cache so next tick has a baseline
        oi_now = _f(data.get("data", {}).get("asset_context", {}) or {}, "openInterest")
        if oi_now > 0:
            cfg.record_oi(asset, oi_now)
        return None

    # Always refresh the OI baseline for this asset (warms the fallback cache).
    oi_now = _f(data.get("data", {}).get("asset_context", {}) or {}, "openInterest")

    # GATE 1 — price breakout
    bo_dir, bo_mag = breakout_signal(candles_1h, lookback)
    if bo_dir is None:
        if oi_now > 0:
            cfg.record_oi(asset, oi_now)
        return None
    direction = "LONG" if bo_dir == "UP" else "SHORT"

    # GATE 2 — OI rising (the conviction gate, Badger's core edge)
    oi_pct, oi_src = oi_velocity_1h(data, asset)
    if oi_now > 0:
        cfg.record_oi(asset, oi_now)
    oi_min = float(entry_cfg.get("oiRisingMinPct", DEFAULT_OI_RISING_MIN_PCT))
    if oi_pct is None:
        return None  # OI unknown (cache warming) — no conviction read, skip
    if oi_pct < oi_min:
        return None  # breakout without rising OI = likely fakeout

    # GATE 3 — Smart-Money agreement
    sm_dir, sm_tilt = fetch_sm_direction(asset)
    sm_min = float(entry_cfg.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(entry_cfg.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    trend_4h, str_4h = trend_structure(candles_4h)
    oi_strong = float(entry_cfg.get("oiStrongPct", DEFAULT_OI_STRONG_PCT))

    score = 0
    reasons = []

    # Breakout magnitude
    if bo_mag >= 1.0:
        score += 3
    elif bo_mag >= 0.3:
        score += 2
    else:
        score += 1
    reasons.append(f"breakout_{bo_dir.lower()}_{bo_mag:+.2f}%")

    # OI rising (gate-confirmed) + strongly-building bonus
    score += 2
    reasons.append(f"oi_rising_{oi_pct:+.1f}%_{oi_src}")
    if oi_pct >= oi_strong:
        score += 1
        reasons.append("oi_strongly_building")

    # SM aligned (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= sm_strong:
        score += 1
        reasons.append("sm_strongly_tilted")

    # 4h structure confirms breakout direction
    if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
        score += 1
        reasons.append(f"4h_confirms_{trend_4h.lower()}")

    # Volume rising bonus
    vol_pct = volume_trend(candles_1h)
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": asset,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "breakout_dir": bo_dir,
        "breakout_mag_pct": round(bo_mag, 3),
        "oi_change_pct": round(oi_pct, 3),
        "oi_source": oi_src,
        "trend_4h": trend_4h,
        "sm_direction": sm_dir,
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": round(vol_pct, 2),
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
    # Coalesce numeric fields — runtime schema rejects null for `number`.
    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "breakoutDir": thesis["breakout_dir"],
        "breakoutMagPct": thesis.get("breakout_mag_pct") or 0.0,
        "oiChangePct": thesis.get("oi_change_pct") or 0.0,
        "oiSource": thesis.get("oi_source") or "unavailable",
        "trend4h": thesis["trend_4h"],
        "smDirection": thesis["sm_direction"],
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "volumeTrendPct": thesis.get("volume_trend_pct") or 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 10.0, 1.0),
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
# MAIN — multi-asset whitelist scan. Daemon owns the per-tick lock.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    universe = [a.upper() for a in config.get("universe", DEFAULT_UNIVERSE)]

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_badger_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_badger_producer_version": VERSION})
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
            "note": "WAITING — no OI-confirmed breakout with SM agreement on universe",
            "universe": universe,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_badger_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.20))
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
        "_badger_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,
        name=f"badger-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
