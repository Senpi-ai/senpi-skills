#!/usr/bin/env python3
# Senpi ELEPHANT Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""ELEPHANT v1.0.0 Producer — global macro, two books, one script.

Elephant trades the CROSS-ASSET MACRO COMPLEX — equity indices, precious
metals, energy, FX (all on XYZ) plus BTC as the macro risk asset — which moves
on macro regime, not crypto noise. ONE producer script serves both books; the
ELEPHANT_LEG env var selects which:

  ELEPHANT_LEG=trend  Macro trend book (BOTH directions).
    Rides the medium-term multi-timeframe trend on the macro whitelist —
    LONG clean uptrends, SHORT clean downtrends. Macro trends (oil on
    geopolitics, indices on the AI-equity bid, metals on risk-off) are slow
    and clean. Wide let-it-run DSL.

  ELEPHANT_LEG=fade   Macro mean-reversion book (BOTH directions).
    Fades short-TF stretch + RSI extremes on the same macro whitelist back
    toward the mean, with a higher-TF regime filter so it never fades a
    strong macro trend. Tight fast-capture DSL.

The edge is GLOBAL MACRO: trade the durable macro direction (trend book) and
the macro over-extensions (fade book). Aware of the themes (oil/Iran,
AI-equity strength) and trading them AS macro, not as momentum chases. NOT a
copy-trader. Each book scores its own universe and pushes signals via
SenpiClient.push_signal(); runtime owns the LLM gate (pass-through), DSL
exits, and all risk.guard_rails.

Environment / config resolution:
  ELEPHANT_LEG          — REQUIRED. "trend" or "fade".
  SENPI_AUTH_TOKEN      — REQUIRED. Bearer token for MCP + signal POST.
  ELEPHANT_TREND_WALLET — trend-book strategy wallet (or config.wallet)
  ELEPHANT_FADE_WALLET  — fade-book strategy wallet (or config.wallet)
  ELEPHANT_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL         — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elephant_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "trend" | "fade"
SCANNER_NAME = f"elephant_{LEG}_signals"
SIGNAL_TYPE = "ELEPHANT_MACRO_TREND" if LEG == "trend" else "ELEPHANT_MACRO_FADE"

# Max raw score ~ 8 (trend) / 7 (fade).
NORM_DIV = 9.0

# Cross-asset macro whitelist (config.allowedAssets overrides). XYZ indices /
# metals / energy / FX + BTC as the macro risk asset. Names not live on the
# instrument board are filtered out at universe-build time.
_MACRO_WHITELIST = [
    "BTC",
    "xyz:SP500", "xyz:XYZ100", "xyz:JP225", "xyz:KR200", "xyz:NIFTY", "xyz:IBOV",
    "xyz:GOLD", "xyz:SILVER", "xyz:PLATINUM", "xyz:COPPER",
    "xyz:BRENTOIL", "xyz:CL", "xyz:NATGAS",
    "xyz:EUR", "xyz:JPY", "xyz:GBP", "xyz:DXY",
]

_DEFAULTS = {
    "trend": {
        "allowedAssets": _MACRO_WHITELIST,
        "minScore": 5,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "momThresholdPct": 1.5,   # 24h move for momentum confirmation (macro is slow)
        "rsiOverbought": 75,
        "rsiOversold": 25,
    },
    "fade": {
        "allowedAssets": _MACRO_WHITELIST,
        "minScore": 4,
        "marginPct": 0.15,
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "rsiOversold": 30,
        "rsiOverbought": 70,
        "stretchThresholdPct": 1.0,   # macro stretch from the 20-bar MA
    },
}[LEG]


def _resolve_wallet():
    wallet, _ = cfg.get_wallet_and_strategy()
    return wallet


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# Technical helpers
# ═══════════════════════════════════════════════════════════════

def _close(c):
    return float(c.get("close", c.get("c", 0)) or 0)


def _high(c):
    return float(c.get("high", c.get("h", 0)) or 0)


def _low(c):
    return float(c.get("low", c.get("l", 0)) or 0)


def trend_structure(candles, lookback=6):
    if len(candles) < lookback:
        return "NEUTRAL", 0
    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    elif lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
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


def simple_ma(closes, period):
    if not closes:
        return 0
    window = closes[-period:] if len(closes) >= period else closes
    return sum(window) / len(window)


# ═══════════════════════════════════════════════════════════════
# Data fetchers
# ═══════════════════════════════════════════════════════════════

def _dex_for(asset):
    return "xyz" if asset.lower().startswith("xyz:") else ""


def get_universe_meta():
    data = cfg.mcp_call("market_list_instruments")
    out, canonical = {}, []
    if not data:
        return out, canonical
    insts = data.get("data", data)
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name") or inst.get("context", {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
        canonical.append(name)
    return out, canonical


def fetch_candles(asset, intervals):
    data = cfg.mcp_call(
        "market_get_asset_data",
        asset=asset,
        candle_intervals=intervals,
        dex=_dex_for(asset),
        include_funding=False,
        include_order_book=False,
    )
    if not data or not data.get("success", True):
        return None
    d = data.get("data", data)
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def ret_24h(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if prev <= 0 or mark <= 0:
        return 0.0
    return (mark - prev) / prev * 100.0


# ═══════════════════════════════════════════════════════════════
# TREND scoring — ride the macro multi-TF trend, both directions
# ═══════════════════════════════════════════════════════════════

def score_trend(asset, meta, config):
    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 8 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    trend4, s4 = trend_structure(c4)
    trend1, s1 = trend_structure(c1)
    rsi = calc_rsi(closes1)
    own = ret_24h(meta)
    mom = float(config.get("momThresholdPct", _DEFAULTS["momThresholdPct"]))
    rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
    rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))

    # The 4h structure IS the macro trend; a NEUTRAL macro = no clean trend.
    if trend4 == "BULLISH":
        direction = "LONG"
    elif trend4 == "BEARISH":
        direction = "SHORT"
    else:
        return None

    score = 3
    reasons = [f"4h_{trend4.lower()}_{s4:.0%}"]

    if (direction == "LONG" and trend1 == "BULLISH") or (direction == "SHORT" and trend1 == "BEARISH"):
        score += 2
        reasons.append(f"1h_confirm_{s1:.0%}")
    elif (direction == "LONG" and trend1 == "BEARISH") or (direction == "SHORT" and trend1 == "BULLISH"):
        score -= 1
        reasons.append("1h_against")

    if direction == "LONG":
        if own >= mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own >= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if rsi < rsi_ob:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")
    else:
        if own <= -mom:
            score += 2
            reasons.append(f"mom_{own:+.1f}%")
        elif own <= 0:
            score += 1
            reasons.append(f"mom_{own:+.1f}%")
        if rsi > rsi_os:
            score += 1
            reasons.append(f"rsi_room_{rsi:.0f}")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "own24h": own,
    }


# ═══════════════════════════════════════════════════════════════
# FADE scoring — fade macro over-extensions, both directions
# ═══════════════════════════════════════════════════════════════

def score_fade(asset, meta, config):
    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    if len(c1) < 22 or len(c4) < 6:
        return None
    closes1 = [_close(c) for c in c1]
    price = closes1[-1]
    ma = simple_ma(closes1, 20)
    stretch = ((price - ma) / ma * 100) if ma > 0 else 0
    rsi = calc_rsi(closes1)
    trend4, _ = trend_structure(c4)
    rsi_os = float(config.get("rsiOversold", _DEFAULTS["rsiOversold"]))
    rsi_ob = float(config.get("rsiOverbought", _DEFAULTS["rsiOverbought"]))
    st = float(config.get("stretchThresholdPct", _DEFAULTS["stretchThresholdPct"]))

    oversold_mag = max(rsi_os - rsi, 0) / max(rsi_os, 1) + max(-stretch, 0) / st
    overbought_mag = max(rsi - rsi_ob, 0) / max(100 - rsi_ob, 1) + max(stretch, 0) / st
    if oversold_mag <= 0 and overbought_mag <= 0:
        return None
    direction = "LONG" if oversold_mag >= overbought_mag else "SHORT"

    score = 0
    reasons = []
    if direction == "LONG":
        if rsi <= 20:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_os")
        elif rsi <= 25:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_os")
        elif rsi <= rsi_os:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_os")
        if -stretch >= 2 * st:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif -stretch >= st:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        # Regime filter: never fade a strong macro downtrend (knife guard).
        if trend4 == "BULLISH":
            score += 1
            reasons.append("macro_uptrend_dip")
        elif trend4 == "BEARISH":
            score -= 2
            reasons.append("macro_downtrend_knife")
    else:
        if rsi >= 80:
            score += 3
            reasons.append(f"rsi_{rsi:.0f}_deep_ob")
        elif rsi >= 75:
            score += 2
            reasons.append(f"rsi_{rsi:.0f}_ob")
        elif rsi >= rsi_ob:
            score += 1
            reasons.append(f"rsi_{rsi:.0f}_ob")
        if stretch >= 2 * st:
            score += 2
            reasons.append(f"stretch_{stretch:+.2f}%")
        elif stretch >= st:
            score += 1
            reasons.append(f"stretch_{stretch:+.2f}%")
        if trend4 == "BEARISH":
            score += 1
            reasons.append("macro_downtrend_rip")
        elif trend4 == "BULLISH":
            score -= 2
            reasons.append("macro_uptrend_knife")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price, "rsi": rsi,
        "trend4h": trend4, "stretchPct": stretch,
    }


# ═══════════════════════════════════════════════════════════════
# Leverage clamp + emit
# ═══════════════════════════════════════════════════════════════

def clamp_leverage(desired, meta):
    venue = (meta or {}).get("max_leverage")
    try:
        venue = int(venue)
    except (TypeError, ValueError):
        venue = desired
    if venue <= 0:
        venue = desired
    return max(1, min(int(desired), venue))


def push_signal(thesis, margin_usd, leverage, held_assets):
    if not STRATEGY_ADDRESS:
        cfg.log("ERROR: strategy wallet not resolved")
        return False
    if thesis["coin"].upper() in {h.upper() for h in held_assets}:
        return False

    data_block = {
        "score": thesis["score"],
        "leverage": leverage,
        "marginUsd": margin_usd,
        "direction": thesis["direction"],
        "reasons": thesis["reasons"],
        "heldAssets": held_assets,
        "trend4h": thesis.get("trend4h"),
    }
    if LEG == "trend":
        data_block["own24h"] = round(thesis.get("own24h", 0), 2)
    else:
        data_block["stretchPct"] = round(thesis.get("stretchPct", 0), 3)
        data_block["rsi"] = round(thesis.get("rsi", 0), 1)

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=thesis["coin"],
            direction=thesis["direction"],
            score=min(thesis["score"] / NORM_DIV, 1.0),
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        cfg.log(f"INGEST_REJECTED {thesis['coin']}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        cfg.log(f"INGEST_EXCEPTION {thesis['coin']}: {type(e).__name__}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# Universe — static macro whitelist, intersected with the live board
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map):
    """Macro whitelist (config.allowedAssets) intersected with the live
    instrument board so dead/unavailable names are skipped."""
    wl = config.get("allowedAssets", _DEFAULTS["allowedAssets"])
    out = []
    for name in wl:
        if not isinstance(name, str):
            continue
        if meta_map.get(name) or meta_map.get(name.upper()):
            out.append(name)
    return out


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_elephant_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_elephant_producer_version": VERSION})
        return

    min_score = config.get("minScore", _DEFAULTS["minScore"])
    margin_pct = config.get("marginPct", _DEFAULTS["marginPct"])
    max_lev = config.get("maxLeverage", _DEFAULTS["maxLeverage"])
    max_slots = config.get("maxSlots", _DEFAULTS["maxSlots"])
    min_notional = max(account_value * float(config.get("minNotionalPctOfEquity", 0.01)), float(config.get("venueMinNotionalUsd", 10)))  # scales with budget; floor = HL venue minimum order value

    open_slots = max_slots - len(held_assets)
    if open_slots <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "slots full",
                    "held_assets": held_assets, "max_slots": max_slots,
                    "_elephant_producer_version": VERSION})
        return

    meta_map, _canonical = get_universe_meta()
    universe = build_universe(config, meta_map)

    candidates = []
    recently_skipped = []
    for name in universe:
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        if not meta:
            continue
        thesis = score_trend(name, meta, config) if LEG == "trend" else score_fade(name, meta, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG,
            "scanned": len(universe), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no macro name cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_elephant_producer_version": VERSION,
        })
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    margin_usd = round(account_value * margin_pct, 2)
    # Cap emissions to what the wallet can actually FUND — never emit an entry we
    # can't afford. Without this, an open slot with no free margin re-emits an
    # un-fillable order every tick (insufficient-funds create_position spam).
    # free margin = equity minus on-chain committed margin (marginUsed).
    free_margin = max(0.0, account_value - sum(p.get("margin", 0) for p in positions))
    affordable = int(free_margin / (margin_usd * 1.1)) if margin_usd > 0 else 0  # 1.1 = fee/slippage headroom
    to_emit = candidates[:min(open_slots, affordable)]

    pushed = 0
    emitted = []
    for th in to_emit:
        leverage = clamp_leverage(max_lev, th["_meta"])
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        if push_signal(th, margin_usd, leverage, held_assets):
            pushed += 1
            cfg.record_signal(th["coin"])
            emitted.append({
                "coin": th["coin"], "direction": th["direction"],
                "score": th["score"], "leverage": leverage,
                "margin_usd": margin_usd, "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "scanned": len(universe), "candidates": len(candidates),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_elephant_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"elephant-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
