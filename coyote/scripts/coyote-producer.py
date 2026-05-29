#!/usr/bin/env python3
# Senpi COYOTE Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""COYOTE v1.0.0 — Regime Classifier / Meta-Router.

Coyote watches macro conditions and classifies the market into TREND_UP /
TREND_DOWN / CHOP, based on:

  - BTC 7d move magnitude (trend strength)
  - BTC realized volatility (recent stdev of log returns)
  - Cross-asset dispersion (standard deviation of recent returns across the
    monitored universe — high = mixed market, low = synchronized)

Regime rules (in order):

  TREND_UP   : btc_7d_move >= trendUpThresholdPct AND realized_vol <= maxVolForTrendPct
  TREND_DOWN : btc_7d_move <= -trendDownThresholdPct AND realized_vol >= minVolForCrashPct
  CHOP       : anything else

In TREND_UP, Coyote takes a LONG BTC position. In TREND_DOWN, SHORT BTC.
In CHOP, no signal — but the regime is still published in the producer
output so operators can read Coyote's regime view at any tick.

NEW archetype #16: Regime classifier / meta-router. The "meta" framing is
aspirational — once the runtime supports regime-subscription, other agents
will be able to gate on Coyote's regime channel directly.

Architecture: helpers-native producer + balanced DSL. Stateless regime
computation + race-window dedup + 6h per-asset cooldown (regime regimes
don't flip in five minutes). Producer NEVER closes — DSL owns exits.

REQUIRES USER-SCOPE AUTH for leaderboard_get_markets.
"""

import hashlib
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coyote_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402

VERSION = "1.0.0"
SCANNER_NAME = "coyote_signals"
SIGNAL_TYPE = "COYOTE_REGIME"

MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3

DEFAULT_BTC = "BTC"
DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL", "HYPE"]   # used for dispersion

# 4h-bar lookbacks:
DEFAULT_TREND_LOOKBACK = 42            # 7d
DEFAULT_VOL_LOOKBACK = 42              # 7d realized vol
DEFAULT_DISPERSION_LOOKBACK = 24       # 4d dispersion

# Regime thresholds (operator-tunable; defaults conservative):
DEFAULT_TREND_UP_THRESHOLD_PCT = 5.0
DEFAULT_TREND_DOWN_THRESHOLD_PCT = 5.0   # symmetric default
DEFAULT_MAX_VOL_FOR_TREND_PCT = 80.0     # annualized realized vol cap for TREND_UP
DEFAULT_MIN_VOL_FOR_CRASH_PCT = 60.0     # vol floor for TREND_DOWN to confirm crash regime


def _resolve_wallet():
    env_val = (os.environ.get("COYOTE_WALLET") or "").strip()
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
# Pure regime-classification logic (unit-tested in tests/test_signal.py)
# ═══════════════════════════════════════════════════════════════

def pct_move(closes, lookback):
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def realized_vol_pct(closes, lookback, bars_per_year=2190):
    """Annualized realized volatility (stdev of log returns × sqrt(N)),
    expressed as a percentage. `bars_per_year` defaults to 2190 (4h bars,
    24/7 trading). None if insufficient data."""
    if not closes or len(closes) < lookback + 1:
        return None
    series = closes[-(lookback + 1):]
    log_returns = []
    for prev, curr in zip(series[:-1], series[1:]):
        if prev is None or prev <= 0 or curr is None or curr <= 0:
            continue
        log_returns.append(math.log(curr / prev))
    if len(log_returns) < 2:
        return None
    mean = sum(log_returns) / len(log_returns)
    var = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    stdev = math.sqrt(var)
    return stdev * math.sqrt(bars_per_year) * 100.0


def dispersion_pct(returns_by_asset):
    """Cross-sectional dispersion: stdev of returns across assets.
    `returns_by_asset` is a dict {asset: recent_return_pct}.
    High dispersion = mixed market; low = synchronized. Returns None if
    fewer than 2 assets have data."""
    values = [r for r in returns_by_asset.values() if r is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def classify_regime(btc_7d_pct, realized_vol_pct_value,
                    trend_up_threshold, trend_down_threshold,
                    max_vol_for_trend, min_vol_for_crash):
    """Classify the market regime from BTC trend strength + realized vol.

    Returns one of: "TREND_UP" | "TREND_DOWN" | "CHOP" | "UNKNOWN".
    UNKNOWN is returned if required inputs are missing.
    """
    if btc_7d_pct is None or realized_vol_pct_value is None:
        return "UNKNOWN"
    if btc_7d_pct >= trend_up_threshold and realized_vol_pct_value <= max_vol_for_trend:
        return "TREND_UP"
    if btc_7d_pct <= -trend_down_threshold and realized_vol_pct_value >= min_vol_for_crash:
        return "TREND_DOWN"
    return "CHOP"


def regime_to_direction(regime):
    """Map a regime classification to an entry direction. None if no trade."""
    if regime == "TREND_UP":
        return "LONG"
    if regime == "TREND_DOWN":
        return "SHORT"
    return None


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def fetch_candles(asset):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=["4h"],
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return []
    return data.get("data", {}).get("candles", {}).get("4h", [])


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def push_signal(direction, regime, btc_move, vol, dispersion, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        return False
    if DEFAULT_BTC in {h.upper() for h in held_assets}:
        return False
    data_block = {
        "score": 5,
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": direction,
        "reasons": [f"regime_{regime}", f"btc_7d_{btc_move:+.1f}%", f"vol_{vol:.0f}%"],
        "regime": regime,
        "btc7dMovePct": float(btc_move) or 0.0,
        "realizedVolPct": float(vol) or 0.0,
        "dispersionPct": float(dispersion) if dispersion is not None else 0.0,
        "heldAssets": held_assets,
    }
    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=DEFAULT_BTC,
            direction=direction,
            score=0.7,
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED BTC: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"INGEST_EXCEPTION BTC: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    run_start = time.time()
    config = cfg.load_config()
    btc_asset = config.get("btcAsset", DEFAULT_BTC)
    universe = config.get("dispersionUniverse", DEFAULT_UNIVERSE)

    trend_lb = int(config.get("trendLookbackBars", DEFAULT_TREND_LOOKBACK))
    vol_lb = int(config.get("volLookbackBars", DEFAULT_VOL_LOOKBACK))
    disp_lb = int(config.get("dispersionLookbackBars", DEFAULT_DISPERSION_LOOKBACK))

    trend_up_th = float(config.get("trendUpThresholdPct", DEFAULT_TREND_UP_THRESHOLD_PCT))
    trend_dn_th = float(config.get("trendDownThresholdPct", DEFAULT_TREND_DOWN_THRESHOLD_PCT))
    max_vol_trend = float(config.get("maxVolForTrendPct", DEFAULT_MAX_VOL_FOR_TREND_PCT))
    min_vol_crash = float(config.get("minVolForCrashPct", DEFAULT_MIN_VOL_FOR_CRASH_PCT))

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "_coyote_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value", "_coyote_producer_version": VERSION})
        return

    # Fetch BTC candles for trend + vol
    btc_candles = fetch_candles(btc_asset)
    btc_closes = [_f(c, "close", "c") for c in btc_candles]
    btc_move = pct_move(btc_closes, trend_lb)
    btc_vol = realized_vol_pct(btc_closes, vol_lb)

    # Fetch all universe candles for dispersion (each asset's recent return)
    returns_by_asset = {}
    for a in universe:
        cd = fetch_candles(a) if a != btc_asset else btc_candles
        closes = [_f(c, "close", "c") for c in cd]
        returns_by_asset[a] = pct_move(closes, disp_lb)
    disp = dispersion_pct(returns_by_asset)

    regime = classify_regime(btc_move, btc_vol, trend_up_th, trend_dn_th, max_vol_trend, min_vol_crash)
    direction = regime_to_direction(regime)

    # Always publish regime view (even when no trade)
    if direction is None:
        cfg.output({
            "status": "ok",
            "note": f"REGIME={regime} — no positional expression (CHOP or UNKNOWN)",
            "regime": regime,
            "btc_7d_move_pct": round(btc_move, 2) if btc_move is not None else None,
            "realized_vol_pct": round(btc_vol, 1) if btc_vol is not None else None,
            "dispersion_pct": round(disp, 2) if disp is not None else None,
            "returns_by_asset": {k: (round(v, 2) if v is not None else None) for k, v in returns_by_asset.items()},
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_coyote_producer_version": VERSION,
        })
        return

    if btc_asset.upper() in {h.upper() for h in held_assets} or cfg.was_recently_signaled(btc_asset):
        cfg.output({
            "status": "ok",
            "note": f"REGIME={regime}, direction={direction}, but BTC already held or recently signaled",
            "regime": regime,
            "held_assets": held_assets,
            "elapsed_sec": round(time.time() - run_start, 2),
            "_coyote_producer_version": VERSION,
        })
        return

    margin_pct = float(config.get("marginPct", 0.25))
    leverage = min(int(config.get("leverage", DEFAULT_LEVERAGE)), MAX_LEVERAGE)
    margin_usd = round(account_value * margin_pct, 2)

    pushed = push_signal(direction, regime, btc_move, btc_vol, disp, margin_usd, leverage, held_assets)
    if pushed:
        cfg.record_signal(btc_asset)

    cfg.output({
        "status": "ok",
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": btc_asset,
            "direction": direction,
            "regime": regime,
            "btc_7d_move_pct": round(btc_move, 2),
            "realized_vol_pct": round(btc_vol, 1),
            "dispersion_pct": round(disp, 2) if disp is not None else None,
            "leverage": leverage,
            "margin_usd": margin_usd,
        },
        "held_assets": held_assets,
        "elapsed_sec": round(time.time() - run_start, 2),
        "_coyote_producer_version": VERSION,
    })


if __name__ == "__main__":
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=900,   # 15min — regimes don't flip in 5 minutes
        name=f"coyote-producer-{_wallet_lock_id}",
        tick_timeout=180,
    )
