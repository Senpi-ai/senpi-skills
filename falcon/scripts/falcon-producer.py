#!/usr/bin/env python3
# Senpi FALCON Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""FALCON v1.0.0 — Conversion-Event Momentum (XYZ Pre-IPO → equity).

A Pre-IPO Perpetual (IPOP) on Hyperliquid XYZ carries a structural funding
signature (|funding| <= ~1e-7, 1% multiplier; max_leverage <= 5) and is
price-throttled by trade.xyz Discovery Bounds. When the underlying company
IPOs, the product CONVERTS to a standard equity perp: funding jumps ~100x
(~6.25e-8 → ~6.25e-6), the leverage cap lifts, and the Discovery-Bounds throttle
is removed — opening a window of free price discovery.

Falcon detects that transition and trades the post-conversion momentum:

  1. Each tick, classify every xyz instrument IPOP vs STANDARD by funding sig.
  2. Compare against the prior-tick class cache → detect IPOP→STANDARD flips.
  3. A flip stamps the instrument into a conversionWindowHours eligibility
     window (so momentum that develops over hours/days is still tradeable).
  4. Within that window, require confirmed price momentum (and SM agreement)
     and enter in the momentum direction.

Distinct from Lemur, which trades the IPOP basket WHILE it is still an IPOP.
Falcon only fires AROUND the conversion. Event/momentum class → WIDE
"let winners run" DSL (price discovery can trend hard), taker-true entry,
long hard_timeout. Producer NEVER closes — DSL owns exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets.
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import falcon_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "falcon_signals"
SIGNAL_TYPE = "FALCON_CONVERSION_MOMENTUM"

MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 5
DEFAULT_SM_TILT_MIN = 55
DEFAULT_SM_STRONG = 70

# IPOP funding signature (same heuristic Lemur uses to FIND IPOPs; Falcon uses
# it to detect when one STOPS being an IPOP).
DEFAULT_IPOP_FUNDING_MAX = 1e-7
DEFAULT_IPOP_LEV_CAP = 5

DEFAULT_MOMENTUM_LOOKBACK = 6       # 1h bars
DEFAULT_MIN_MOMENTUM_PCT = 3.0      # |move| to call a discovery trend
DEFAULT_STRONG_MOMENTUM_PCT = 8.0
DEFAULT_CONVERSION_WINDOW_HOURS = 72   # how long after a flip an instrument stays eligible


def _resolve_wallet():
    env_val = (os.environ.get("FALCON_WALLET") or "").strip()
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
# Pure conversion/momentum logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def classify_instrument(funding_abs, max_leverage, ipop_funding_max, ipop_lev_cap):
    """Classify an xyz equity instrument by its funding signature.

    IPOP   = pre-listing product: |funding| <= ipop_funding_max AND
             max_leverage <= ipop_lev_cap.
    STANDARD = a normal equity perp: funding has normalized OR the leverage
               cap has lifted.
    Returns "IPOP" or "STANDARD"."""
    try:
        f = abs(float(funding_abs))
        lev = int(max_leverage)
    except (TypeError, ValueError):
        return "STANDARD"
    if f <= ipop_funding_max and lev <= ipop_lev_cap:
        return "IPOP"
    return "STANDARD"


def detect_conversion(prev_class, curr_class):
    """A conversion event is an IPOP that has become STANDARD since last tick.
    Requires a known prior class (None prev = first sighting, not a flip)."""
    return prev_class == "IPOP" and curr_class == "STANDARD"


def momentum_pct(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def conversion_direction(momentum, min_momentum_pct):
    """Direction to trade post-conversion price discovery: ride the momentum.
    None if momentum is missing or below the minimum magnitude."""
    if momentum is None or abs(momentum) < min_momentum_pct:
        return None
    return "LONG" if momentum > 0 else "SHORT"


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
# Universe scan + conversion detection
# ═══════════════════════════════════════════════════════════════

def scan_instruments(config):
    """Pull the xyz instrument list and classify each. Returns
    {name: {"class": ..., "max_leverage": int, "funding": float, "vol_usd": float}}."""
    raw = cfg.mcp_call("market_list_instruments", dex="xyz")
    if not raw or not raw.get("success", True):
        return {}
    data = raw.get("data", raw)
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return {}

    ipop_funding_max = float(config.get("ipopFundingMaxAbs", DEFAULT_IPOP_FUNDING_MAX))
    ipop_lev_cap = int(config.get("ipopMaxLeverageCap", DEFAULT_IPOP_LEV_CAP))

    out = {}
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        if not name.startswith("xyz:"):
            continue
        if inst.get("is_delisted", False):
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(ctx.get("funding", 0)))
        max_lev = int(inst.get("max_leverage", 5))
        out[name] = {
            "class": classify_instrument(funding_abs, max_lev, ipop_funding_max, ipop_lev_cap),
            "max_leverage": max_lev,
            "funding": funding_abs,
            "vol_usd": float(ctx.get("dayNtlVlm", 0)),
        }
    return out


def reconcile_conversions(scan, prev_state, config, now):
    """Compare this scan against the prior class cache, stamp any new
    IPOP→STANDARD flips into the conversion window, persist the new class
    cache. Returns the set of instrument names currently inside the window."""
    window_hours = float(config.get("conversionWindowHours", DEFAULT_CONVERSION_WINDOW_HOURS))

    new_class_state = {}
    for name, info in scan.items():
        curr_class = info["class"]
        prev_class = (prev_state.get(name) or {}).get("class")
        if detect_conversion(prev_class, curr_class):
            cfg.record_conversion(name, now)
        new_class_state[name] = {"class": curr_class, "ts": now}

    cfg.write_class_state(new_class_state)
    live = cfg.prune_conversions(window_hours)
    return set(live.keys())


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_candles(asset):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["1h"],
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return []
    return data.get("data", {}).get("candles", {}).get("1h", [])


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
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one freshly-converted instrument
# ═══════════════════════════════════════════════════════════════

def build_thesis(name, scan_info, config):
    candles = fetch_candles(name)
    if len(candles) < 8:
        return None
    closes = [_f(c, "close", "c") for c in candles]

    lookback = int(config.get("momentumLookbackBars", DEFAULT_MOMENTUM_LOOKBACK))
    min_mom = float(config.get("minMomentumPct", DEFAULT_MIN_MOMENTUM_PCT))
    strong_mom = float(config.get("strongMomentumPct", DEFAULT_STRONG_MOMENTUM_PCT))

    mom = momentum_pct(closes, lookback)
    direction = conversion_direction(mom, min_mom)
    if direction is None:
        return None

    sm_dir, sm_tilt = fetch_sm_direction(name)
    sm_min = float(config.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))
    sm_strong = float(config.get("smStrongTiltPct", DEFAULT_SM_STRONG))
    vol_trend = volume_trend(candles)

    score = 0
    reasons = [f"converted_ipop_to_equity", f"mom_{mom:+.1f}%"]
    score += 3  # inside conversion window + momentum >= min (gate-confirmed)
    if abs(mom) >= strong_mom:
        score += 2
        reasons.append(f"mom_strong_{mom:+.1f}%")
    # SM is often sparse on a freshly-listed name — agreement is a bonus, not a gate.
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vol_trend > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend:+.0f}%")

    return {
        "coin": name,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "momentum_pct": round(mom, 2),
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": sm_tilt,
        "volume_trend_pct": round(vol_trend, 2),
        "max_leverage_cap": scan_info.get("max_leverage", MAX_LEVERAGE),
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
        "momentumPct": thesis.get("momentum_pct") or 0.0,
        "smDirection": thesis.get("sm_direction") or "NONE",
        "smTiltPct": thesis.get("sm_tilt_pct") or 0.0,
        "volumeTrendPct": thesis.get("volume_trend_pct") or 0.0,
        "conversionEvent": True,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=coin,
            direction=thesis["direction"],
            score=min(thesis["score"] / 8.0, 1.0),
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
# MAIN — detect conversions, score momentum, fire the best
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()
    now = time.time()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_falcon_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_falcon_producer_version": VERSION})
        return

    scan = scan_instruments(config)
    prev_state = cfg.read_class_state()
    # Always reconcile (persists the class cache + stamps flips) even on the
    # very first tick — the first tick just seeds the cache with no flips.
    in_window = reconcile_conversions(scan, prev_state, config, now)

    if not in_window:
        cfg.output({
            "status": "ok",
            "note": "WAITING — no IPOP→equity conversion inside the eligibility window",
            "tracked_instruments": len(scan),
            "ipops_now": [n for n, i in scan.items() if i["class"] == "IPOP"],
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_falcon_producer_version": VERSION,
        })
        return

    candidates = []
    for name in sorted(in_window):
        if name.upper() in held_set or cfg.was_recently_signaled(name):
            continue
        info = scan.get(name)
        if not info:
            continue  # converted name no longer listed
        thesis = build_thesis(name, info, config)
        if thesis and thesis["score"] >= int(config.get("minScore", DEFAULT_MIN_SCORE)):
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok",
            "note": "WAITING — conversion(s) in window but no confirmed post-conversion momentum",
            "conversions_in_window": sorted(in_window),
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_falcon_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    margin_pct = float(config.get("marginPct", 0.15))
    config_leverage = int(config.get("leverage", DEFAULT_LEVERAGE))
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
            "momentum_pct": best["momentum_pct"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "reasons": best["reasons"][:5],
        },
        "conversions_in_window": sorted(in_window),
        "candidates_count": len(candidates),
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_falcon_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=600,   # 10min — conversions are rare; a window lasts days
        name=f"falcon-producer-{_wallet_lock_id}",
        tick_timeout=240,
    )
