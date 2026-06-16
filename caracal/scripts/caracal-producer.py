#!/usr/bin/env python3
# Senpi CARACAL Producer v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills
"""CARACAL v1.0.0 Producer — volatility compression->expansion, two books.

Caracal harvests VOLATILITY: compression precedes expansion, and a breakout
FROM a low-volatility coil has higher follow-through than a breakout in
already-volatile tape. It trades MOVEMENT, not a directional view — each book
takes the breakout long or short, whichever way it breaks. ONE producer
script serves both books; the CARACAL_LEG env var selects which:

  CARACAL_LEG=breakout  Crypto vol compression->expansion book.
    Scans the liquid main-DEX crypto universe for names coiled in a
    low-volatility squeeze (recent ATR << baseline ATR) that break their
    recent range with an expansion surge, and rides the break direction.

  CARACAL_LEG=catalyst  XYZ event/catalyst vol book.
    Same engine on XYZ (equities / energy / metals / indices) — captures
    oil-geopolitics and AI-infra moves as direction-agnostic volatility
    events, 24/7 (XYZ trades through weekends).

Signal = (range breakout) + (compression precondition) + (expansion surge) +
(higher-TF agreement). Both directions. NOT a copy-trader. Each book scores
its own universe and pushes signals via SenpiClient.push_signal(); runtime
owns the LLM gate (pass-through), DSL exits, and all risk.guard_rails.

Environment / config resolution:
  CARACAL_LEG            — REQUIRED. "breakout" or "catalyst".
  SENPI_AUTH_TOKEN       — REQUIRED. Bearer token for MCP + signal POST.
  CARACAL_BREAKOUT_WALLET— breakout-book strategy wallet (or config.wallet)
  CARACAL_CATALYST_WALLET— catalyst-book strategy wallet (or config.wallet)
  CARACAL_DECISION_MODEL — bare LLM model name; resolved into runtime.yaml
  SENPI_MCP_URL          — optional, default https://mcp.prod.senpi.ai/mcp
"""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caracal_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


VERSION = "1.0.0"
LEG = cfg.LEG  # "breakout" | "catalyst"
SCANNER_NAME = f"caracal_{LEG}_signals"
SIGNAL_TYPE = "CARACAL_VOL_BREAKOUT" if LEG == "breakout" else "CARACAL_VOL_CATALYST"

# Score normalization divisor for the 0..1 ingest-ranking score. Max raw ~ 8.
NORM_DIV = 8.0

_DEFAULTS = {
    "breakout": {
        "minScore": 5,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 20000000,      # liquid main-DEX crypto
        "universeMaxNames": 20,        # bounds per-tick candle fetches
        "wantXyz": False,
        "breakoutBars": 20,            # prior-range lookback for the break
        "recentBars": 10,              # ATR window — "recent" vol
        "baseBars": 30,                # ATR window — baseline vol
        "squeezeTight": 0.70,          # recent/baseline ATR <= this = tight coil
        "squeezeLoose": 0.90,          # ... <= this = mild coil
        "surgeStrong": 2.0,            # breakout-bar TR / baseline ATR
        "surgeMod": 1.3,
    },
    "catalyst": {
        "minScore": 5,
        "marginPct": 0.18,
        "maxLeverage": 5,
        "maxSlots": 3,
        "venueMinNotionalUsd": 10,
        "minNotionalPctOfEquity": 0.01,
        "tickSeconds": 300,
        "volFloorUsd": 3000000,       # XYZ is less liquid than crypto majors
        "universeMaxNames": 15,
        "wantXyz": True,
        "breakoutBars": 20,
        "recentBars": 10,
        "baseBars": 30,
        "squeezeTight": 0.70,
        "squeezeLoose": 0.90,
        "surgeStrong": 2.0,
        "surgeMod": 1.3,
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


def true_range(c, prev_close):
    h, l = _high(c), _low(c)
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr(candles, period):
    """Average true range over the last `period` bars."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], _close(candles[i - 1])))
    w = trs[-period:] if len(trs) >= period else trs
    return sum(w) / len(w) if w else 0.0


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


def day_vol(meta):
    ctx = meta.get("ctx", {}) if meta else {}
    try:
        return float(ctx.get("dayNtlVlm", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════
# Volatility-breakout scoring — compression precedes expansion
# ═══════════════════════════════════════════════════════════════

def score_vol_breakout(asset, meta, config):
    """Detect a range breakout from a low-volatility coil and ride the break
    direction. Returns None if no breakout / not enough data."""
    look = int(config.get("breakoutBars", _DEFAULTS["breakoutBars"]))
    recent_bars = int(config.get("recentBars", _DEFAULTS["recentBars"]))
    base_bars = int(config.get("baseBars", _DEFAULTS["baseBars"]))
    sq_tight = float(config.get("squeezeTight", _DEFAULTS["squeezeTight"]))
    sq_loose = float(config.get("squeezeLoose", _DEFAULTS["squeezeLoose"]))
    surge_strong = float(config.get("surgeStrong", _DEFAULTS["surgeStrong"]))
    surge_mod = float(config.get("surgeMod", _DEFAULTS["surgeMod"]))

    md = fetch_candles(asset, ["1h", "4h"])
    if not md:
        return None
    c1 = md["candles"].get("1h", [])
    c4 = md["candles"].get("4h", [])
    need = max(look, base_bars) + 2
    if len(c1) < need or len(c4) < 6:
        return None
    highs = [_high(c) for c in c1]
    lows = [_low(c) for c in c1]
    closes = [_close(c) for c in c1]
    price = closes[-1]

    # Range breakout vs the prior `look` bars (excluding the current bar).
    prior_high = max(highs[-(look + 1):-1])
    prior_low = min(lows[-(look + 1):-1])
    broke_up = price > prior_high
    broke_dn = price < prior_low
    if not (broke_up or broke_dn):
        return None
    direction = "LONG" if broke_up else "SHORT"

    a_recent = atr(c1[-(recent_bars + 1):], recent_bars)
    a_base = atr(c1[-(base_bars + 1):], base_bars)
    squeeze = (a_recent / a_base) if a_base > 0 else 1.0
    last_tr = true_range(c1[-1], closes[-2])
    surge = (last_tr / a_base) if a_base > 0 else 0.0
    trend4, _ = trend_structure(c4)

    score = 0
    reasons = [f"breakout_{'up' if broke_up else 'down'}"]
    score += 3  # the breakout trigger

    # Compression precondition — the edge. A coil scores; no coil is penalized.
    if squeeze <= sq_tight:
        score += 2
        reasons.append(f"coil_{squeeze:.2f}")
    elif squeeze <= sq_loose:
        score += 1
        reasons.append(f"coil_{squeeze:.2f}")
    else:
        score -= 1
        reasons.append(f"no_coil_{squeeze:.2f}")

    # Expansion surge — the breakout bar should be an outsized move.
    if surge >= surge_strong:
        score += 2
        reasons.append(f"surge_{surge:.1f}x")
    elif surge >= surge_mod:
        score += 1
        reasons.append(f"surge_{surge:.1f}x")

    # Higher-TF agreement (a break with the 4h structure follows through more).
    if (broke_up and trend4 == "BULLISH") or (broke_dn and trend4 == "BEARISH"):
        score += 1
        reasons.append(f"4h_{trend4.lower()}_agree")
    elif (broke_up and trend4 == "BEARISH") or (broke_dn and trend4 == "BULLISH"):
        score -= 1
        reasons.append("4h_against")

    return {
        "coin": asset, "direction": direction, "score": score,
        "reasons": reasons, "price": price,
        "squeeze": squeeze, "surge": surge, "trend4h": trend4,
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
        "squeeze": round(thesis.get("squeeze", 0), 3),
        "surge": round(thesis.get("surge", 0), 2),
    }

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
# Universe — crypto (breakout) or XYZ (catalyst), liquid names only
# ═══════════════════════════════════════════════════════════════

def build_universe(config, meta_map, canonical):
    """Liquid names on the leg's DEX, capped to universeMaxNames by 24h volume.
    breakout -> main-DEX crypto; catalyst -> XYZ (equities/energy/metals/indices)."""
    vol_floor = float(config.get("volFloorUsd", _DEFAULTS["volFloorUsd"]))
    max_names = int(config.get("universeMaxNames", _DEFAULTS["universeMaxNames"]))
    want_xyz = bool(config.get("wantXyz", _DEFAULTS["wantXyz"]))
    seen, pool = set(), []
    for name in canonical:
        if not isinstance(name, str):
            continue
        is_xyz = name.lower().startswith("xyz:")
        if is_xyz != want_xyz:
            continue
        key = name.upper()
        if key in seen:
            continue
        meta = meta_map.get(name) or meta_map.get(key)
        if not meta:
            continue
        vol = day_vol(meta)
        if vol < vol_floor:
            continue
        seen.add(key)
        pool.append((name, vol))
    pool.sort(key=lambda x: x[1], reverse=True)
    return [n for n, _ in pool[:max_names]]


# ═══════════════════════════════════════════════════════════════
# MAIN — single tick. NO inner scanner_lock; daemon owns it.
# ═══════════════════════════════════════════════════════════════

def main():
    run_start = time.time()
    config = cfg.load_config()

    if not STRATEGY_ADDRESS:
        cfg.output({"status": "error", "reason": "no_wallet", "leg": LEG,
                    "_caracal_producer_version": VERSION})
        return

    account_value, positions = cfg.get_positions(STRATEGY_ADDRESS)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        cfg.output({"status": "ok", "leg": LEG, "note": "no account value",
                    "_caracal_producer_version": VERSION})
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
                    "_caracal_producer_version": VERSION})
        return

    meta_map, canonical = get_universe_meta()
    universe = build_universe(config, meta_map, canonical)

    candidates = []
    recently_skipped = []
    for name in universe:
        if name.upper() in held_set:
            continue
        if cfg.was_recently_signaled(name):
            recently_skipped.append(name)
            continue
        meta = meta_map.get(name) or meta_map.get(name.upper())
        thesis = score_vol_breakout(name, meta, config)
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    if not candidates:
        cfg.output({
            "status": "ok", "leg": LEG,
            "scanned": len(universe), "candidates": 0, "signals_pushed": 0,
            "min_score": min_score, "held_assets": held_assets,
            "recently_signaled_skipped": recently_skipped,
            "note": f"WAITING — no coiled breakout cleared min score {min_score}",
            "elapsed_sec": round(time.time() - run_start, 2),
            "_caracal_producer_version": VERSION,
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
                "margin_usd": margin_usd, "squeeze": round(th["squeeze"], 2),
                "surge": round(th["surge"], 2), "reasons": th["reasons"][:6],
            })

    cfg.output({
        "status": "ok", "leg": LEG,
        "scanned": len(universe), "candidates": len(candidates),
        "open_slots": open_slots, "signals_pushed": pushed, "emitted": emitted,
        "held_assets": held_assets, "recently_signaled_skipped": recently_skipped,
        "account_value": round(account_value, 2),
        "elapsed_sec": round(time.time() - run_start, 2),
        "_caracal_producer_version": VERSION,
    })


if __name__ == "__main__":
    _lock_id = hashlib.sha256(
        (STRATEGY_ADDRESS or LEG).lower().encode()
    ).hexdigest()[:12]
    _tick = int(cfg.load_config().get("tickSeconds", _DEFAULTS["tickSeconds"]))
    producer_daemon(
        fn=main,
        interval_seconds=_tick,
        name=f"caracal-{LEG}-producer-{_lock_id}",
        tick_timeout=min(180, max(30, _tick - 10)),
    )
