#!/usr/bin/env python3
# Senpi BISON Producer v3.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""BISON v3.0.0 Producer — Conviction Holder, senpi_runtime_helpers migration.

v3.0.0 (2026-05-12): plumbing migration from v2.1. NO thesis change.
v2.1 asset whitelist (BTC/ETH/SOL), minScore 11, conviction-scaled
margin (25%/31%/37%), direction waterfall (4H trend → SM → 1H
momentum), all preserved verbatim. The producer no longer executes —
it emits scored signals and the runtime owns:
  - LLM gate (decision_mode: llm, pass-through)
  - risk.guard_rails — max_entries_per_day, per_asset_cooldown,
    daily_loss_limit, drawdown_halt, consecutive_losses
  - DSL exits via FEE_OPTIMIZED_LIMIT (replaces v2.x MARKET exits)

What v2.1 did that v3.0 does NOT do:
  - call create_position directly         → runtime opens via signal flow
  - track entries in state/trade-counter  → runtime guard_rails enforce
  - manage per-asset cooldowns in Python  → runtime per_asset_cooldown
  - dynamic-slots PnL-based daily cap     → static max_entries_per_day=3
                                            + drawdown_halt_pct=25
  - hardcoded DSL state template          → DSL preset in runtime.yaml

What v2.1 did that v3.0 STILL DOES (preserved verbatim):
  - Asset whitelist: BTC/ETH/SOL (override via "allowedAssets" in config)
  - MIN_SCORE 11 (real conviction, not first-bar-crossing)
  - Score components: 4H trend (+3/-1), 1H trend (+2/-1), 1H momentum
    (+1/+2/-1), SM alignment (+2/-2), funding (+2/-1), volume (+1),
    OI proxy (+1), RSI (+1/-1), 4H momentum (+1)
  - Direction waterfall: 4H trend → SM → 1H momentum
  - Conviction-scaled margin: score 8-9 → 25%, 10-11 → 31%, 12+ → 37%
  - MAX_LEVERAGE 10, MIN_LEVERAGE 7, default 10
  - XYZ banned at producer scan level
  - 5-min producer tick cadence

Environment / config resolution:
  Strategy wallet is read from config/bison-config.json (the canonical
  source). BISON_WALLET env var is supported as an optional override
  but is NOT required for routine daemon runs.

  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  BISON_WALLET          — optional override; defaults to config.wallet
  BISON_DECISION_MODEL  — bare LLM model name (no provider prefix);
                          resolved into runtime.yaml at create time
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bison_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "3.0.2"
SCANNER_NAME = "bison_signals"
SIGNAL_TYPE = "BISON_CONVICTION_HOLDER"

# v3.0.1: each push_signal() success is recorded via
# cfg.record_signal(coin). main() skips any coin seen in the cache
# within RECENT_SIGNAL_TTL_SEC (default 180s) BEFORE scoring. Covers
# the race window where a freshly-pushed signal hasn't yet appeared
# in the next-tick clearinghouse pull but has already triggered a
# runtime LLM-gate fire. Pre-v3.0.1 audit (M192943, 2026-05-13 to
# 2026-05-17): 16 BISON_CONVICTION ENGINE_FAILURE retries on held
# assets — all eliminated by this cache.


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2.1
# ═══════════════════════════════════════════════════════════════

MAX_LEVERAGE = 10
MIN_LEVERAGE = 7
XYZ_BANNED = True

# v2.1: asset whitelist default. Override via "allowedAssets" in config.
ALLOWED_ASSETS_DEFAULT = ["BTC", "ETH", "SOL"]


def _resolve_wallet():
    """Resolve strategy wallet — env var first, config.json second."""
    env_val = (os.environ.get("BISON_WALLET") or "").strip()
    if env_val:
        return env_val
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers — preserved verbatim from v2.1
# ═══════════════════════════════════════════════════════════════

def price_momentum(candles, n_bars=1):
    if len(candles) < n_bars + 1:
        return 0
    old = float(candles[-(n_bars + 1)].get("close", candles[-(n_bars + 1)].get("c", 0)))
    new = float(candles[-1].get("close", candles[-1].get("c", 0)))
    if old == 0:
        return 0
    return ((new - old) / old) * 100


def trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH."""
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
    vols = [float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-(lookback + 2):]]
    half = lookback // 2
    recent = sum(vols[-half:]) / half if half > 0 else 1
    earlier = sum(vols[:half]) / half if half > 0 else 1
    if earlier == 0:
        return 0
    return ((recent - earlier) / earlier) * 100


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


# ═══════════════════════════════════════════════════════════════
# Data fetchers — preserved (mcporter_call now routes via SenpiClient)
# ═══════════════════════════════════════════════════════════════

def get_top_assets(n=10, allowed_assets=None):
    """Return assets to evaluate, sorted by 24h notional volume.

    v2.1: filters to ALLOWED_ASSETS_DEFAULT (BTC/ETH/SOL) by default.
    Operators can override via "allowedAssets" key in bison-config.json.
    """
    data = cfg.mcp_call("market_list_instruments")
    if not data or not data.get("success"):
        return []
    instruments = data.get("data", data)
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])

    allow_set = {a.upper() for a in (allowed_assets or ALLOWED_ASSETS_DEFAULT)}

    assets = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        coin = inst.get("coin") or inst.get("name", "")
        if not coin or coin.upper() not in allow_set:
            continue
        ctx = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        vol = float(ctx.get("dayNtlVlm", ctx.get("volume24h",
                             inst.get("dayNtlVlm", inst.get("volume24h", 0)))) or 0)
        mark_px = float(ctx.get("markPx", ctx.get("midPx",
                                 inst.get("markPx", inst.get("midPx", 0)))) or 0)
        if vol > 0:
            assets.append({"coin": coin, "volume": vol, "price": mark_px})
    assets.sort(key=lambda x: x["volume"], reverse=True)
    return assets[:n]


def get_sm_direction(coin):
    data = cfg.mcp_call("leaderboard_get_markets")
    if not data or not data.get("success"):
        return None, 0
    markets = data.get("data", data)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])

    coin_long_pct = 0
    coin_short_pct = 0
    found = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if token != coin:
            continue
        found = True
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        if direction == "long":
            coin_long_pct = pct
        elif direction == "short":
            coin_short_pct = pct

    if not found:
        return None, 0
    total = coin_long_pct + coin_short_pct
    if total == 0:
        return "NEUTRAL", 50
    long_ratio = (coin_long_pct / total) * 100 if total > 0 else 50
    if long_ratio > 58:
        return "LONG", long_ratio
    elif long_ratio < 42:
        return "SHORT", 100 - long_ratio
    return "NEUTRAL", 50


# ═══════════════════════════════════════════════════════════════
# Thesis builder — preserved verbatim from v2.1
# ═══════════════════════════════════════════════════════════════

def build_thesis(coin, entry_cfg):
    """Build conviction thesis. ALL signals are score contributors.
    The minScore threshold is the only gate."""
    data = cfg.mcp_call("market_get_asset_data", asset=coin,
                        candle_intervals=["15m", "1h", "4h"],
                        include_funding=True, include_order_book=False)
    if not data or not data.get("success"):
        return None

    candles_15m = data.get("data", {}).get("candles", {}).get("15m", [])
    candles_1h = data.get("data", {}).get("candles", {}).get("1h", [])
    candles_4h = data.get("data", {}).get("candles", {}).get("4h", [])
    asset_ctx = data.get("data", {}).get("asset_context", data.get("data", {}))
    funding = float(asset_ctx.get("funding", data.get("data", {}).get("funding", 0)))

    if len(candles_1h) < 8 or len(candles_4h) < 4:
        return None

    price = float(candles_15m[-1].get("close", candles_15m[-1].get("c", 0))) if candles_15m else 0

    trend_4h, trend_strength = trend_structure(candles_4h)
    trend_1h, trend_1h_strength = trend_structure(candles_1h)
    sm_dir, sm_pct = get_sm_direction(coin)
    mom_1h = price_momentum(candles_1h, 2)

    # Direction waterfall: 4H trend → SM direction → 1H momentum
    direction = None
    direction_source = None
    if trend_4h == "BULLISH":
        direction = "LONG"
        direction_source = "4h_trend"
    elif trend_4h == "BEARISH":
        direction = "SHORT"
        direction_source = "4h_trend"
    elif sm_dir and sm_dir != "NEUTRAL":
        direction = sm_dir
        direction_source = "sm_direction"
    elif mom_1h > 0.5:
        direction = "LONG"
        direction_source = "1h_momentum"
    elif mom_1h < -0.5:
        direction = "SHORT"
        direction_source = "1h_momentum"

    if direction is None:
        return None

    score = 0
    reasons = []

    # 4H trend structure (+3 / -1)
    if trend_4h != "NEUTRAL":
        if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
            score += 3
            reasons.append(f"4h_{trend_4h.lower()}_{trend_strength:.0%}")
        else:
            score -= 1
            reasons.append(f"4h_opposing_{trend_4h.lower()}")

    # 1H trend agreement (+2 / -1)
    if trend_1h != "NEUTRAL":
        if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
            score += 2
            reasons.append(f"1h_confirms_{trend_1h.lower()}")
        else:
            score -= 1
            reasons.append(f"1h_opposing_{trend_1h.lower()}")

    # 1H momentum (+2 / +1 / -1)
    if direction == "LONG":
        if mom_1h >= 1.0:
            score += 2; reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h >= 0.5:
            score += 1; reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h < -0.5:
            score -= 1; reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")
    else:
        if mom_1h <= -1.0:
            score += 2; reasons.append(f"1h_strong_momentum_{mom_1h:+.2f}%")
        elif mom_1h <= -0.5:
            score += 1; reasons.append(f"1h_momentum_{mom_1h:+.2f}%")
        elif mom_1h > 0.5:
            score -= 1; reasons.append(f"1h_counter_momentum_{mom_1h:+.2f}%")

    # SM alignment (±2)
    if sm_dir == direction:
        score += 2
        reasons.append(f"sm_aligned_{sm_pct:.0f}%")
    elif sm_dir and sm_dir != "NEUTRAL" and sm_dir != direction:
        score -= 2
        reasons.append(f"sm_opposing_{sm_dir}")

    # Funding alignment (+2 / -1)
    if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0):
        score += 2
        reasons.append(f"funding_aligned_{funding:+.4f}")
    elif (direction == "LONG" and funding > 0.01) or (direction == "SHORT" and funding < -0.005):
        score -= 1
        reasons.append("funding_crowded")

    # Volume trend (+1)
    vol_1h = volume_trend(candles_1h)
    if vol_1h > entry_cfg.get("minVolTrendPct", 10):
        score += 1
        reasons.append(f"vol_rising_{vol_1h:+.0f}%")

    # OI proxy (+1)
    vol_recent = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-3:])
    vol_earlier = sum(float(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles_1h[-6:-3])
    oi_proxy = ((vol_recent - vol_earlier) / vol_earlier * 100) if vol_earlier > 0 else 0
    if oi_proxy > 10:
        score += 1
        reasons.append(f"oi_growing_{oi_proxy:+.0f}%")

    # RSI (+1 / -1)
    closes_1h = [float(c.get("close", c.get("c", 0))) for c in candles_1h]
    rsi = calc_rsi(closes_1h)
    if direction == "LONG" and rsi > entry_cfg.get("rsiMaxLong", 72):
        score -= 1
        reasons.append(f"rsi_overbought_{rsi:.0f}")
    elif direction == "SHORT" and rsi < entry_cfg.get("rsiMinShort", 28):
        score -= 1
        reasons.append(f"rsi_oversold_{rsi:.0f}")
    elif (direction == "LONG" and rsi < 55) or (direction == "SHORT" and rsi > 45):
        score += 1
        reasons.append(f"rsi_room_{rsi:.0f}")

    # 4H momentum (+1)
    mom_4h = price_momentum(candles_4h, 1)
    if abs(mom_4h) > 1.5:
        if (direction == "LONG" and mom_4h > 0) or (direction == "SHORT" and mom_4h < 0):
            score += 1
            reasons.append(f"4h_momentum_{mom_4h:+.1f}%")

    return {
        "coin": coin, "direction": direction, "score": score, "reasons": reasons,
        "directionSource": direction_source,
        "price": price, "trend_4h": trend_4h, "trend_1h": trend_1h,
        "momentum_1h": mom_1h, "momentum_4h": mom_4h,
        "sm_direction": sm_dir, "sm_pct": sm_pct, "funding": funding,
        "rsi": rsi, "volume_trend": vol_1h, "oi_proxy": oi_proxy,
    }


# ═══════════════════════════════════════════════════════════════
# Signal emit — v3.0 helpers-native (replaces create_position)
# ═══════════════════════════════════════════════════════════════

def fetch_held_assets():
    if not STRATEGY_ADDRESS:
        return []
    try:
        _, positions = cfg.get_positions(STRATEGY_ADDRESS)
        return [p["coin"] for p in positions if p.get("coin")]
    except Exception:
        return []


def push_signal(thesis, margin_usd, leverage, held_assets):
    """Emit a CONVICTION_HOLDER signal. asset/direction top-level routing,
    marginUsd + leverage in data block for LLM gate to pass through."""
    if not STRATEGY_ADDRESS:
        print("ERROR: strategy wallet not resolved", file=sys.stderr)
        return False

    # Defense in depth — runtime per_asset_cooldown is authoritative
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "directionSource": thesis["directionSource"],
        "reasons": thesis["reasons"],
        "trend4h": thesis["trend_4h"],
        "trend1h": thesis["trend_1h"],
        "momentum1hPct": thesis["momentum_1h"],
        "momentum4hPct": thesis["momentum_4h"],
        "smDirection": thesis["sm_direction"],
        "smPct": thesis["sm_pct"],
        "funding": thesis["funding"],
        "rsi": thesis["rsi"],
        "volumeTrendPct": thesis["volume_trend"],
        "oiProxyPct": thesis["oi_proxy"],
        "heldAssets": held_assets,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / 16.0, 1.0),   # normalize to 0..1 (max ~16)
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


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet",
                    "_bison_producer_version": VERSION})
        return

    # Pull account state + held positions
    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    if account_value <= 0:
        cfg.output({"status": "ok", "note": "no account value",
                    "_bison_producer_version": VERSION})
        return

    entry_cfg = config.get("entry", {})
    # v2.1: minScore raised 8 → 11 — real conviction, not first-bar-crossing.
    min_score = entry_cfg.get("minScore", 11)

    # v2.1: whitelist defaults to BTC/ETH/SOL; operators can override
    # via "allowedAssets" in config.
    top_n = config.get("topAssets", 10)
    allowed = config.get("allowedAssets", ALLOWED_ASSETS_DEFAULT)
    candidates = get_top_assets(top_n, allowed_assets=allowed)

    # Score all candidates; collect those at/above MIN_SCORE.
    # v3.0.1: skip coins recently signaled (race-window dedup) BEFORE
    # build_thesis() — avoids paying the per-coin MCP fetch cost when
    # we already know we won't emit.
    held_set = {h.upper() for h in held_assets}
    signals = []
    recently_skipped = []
    for asset in candidates:
        coin = asset["coin"]
        if coin.lower().startswith("xyz:"):
            continue
        if coin.upper() in held_set:
            continue
        if cfg.was_recently_signaled(coin):
            recently_skipped.append(coin)
            continue
        thesis = build_thesis(coin, entry_cfg)
        if thesis and thesis["score"] >= min_score:
            signals.append(thesis)

    if not signals:
        elapsed = time.time() - run_start
        cfg.output({
            "status": "ok",
            "scanned": len(candidates),
            "candidates": 0,
            "signals_pushed": 0,
            "min_score": min_score,
            "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no conviction thesis (min score {min_score})",
            "elapsed_sec": round(elapsed, 2),
            "_bison_producer_version": VERSION,
        })
        return

    signals.sort(key=lambda x: x["score"], reverse=True)
    best = signals[0]

    # Conviction-scaled margin (preserved from v2.1)
    base_margin_pct = entry_cfg.get("marginPctBase", 0.25)
    if best["score"] >= 12:
        margin_pct = base_margin_pct * 1.5    # 37.5%
    elif best["score"] >= 10:
        margin_pct = base_margin_pct * 1.25   # 31.25%
    else:
        margin_pct = base_margin_pct          # 25%
    margin_usd = round(account_value * margin_pct, 2)

    # Leverage (preserved from v2.1)
    leverage = min(config.get("leverage", {}).get("default", 10), MAX_LEVERAGE)
    leverage = max(leverage, MIN_LEVERAGE)

    # Push to runtime — LLM gate decides whether to open
    pushed = push_signal(best, margin_usd, leverage, held_assets)

    # v3.0.1: record successful push for race-window dedup. The
    # cache is checked at the top of the next tick BEFORE scoring,
    # so even if the position hasn't yet appeared in clearinghouse,
    # we won't fire a duplicate runtime LLM gate.
    if pushed:
        cfg.record_signal(best["coin"])

    elapsed = time.time() - run_start
    cfg.output({
        "status": "ok",
        "scanned": len(candidates),
        "candidates": len(signals),
        "signals_pushed": 1 if pushed else 0,
        "best": {
            "coin": best["coin"],
            "direction": best["direction"],
            "score": best["score"],
            "leverage": leverage,
            "margin_usd": margin_usd,
            "direction_source": best["directionSource"],
            "reasons": best["reasons"][:6],   # top reasons only
        },
        "held_assets": held_assets,
        "recently_signaled_skipped": recently_skipped,
        "elapsed_sec": round(elapsed, 2),
        "_bison_producer_version": VERSION,
    })


if __name__ == "__main__":
    # v3.0.0 — long-lived daemon. producer_daemon owns the per-tick
    # scanner_lock with stale-PID auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=300,                  # 5min — preserved from v2.1
        name=f"bison-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=180,
    )
